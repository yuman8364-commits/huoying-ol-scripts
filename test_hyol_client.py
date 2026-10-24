"""Unified adapter contracts and real Tk controls; never start a game worker."""
from test_identity_fixtures import ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F, legacy_config
from pathlib import Path
import json
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import tkinter as tk

import hyol_runtime as runtime
from hyol_client import UnifiedClient, INDEPENDENT, format_task_event


def fake_scheduler():
    return SimpleNamespace(config={}, stop=threading.Event(), allow_purchase=True,
                           market_direct_buy_verified=True)


def finished_daily():
    return SimpleNamespace(scheduler=fake_scheduler(), run=Mock(return_value=0),
                           records={'mail': {'status': 'done', 'task_completed': True, 'home_returned': True},
                                    'managed': {'status': 'done', 'task_completed': True, 'evidence': 'verified.png'}})


class EventMessageTests(unittest.TestCase):
    def test_full_backpack_notice_names_the_affected_account_and_required_cleanup(self):
        message = format_task_event(dict(event='mail_backpack_full', account=ROLE_C, tab_index=3), 'team_then_multi')
        self.assertIn(ROLE_C, message)
        self.assertIn('邮件附件未领', message)
        self.assertIn('背包已满，需清理', message)

    def test_solo_pending_uses_identity_and_task_instead_of_configured_tab_number(self):
        event = dict(event='multi_solo_task_pending', account=ROLE_D, tab_index=3,
                     task='mail', detail='原领取结果尚未核实')
        message = format_task_event(event, 'team_then_multi')
        self.assertIn(f'{ROLE_D} 邮件', message)
        self.assertIn('原领取结果尚未核实', message)
        self.assertNotIn('标签3', message)

    def test_captain_and_accountless_stage_have_clear_labels(self):
        captain = dict(event='pipeline_battle_complete', account='当前窗口标签6', tab_index=6,
                       task='group', detail='本日剩余0，友情剩余0')
        self.assertTrue(format_task_event(captain, 'team_then_multi').startswith('标签6 '))
        stage = dict(event='multi_solo_stage_started', task='quiz')
        self.assertEqual(format_task_event(stage, 'team_then_multi'), '忍者测验 开始单人任务')

    def test_solo_completion_and_existing_done_are_visible(self):
        for event_name in ('multi_solo_task_started', 'multi_solo_task_complete', 'multi_solo_task_skip_done'):
            event = dict(event=event_name, account=ROLE_E, tab_index=6, task='welfare')
            self.assertTrue(format_task_event(event, 'team_then_multi').startswith(f'{ROLE_E} 在线福利 '))


