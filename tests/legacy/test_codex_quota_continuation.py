"""Offline quota-continuation guards; no model turn or real queue is started."""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
import codex_quota as quota
import quota_continuation as continuation

THREAD = '01a101c6-2488-7a20-8716-9037dcb16138'


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config_path = self.root / 'state' / 'codex_continuation.json'
        self.config_path.parent.mkdir()
        self.session = self.root / 'session.jsonl'
        self.now = time.time()
        self.config = {'enabled': True, 'thread_id': THREAD,
                       'session_path': str(self.session),
                       'not_before_at': self.now - 10}
        self.snapshot = {'ordinary_usage_allowed': True, 'buckets': {'codex': {
            'primary': {'usedPercent': 25, 'windowDurationMins': 300,
                        'resetsAt': self.now + 15000}, 'secondary': None}}}
        self.patches = [patch.object(continuation, 'ROOT', self.root),
                        patch.object(continuation, 'CONFIG', self.config_path),
                        patch.object(continuation, 'manual_pause', return_value={'paused': False}),
                        patch.object(continuation, 'writer_status', return_value='available'),
                        patch.object(continuation, 'read_thread_status', return_value={'type': 'unavailable'})]
        for item in self.patches:
            item.start()
        self.record('task_started', 'task_complete')
        self.save()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def save(self):
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')

    def record(self, *events):
        records = [{'type': 'session_meta', 'payload': {'id': THREAD, 'cwd': str(self.root)}}]
        records += [{'type': 'event_msg', 'payload': {'type': event, 'turn_id': 't1'}}
                    for event in events]
        self.session.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
        os.utime(self.session, (self.now - 1000, self.now - 1000))

    def status(self):
        return continuation.conditions(self.config, self.snapshot, self.now)['status']

    def test_active_old_rollout_never_resumes(self):
        self.record('task_started')
        self.assertEqual('existing_chat_active', self.status())

    def test_aborted_turn_is_terminal(self):
        self.record('task_started', 'turn_aborted')
        self.assertEqual('ready', self.status())

    def test_partial_or_corrupt_rollout_fails_closed(self):
        self.session.write_text('{', encoding='utf-8')
        self.assertEqual('existing_chat_writing', self.status())
        self.session.write_text('{\n', encoding='utf-8')
        self.assertEqual('existing_chat_unreadable', self.status())

    def test_wrong_thread_never_resumes(self):
        self.session.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'different'}}) + '\n')
        self.assertEqual('existing_chat_wrong_thread', self.status())

    def test_recent_write_blocks_even_terminal_rollout(self):
        os.utime(self.session, (self.now, self.now))
        self.assertEqual('existing_chat_recently_active', self.status())

    def test_shared_active_overrides_idle_rollout(self):
        with patch.object(continuation, 'read_thread_status', return_value={'type': 'active'}):
            self.assertEqual('existing_chat_active', self.status())

    def test_idle_chat_with_desktop_writer_does_not_start_cli(self):
        with patch.object(continuation, 'writer_status', return_value='held'):
            result = continuation.conditions(self.config, self.snapshot, self.now)
        self.assertEqual('ready', result['status'])
        self.assertEqual('queue', result['dispatch_kind'])

    def test_failed_writer_conflict_can_retry_without_resetting_real_state(self):
        log = self.root / 'failed.jsonl'
        log.write_text('Failed to create session: already has an active writer\n'
                       'Error: thread/resume failed\n', encoding='utf-8')
        self.config.update(dispatch_status='failed', dispatch_log=str(log),
                           last_window_reset_at=self.snapshot['buckets']['codex']['primary']['resetsAt'])
        self.assertEqual('ready', self.status())
        log.write_text(log.read_text() + '{"type":"turn.started"}\n', encoding='utf-8')
        self.assertEqual('already_dispatched_this_window', self.status())

    def test_active_root_blocks_even_with_previous_window_reservation(self):
        self.config['last_window_reset_at'] = self.snapshot['buckets']['codex']['primary']['resetsAt']
        self.record('task_started')
        self.assertEqual('existing_chat_active', self.status())

    def test_notloaded_does_not_override_active_rollout(self):
        self.record('task_started')
        with patch.object(continuation, 'read_thread_status', return_value={'type': 'notLoaded'}):
            self.assertEqual('existing_chat_active', self.status())

    def test_initial_reset_and_permission_are_both_required(self):
        self.config['not_before_at'] = self.now + 1
        self.assertEqual('waiting_for_initial_reset', self.status())
        self.config['not_before_at'] = self.now - 1
        for permission in (False, None, 1):
            self.snapshot['ordinary_usage_allowed'] = permission
            self.assertEqual('waiting_for_quota_permission', self.status())

    def test_stale_exhausted_or_missing_window_never_resumes(self):
        window = self.snapshot['buckets']['codex']['primary']
        window['usedPercent'] = 100
        self.assertEqual('waiting_for_quota_window', self.status())
        window['usedPercent'] = 1
        window['resetsAt'] = self.now - 1
        self.assertEqual('waiting_for_quota_window', self.status())
        window['windowDurationMins'] = 200
        self.assertEqual('unknown_quota_window', self.status())

    def test_duplicate_window_and_unknown_dispatch_block(self):
        self.config['last_window_reset_at'] = self.snapshot['buckets']['codex']['primary']['resetsAt']
        self.assertEqual('already_dispatched_this_window', self.status())
        self.config.pop('last_window_reset_at')
        self.config['dispatch_status'] = 'dispatch_unknown'
        self.assertEqual('previous_dispatch_needs_review', self.status())

    def test_pause_preserves_offline_work_only(self):
        with patch.object(continuation, 'manual_pause', return_value={'paused': True}):
            result = continuation.conditions(self.config, self.snapshot, self.now)
        self.assertEqual('ready', result['status'])
        self.assertTrue(result['offline_only'])
        self.assertIn('禁止游戏截图和点击', continuation.continuation_prompt(True))
        self.assertIn('禁止调用 clear_manual_pause', continuation.continuation_prompt(True))

    def test_check_only_is_read_only_and_never_spawns(self):
        before = self.config_path.read_bytes()
        with patch.object(continuation, 'read_quota', return_value=self.snapshot), \
                patch.object(continuation.subprocess, 'Popen') as spawn, \
                patch.object(continuation.subprocess, 'run') as queue:
            self.assertEqual('ready', continuation.run(check_only=True)['status'])
        spawn.assert_not_called()
        queue.assert_not_called()
        self.assertEqual(before, self.config_path.read_bytes())
        self.assertFalse(self.config_path.with_suffix('.lock').exists())
        self.assertFalse((self.root / 'logs').exists())

    def test_kernel_lock_prevents_two_workers(self):
        with continuation.dispatch_lock() as first:
            self.assertTrue(first)
            with continuation.dispatch_lock() as second:
                self.assertFalse(second)
        with continuation.dispatch_lock() as subsequent:
            self.assertTrue(subsequent)

    def test_atomic_merge_keeps_completion_disable(self):
        self.config['enabled'] = False
        self.save()
        continuation.merge_dispatch({'dispatch_status': 'finished'}, THREAD)
        value = json.loads(self.config_path.read_text())
        self.assertFalse(value['enabled'])
        self.assertEqual('finished', value['dispatch_status'])
        self.assertFalse(list(self.config_path.parent.glob('*.tmp')))

    def test_prelaunch_failure_is_retryable(self):
        with patch.object(continuation, 'read_quota', return_value=self.snapshot), \
                patch.object(continuation, 'resolve_codex', return_value='mock-codex'), \
                patch.object(continuation.subprocess, 'run', side_effect=FileNotFoundError):
            result = continuation.run()
        value = json.loads(self.config_path.read_text())
        self.assertEqual('failed_launch', result['status'])
        self.assertNotIn('last_window_reset_at', value)
        self.assertGreater(value['retry_after_at'], self.now)

    def test_mock_queue_uses_exact_uuid_and_reserves_unknown_failure(self):
        process = Mock(returncode=2, stdout='')
        with patch.object(continuation, 'read_quota', return_value=self.snapshot), \
                patch.object(continuation, 'resolve_codex', return_value='mock-codex'), \
                patch.object(continuation.subprocess, 'run', return_value=process) as spawn:
            result = continuation.run()
        args = spawn.call_args.args[0]
        self.assertEqual(['queue', '--thread', THREAD], args[1:4])
        self.assertIn('--message', args)
        self.assertNotIn('--last', args)
        self.assertNotIn('--model', args)
        self.assertEqual('previous_dispatch_needs_review', result['status'])
        value = json.loads(self.config_path.read_text())
        self.assertEqual(self.snapshot['buckets']['codex']['primary']['resetsAt'], value['last_window_reset_at'])

    def test_ambiguous_started_launch_never_auto_retries(self):
        with patch.object(continuation, 'read_quota', return_value=self.snapshot), \
                patch.object(continuation, 'resolve_codex', return_value='mock-codex'), \
                patch.object(continuation.subprocess, 'run', side_effect=continuation.subprocess.TimeoutExpired('codex', 30)):
            self.assertEqual('previous_dispatch_needs_review', continuation.run()['status'])
        self.assertEqual('dispatch_unknown', json.loads(self.config_path.read_text())['dispatch_status'])

    def test_successful_queue_exit_is_not_reported_as_consumed(self):
        queued_id = '01a1026c-ba27-7f50-8df9-ca6cd46d19b3'
        process = Mock(returncode=0, stdout=f'Queued message {queued_id} for thread {THREAD}.\n')
        with patch.object(continuation, 'read_quota', return_value=self.snapshot), \
                patch.object(continuation, 'resolve_codex', return_value='mock-codex'), \
                patch.object(continuation.subprocess, 'run', return_value=process):
            result = continuation.run()
        value = json.loads(self.config_path.read_text())
        self.assertEqual('queued_unconfirmed', result['status'])
        self.assertEqual('queued', value['dispatch_status'])
        self.assertEqual(queued_id, value['queued_submission_id'])
        self.assertIn('HYOL_QUOTA_CONTINUATION_', value['queued_prompt_marker'])

    def test_reset_estimate_changes_do_not_allow_thirty_minute_repeat(self):
        self.config.update(dispatch_status='consumed', last_dispatch_epoch=self.now - 1800)
        self.assertEqual('already_dispatched_recently', self.status())

    def test_consumption_requires_queue_deletion_and_real_user_turn(self):
        queued_id = '01a1026c-ba27-7f50-8df9-ca6cd46d19b3'
        marker = 'HYOL_QUOTA_CONTINUATION_TEST'
        db_path = self.root / 'queue_1.sqlite'
        with closing(sqlite3.connect(db_path)) as db:
            db.execute('CREATE TABLE queued_items(id TEXT,thread_id TEXT)')
            db.execute('INSERT INTO queued_items VALUES(?,?)', (queued_id, THREAD))
            db.commit()
        self.config.update(dispatch_status='queued', queued_submission_id=queued_id,
                           queued_prompt_marker=marker, queued_rollout_offset=self.session.stat().st_size)
        with patch.dict(os.environ, {'CODEX_HOME': str(self.root)}):
            self.assertEqual('waiting_for_queued_turn', continuation.queued_dispatch_status(self.config))
            with closing(sqlite3.connect(db_path)) as db:
                db.execute('DELETE FROM queued_items')
                db.commit()
            self.assertEqual('queued_turn_unconfirmed', continuation.queued_dispatch_status(self.config))
            with self.session.open('a', encoding='utf-8') as log:
                log.write(json.dumps({'type': 'event_msg', 'payload': {
                    'type': 'task_started', 'turn_id': 'probe'}}) + '\n')
                log.write(json.dumps({'type': 'response_item', 'payload': {
                    'role': 'user', 'content': [{'type': 'input_text', 'text': marker}]}}) + '\n')
            self.assertEqual('queued_turn_consumed', continuation.queued_dispatch_status(self.config))

    def test_consumed_confirmation_preserves_completion_disable(self):
        self.config['dispatch_status'] = 'queued'
        self.save()
        with patch.object(continuation, 'read_quota', return_value=self.snapshot), \
                patch.object(continuation, 'queued_dispatch_status', return_value='queued_turn_consumed'), \
                patch.object(continuation.subprocess, 'run') as spawn:
            self.assertEqual('queued_turn_consumed', continuation.run()['status'])
        spawn.assert_not_called()
        self.assertEqual('consumed', json.loads(self.config_path.read_text())['dispatch_status'])


