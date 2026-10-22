from test_identity_fixtures import ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F, legacy_config
import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

import hyol_multi_solo as multi
import hyol_scheduler as hs


class MultiSoloTests(unittest.TestCase):
    def test_survival_stage_starts_all_accounts_in_one_interleaved_scheduler(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        accounts = [hs.Account(1, ROLE_A, 180), hs.Account(2, ROLE_B, 310)]
        runners = {}
        records = {}
        for account in accounts:
            record = {'status': 'new', 'task_completed': False,
                      'home_returned': False, 'followup_check_due': False}
            records[account.name] = record
            runners[account.name] = SimpleNamespace(
                account=account, formation_checked=False,
                record=lambda task, record=record: record,
                visible=Mock(return_value=False),
                ensure_formation_once=Mock(),
                save=Mock(),
            )
        state = SimpleNamespace(
            accounts={account.name: {'actions': {}} for account in accounts},
            action_status=Mock(return_value='new'),
        )
        scheduler = SimpleNamespace(
            accounts=list(accounts), by_name={account.name: account for account in accounts},
            state=state, navigator='original-navigator', _task_observation=None,
            survival_inline_two_runs=False, capture=Mock(return_value=Image.new('RGB', (1920, 1032))),
            _combat_active_visible=Mock(return_value=False),
            _battle_hud_visible=Mock(return_value=False),
            template=Mock(return_value=(False, 0.0, 0, 0)),
            run_combat_interleaved=Mock(),
            _account_module_complete=Mock(return_value=True),
        )
        def run_interleaved(mode):
            self.assertEqual(mode, 'survival')
            self.assertEqual(scheduler.accounts, accounts)
            self.assertEqual(set(scheduler._combat_navigators), {account.name for account in accounts})
        scheduler.run_combat_interleaved.side_effect = run_interleaved
        coordinator.scheduler = scheduler
        coordinator.startup_returned = {account.name for account in accounts}
        coordinator.manual_deferred = set()
        coordinator.deferred = {}
        coordinator.deferred_at = {}
        coordinator.excluded = set()
        coordinator.disconnected = set()
        coordinator.blocked_tasks = {}
        coordinator.check_account = Mock(return_value=True)
        coordinator.runner_for = lambda account: runners[account.name]
        coordinator.note_skipped = Mock()
        coordinator.event = Mock()
        coordinator.save_progress = Mock()

        self.assertTrue(coordinator._run_survival_stage())

        scheduler.run_combat_interleaved.assert_called_once_with('survival')
        self.assertEqual(scheduler.accounts, accounts)
        self.assertEqual(scheduler.navigator, 'original-navigator')
        self.assertFalse(scheduler.survival_inline_two_runs)
        for account in accounts:
            self.assertEqual(records[account.name]['status'], 'done')
            self.assertFalse(records[account.name]['followup_check_due'])

    def test_ranking_stage_round_robins_after_each_end_battle_yield(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        accounts = [hs.Account(1, ROLE_A, 180), hs.Account(2, ROLE_B, 310)]
        runners = {}
        records = {}
        call_order = []
        for account in accounts:
            record = {'status': 'new', 'phase': 'ready', 'completed': 0,
                      'remaining': 2, 'zero_verified': False}
            records[account.name] = record
            count = {'value': 0}
            def ranking_step(account=account, record=record, count=count):
                count['value'] += 1
                call_order.append(account.name)
                if count['value'] == 1:
                    record.update(phase='end_sent', status='running')
                    return 'yield_after_end'
                if count['value'] == 2:
                    return 'yield_waiting_result'
                record.update(phase='ready', zero_verified=True, remaining=0, completed=2)
                return None
            runners[account.name] = SimpleNamespace(
                account=account, record=lambda task, record=record: record,
                save=Mock(), ranking=Mock(side_effect=ranking_step),
                return_to_main=Mock(), interleave_after_end=False,
            )
        scheduler = SimpleNamespace(
            accounts=list(accounts), by_name={account.name: account for account in accounts},
            state=SimpleNamespace(action_status=Mock(return_value='new')),
            check_pause_boundary=Mock(return_value=False), check_stop=Mock(),
            switch_account_for_combat=Mock(), wait_interruptible=Mock(),
            capture=Mock(return_value=Image.new('RGB', (1920, 1032))),
            main_ready=Mock(return_value=True),
            template=Mock(return_value=(True, 1.0, 0, 0)),
        )
        coordinator.scheduler = scheduler
        coordinator.startup_returned = {account.name for account in accounts}
        coordinator.manual_deferred = set()
        coordinator.deferred = {}
        coordinator.deferred_at = {}
        coordinator.excluded = set()
        coordinator.disconnected = set()
        coordinator.blocked_tasks = {}
        coordinator.runner_for = lambda account: runners[account.name]
        coordinator.note_skipped = Mock()
        coordinator.skip_disconnected = Mock()
        coordinator.skip_stalled = Mock()
        coordinator.event = Mock()
        coordinator.save_progress = Mock()

        clock = {'value': 0.0}
        scheduler.wait_interruptible.side_effect = lambda seconds: clock.update(value=clock['value'] + seconds)
        with patch.object(multi.time, 'monotonic', side_effect=lambda: clock['value']):
            self.assertTrue(coordinator._run_ranking_stage())

        self.assertEqual(call_order, [accounts[0].name, accounts[1].name,
                                      accounts[0].name, accounts[1].name,
                                      accounts[0].name, accounts[1].name])
        self.assertEqual([call.args[0].name for call in scheduler.switch_account_for_combat.call_args_list],
                         call_order)
        for account in accounts:
            self.assertEqual(records[account.name]['status'], 'done')
            self.assertTrue(records[account.name]['zero_verified'])
            self.assertEqual(records[account.name]['remaining'], 0)

    def test_arena_stage_does_not_restart_accounts_already_audited(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        audited = hs.Account(1, ROLE_A, 180)
        pending = hs.Account(2, ROLE_B, 310)
        state = SimpleNamespace(global_state={'arena_requests': {}})
        pending_runner = Mock(account=pending, current=None)
        pending_record = {'status': 'new'}
        pending_runner.record.return_value = pending_record
        scheduler = SimpleNamespace(
            accounts=[audited, pending],
            by_name={audited.name: audited, pending.name: pending},
            state=state,
            prepare_additional_arena_request=Mock(),
            run_combat_interleaved=Mock(),
            _account_module_complete=Mock(return_value=False),
            _arena_victory_count=Mock(return_value=0),
        )
        def prepare(account, wins, *, target_mode):
            state.global_state['arena_requests'][account.name] = {
                'status': 'running', 'attempt_limit': 10, 'target_mode': target_mode,
            }
        scheduler.prepare_additional_arena_request.side_effect = prepare
        coordinator.scheduler = scheduler
        coordinator.startup_returned = {audited.name, pending.name}
        coordinator.manual_deferred = set()
        coordinator.deferred = {}
        coordinator.excluded = set()
        coordinator.disconnected = set()
        coordinator.arena_rewards_audited = {audited.name}
        coordinator.arena_target_mode = 'tickets'
        coordinator.arena_wins = 5
        coordinator.check_account = Mock(return_value=True)
        coordinator.note_skipped = Mock()
        coordinator.runner_for = Mock(return_value=pending_runner)
        coordinator.event = Mock()
        coordinator._run_arena_stage()
        scheduler.prepare_additional_arena_request.assert_called_once_with(
            pending, 5, target_mode='tickets')
        scheduler.run_combat_interleaved.assert_called_once_with('arena')
        self.assertEqual('pending', pending_record['status'])
        self.assertEqual(1, coordinator.runner_for.call_count)

    def test_scheduler_accepts_seven_configured_solo_identity_candidates(self):
        names = [f'账号{i}' for i in range(7)]
        config = {'AccountMode': 'tab6', 'TabAccountNames': ','.join(names),
                  'TabXs': ','.join(str(100 + i*100) for i in range(7))}
        scheduler = hs.Scheduler(config, 'multi_solo', False, True)
        self.assertEqual(7, len(scheduler.accounts))
        self.assertEqual(set(names), set(scheduler.by_name))

    def test_x_accounts_can_be_chosen_from_six_verified_tabs(self):
        runner = multi.MultiSoloRunner(('mail',), account_count=3)
        self.assertEqual(6, len(runner.scheduler.accounts))
        frame = Image.open(hs.LOGS / 'multi_solo_six_tabs_reference_20260923.png')
        runner.scheduler.capture = Mock(return_value=frame)
        runner.scheduler.wait_interruptible = Mock()
        runner.event = Mock()
        self.assertEqual(6, len(runner.discover_tabs()))
        runner.scheduler.rebind_tabs_by_identity = Mock()
        runner.scheduler.available_account_names = {a.name for a in runner.scheduler.accounts}
        with patch('hyol_accounts.discover_accounts',return_value=(
                tuple(a.tab_x for a in runner.scheduler.accounts),
                {a.index:a.name for a in runner.scheduler.accounts},{},{})):
            runner.bind_accounts()
        self.assertEqual(3, len(runner.scheduler.accounts))
        self.assertEqual(3, len(runner.scheduler.by_name))

    def test_qinqin_arena_page_timeout_skips_to_next_account(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        qinqin = hs.Account(1, ROLE_F, 445)
        next_account = hs.Account(2, ROLE_A, 180)
        scheduler = Mock(accounts=[qinqin, next_account], active=qinqin)
        scheduler.state.global_state = {}
        scheduler._combat_active_visible = Mock(return_value=False)
        scheduler.ensure_account_main_before_release.return_value = True
        scheduler.main_ready.return_value = True
        scheduler.template.return_value = (True, 1.0, 0, 0)
        coordinator.scheduler = scheduler
        coordinator.event = Mock()
        first = Mock(account=qinqin)
        first_record = {'status': 'new'}
        first.record.return_value = first_record
        first.arena.side_effect = hs.PendingError('arena page timeout')
        coordinator._run_task(first, 'arena')
        self.assertEqual('pending', first_record['status'])
        second = Mock(account=next_account)
        second_record = {'status': 'new'}
        second.record.return_value = second_record
        coordinator._run_task(second, 'mail')
        self.assertEqual('done', second_record['status'])

    def test_xiaohei_occluded_identity_skips_without_disconnect(self):
        runner = object.__new__(multi.MultiSoloRunner)
        runner.scheduler = Mock()
        runner.scheduler.available_account_names = set()
        runner.scheduler.template.return_value = (False, 0, 0, 0)
        runner.scheduler.healthy.return_value = True
        runner.scheduler._combat_active_visible.return_value = True
        runner.scheduler.switch_account.side_effect = hs.PendingError('identity occluded')
        runner._tab_exists = Mock(return_value=True)
        runner.stall_checks = {}
        runner.last_failed_at = {}
        runner.excluded = set()
        runner.disconnected = set()
        runner.exclusion_reasons = {}
        runner.event = Mock()
        runner.save_progress = Mock()
        account = hs.Account(1, ROLE_B, 590)
        with patch.object(multi.time, 'monotonic', side_effect=(0.0, 4.0)):
            self.assertFalse(runner.check_account(account))
            self.assertFalse(runner.check_account(account))
        self.assertEqual({ROLE_B}, runner.excluded)
        self.assertEqual('activity_occluded_identity', runner.exclusion_reasons[account.name])
        self.assertEqual(set(), runner.disconnected)

    def test_arena_loading_wait_defers_without_restarting_battle(self):
        scheduler = object.__new__(hs.Scheduler)
        scheduler.offline_dialog_visible = Mock(return_value=False)
        scheduler.template = Mock(side_effect=lambda name, *_args, **_kwargs:
                                  (name == 'arena_loading_anchor.bmp', 1.0, 0, 0))
        scheduler._arena_victory_visible = Mock(return_value=False)
        scheduler._arena_defeat_visible = Mock(return_value=False)
        scheduler.save_raw_evidence = Mock(return_value='loading.png')
        scheduler.event = Mock()
        scheduler.click_template = Mock()
        slot = hs.CombatSlot(hs.Account(1, 'a', 180), 'arena', running=True)
        frame = Image.new('RGB', (1920, 1032))
        with patch.object(hs.time, 'monotonic', side_effect=(10.0, 40.0)):
            scheduler._combat_progress_watchdog(slot, frame)
        with self.assertRaisesRegex(hs.BattleStalled, '页面连续30秒无变化'):
                scheduler._combat_progress_watchdog(slot, frame)
        scheduler.click_template.assert_not_called()

    def test_yesterday_arena_ready_and_loading_screens_replay_distinctly(self):
        scheduler = hs.Scheduler(legacy_config(), 'multi_solo', False, True)
        ready = Image.open(hs.ROOT / 'logs/scheduler_evidence/2026-09-30/account_c9ba2069ff/220833_720774_before_arena_casual_battle_4.jpg').convert('RGB')
        loading = Image.open(hs.ROOT / 'logs/scheduler_evidence/2026-09-30/account_72cd26215f/201638_552398_pending_arena_queue_stalled.png').convert('RGB')
        self.assertTrue(scheduler._combat_ready_visible('arena', ready))
        self.assertTrue(scheduler.template('arena_loading_anchor.bmp', hs.ARENA_LOADING_ROI, loading)[0])
        self.assertFalse(scheduler._combat_ready_visible('arena', loading))

    def test_yesterday_return_icon_variants_replay_all_pending_start_frames(self):
        store = hs.TemplateStore(.90)
        frames = sorted((hs.ROOT / 'logs/scheduler_evidence/2026-09-30').rglob(
            '*pending_multi_solo_start_before_return.png'))
        self.assertEqual(15, len(frames))
        for path in frames:
            with Image.open(path) as frame:
                matched, _score, x, y = store.match(
                    frame.convert('RGB'), hs.ASSETS / 'leaf_return_current.bmp',
                    (1870, 69, 1920, 125))
            self.assertTrue(matched, path.name)
            self.assertEqual((1892, 78), (x, y), path.name)

    def test_preclaimed_survival_reward_reconciles_without_second_click(self):
        scheduler = object.__new__(hs.Scheduler)
        account = hs.Account(1, 'a', 180)
        scheduler.state = Mock()
        scheduler.state.action_status.return_value = 'new'
        scheduler.template = Mock(side_effect=lambda name, *_args, **_kwargs:
                                  (name == 'survival_final_claimed.bmp', 1.0, 0, 0))
        scheduler.commit_action = Mock()
        scheduler.click_template = Mock()
        scheduler.claim_survival_rewards(account)
        scheduler.commit_action.assert_called_once()
        self.assertEqual('survival_claim_rewards_run_1', scheduler.commit_action.call_args.args[1])
        scheduler.click_template.assert_not_called()

    def test_waiting_battle_overlay_blocks_underlying_arena_close(self):
        scheduler = object.__new__(hs.Scheduler)
        waiting = Image.open(hs.LOGS / 'arena_unknown_after_stop_20260926.png')
        battle = Image.open(hs.LOGS / 'arena_live_observe_20260926_1120.png')
        ready = Image.open(hs.LOGS / 'arena_resume_ready_20260913.png')
        self.assertTrue(scheduler._arena_waiting_battle_visible(waiting))
        self.assertFalse(scheduler._arena_waiting_battle_visible(battle))
        self.assertFalse(scheduler._arena_waiting_battle_visible(ready))
        scheduler.template = Mock(return_value=(False, 0, 0, 0))
        scheduler.click_template = Mock()
        self.assertFalse(scheduler.recover_known_page(hs.Account(1, 'a', 180), waiting))
        scheduler.click_template.assert_not_called()

    def test_five_tabs_two_unknown_slots_run_only_four_verified_accounts(self):
        scheduler = object.__new__(hs.Scheduler)
        names = ('a', 'b', 'c', 'd', 'e', 'f')
        scheduler.accounts = [hs.Account(i, name, x) for i, (name, x) in enumerate(
            zip(names, (180, 310, 445, 590, 730, 860)), 1)]
        scheduler.by_name = {account.name: account for account in scheduler.accounts}
        scheduler.state = Mock(global_state={})
        scheduler.active = None
        scheduler.no_content_clicks = True
        scheduler.command_kind = 'multi_solo'
        scheduler.config = {}
        scheduler.identity_assignment_threshold = .9
        scheduler.identity_assignment_margin = .1
        scheduler.event = Mock()
        scheduler.wait_interruptible = Mock()
        scheduler.healthy = Mock(return_value=True)
        def capture():
            frame = Image.new('RGB', (1920, 1032))
            frame.info['probe_x'] = getattr(scheduler, '_probe_x', 0)
            return frame
        scheduler.capture = Mock(side_effect=capture)
        scheduler.chrome_click = Mock(side_effect=lambda x, *_: setattr(scheduler, '_probe_x', x))
        scheduler.template = Mock(side_effect=lambda name, *_args, **_kwargs:
                                  (True, 1.0, 840, 0) if name == 'microclient_account_multi.bmp'
                                  else (False, 0.0, 0, 0))
        known = {180: 'a', 445: 'c', 590: 'd', 730: 'e'}
        scheduler.identity_visible_for_rebind = Mock(side_effect=lambda *_args: scheduler._probe_x in known)
        scheduler.identity_scores = Mock(side_effect=lambda frame:
                                         {name: (1.0 if name == known.get(frame.info['probe_x']) else .1)
                                          for name in names})
        with tempfile.TemporaryDirectory() as evidence, \
             patch.object(hs, 'EVIDENCE_DIR', Path(evidence)), \
             patch.object(hs.time, 'sleep'), patch.object(hs, 'atomic_json'):
            scheduler.rebind_tabs_by_identity(skip_unavailable=True, verified_only=True)
        self.assertEqual(set(known.values()), set(scheduler.by_name))
        self.assertEqual(set(known.values()), scheduler.available_account_names)
        self.assertNotIn('tab_map', scheduler.state.global_state)
        self.assertEqual({name: x for x, name in known.items()},
                         scheduler.state.global_state['verified_solo_tab_map'])
        self.assertEqual(5, scheduler.chrome_click.call_count)

    def test_welfare_grey_claim_button_is_not_clickable(self):
        scheduler = object.__new__(hs.Scheduler)
        scheduler.template = Mock(return_value=(True, .915, 949, 704))
        template = Image.open(hs.ASSETS / 'welfare_claim_2.bmp').convert('RGB')
        for button, expected in ((template, True),
                                 (template.convert('L').convert('RGB'), False)):
            frame = Image.new('RGB', (1920, 1032))
            frame.paste(button, (949, 704))
            self.assertEqual(expected, scheduler.welfare_claim_enabled(2, frame))

    def test_real_offline_slot_does_not_remove_a_reordered_visible_candidate(self):
        # The actual fault had a partial saved map and a different configured
        # order: slot 6 was assumed to be ROLE_A, forcing slot 1 to another role.
        import json
        from hyol_checkpoints import read_checkpoint
        names = list(read_checkpoint(hs.STATE_DIR/'account_identities.json'))
        scheduler = object.__new__(hs.Scheduler)
        original = [hs.Account(i+1, name, x) for i, (name, x) in enumerate(
            zip(names, (180, 315, 450, 585, 720, 855)))]
        scheduler.accounts = list(original)
        scheduler.identity_candidates = tuple(original)
        scheduler.by_name = {a.name: a for a in original}
        scheduler.state = Mock(global_state={'verified_solo_tab_map': {ROLE_B: 180, ROLE_C: 315}})
        scheduler.active = None
        scheduler.no_content_clicks = True
        scheduler.command_kind = 'multi_solo'
        scheduler.config = {}
        scheduler.identity_assignment_threshold = .80
        scheduler.identity_assignment_margin = .01
        scheduler.templates = hs.TemplateStore(.98)
        scheduler.event = Mock()
        scheduler.wait_interruptible = Mock()
        scheduler.healthy = Mock(return_value=True)
        report = json.loads((hs.LOGS/'identity_recovery_20261004_235812_495402_8416f2bc.json').read_text(encoding='utf-8'))
        frames = {}
        for row in report['accounts']:
            with Image.open(row['evidence'][0]) as image:
                frames[row['tab_x']] = image.convert('RGB')
        with Image.open(hs.LOGS/'scheduler_evidence/2026-10-04/identity_rebind/tab_6_x855_offline.png') as image:
            frames[855] = image.convert('RGB')
        scheduler._probe_x = 585
        scheduler.capture = Mock(side_effect=lambda: frames[scheduler._probe_x])
        scheduler.chrome_click = Mock(side_effect=lambda x, *_: setattr(scheduler, '_probe_x', x))
        expected = {ROLE_A: 180, ROLE_C: 315, ROLE_E: 450,
                    ROLE_F: 585, ROLE_D: 720}
        with tempfile.TemporaryDirectory() as evidence, \
             patch.object(hs, 'EVIDENCE_DIR', Path(evidence)), patch.object(hs.time, 'sleep'):
            for verified_only in (False, True):
                for _ in range(2):
                    scheduler.accounts = list(original)
                    scheduler.by_name = {a.name: a for a in original}
                    scheduler.rebind_tabs_by_identity(skip_unavailable=True, verified_only=verified_only)
                    self.assertEqual(expected, {a.name: a.tab_x for a in scheduler.accounts})
                    self.assertNotIn(ROLE_B, scheduler.available_account_names)
            # A second bind preserves the first bind's pictures and scores.
            directories = list(Path(evidence).glob('*/identity_rebind/*'))
            self.assertEqual(4, len(directories))
            self.assertTrue(all((folder/'matrix.json').is_file() for folder in directories))
            self.assertTrue(all((folder/'tab_6_x855_offline.png').is_file() for folder in directories))

    def test_managed_only_after_every_task_is_done_and_home(self):
        runner = object.__new__(multi.MultiSoloRunner)
        account = hs.Account(1, 'a', 180)
        daily = Mock()
        records = {'mail': {'status': 'done', 'task_completed': True, 'home_returned': True},
                   'survival': {'status': 'done', 'task_completed': True,
                                'home_returned': True, 'followup_check_due': True}}
        daily.record.side_effect = lambda task: records.setdefault(task, {})
        runner.tasks = ('mail', 'survival')
        runner.scheduler = Mock(accounts=[account], by_name={'a': account})
        runner.scheduler.check_pause_boundary.return_value = False
        runner.runners = {'a': daily}
        runner.excluded = set()
        runner.deferred = {}
        runner.event = Mock()
        runner.check_account = Mock(return_value=True)
        runner.save_progress = Mock()
        self.assertTrue(runner.finish_managed_accounts())
        daily.finish_solo_managed.assert_not_called()
        records['survival']['followup_check_due'] = False
        self.assertTrue(runner.finish_managed_accounts())
        daily.finish_solo_managed.assert_called_once()

    def test_managed_disabled_never_enters_or_changes_managed_state(self):
        runner = object.__new__(multi.MultiSoloRunner)
        runner.managed_enabled = False
        runner.event = Mock()
        runner.scheduler = Mock()
        runner.runners = {'test': Mock()}
        self.assertTrue(runner.finish_managed_accounts())
        runner.runners['test'].finish_solo_managed.assert_not_called()
        runner.scheduler.switch_account.assert_not_called()
        runner.runners['test'].record.assert_not_called()

    def test_managed_option_is_propagated_to_account_runner(self):
        runner = multi.MultiSoloRunner(('mail',), managed_enabled=False)
        account = runner.scheduler.accounts[0]
        self.assertFalse(runner.runner_for(account).managed_enabled)

    def test_managed_option_rejects_truthy_text(self):
        with self.assertRaisesRegex(ValueError, '布尔'):
            multi.MultiSoloRunner(('mail',), managed_enabled='false')

    def startup_runner(self, *, frame=None, main=True, records=None, actions=None):
        runner = object.__new__(multi.MultiSoloRunner)
        account = hs.Account(1, 'a', 180)
        scheduler = Mock()
        scheduler.capture.return_value = frame or Image.new('RGB', (1920, 1032))
        scheduler.save_raw_evidence.side_effect = ['before.png', 'after.png']
        scheduler.state = SimpleNamespace(accounts={'a': {'actions': actions or {}, 'navigation': {}}},
                                          save_account=Mock())
        scheduler._combat_active_visible.return_value = False
        scheduler._combat_ready_visible.return_value = False
        scheduler.main_ready.return_value = main
        scheduler.verify_identity.return_value = (True, {'a': 1.0})
        scheduler.template.return_value = (False, 0.0, 0, 0)
        scheduler.hwnd = None
        runner.scheduler = scheduler
        runner.runner_for = Mock(return_value=SimpleNamespace(records=records or {}))
        runner.event = Mock()
        return runner, account, scheduler

    def test_real_leaf_village_startup_frame_skips_return_click(self):
        runner = object.__new__(multi.MultiSoloRunner)
        account = hs.Account(1, 'a', 180)
        frame = Image.open(hs.LOGS / 'scheduler_evidence' / '2026-10-01' /
            'account_ae1662240e' / '214059_449056_pending_multi_solo_start_before_return.png').convert('RGB')
        scheduler = hs.Scheduler.__new__(hs.Scheduler)
        scheduler.templates = hs.TemplateStore(.92)
        scheduler.detector = None
        scheduler.diagnostics = None
        scheduler.capture = Mock(return_value=frame)
        scheduler.state = SimpleNamespace(accounts={'a': {'actions': {}, 'navigation': {}}},
                                          save_account=Mock())
        scheduler._combat_active_visible = Mock(return_value=False)
        scheduler._combat_ready_visible = Mock(return_value=False)
        scheduler.verify_identity = Mock(return_value=(True, {'a': 1.0}))
        scheduler.click_template = Mock()
        scheduler.wait_interruptible = Mock()
        scheduler.check_stop = Mock()
        scheduler.event = Mock()
        scheduler.hwnd = None
        scheduler.save_raw_evidence = Mock(side_effect=lambda _frame, _account, label, _status:
            'before.png' if label == 'multi_solo_start_before_return'
            else 'after.png' if label == 'multi_solo_start_return_origin' else 'navigation.png')
        runner.scheduler = scheduler
        runner.runner_for = Mock(return_value=SimpleNamespace(records={}))
        runner.event = Mock()
        runner.return_account_to_origin(account)
        scheduler.click_template.assert_not_called()
        self.assertEqual('leaf_verified', scheduler.state.accounts['a']['navigation']['return_leaf']['phase'])
        runner.event.assert_any_call('multi_solo_start_return_verified', account,
                                     before='before.png', after='after.png',
                                     already_home=True, clicked=False)
        self.assertEqual(5, scheduler.verify_identity.call_count)  # Startup pair plus each village frame.

    def test_real_offline_startup_frame_never_skips_to_task_or_clicks(self):
        frame = Image.open(hs.LOGS / 'scheduler_evidence' / '2026-10-01' /
            'account_c9ba2069ff' / '214000_653728_pending_multi_solo_start_before_return.png').convert('RGB')
        runner, account, scheduler = self.startup_runner(frame=frame, main=False)
        with patch('hyol_navigation.return_to_leaf') as navigate:
            with self.assertRaisesRegex(hs.PendingError, '主界面与温泉页面未确认'):
                runner.return_account_to_origin(account)
        navigate.assert_not_called()
        scheduler.click_template.assert_not_called()

    def test_capture_disconnect_propagates_without_navigation(self):
        runner, account, scheduler = self.startup_runner()
        scheduler.capture.side_effect = hs.AccountOffline(account)
        with patch('hyol_navigation.return_to_leaf') as navigate:
            with self.assertRaises(hs.AccountOffline):
                runner.return_account_to_origin(account)
        navigate.assert_not_called()
        scheduler.click_template.assert_not_called()
        runner.runner_for.assert_not_called()

    def test_unresolved_result_blocks_startup_return_click(self):
        actions = {'arena_casual_battle_4': {'status': 'pending'}}
        runner, account, scheduler = self.startup_runner(actions=actions)
        with patch('hyol_navigation.return_to_leaf') as navigate:
            with self.assertRaisesRegex(hs.PendingError, '未决操作'):
                runner.return_account_to_origin(account)
        navigate.assert_not_called()
        runner.runner_for.assert_not_called()
        scheduler.click_template.assert_not_called()
        self.assertEqual({'arena_casual_battle_4': {'status': 'pending'}}, actions)

    def test_any_carried_pending_task_blocks_before_navigation(self):
        for task in ('mail', 'welfare', 'ranking', 'survival', 'quiz', 'arena', 'treasure', 'weekend'):
            with self.subTest(task=task):
                records = {task: {'status': 'pending', 'continuation_day': '2026-10-03'}}
                runner, account, scheduler = self.startup_runner(records=records)
                with patch('hyol_navigation.return_to_leaf') as navigate:
                    with self.assertRaisesRegex(hs.PendingError, '未决任务'):
                        runner.return_account_to_origin(account)
                navigate.assert_not_called()
                scheduler.click_template.assert_not_called()
                self.assertEqual('2026-10-03', records[task]['continuation_day'])
                self.assertEqual('pending', records[task]['status'])

    def test_survival_followup_blocks_before_navigation(self):
        records = {'survival': {'status': 'done', 'followup_check_due': True}}
        runner, account, scheduler = self.startup_runner(records=records)
        with patch('hyol_navigation.return_to_leaf') as navigate:
            with self.assertRaisesRegex(hs.PendingError, '未决任务'):
                runner.return_account_to_origin(account)
        navigate.assert_not_called()
        scheduler.click_template.assert_not_called()

    def test_startup_return_requires_identity_and_known_page(self):
        for identity, main_ready in (((False, {}), True), ((True, {}), False)):
            runner, account, scheduler = self.startup_runner(main=main_ready)
            scheduler.verify_identity.return_value = identity
            with patch('hyol_navigation.return_to_leaf') as navigate:
                with self.assertRaises(hs.PendingError):
                    runner.return_account_to_origin(account)
            navigate.assert_not_called()
            scheduler.click_template.assert_not_called()

    def test_confirmed_hot_spring_delegates_even_without_main_page(self):
        runner, account, scheduler = self.startup_runner(main=False)
        with patch('hyol_navigation.hot_spring_visible', return_value=True), \
                patch('hyol_navigation.return_to_leaf', side_effect=self.navigation_success) as navigate:
            runner.return_account_to_origin(account)
        navigate.assert_called_once_with(scheduler, account)
        scheduler.click_template.assert_not_called()
        runner.event.assert_any_call('multi_solo_start_return_verified', account,
                                    before='before.png', after='after.png', delegated=True)

    def test_confirm_button_without_exit_text_never_authorizes_navigation(self):
        runner, account, scheduler = self.startup_runner(main=False)
        scheduler.template.side_effect = lambda name, *_args, **_kwargs: (
            name == 'hot_spring_leave_confirm.bmp', 1.0, 0, 0)
        with patch('hyol_navigation.hot_spring_visible', return_value=False), \
                patch('hyol_navigation.return_to_leaf') as navigate:
            with self.assertRaisesRegex(hs.PendingError, '主界面与温泉页面未确认'):
                runner.return_account_to_origin(account)
        navigate.assert_not_called()
        scheduler.click_template.assert_not_called()

    def test_verified_exit_body_and_button_delegate_resumed_prompt(self):
        runner, account, scheduler = self.startup_runner(main=False)
        scheduler.template.side_effect = lambda name, *_args, **_kwargs: (
            name in ('hot_spring_leave_prompt_text.bmp', 'hot_spring_leave_confirm.bmp'), 1.0, 0, 0)
        with patch('hyol_navigation.hot_spring_visible', return_value=False), \
                patch('hyol_navigation.return_to_leaf', side_effect=self.navigation_success) as navigate:
            runner.return_account_to_origin(account)
        navigate.assert_called_once_with(scheduler, account)
        scheduler.click_template.assert_not_called()

    def test_hot_spring_exit_unconfirmed_propagates_without_wrapper_retry(self):
        runner, account, scheduler = self.startup_runner(main=False)
        with patch('hyol_navigation.hot_spring_visible', return_value=True), \
                patch('hyol_navigation.return_to_leaf', side_effect=hs.PendingError('温泉退出正文未确认')) as navigate:
            with self.assertRaisesRegex(hs.PendingError, '温泉退出正文未确认'):
                runner.return_account_to_origin(account)
        navigate.assert_called_once_with(scheduler, account)
        scheduler.click_template.assert_not_called()
        self.assertEqual(1, scheduler.save_raw_evidence.call_count)

    def test_return_identity_change_keeps_account_pending(self):
        runner, account, scheduler = self.startup_runner()
        scheduler.verify_identity.side_effect = [(True, {'a': 1.0}), (False, {'other': 1.0})]
        with patch('hyol_navigation.return_to_leaf', return_value=True):
            with self.assertRaisesRegex(hs.PendingError, '回村后账号身份复核失败'):
                runner.return_account_to_origin(account)
        scheduler.click_template.assert_not_called()
        self.assertFalse(any(call.args[0] == 'multi_solo_start_return_verified'
                             for call in runner.event.call_args_list))

    @staticmethod
    def navigation_success(scheduler, _account):
        scheduler.main_ready.return_value = True
        scheduler.template.side_effect = lambda name, *_args, **_kwargs: (
            name == 'leaf_village_name_current.bmp', 1.0, 0, 0)
        return True

    def test_page_change_after_navigation_keeps_origin_unverified(self):
        runner, account, scheduler = self.startup_runner()
        scheduler.main_ready.side_effect = [True, False]
        with patch('hyol_navigation.return_to_leaf', return_value=True):
            with self.assertRaisesRegex(hs.PendingError, '木叶村主界面变化'):
                runner.return_account_to_origin(account)
        scheduler.click_template.assert_not_called()
        self.assertFalse(any(call.args[0] == 'multi_solo_start_return_verified'
                             for call in runner.event.call_args_list))

    def test_battle_and_result_pages_block_before_shared_navigation(self):
        for active in ('battle', 'arena_loading_anchor.bmp', 'arena_result_confirm.bmp',
                       'ranking_result_confirm.bmp', 'survival_result_confirm.bmp'):
            with self.subTest(active=active):
                runner, account, scheduler = self.startup_runner()
                scheduler._combat_active_visible.return_value = active == 'battle'
                scheduler.template.side_effect = lambda name, *_args, **_kwargs: (
                    name == active, 1.0, 0, 0)
                with patch('hyol_navigation.return_to_leaf') as navigate:
                    with self.assertRaisesRegex(hs.PendingError, '战斗、加载或结算'):
                        runner.return_account_to_origin(account)
                navigate.assert_not_called()
                scheduler.click_template.assert_not_called()

    def test_missing_return_button_keeps_shared_startup_pending(self):
        runner, account, scheduler = self.startup_runner()
        with patch('hyol_navigation.return_to_leaf', side_effect=hs.PendingError('返回原点按钮未确认')) as navigate:
            with self.assertRaisesRegex(hs.PendingError, '返回原点按钮未确认'):
                runner.return_account_to_origin(account)
        navigate.assert_called_once_with(scheduler, account)
        scheduler.click_template.assert_not_called()

    def test_current_survival_twenty_recognition_does_not_match_eighty(self):
        scheduler = object.__new__(hs.Scheduler)
        scheduler.templates = hs.TemplateStore(.9)
        scheduler.detector = None
        eighty = Image.open(hs.ASSETS / 'survival_score_80.bmp').convert('RGB')
        two_twenty = Image.open(hs.ASSETS / 'survival_complete_220.bmp').convert('RGB')
        frame = Image.new('RGB', (1920, 1032))
        frame.paste(eighty, (800, 850))
        self.assertFalse(scheduler._survival_score_twenty_visible(frame))
        twenty = eighty.copy()
        twenty.paste(two_twenty.crop((92, 0, 104, 37)), (105, 0))
        frame.paste(twenty, (800, 850))
        self.assertTrue(scheduler._survival_score_twenty_visible(frame))

    def test_two_obscured_tabs_keep_their_last_verified_accounts(self):
        known = [hs.Account(1, ROLE_C, 445), hs.Account(4, ROLE_B, 310)]
        previous = {590: hs.Account(3, ROLE_D, 590),
                    860: hs.Account(2, ROLE_E, 860)}
        result = hs.preserve_unknown_tab_assignments(
            known, {590, 860}, previous,
            {ROLE_C, ROLE_B, ROLE_D, ROLE_E})
        self.assertEqual({ROLE_D: 590, ROLE_E: 860},
                         {account.name: account.tab_x for account in result
                          if account.name in (ROLE_D, ROLE_E)})

    def test_unsolicited_invite_declines_only_verified_modal(self):
        source = Image.open(hs.ROOT / 'tests' / 'fixtures' / 'team_invite_unsolicited_reference.png')
        frame = Image.new('RGB', (1920, 1032))
        frame.paste(source, (740, 420))
        account = hs.Account(1, 'test', 180)
        scheduler = object.__new__(hs.Scheduler)
        scheduler.templates = hs.TemplateStore(.90)
        scheduler.detector = None
        scheduler.no_content_clicks = False
        scheduler.generic_current_tab_x = None
        scheduler.expected_invitation_members = set()
        scheduler.active = account
        scheduler.capture = Mock(return_value=frame)
        scheduler.tab_is_visually_active = Mock(return_value=True)
        scheduler.verify_identity = Mock(return_value=(True, {'test': 1.0}))
        scheduler.click_template = Mock(return_value=True)
        scheduler.wait_absent = Mock(return_value=True)
        scheduler.event = Mock()
        self.assertTrue(scheduler.dismiss_unsolicited_team_invite(account, frame))
        args = scheduler.click_template.call_args.args
        self.assertEqual('team_invite_decline_no_current.bmp', args[1])
        self.assertEqual('unsolicited_team_invite_decline', args[5])

        scheduler.expected_invitation_members.add(account.name)
        self.assertFalse(scheduler.dismiss_unsolicited_team_invite(account, frame))
        self.assertEqual(1, scheduler.click_template.call_count)

        scheduler.expected_invitation_members.clear()
        scheduler.verify_identity.return_value = (False, {})
        with self.assertRaises(hs.PendingError):
            scheduler.dismiss_unsolicited_team_invite(account, frame)
        self.assertEqual(1, scheduler.click_template.call_count)
        scheduler.verify_identity.return_value = (True, {'test': 1.0})

        scheduler.expected_invitation_members.clear()
        without_button = frame.copy()
        without_button.paste((0, 0, 0), (980, 509, 1081, 545))
        with self.assertRaises(hs.PendingError):
            scheduler.dismiss_unsolicited_team_invite(account, without_button)
        self.assertEqual(1, scheduler.click_template.call_count)

    def test_initial_unavailable_is_rechecked_twice_in_later_stages(self):
        runner = object.__new__(multi.MultiSoloRunner)
        a, b = hs.Account(1, 'a', 180), hs.Account(2, 'b', 310)
        runner.scheduler = Mock(accounts=[a, b])
        runner.scheduler.available_accounts.return_value = [a]
        runner.scheduler.no_content_clicks = False
        runner.event = Mock()
        runner.bind_accounts()
        self.assertEqual({'b'}, runner.initial_unavailable)
        self.assertEqual(0, runner.stall_checks['b'])
        self.assertFalse(runner.scheduler.no_content_clicks)

    def test_tab_xs_are_counted_without_using_close_as_click_target(self):
        frame = Image.open(hs.LOGS / 'multi_solo_six_tabs_reference_20260923.png')
        markers = multi.tab_close_positions(frame)
        self.assertEqual(6, len(markers))
        centers = multi.active_tab_centers(markers)
        self.assertEqual(tuple(x-34 for x in markers), centers)
        self.assertTrue(all(center < marker-30 for center, marker in zip(centers, markers)))
        current = Image.open(hs.LOGS / 'preflight_20260923_230816_282983_fd95c5f0.png')
        self.assertEqual(6, len(multi.tab_close_positions(current)))
        self.assertEqual(5, len(multi.tab_close_positions(current.crop((0, 0, 850, 39)))))

    def test_one_occluded_x_does_not_hide_other_tabs(self):
        frame = Image.open(hs.ROOT / 'tests' / 'fixtures' / 'tab_hover_occluded_20260924.png')
        self.assertEqual((214, 349, 484, 619, 754, 889), multi.tab_close_positions(frame))
        runner = object.__new__(multi.MultiSoloRunner)
        runner.scheduler = Mock()
        runner.scheduler.capture.return_value = frame
        self.assertTrue(runner._tab_exists(hs.Account(1, 'selected', 310)))
        self.assertTrue(runner._tab_exists(hs.Account(2, 'visible', 445)))
        self.assertTrue(runner._tab_exists(hs.Account(3, 'visible', 590)))
        without_selected_x = frame.copy()
        without_selected_x.paste((160, 160, 160), (345, 16, 375, 34))
        runner.scheduler.capture.return_value = without_selected_x
        self.assertFalse(runner._tab_exists(hs.Account(1, 'missing', 310)))

    def test_two_failed_health_checks_stop_future_probes(self):
        runner = object.__new__(multi.MultiSoloRunner)
        runner.scheduler = Mock()
        runner.scheduler.available_account_names = set()
        runner.scheduler.template.return_value = (False, 0, 0, 0)
        runner.scheduler.healthy.return_value = True
        runner.scheduler._combat_active_visible.return_value = False
        runner.stall_checks = {}
        runner.last_failed_at = {}
        runner.excluded = set()
        runner.disconnected = set()
        runner.exclusion_reasons = {}
        runner._tab_exists = Mock(return_value=True)
        runner.scheduler.switch_account.side_effect = hs.PendingError('offline')
        runner.event = Mock()
        runner.save_progress = Mock()
        account = hs.Account(1, 'test', 180)
        with patch.object(multi.time, 'monotonic', side_effect=(0., 1., 4., 8.)):
            self.assertFalse(runner.check_account(account))
            self.assertFalse(runner.check_account(account))
            self.assertFalse(runner.check_account(account))
            self.assertFalse(runner.check_account(account))
        self.assertEqual(2, runner.scheduler.switch_account.call_count)
        self.assertEqual({'test'}, runner.excluded)
        self.assertEqual(set(), runner.disconnected)
        self.assertEqual('identity_unverified', runner.exclusion_reasons['test'])

    def test_stage_order_finishes_each_task_across_accounts(self):
        runner = object.__new__(multi.MultiSoloRunner)
        a, b = hs.Account(1, 'a', 180), hs.Account(2, 'b', 310)
        runner.scheduler = Mock(accounts=[a, b], by_name={'a': a, 'b': b})
        runner.scheduler.check_pause_boundary.return_value = False
        runner.tasks = ('mail', 'welfare')
        runner.disconnected = set()
        runner.excluded = set()
        runner.deferred = {}
        runner.skipped_tasks = {}
        runner.runners = {}
        runner.discover_tabs = Mock()
        runner.bind_accounts = Mock()
        runner.audit_previous_day = Mock()
        runner.prepare_account_origins = Mock(return_value=True)
        runner.startup_returned = {'a', 'b'}
        runner.startup_pending = {}
        runner.check_account = Mock(return_value=True)
        runner.managed_enabled = False
        records = {name: {} for name in ('a', 'b')}
        runners = {account.name: Mock(account=account, record=Mock(
            side_effect=lambda task, name=account.name: records[name].setdefault(task, {'status': 'new'})))
            for account in (a, b)}
        runner.runner_for = Mock(side_effect=lambda account: runners[account.name])
        order = []
        def run_task(account_runner, task):
            order.append((account_runner.account.name, task))
            account_runner.record(task).update(status='done', task_completed=True, home_returned=True)
        runner._run_task = run_task
        runner.event = Mock()
        runner.save_progress = Mock()
        with patch.object(hs, 'ForegroundRuntimeLease') as lease:
            self.assertEqual(0, runner.run())
            lease.return_value.release.assert_called_once()
        self.assertEqual([('a', 'mail'), ('b', 'mail'), ('a', 'welfare'), ('b', 'welfare')], order)

    def test_completed_tasks_skip_tab_switch_after_identity_binding(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        account = hs.Account(1, 'a', 180)
        coordinator.scheduler = Mock(accounts=[account], by_name={'a': account})
        coordinator.scheduler.check_pause_boundary.return_value = False
        coordinator.tasks = ('mail',)
        coordinator.excluded = set()
        coordinator.deferred = {}
        coordinator.disconnected = set()
        coordinator.discover_tabs = Mock()
        coordinator.bind_accounts = Mock()
        coordinator.audit_previous_day = Mock()
        coordinator.prepare_account_origins = Mock(return_value=True)
        coordinator.startup_returned = {'a'}
        coordinator.startup_pending = {}
        coordinator.check_account = Mock(return_value=True)
        coordinator._run_task = Mock()
        coordinator.run_skipped_tasks = Mock(return_value=True)
        coordinator.finish_managed_accounts = Mock(return_value=True)
        coordinator.event = Mock()
        coordinator.save_progress = Mock()
        prior = {'status': 'done', 'task_completed': True, 'home_returned': True}
        completed = Mock(account=account)
        completed.record.return_value = prior
        coordinator.runner_for = Mock(return_value=completed)
        with patch.object(hs, 'ForegroundRuntimeLease'):
            self.assertEqual(0, coordinator.run())
        coordinator.check_account.assert_not_called()
        coordinator._run_task.assert_not_called()

    def test_recovered_account_backfills_skipped_stage_once(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        account = hs.Account(1, 'a', 180)
        coordinator.scheduler = Mock(accounts=[account], by_name={'a': account})
        coordinator.scheduler.check_pause_boundary.return_value = False
        coordinator.tasks = ('mail', 'welfare')
        coordinator.skipped_tasks = {'a': ['mail']}
        coordinator.disconnected = set()
        coordinator.excluded = set()
        coordinator.deferred = {}
        coordinator.check_account = Mock(return_value=True)
        records = {'mail': {'status': 'new'}, 'welfare': {'status': 'done'}}
        runner = Mock(account=account)
        runner.record.side_effect = lambda task: records[task]
        coordinator.runner_for = Mock(return_value=runner)
        order = []
        def finish(_runner, task):
            order.append(task)
            records[task].update(status='done', home_returned=True)
        coordinator._run_task = finish
        coordinator.save_progress = Mock()
        self.assertTrue(coordinator.run_skipped_tasks())
        self.assertEqual(['mail'], order)
        self.assertEqual([], coordinator.skipped_tasks['a'])
        self.assertTrue(coordinator.run_skipped_tasks())
        self.assertEqual(['mail'], order)

    def test_startup_blocked_account_is_rechecked_after_other_stages(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        account = hs.Account(1, 'a', 180)
        coordinator.scheduler = Mock(accounts=[account], by_name={'a': account}, active=account)
        coordinator.scheduler.check_pause_boundary.return_value = False
        coordinator.tasks = ('mail',)
        coordinator.skipped_tasks = {'a': ['mail']}
        coordinator.startup_returned = set()
        coordinator.startup_pending = {'a': 'return button was not visible'}
        coordinator.startup_rechecked = set()
        coordinator.excluded = set()
        coordinator.deferred = {}
        coordinator.manual_deferred = set()
        coordinator.check_account = Mock(return_value=True)
        coordinator.return_account_to_origin = Mock()
        coordinator.event = Mock()
        coordinator.save_progress = Mock()
        record = {'status': 'new'}
        runner = Mock(account=account)
        runner.record.return_value = record
        coordinator.runner_for = Mock(return_value=runner)
        order = []
        def finish(_runner, task):
            self.assertEqual('mail', task)
            order.append(task)
            record.update(status='done', home_returned=True)
        coordinator._run_task = finish
        self.assertTrue(coordinator.run_skipped_tasks())
        self.assertEqual({'a'}, coordinator.startup_returned)
        self.assertNotIn('a', coordinator.startup_pending)
        coordinator.return_account_to_origin.assert_called_once_with(account)
        self.assertEqual([], coordinator.skipped_tasks['a'])
        self.assertTrue(coordinator.run_skipped_tasks())
        self.assertEqual(['mail'], order)

    def test_module_boundary_pause_does_not_enter_later_stages(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        coordinator.scheduler = Mock()
        coordinator.scheduler.check_pause_boundary.return_value = True
        coordinator.tasks = ('mail',)
        coordinator.discover_tabs = Mock()
        coordinator.bind_accounts = Mock()
        coordinator.audit_previous_day = Mock()
        coordinator.prepare_account_origins = Mock(return_value=True)
        coordinator.run_skipped_tasks = Mock()
        coordinator.save_progress = Mock()
        with patch.object(hs, 'ForegroundRuntimeLease'):
            self.assertEqual(2, coordinator.run())
        coordinator.run_skipped_tasks.assert_not_called()
        self.assertEqual(['bound', 'paused'],
                         [call.args[0] for call in coordinator.save_progress.call_args_list])

    def test_missing_home_after_task_is_pending_not_done(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        account = hs.Account(1, 'a', 180)
        coordinator.scheduler = Mock(accounts=[account], active=account)
        coordinator.scheduler.main_ready.return_value = False
        coordinator.scheduler.state.global_state = {
            'verified_solo_tab_map': {account.name: account.tab_x}, 'tab_map': {}}
        coordinator.scheduler._combat_active_visible.return_value = False
        coordinator.scheduler.ensure_account_main_before_release.return_value = True
        coordinator.event = Mock()
        runner = Mock(account=account)
        rec = {'status': 'new'}
        runner.record.return_value = rec
        coordinator._run_task(runner, 'mail')
        runner.mail.assert_called_once()
        runner.return_to_main.assert_called_once()
        self.assertEqual('pending', rec['status'])
        self.assertNotIn('task_completed', rec)

    def test_unresolved_arena_uses_verified_solo_map_and_is_quarantined(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        account = hs.Account(1, 'a', 180)
        coordinator.scheduler = Mock(accounts=[account], active=account)
        coordinator.scheduler.state.global_state = {
            'verified_solo_tab_map': {account.name: account.tab_x}, 'tab_map': {}}
        coordinator.scheduler._saved_tab_mapping.return_value = {account.name: account.tab_x}
        coordinator.scheduler._combat_active_visible.return_value = True
        coordinator.scheduler.capture.return_value = Image.new('RGB', (1920, 1032))
        coordinator.deferred = {}
        coordinator.deferred_at = {}
        coordinator.event = Mock()
        runner = Mock(account=account)
        record = {'status': 'new'}
        runner.record.return_value = record
        runner.arena.side_effect = hs.PendingError('battle result not yet known')

        coordinator._run_task(runner, 'arena')

        self.assertEqual('pending', record['status'])
        self.assertEqual({'a': 'arena'}, coordinator.deferred)
        coordinator.scheduler.ensure_account_main_before_release.assert_not_called()
        coordinator.event.assert_any_call(
            'multi_solo_combat_deferred', account, task='arena',
            detail='已核实仍在战斗，保留断点并处理其他账号')

    def test_survival_reset_is_deferred_until_followup_pass(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        coordinator.scheduler = Mock(accounts=[hs.Account(1, 'a', 180)])
        coordinator.scheduler._survival_score_twenty_visible.return_value = False
        coordinator.event = Mock()
        account = hs.Account(1, 'a', 180)
        rec = {'followup_check_due': True, 'status': 'done', 'completed': 1}
        runner = Mock(account=account, formation_checked=True)
        runner.record.return_value = rec
        runner.visible.side_effect = lambda name, *args: name in (
            'survival_anchor.bmp', 'survival_complete_220.bmp', 'survival_reset.bmp')
        coordinator.scheduler.state.accounts = {'a': {'actions': {}}}
        coordinator.scheduler._combat_limit.return_value = 20
        coordinator.scheduler._survival_full_score_visible.return_value = True
        coordinator._survival_followup(runner)
        coordinator.scheduler._ensure_survival_reset.assert_called_once_with(account)
        runner.survival.assert_called_once()
        self.assertEqual(2, rec['completed'])
        self.assertFalse(rec['followup_check_due'])

    def test_survival_without_reset_stays_one_completed_run(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        coordinator.scheduler = Mock(accounts=[hs.Account(1, 'a', 180)])
        coordinator.scheduler._survival_score_twenty_visible.return_value = False
        coordinator.event = Mock()
        account = hs.Account(1, 'a', 180)
        rec = {'followup_check_due': True, 'status': 'done', 'completed': 1}
        runner = Mock(account=account, formation_checked=True)
        runner.record.return_value = rec
        runner.visible.side_effect = lambda name, *args: name in (
            'survival_anchor.bmp', 'survival_complete_220.bmp', 'survival_final_claimed.bmp')
        coordinator.scheduler.state.accounts = {'a': {'actions': {}}}
        coordinator.scheduler._combat_limit.return_value = 20
        coordinator.scheduler._survival_full_score_visible.return_value = True
        coordinator._survival_followup(runner)
        coordinator.scheduler._ensure_survival_reset.assert_not_called()
        runner.survival.assert_not_called()
        runner.close_page.assert_called_once_with('survival')
        self.assertFalse(rec['followup_check_due'])

    def test_survival_followup_twenty_resumes_after_prior_reset(self):
        coordinator = object.__new__(multi.MultiSoloRunner)
        account = hs.Account(1, 'a', 180)
        coordinator.scheduler = Mock(accounts=[account])
        coordinator.scheduler._survival_score_twenty_visible.return_value = True
        coordinator.scheduler.state.action_status.return_value = 'new'
        coordinator.event = Mock()
        rec = {'followup_check_due': True, 'status': 'done',
               'task_completed': True, 'completed': 1}
        runner = Mock(account=account, formation_checked=True)
        runner.record.return_value = rec
        runner.visible.side_effect = lambda name, *args: name == 'survival_anchor.bmp'
        coordinator.scheduler.state.accounts = {'a': {'actions': {}}}
        coordinator.scheduler._combat_limit.return_value = 20
        coordinator.scheduler._survival_full_score_visible.return_value = True
        coordinator._survival_followup(runner)
        runner.survival.assert_called_once()
        self.assertEqual(2, rec['completed'])
        self.assertFalse(rec['followup_check_due'])


if __name__ == '__main__':
    unittest.main()