class RequestTests(unittest.TestCase):
    def test_defaults_exhaust_tickets_and_keep_five_wins_as_auxiliary(self):
        request = runtime.RunRequest()
        self.assertEqual(request.arena_wins, 5)
        self.assertEqual(request.arena_target_mode, 'tickets')
        self.assertEqual(runtime.RunRequest(arena_target_mode='wins', arena_wins=3).arena_target_mode, 'wins')
        self.assertEqual(request.account_count, 6)
        self.assertNotIn('treasure', request.tasks)
        self.assertTrue(request.final_managed)

    def test_invalid_team_members_or_duplicate_tasks_are_rejected(self):
        for options in ({'mode': 'team', 'leader': 1, 'members': (1,)},
                        {'mode': 'team', 'members': (2, 2)},
                        {'tasks': ('mail', 'mail')}, {'account_count': 7}, {'arena_target_mode': 'unknown'}):
            with self.assertRaises(ValueError):
                runtime.RunRequest(**options)

    def test_two_three_and_friend_selections_use_existing_team_runner(self):
        fake = finished_daily()
        with patch.object(runtime, 'TeamRunner', return_value=fake) as create:
            for members, shortcut in (((2,), False), ((2, 3), False), ((), True)):
                request = runtime.RunRequest(mode='team', members=members, friend_shortcut=shortcut)
                runtime.make_runner(request, Mock(), Path('unused'))
                selection = create.call_args.args[0]
                self.assertEqual(selection.members, members)
                self.assertEqual(selection.friend_shortcut, shortcut)

    def test_all_current_account_entries_reuse_daily_runner_and_prohibit_purchase(self):
        fake = finished_daily()
        with patch.object(runtime, 'DailyRunner', return_value=fake) as create:
            for mode in ('solo', 'arena', 'weekend', 'treasure'):
                request = runtime.RunRequest(mode=mode, tasks=('mail',))
                runtime.make_runner(request, Mock(), Path('unused'))
                self.assertEqual(create.call_args.args, ('all',))
                self.assertEqual(create.call_args.kwargs['selected_tasks'], ('mail',) if mode == 'solo' else (mode,))
                self.assertTrue(create.call_args.kwargs['generic_current_window'])
                self.assertEqual(create.call_args.kwargs['arena_target_mode'], 'tickets')
                self.assertFalse(fake.scheduler.allow_purchase)
                self.assertEqual(fake.scheduler.config['AllowMarketPurchase'], '0')
                self.assertEqual(fake.scheduler.config['ManagedSafetyVerified'], '1')

    def test_multi_and_bond_parameters_reach_their_existing_runners(self):
        fake = finished_daily()
        with patch.object(runtime, 'MultiSoloRunner', return_value=fake) as create:
            runtime.make_runner(runtime.RunRequest(mode='multi', account_count=3, arena_wins=7, arena_target_mode='wins'), Mock(), Path('unused'))
            self.assertEqual(create.call_args.kwargs['account_count'], 3)
            self.assertEqual(create.call_args.kwargs['arena_wins'], 7)
            self.assertEqual(create.call_args.kwargs['arena_target_mode'], 'wins')
        with patch.object(runtime, 'BondRunner', return_value=fake) as create:
            runtime.make_runner(runtime.RunRequest(mode='bond', bond_count=8), Mock(), Path('unused'))
            self.assertEqual(create.call_args.args, (8,))
            self.assertTrue(create.call_args.kwargs['require_identity'])
            self.assertFalse(create.call_args.kwargs['new_round'])

    def test_managed_selection_reaches_every_supported_runner(self):
        fake = finished_daily()
        for enabled in (False, True):
            with self.subTest(enabled=enabled), \
                 patch.object(runtime, 'DailyRunner', return_value=fake) as daily, \
                 patch.object(runtime, 'MultiSoloRunner', return_value=fake) as multi, \
                 patch.object(runtime, 'TeamRunner', return_value=fake) as team:
                for mode, create in (('solo', daily), ('arena', daily), ('treasure', daily),
                                     ('weekend', daily), ('multi', multi), ('team', team)):
                    runtime.make_runner(runtime.RunRequest(mode=mode, final_managed=enabled), Mock(), Path('unused'))
                    self.assertIs(create.call_args.kwargs['managed_enabled'], enabled)

    def test_resumed_bond_keeps_original_target_and_unknown_other_entry_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'client'
            root.mkdir()
            (root/'bond_client_state.json').write_text(json.dumps({'status': 'running', 'requested': 12}), encoding='utf-8')
            self.assertEqual(runtime.bond_requested_count(root, 5), 12)
            shared = Path(folder)/'project/state'
            shared.parent.mkdir()
            (root/'HYOL_Daily.ini').write_text('SharedStateDirectory='+str(shared), encoding='utf-8')
            (shared.parent/'bond_client_state.json').write_text(json.dumps({'status': 'running', 'requested': 9}), encoding='utf-8')
            with self.assertRaisesRegex(runtime.hs.PendingError, '未关联'):
                runtime.bond_requested_count(root, 5)