class QuotaReaderTests(unittest.TestCase):
    def test_response_only_exports_permission_and_window_fields(self):
        response = {'ordinaryUsageAllowed': True, 'accountSecret': 'do-not-export',
                    'rateLimitResetCredits': 'do-not-export', 'rateLimitsByLimitId': {
                        'codex': {'accountId': 'do-not-export', 'primary': {
                            'usedPercent': 25, 'windowDurationMins': 300,
                            'resetsAt': 2000000000, 'accessToken': 'do-not-export'},
                            'secondary': None}}}
        with patch.object(quota, '_read_rpc', return_value=response) as rpc:
            result = quota.read_quota()
        self.assertNotIn('do-not-export', json.dumps(result))
        self.assertEqual('account/rateLimits/read', rpc.call_args.args[0])
        self.assertTrue(rpc.call_args.args[1]['excludeResetCreditDetails'])

    def test_mutating_rpc_is_rejected_before_any_process(self):
        with patch.object(quota.subprocess, 'Popen') as spawn:
            with self.assertRaises(ValueError):
                quota._read_rpc('account/rateLimits/consumeResetCredit', {})
        spawn.assert_not_called()

    def test_thread_read_requires_shared_transport(self):
        with patch.object(quota, '_read_rpc', return_value={'thread': {
                'id': THREAD, 'status': {'type': 'active', 'activeFlags': []}}}) as rpc:
            self.assertEqual('active', quota.read_thread_status(THREAD)['type'])
        self.assertTrue(rpc.call_args.kwargs['shared'])
        self.assertFalse(rpc.call_args.args[1]['includeTurns'])

    def test_removed_cli_build_falls_back_to_new_install(self):
        with tempfile.TemporaryDirectory() as folder:
            new = Path(folder) / 'OpenAI' / 'Codex' / 'bin' / 'new' / 'codex.exe'
            new.parent.mkdir(parents=True)
            new.touch()
            with patch.object(quota.shutil, 'which', return_value=None), \
                    patch.dict(os.environ, {'LOCALAPPDATA': folder}):
                self.assertEqual(str(new), quota.resolve_codex(str(Path(folder) / 'missing.exe')))


if __name__ == '__main__':
    unittest.main()