class CompletionTests(unittest.TestCase):
    def test_disabled_managed_still_needs_all_task_home_and_followup_evidence(self):
        request = runtime.RunRequest(tasks=('mail',), final_managed=False)
        runner = finished_daily()
        runner.records.pop('managed')
        self.assertTrue(runtime.completion_verified(request, runner, 0))
        for changes in ({'status': 'pending'}, {'task_completed': False},
                        {'home_returned': False}, {'followup_check_due': True}):
            with self.subTest(changes=changes):
                runner.records['mail'] = dict(status='done', task_completed=True, home_returned=True, **{})
                runner.records['mail'].update(changes)
                self.assertFalse(runtime.completion_verified(request, runner, 0))

    def test_disabled_multi_and_team_do_not_require_managed_or_fake_it(self):
        local = finished_daily()
        local.records.pop('managed')
        account = SimpleNamespace(name='one')
        multi = SimpleNamespace(scheduler=SimpleNamespace(accounts=[account]), excluded=set(),
                                startup_pending={}, runners={'one': local})
        self.assertTrue(runtime.completion_verified(
            runtime.RunRequest(mode='multi', tasks=('mail',), final_managed=False), multi, 0))
        record = {'phase': 'done', 'source_day': '2026-10-04', 'managed_enabled': False, 'dissolve': {}}
        team = SimpleNamespace(team=SimpleNamespace(members=('one', 'two')),
                               scheduler=SimpleNamespace(state=SimpleNamespace(day='2026-10-04')),
                               _selected_tasks_complete=Mock(return_value=True),
                               _run_record=Mock(return_value=record), _dissolve_complete=Mock(return_value=True))
        self.assertTrue(runtime.completion_verified(runtime.RunRequest(mode='team', final_managed=False), team, 0))
        team._dissolve_complete.return_value = False
        self.assertFalse(runtime.completion_verified(runtime.RunRequest(mode='team', final_managed=False), team, 0))
    def test_zero_exit_needs_task_home_and_managed_evidence(self):
        request = runtime.RunRequest(tasks=('mail',))
        runner = finished_daily()
        self.assertTrue(runtime.completion_verified(request, runner, 0))
        for key in ('home_returned', 'task_completed'):
            runner = finished_daily()
            runner.records['mail'][key] = False
            self.assertFalse(runtime.completion_verified(request, runner, 0))
        runner = finished_daily()
        runner.records['managed'].pop('evidence')
        self.assertFalse(runtime.completion_verified(request, runner, 0))

    def test_calendar_skip_pending_or_survival_followup_never_count_complete(self):
        runner = finished_daily()
        self.assertFalse(runtime.completion_verified(runtime.RunRequest(mode='weekend'), runner, 0))
        runner.records['mail']['status'] = 'pending'
        self.assertFalse(runtime.completion_verified(runtime.RunRequest(tasks=('mail',)), runner, 0))
        runner.records['mail'].update(status='done', followup_check_due=True)
        self.assertFalse(runtime.completion_verified(runtime.RunRequest(tasks=('mail',)), runner, 0))

    def test_multi_requires_all_members_and_no_excluded_account(self):
        account = SimpleNamespace(name='one')
        runner = SimpleNamespace(scheduler=SimpleNamespace(accounts=[account]), excluded=set(),
                                 startup_pending={}, runners={'one': finished_daily()})
        request = runtime.RunRequest(mode='multi', tasks=('mail',))
        self.assertTrue(runtime.completion_verified(request, runner, 0))
        runner.excluded.add('disconnected')
        self.assertFalse(runtime.completion_verified(request, runner, 0))

    def test_team_requires_current_day_full_dissolve_and_each_managed_member(self):
        team = SimpleNamespace(members=('one', 'two'))
        record = {'phase': 'done', 'source_day': '2026-10-04', 'dissolve': {},
                  'managed': {name: {'status': 'done', 'task_completed': True, 'evidence': 'verified.png'}
                              for name in team.members}}
        runner = SimpleNamespace(team=team, scheduler=SimpleNamespace(state=SimpleNamespace(day='2026-10-04')),
                                 _selected_tasks_complete=Mock(return_value=True),
                                 _run_record=Mock(return_value=record), _dissolve_complete=Mock(return_value=True))
        request = runtime.RunRequest(mode='team')
        self.assertTrue(runtime.completion_verified(request, runner, 0))
        record['source_day'] = '2026-10-03'
        self.assertFalse(runtime.completion_verified(request, runner, 0))
        record['source_day'] = '2026-10-04'
        record['managed']['two']['status'] = 'pending'
        self.assertFalse(runtime.completion_verified(request, runner, 0))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        lease_patch=patch.object(runtime.hs,'ForegroundRuntimeLease')
        lease_patch.start();self.addCleanup(lease_patch.stop)
        clearer=patch('hyol_diagnostics.clear_manual_pause')
        clearer.start();self.addCleanup(clearer.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.events = []
        self.controller = runtime.RuntimeController(self.events.append, Path(folder.name))

    def test_single_worker_rejects_another_run_check_or_resume(self):
        worker = Mock()
        worker.is_alive.return_value = True
        with patch.object(runtime.threading, 'Thread', return_value=worker) as create:
            self.assertTrue(self.controller.start(runtime.RunRequest()))
            self.assertFalse(self.controller.check_window())
            self.assertFalse(self.controller.start(runtime.RunRequest()))
            self.assertEqual(create.call_count, 1)
            worker.start.assert_called_once()


    def test_failed_preflight_does_not_start_or_create_runner(self):
        with patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
             patch.object(runtime, 'preflight_live', return_value={'status': 'offline', 'content_clicks': 0}), \
             patch.object(runtime, 'make_runner') as create, patch.object(runtime.hs, 'atomic_json'):
            self.controller._run(runtime.RunRequest(tasks=('mail',)))
        create.assert_not_called()

    def test_pause_after_preflight_or_during_runner_construction_blocks_game_handoff(self):
        for pause_after_construction in (False, True):
            with self.subTest(pause_after_construction=pause_after_construction):
                runner = finished_daily()
                states = [{'paused': False}, {'paused': True}] if pause_after_construction else [{'paused': True}]
                with patch.object(runtime, 'manual_pause_status', side_effect=states), \
                     patch.object(runtime, 'preflight_live', return_value={'status': 'ready', 'healthy': True, 'content_clicks': 0}), \
                     patch.object(runtime, 'make_runner', return_value=runner) as create, \
                     patch.object(runtime, 'mark_manual_pause'), patch.object(runtime.hs, 'atomic_json'):
                    self.controller._run(runtime.RunRequest(tasks=('mail',)))
                runner.run.assert_not_called()
                if pause_after_construction:
                    create.assert_called_once()
                else:
                    create.assert_not_called()
                self.assertEqual(next(event for event in reversed(self.events)
                                      if event['event'] == 'unified_finished')['status'], 'stopped')

    def test_existing_runner_owns_lease_and_pending_is_not_upgraded(self):
        runner = finished_daily()
        runner.run.return_value = 2
        with patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
             patch.object(runtime, 'preflight_live', return_value={'status': 'ready', 'healthy': True, 'content_clicks': 0}), \
             patch.object(runtime, 'make_runner', return_value=runner), \
             patch.object(runtime.hs, 'ForegroundRuntimeLease') as lease, patch.object(runtime.hs, 'atomic_json'):
            self.controller._run(runtime.RunRequest(tasks=('mail',)))
        runner.run.assert_called_once()
        lease.return_value.acquire.assert_called_once()
        lease.return_value.release.assert_called_once()
        self.assertEqual(next(event for event in self.events if event['event'] == 'unified_finished')['status'], 'pending')

    def test_pending_daily_return_retains_its_specific_failure_and_receipt(self):
        runner = finished_daily()
        runner.run.return_value = 2
        runner.last_summary = {'result': 2, 'detail': '原邮件领取未核实，预算已达2次', 'current': 'mail'}
        with patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
             patch.object(runtime, 'preflight_live', return_value={'status': 'ready', 'healthy': True, 'content_clicks': 0}), \
             patch.object(runtime, 'make_runner', return_value=runner), patch.object(runtime.hs, 'atomic_json'):
            self.controller._run(runtime.RunRequest(tasks=('mail',)))
        receipt = next(event for event in self.events if event['event'] == 'unified_finished')
        self.assertEqual(receipt['detail'], runner.last_summary['detail'])
        self.assertEqual(receipt['runner_summary'], runner.last_summary)
        self.assertEqual(receipt['status'], 'pending')

    def test_pending_without_a_runner_summary_never_emits_an_empty_reason(self):
        runner = finished_daily()
        runner.run.return_value = 2
        with patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
             patch.object(runtime, 'preflight_live', return_value={'status': 'ready', 'healthy': True, 'content_clicks': 0}), \
             patch.object(runtime, 'make_runner', return_value=runner), patch.object(runtime.hs, 'atomic_json'):
            self.controller._run(runtime.RunRequest(tasks=('mail',)))
        receipt = next(event for event in self.events if event['event'] == 'unified_finished')
        self.assertTrue(receipt['detail'])
        self.assertEqual(receipt['status'], 'pending')

    def test_stop_sets_signal_before_persisting_pause(self):
        self.controller.worker = Mock()
        self.controller.worker.is_alive.return_value = True
        self.controller.scheduler = fake_scheduler()
        observed = []
        with patch.object(runtime, 'mark_manual_pause', side_effect=lambda *args: observed.append(
                self.controller.cancel.is_set() and self.controller.scheduler.stop.is_set())):
            self.assertTrue(self.controller.stop())
        self.assertEqual(observed, [True])


    def test_pending_bond_does_not_create_finish_scheduler_or_enter_managed(self):
        runner = SimpleNamespace(request={'status': 'running'}, scheduler=SimpleNamespace(
            state=SimpleNamespace(accounts={runtime.BOND_ACCOUNT.name: {'actions': {}}})))
        with patch.object(runtime.hs, 'Scheduler') as create:
            with self.assertRaises(runtime.hs.PendingError):
                self.controller._finish_bond(runner)
        create.assert_not_called()

    def test_resumed_bond_finish_does_not_run_battles_again(self):
        folder = self.controller.root/'state'
        folder.mkdir()
        ticket = {'status': 'pending', 'owner': 'confirmed-role',
                  'request': {'status': 'done', 'requested': 5, 'completed': 5, 'run_id': 'original-run', 'owner': 'confirmed-role', 'phase': 'ready'}}
        (folder/'unified_bond_finish.json').write_text(json.dumps(ticket), encoding='utf-8')
        runner = finished_daily()
        runner.request = {}
        with patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
             patch.object(runtime, 'preflight_live', return_value={'status': 'ready', 'healthy': True, 'content_clicks': 0}), \
             patch.object(runtime, 'make_runner', return_value=runner), \
             patch.object(self.controller, '_finish_bond', return_value='verified.png') as finish, \
             patch.object(runtime.hs, 'atomic_json'):
            self.controller._run(runtime.RunRequest(mode='bond'))
        runner.run.assert_not_called()
        self.assertEqual(runner.request['run_id'], 'original-run')
        self.assertEqual(runner._unified_owner, 'confirmed-role')
        finish.assert_called_once_with(runner, managed_enabled=True)

    def test_corrupt_bond_finish_ticket_never_starts_fresh_battles(self):
        folder = self.controller.root/'state'
        folder.mkdir()
        original = {'status': 'done', 'requested': 5, 'completed': 5, 'run_id': 'original-run', 'owner': 'confirmed-role', 'phase': 'ready'}
        for ticket in ({'status': 'unknown', 'request': original},
                       {'status': 'pending', 'request': {**original, 'completed': 4}},
                       {'status': 'pending', 'request': {**original, 'requested': True}},
                       {'status': 'pending', 'owner': 'confirmed-role', 'request': {**original, 'phase': 'running'}},
                       {'status': 'pending', 'owner': 'other-role', 'request': original},
                       {'status': 'done', 'request': original}):
            with self.subTest(ticket=ticket):
                (folder/'unified_bond_finish.json').write_text(json.dumps(ticket), encoding='utf-8')
                runner = finished_daily()
                with patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
                     patch.object(runtime, 'preflight_live', return_value={'status': 'ready', 'healthy': True, 'content_clicks': 0}), \
                     patch.object(runtime, 'make_runner', return_value=runner), \
                     patch.object(runtime.hs, 'atomic_json'):
                    self.controller._run(runtime.RunRequest(mode='bond'))
                runner.run.assert_not_called()
                self.assertEqual(next(event for event in reversed(self.events) if event['event'] == 'unified_finished')['status'], 'pending')

    def test_completed_bond_ticket_is_idempotent_until_explicit_new_round(self):
        folder = self.controller.root/'state'
        folder.mkdir()
        original = {'status': 'done', 'requested': 5, 'completed': 5, 'run_id': 'original-run', 'owner': 'confirmed-role', 'phase': 'ready'}
        ticket = {'status': 'done', 'owner': 'confirmed-role', 'request': original, 'evidence': 'old.png'}
        for new_round in (False, True):
            with self.subTest(new_round=new_round):
                (folder/'unified_bond_finish.json').write_text(json.dumps(ticket), encoding='utf-8')
                runner = finished_daily()
                runner.request = dict(original, run_id='new-run')
                runner._unified_owner = 'confirmed-role'
                runner.scheduler._last_frame = object()
                runner.scheduler.identity_scores = Mock(return_value={'confirmed-role': 1., 'other': .5})
                with patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
                     patch.object(runtime, 'preflight_live', return_value={'status': 'ready', 'healthy': True, 'content_clicks': 0}), \
                     patch.object(runtime, 'make_runner', return_value=runner), \
                     patch.object(self.controller, '_finish_bond', return_value='verified.png') as finish, \
                     patch.object(runtime.hs, 'atomic_json'):
                    self.controller._run(runtime.RunRequest(mode='bond', new_bond_round=new_round))
                if new_round:
                    runner.run.assert_called_once()
                    self.assertEqual('new-run', runner.request['run_id'])
                else:
                    runner.run.assert_not_called()
                    self.assertEqual('original-run', runner.request['run_id'])
                finish.assert_called_once_with(runner, managed_enabled=True)

    def test_new_round_cannot_override_pending_bond_finish(self):
        folder = self.controller.root/'state'
        folder.mkdir()
        original = {'status': 'done', 'requested': 5, 'completed': 5, 'run_id': 'original-run', 'owner': 'confirmed-role', 'phase': 'ready'}
        (folder/'unified_bond_finish.json').write_text(json.dumps(
            {'status': 'pending', 'owner': 'confirmed-role', 'request': original}), encoding='utf-8')
        runner = finished_daily()
        with patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
             patch.object(runtime, 'preflight_live', return_value={'status': 'ready', 'healthy': True, 'content_clicks': 0}), \
             patch.object(runtime, 'make_runner', return_value=runner), \
             patch.object(self.controller, '_finish_bond', return_value='verified.png'), \
             patch.object(runtime.hs, 'atomic_json'):
            self.controller._run(runtime.RunRequest(mode='bond', new_bond_round=True))
        runner.run.assert_not_called()
        self.assertEqual('original-run', runner.request['run_id'])

    def test_bond_finish_runtime_permission_still_uses_real_managed_gate(self):
        scheduler = fake_scheduler()
        scheduler.state = SimpleNamespace(accounts={runtime.BOND_ACCOUNT.name: {'actions': {}}})
        runner = SimpleNamespace(request={'status': 'done', 'requested': 5, 'completed': 5, 'run_id': 'original-run', 'owner': 'confirmed-role', 'phase': 'ready'},
                                 scheduler=scheduler, _unified_owner='confirmed-role')
        final = fake_scheduler()
        final.start_f12_monitor = Mock()
        final.bind_window = Mock()
        final.bind_generic_current_window = Mock(return_value=SimpleNamespace(name='confirmed-role'))
        final.run_managed_gate = Mock()
        final.managed_active_score = Mock(return_value=0.)
        final.capture = Mock(return_value='new-real-frame')
        final.save_raw_evidence = Mock(return_value='verified.png')
        with patch.object(runtime.hs, 'Scheduler', return_value=final), \
             patch.object(runtime.hs, 'ForegroundRuntimeLease') as lease, \
             patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
             patch.object(runtime, 'read_record', return_value=runner.request), \
             patch('hyol_navigation.return_to_leaf') as home:
            lease.return_value.acquire.return_value = True
            self.assertEqual(self.controller._finish_bond(runner), 'verified.png')
        self.assertEqual(final.config['ManagedSafetyVerified'], '1')
        self.assertEqual(final.config['AllowMarketPurchase'], '0')
        home.assert_called_once()
        final.run_managed_gate.assert_called_once_with(final.bind_generic_current_window.return_value)
        lease.return_value.release.assert_called_once()

    def test_bond_finish_home_only_and_managed_resume_do_not_restart_game_chain(self):
        runner = SimpleNamespace(request={'status': 'done', 'requested': 5, 'completed': 5,
                                         'run_id': 'original-run', 'owner': 'confirmed-role', 'phase': 'ready'},
                                 scheduler=fake_scheduler(), _unified_owner='confirmed-role')
        runner.scheduler.state = SimpleNamespace(accounts={runtime.BOND_ACCOUNT.name: {'actions': {}}})
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                final = fake_scheduler()
                final.start_f12_monitor = Mock()
                final.bind_window = Mock()
                final.bind_generic_current_window = Mock(return_value=SimpleNamespace(name='confirmed-role'))
                final.capture = Mock(return_value='frame')
                final.managed_active_score = Mock(return_value=.95)
                final.run_managed_gate = Mock()
                final.save_raw_evidence = Mock(return_value='new.png')
                with patch.object(runtime.hs, 'Scheduler', return_value=final), \
                     patch.object(runtime.hs, 'ForegroundRuntimeLease') as lease, \
                     patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
                     patch.object(runtime, 'read_record', return_value=runner.request), \
                     patch('hyol_navigation.return_to_leaf') as home:
                    lease.return_value.acquire.return_value = True
                    self.controller._finish_bond(runner, managed_enabled=enabled)
                if enabled:
                    home.assert_not_called()
                    final.run_managed_gate.assert_called_once()
                else:
                    home.assert_called_once()
                    final.run_managed_gate.assert_not_called()

    def test_bond_finish_mismatched_canonical_round_never_navigates(self):
        runner = SimpleNamespace(request={'status': 'done', 'requested': 5, 'completed': 5,
                                         'run_id': 'original-run', 'owner': 'confirmed-role', 'phase': 'ready'},
                                 scheduler=fake_scheduler(), _unified_owner='confirmed-role')
        runner.scheduler.state = SimpleNamespace(accounts={runtime.BOND_ACCOUNT.name: {'actions': {}}})
        final = fake_scheduler()
        final.start_f12_monitor = Mock()
        final.bind_window = Mock()
        final.bind_generic_current_window = Mock(return_value=SimpleNamespace(name='confirmed-role'))
        final.run_managed_gate = Mock()
        with patch.object(runtime.hs, 'Scheduler', return_value=final), \
             patch.object(runtime.hs, 'ForegroundRuntimeLease') as lease, \
             patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), \
             patch.object(runtime, 'read_record', return_value={**runner.request, 'run_id': 'other-run'}), \
             patch('hyol_navigation.return_to_leaf') as home:
            lease.return_value.acquire.return_value = True
            with self.assertRaisesRegex(runtime.hs.PendingError, '原轮次不一致'):
                self.controller._finish_bond(runner)
        home.assert_not_called()
        final.run_managed_gate.assert_not_called()

    def test_unconfirmed_bond_owner_remains_pending_without_new_scheduler(self):
        scheduler = fake_scheduler()
        scheduler.state = SimpleNamespace(accounts={runtime.BOND_ACCOUNT.name: {'actions': {}}})
        runner = SimpleNamespace(request={'status': 'done', 'requested': 5, 'completed': 5, 'run_id': 'original-run', 'owner': 'confirmed-role', 'phase': 'ready'}, scheduler=scheduler)
        with patch.object(runtime.hs, 'Scheduler') as create:
            with self.assertRaisesRegex(runtime.hs.PendingError, '原羁绊账号'):
                self.controller._finish_bond(runner)
        create.assert_not_called()


class GuiControlTests(unittest.TestCase):
    def test_mail_cleanup_notice_persists_while_another_task_runs_and_clears_only_when_resolved(self):
        self.app.active_request = runtime.RunRequest(mode='multi', tasks=('mail', 'welfare'), final_managed=False)
        mail = dict(status='pending', last_error='普通背包已满，邮件附件保留待领取')
        tasks = dict(mail=mail, welfare=dict(status='running'))
        self.app.progress_view.refresh = Mock()
        self.app.progress_view.snapshot = dict(rows=[dict(index=3, account=ROLE_C, label='标签3',
            health='已绑定', tasks=tasks)])
        self.app._refresh_account_list()
        values = self.app.account_tree.item('3', 'values')
        self.assertEqual((ROLE_C, '背包满需清理', '邮件', '0/2'), values)
        self.assertEqual('pending', mail['status'])
        mail.update(status='done', last_error='')
        self.app._refresh_account_list()
        self.assertEqual((ROLE_C, '运行中', '在线福利', '1/2'), self.app.account_tree.item('3', 'values'))
        self.assertIsNone(self.app.controller.worker)

    def test_manual_single_team_choice_is_visible_and_reaches_request(self):
        self.app.tabs.select(1)
        self.app.team_mode.set('已有队伍 → 多人单人')
        self.app._team_mode_changed()
        self.app.pipeline_auto.set(False)
        self.app._pipeline_auto_changed()
        self.app.pipeline_leaders[0].set('1')
        self.app.pipeline_leaders[1].set('不选')
        self.assertIn('不选',self.app.pipeline_leader_controls[1]['values'])
        self.assertNotIn('不选',self.app.pipeline_leader_controls[0]['values'])
        request = self.app.request()
        self.assertEqual(request.mode,'team_then_multi')
        self.assertEqual(request.leaders,(1,))

    def setUp(self):
        # Build actual Tk controls withdrawn, so this never takes game focus.
        self.app = UnifiedClient(root=Path(tempfile.gettempdir())/'unified-gui-contract-unused')
        self.app.withdraw()
        self.app.update_idletasks()
        self.addCleanup(self.app.destroy)

    def test_real_controls_have_safe_defaults_and_no_started_worker(self):
        self.assertEqual(self.app.request().mode, 'solo')
        self.assertEqual(self.app.arena_wins.get(), '5')
        self.assertFalse(self.app.task_vars['treasure'].get())
        self.assertTrue(self.app.final_managed.get())
        self.assertIsNone(self.app.controller.worker)
        self.assertEqual(self.app.tabs.index('end'), 3)
        self.assertFalse(self.app.new_bond_round.get())
        self.assertFalse(self.app.managed_control.instate(('disabled',)))

    def test_real_managed_checkbox_can_disable_the_actual_request(self):
        self.app.managed_control.invoke()
        self.assertFalse(self.app.final_managed.get())
        self.assertFalse(self.app.request().final_managed)
        self.app.managed_control.invoke()
        self.assertTrue(self.app.request().final_managed)

    def test_each_independent_entry_and_multi_scope_produce_correct_request(self):
        self.app.scope.set('多个账号')
        self.assertEqual(self.app.request().mode, 'multi')
        self.app.tabs.select(2)
        for label, mode in INDEPENDENT.items():
            self.app.independent.set(label)
            self.assertEqual(self.app.request().mode, mode)
        self.assertIsNone(self.app.controller.worker)

    def test_white_compact_ui_shows_settings_for_the_selected_independent_task(self):
        self.assertEqual(self.app.cget('background').lower(), '#ffffff')
        self.app.tabs.select(2)
        self.app.independent.set('羁绊')
        self.app._refresh_task_settings()
        self.assertEqual(self.app.current_setting_codes, ('bond',))
        self.assertIsNotNone(self.app.bond_count_control)
        self.assertIsNone(self.app.arena_target_control)
        self.assertTrue(self.app.formation_control.winfo_manager() == '')
        self.assertTrue(self.app.managed_control.winfo_manager() == '')

        self.app.independent.set('挖宝（5分钟）')
        self.app._refresh_task_settings()
        self.assertEqual(self.app.current_setting_codes, ('treasure',))
        labels = []
        def collect_text(widget):
            for child in widget.winfo_children():
                try:
                    value = child.cget('text')
                    if value:
                        labels.append(str(value))
                except tk.TclError:
                    pass
                collect_text(child)
        collect_text(self.app.task_specific_box)
        self.assertTrue(any('固定 5 分钟' in value for value in labels))

        self.app.independent.set('竞技场')
        self.app._refresh_task_settings()
        self.assertEqual(self.app.current_setting_codes, ('arena',))
        self.assertIsNotNone(self.app.arena_target_control)
        self.assertTrue(self.app.arena_wins_control.instate(('disabled',)))
        self.assertEqual(self.app.tabs.index('end'), 3)

    def test_explicit_new_bond_round_is_consumed_by_one_start(self):
        self.app.tabs.select(2)
        self.app.independent.set('羁绊')
        self.app.new_bond_round.set(True)
        with patch.object(self.app.controller, 'start', return_value=True) as start:
            self.app._start()
        self.assertTrue(start.call_args.args[0].new_bond_round)
        self.assertFalse(self.app.new_bond_round.get())
        self.assertFalse(self.app.request().new_bond_round)

    def test_team_controls_support_two_three_and_friend_mode_without_role_names(self):
        self.app.tabs.select(1)
        self.assertEqual(self.app.request().members, (2, 3))
        self.app.member_vars[3].set(False)
        self.assertEqual(self.app.request().members, (2,))
        self.app.friend_shortcut.set(True)
        self.assertTrue(self.app.request().friend_shortcut)
        self.app._friend_changed()
        self.assertTrue(all(button.instate(('disabled',)) for button in self.app.member_controls))
        self.assertIsNone(self.app.controller.worker)


if __name__ == '__main__':
    unittest.main()
