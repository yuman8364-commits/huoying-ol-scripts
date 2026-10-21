"""Exercise PR #2 with real ledgers and distinct account/round receipts."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import hyol_scheduler as hs
import hyol_team_pipeline as pipeline
from hyol_combat_watchdog import BattleWatchdog
from test_hyol_team_pipeline import coordinator


class SurvivalReceiptTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.account = hs.Account(1, 'verified-role', 180)
        self.action = 'survival_battle_step_1'
        self.result = 'survival_result_confirm_1'
        self.frame = Image.new('RGB', (1920, 1032))
        s = self.s = object.__new__(hs.Scheduler)
        s.state = hs.StateStore([self.account.name], Path(self.folder.name), restore_history=False)
        s.state.accounts[self.account.name]['continuation_scope'] = 'session:account:round'
        s.templates = hs.TemplateStore(.92)
        s.capture = Mock(return_value=self.frame)
        s.content_guard = Mock(return_value=self.frame)
        s.verify_identity = Mock(return_value=(True, {}))
        s._combat_ready_visible = Mock(return_value=True)
        s._combat_active_visible = Mock(return_value=False)
        s.wait_interruptible = Mock()
        s.save_raw_evidence = Mock(return_value=Path(self.folder.name) / 'observed.png')
        s.event = Mock()
        s.template = Mock(return_value=(False, 0, 0, 0))
        self.slot = hs.CombatSlot(self.account, 'survival', running=True)

    def pending(self, baseline, *, second=False):
        s = self.s
        if second:
            s.state.set_action(self.account.name, 'survival_second_run_started', 'done')
        for action, status in ((self.action, 'pending'), (self.action + '_start', 'done'),
                               (self.result, 'pending')):
            s.state.set_action(self.account.name, action, status)
        observations, key = s._survival_attempt_evidence(self.account, self.action)
        observations[key] = {'score_before': baseline}

    def observe(self, values):
        self.s._survival_observed_score = Mock(side_effect=values)
        with patch.object(hs, 'grade_candidates', return_value=[]):
            return self.s._observe_survival_pending_page(self.slot, self.frame)

    def test_ordinary_and_vip_progress_in_both_rounds(self):
        for second in (False, True):
            for before, after in ((0, 20), (80, 140), (140, 220)):
                with self.subTest(second=second, before=before):
                    self.pending(before, second=second)
                    self.assertTrue(self.observe([after] * 3))
                    self.assertEqual('done', self.s.state.action_status(self.account.name, self.action))
                    self.assertEqual('done', self.s.state.action_status(self.account.name, self.result))

    def test_stale_unstable_or_out_of_range_score_cannot_settle(self):
        for scores in ([80, 80, 80], [80, 140, 140], [221] * 3, [None] * 3):
            with self.subTest(scores=scores):
                self.pending(80)
                self.assertFalse(self.observe(scores))
                self.assertEqual('pending', self.s.state.action_status(self.account.name, self.action))

    def test_other_operation_or_conflict_cannot_settle(self):
        for target in (self.action, self.action + '_start', self.result):
            for change in ({'operation_id': 'another-session:another-account'},
                           {'checkpoint_conflict': True}):
                with self.subTest(target=target, change=change):
                    self.pending(80)
                    actions = self.s.state.accounts[self.account.name]['actions']
                    actions[target].update(change)
                    self.assertFalse(self.observe([140] * 3))
                    self.assertEqual('pending', actions[self.action]['status'])
                    actions[target].pop('checkpoint_conflict', None)

    def test_identity_change_cannot_settle_or_modify_original(self):
        self.pending(80)
        original = deepcopy(self.s.state.accounts[self.account.name]['actions'])
        self.s.verify_identity.side_effect = [(True, {}), (False, {})]
        self.assertFalse(self.observe([140] * 3))
        self.assertEqual(original, self.s.state.accounts[self.account.name]['actions'])

    def test_first_round_baseline_cannot_prove_second_round_battle(self):
        self.pending(80)
        self.s.state.set_action(self.account.name, 'survival_second_run_started', 'done')
        self.assertFalse(self.observe([140] * 3))
        self.assertEqual('pending', self.s.state.action_status(self.account.name, self.action))

    def test_unsettled_start_blocks_new_battle_and_preserves_baseline(self):
        self.pending(80)
        self.s.state.set_action(self.account.name, self.action, 'new')
        original = deepcopy(self.s.state.accounts[self.account.name])
        self.slot.running = False
        with self.assertRaises(hs.PendingError):
            self.s._start_survival_slot(self.slot)
        self.assertEqual(original, self.s.state.accounts[self.account.name])
        self.s.content_guard.assert_not_called()

    def test_full_score_cannot_overwrite_pending_start_or_result(self):
        for target in (self.action, self.result):
            with self.subTest(target=target):
                self.pending(140)
                self.s.state.set_action(self.account.name, target, 'pending')
                self.s.template.return_value = (True, 1, 0, 0)
                self.s.wait_template = Mock(return_value=True)
                self.s._survival_full_score_visible = Mock(return_value=True)
                self.s.claim_survival_rewards = Mock()
                self.s._ensure_survival_reset = Mock()
                original = deepcopy(self.s.state.accounts[self.account.name])
                with self.assertRaises(hs.PendingError):
                    self.s._finish_survival_slot(self.slot)
                self.s.claim_survival_rewards.assert_not_called()
                self.s._ensure_survival_reset.assert_not_called()
                self.assertEqual(original, self.s.state.accounts[self.account.name])

    def test_result_without_pending_battle_sends_no_click(self):
        self.s.template.side_effect = lambda name, *a, **k: (name == 'survival_result_confirm.bmp', 1, 0, 0)
        self.s.click_template = Mock()
        with self.assertRaises(hs.PendingError):
            self.s._poll_survival_slot(self.slot, self.frame)
        self.s.click_template.assert_not_called()

    def test_reset_modal_preserves_original_before_image(self):
        self.s.state.set_action(self.account.name, 'survival_reset', 'pending')
        record = self.s.state.accounts[self.account.name]['actions']['survival_reset']
        record['before_evidence'] = 'original_full_score_claimed.png'
        self.s.reconcile_survival_partial_reset = Mock(side_effect=[False, True])
        self.s.template.side_effect = lambda name, *a, **k: (name == 'survival_reset_confirm_yes.bmp', 1, 0, 0)
        self.s.click_template = Mock(return_value=True)
        self.s.wait_absent = Mock(return_value=True)
        self.s._ensure_survival_reset(self.account)
        self.assertEqual('original_full_score_claimed.png',
                         self.s.state.action_record(self.account.name, 'survival_reset')['before_evidence'])


    def test_ordinary_and_vip_complete_two_rounds_with_single_inputs(self):
        for initial, scores in ((0, [80, 140, 200, 220]), (80, [160, 220])):
            with self.subTest(initial=initial), TemporaryDirectory() as folder:
                s = self.s
                s.state = hs.StateStore([self.account.name], Path(folder), restore_history=False)
                s.state.accounts[self.account.name]['continuation_scope'] = 'flow:verified-role'
                current = {'score': initial, 'round': 1}
                sent, claimed, reset = [], [], []
                s._survival_observed_score = Mock(side_effect=lambda frame: current['score'])
                s._survival_full_score_visible = Mock(side_effect=lambda *a: current['score'] == 220)
                s.wait_template = Mock(return_value=True)
                s.wait_absent = Mock(return_value=True)
                s.close_survival = Mock()
                s.template = Mock(side_effect=lambda name, *a, **k: (
                    name in ('survival_anchor.bmp', 'survival_result_confirm.bmp'), 1, 0, 0))
                s._select_highest_survival_grade = Mock(side_effect=lambda *a:
                    None if current['score'] == 220 else {'grade': 'SS', 'remaining': 1})
                def click(account, name, roi, dx, dy, label, *args, **kwargs):
                    key = (current['round'], label)
                    self.assertNotIn(key, sent)
                    sent.append(key)
                    s.state.set_action(account.name, label, 'pending')
                    return True
                def claim(account):
                    self.assertEqual(220, current['score'])
                    self.assertNotIn(current['round'], claimed)
                    claimed.append(current['round'])
                    s.state.set_action(account.name, 'survival_claim_rewards', 'done')
                def perform_reset(account):
                    self.assertEqual([1], claimed)
                    reset.append(current['round'])
                    s.state.set_action(account.name, 'survival_reset', 'done')
                    current.update(round=2, score=initial)
                s.click_template = Mock(side_effect=click)
                s.claim_survival_rewards = Mock(side_effect=claim)
                s._ensure_survival_reset = Mock(side_effect=perform_reset)
                slot = hs.CombatSlot(self.account, 'survival')
                for round_index in (1, 2):
                    for after in scores:
                        s._start_survival_slot(slot)
                        self.assertTrue(slot.running)
                        current['score'] = after
                        s._poll_survival_slot(slot, self.frame)
                        self.assertFalse(slot.running)
                    s._start_survival_slot(slot)
                    self.assertEqual(round_index == 2, slot.complete)
                self.assertEqual([1, 2], claimed)
                self.assertEqual([1], reset)
                self.assertEqual(4 * len(scores), len(sent))
                s.close_survival.assert_called_once_with(self.account)
                data = s.state.accounts[self.account.name]
                self.assertIn('inline_run_1', data['survival_round_archives'])
                self.assertEqual(2 * len(scores), len(data['survival_attempt_observations']))

    def test_interrupted_confirmation_is_not_clicked_twice(self):
        self.pending(80)
        self.s.state.set_action(self.account.name, self.result, 'new')
        self.s.template.side_effect = lambda name, *a, **k: (name == 'survival_result_confirm.bmp', 1, 0, 0)
        def sent(*args, **kwargs):
            self.s.state.set_action(self.account.name, self.result, 'pending')
            return True
        self.s.click_template = Mock(side_effect=sent)
        self.s.wait_absent = Mock(side_effect=KeyboardInterrupt('F12'))
        with self.assertRaises(KeyboardInterrupt):
            self.s._poll_survival_slot(self.slot, self.frame)
        original = deepcopy(self.s.state.accounts[self.account.name]['actions'])
        with self.assertRaises(hs.PendingError):
            self.s._poll_survival_slot(self.slot, self.frame)
        self.s.click_template.assert_called_once()
        self.assertEqual(original, self.s.state.accounts[self.account.name]['actions'])
        self.assertTrue(self.observe([140] * 3))
        self.s.click_template.assert_called_once()

    def test_reset_requires_same_identity_and_no_pending_result(self):
        before = hs.ROOT / 'tests/fixtures/survival_reset_before_20261012.png'
        with Image.open(hs.ROOT / 'tests/fixtures/survival_partial_after_20261012.png') as image:
            after = image.convert('RGB')
        s = self.s
        s.template = hs.Scheduler.template.__get__(s)
        s.detector = None
        s.state.set_action(self.account.name, 'survival_reset', 'pending')
        s.state.accounts[self.account.name]['actions']['survival_reset']['before_evidence'] = str(before)
        original = deepcopy(s.state.accounts[self.account.name]['actions'])
        s.verify_identity.return_value = (False, {})
        self.assertFalse(s.reconcile_survival_partial_reset(self.account, after))
        self.assertEqual(original, s.state.accounts[self.account.name]['actions'])
        s.verify_identity.return_value = (True, {})
        s.state.set_action(self.account.name, self.result, 'pending')
        self.assertFalse(s.reconcile_survival_partial_reset(self.account, after))
        self.assertEqual('pending', s.state.action_status(self.account.name, 'survival_reset'))

    def test_two_hundred_is_a_partial_score_and_never_full(self):
        s = self.s
        s.template = hs.Scheduler.template.__get__(s)
        s.detector = None
        with Image.open(hs.ASSETS/'survival_complete_220.bmp') as source:
            badge = source.convert('RGB')
        self.frame.paste(badge, (800, 850))
        self.assertEqual(220, s._survival_observed_score(self.frame))
        badge.paste(badge.crop((118, 0, 131, 37)), (105, 0))
        self.frame.paste(badge, (800, 850))
        self.assertFalse(s._survival_full_score_visible(self.frame))
        self.assertEqual(200, s._survival_observed_score(self.frame))
        self.assertEqual(200, s._survival_partial_score(self.frame))

    def test_real_fresh_run_allocates_account_and_round_scoped_receipts(self):
        from hyol_run_context import RunContext
        ctx = RunContext(Path(self.folder.name), Path(self.folder.name)/'state', identifier='fresh-run')
        s = self.s
        s.run_context = ctx
        s.state = hs.StateStore([self.account.name, 'other-role'], ctx.directory, restore_history=False)
        s._survival_attempt_evidence(self.account, self.action)
        s.state.set_action(self.account.name, self.action, 'pending')
        s.state.set_action(self.account.name, self.result, 'pending')
        first = s.state.action_record(self.account.name, self.action).get('operation_id')
        confirm = s.state.action_record(self.account.name, self.result).get('operation_id')
        self.assertIsInstance(first, str)
        self.assertEqual(first[:-len(self.action)]+self.result, confirm)
        s.state.set_action(self.account.name, self.action, 'done')
        s.state.set_action(self.account.name, self.action, 'new')
        s.state.set_action(self.account.name, 'survival_second_run_started', 'done')
        s.state.set_action(self.account.name, self.action, 'pending')
        second = s.state.action_record(self.account.name, self.action)['operation_id']
        self.assertNotEqual(first, second)
        other = hs.Account(2, 'other-role', 315)
        s._survival_attempt_evidence(other, self.action)
        s.state.set_action(other.name, self.action, 'pending')
        self.assertNotEqual(first, s.state.action_record(other.name, self.action)['operation_id'])

    def hidden_page_context(self):
        from hyol_run_context import RunContext
        ctx = self.s.run_context = RunContext(Path(self.folder.name), Path(self.folder.name)/'state', identifier='hidden-page-test')
        saved = deepcopy(self.s.state.accounts[self.account.name])
        self.s.state = hs.StateStore([self.account.name], ctx.directory, restore_history=False)
        self.s.state.accounts[self.account.name] = saved
        self.frame.paste('black', (140, 10, 185, 20))
        ctx.binding = {'mapping': {1:self.account.name}, 'centers': (180,),
                       'frames': {1:self.frame.copy()}}
        ctx.layout_matches = Mock(return_value=True)
        ctx.check_window = Mock()
        self.s.verify_identity.return_value = (False, {})
        self.s.active = self.account
        self.s.healthy = Mock(return_value=True)
        self.s._generic_active_tab_x = Mock(return_value=180)
        self.s._saved_tab_mapping = Mock(return_value={self.account.name:180})
        return ctx

    def test_hidden_role_page_settles_only_its_original_bound_operation(self):
        self.pending(0)
        self.hidden_page_context()
        self.assertTrue(self.observe([80]*3))
        self.assertEqual('done', self.s.state.action_status(self.account.name, self.action))
        self.assertEqual('done', self.s.state.action_status(self.account.name, self.result))

    def test_hidden_role_cannot_settle_changed_tab_title_layout_or_account(self):
        for changed in ('tab', 'title', 'layout', 'owner', 'active', 'window', 'visible_role'):
            with self.subTest(changed=changed):
                self.pending(80)
                ctx = self.hidden_page_context()
                if changed == 'tab':
                    self.s._generic_active_tab_x.return_value = 315
                elif changed == 'title':
                    self.frame.paste('white', (132,5,204,30))
                elif changed == 'layout':
                    ctx.layout_matches.return_value = False
                elif changed == 'owner':
                    ctx.binding['mapping'][1] = 'another-role'
                elif changed == 'active':
                    self.s.active = hs.Account(2,'other',315)
                elif changed == 'window':
                    ctx.check_window.side_effect = hs.PendingError('different game window')
                original = deepcopy(self.s.state.accounts[self.account.name]['actions'])
                from hyol_checkpoints import current_identity_mask
                with patch('hyol_checkpoints.current_identity_mask', wraps=current_identity_mask) as mask:
                    if changed == 'visible_role':
                        import numpy as np
                        mask.side_effect = None
                        mask.return_value = np.ones((20,160), dtype='uint8')
                    self.assertFalse(self.observe([160]*3))
                self.assertEqual(original, self.s.state.accounts[self.account.name]['actions'])

    def test_unique_blank_title_preserves_bound_account_but_duplicate_blank_does_not(self):
        self.pending(0)
        ctx = self.hidden_page_context()
        self.frame.paste('white',(132,5,204,30))
        ctx.binding['frames'][1] = self.frame.copy()
        self.assertTrue(self.observe([80]*3))
        self.pending(80)
        ctx.binding['mapping'][2] = 'other-role'
        ctx.binding['centers'] = (180,315)
        ctx.binding['frames'][2] = self.frame.copy()
        original = deepcopy(self.s.state.accounts[self.account.name]['actions'])
        self.assertFalse(self.observe([160]*3))
        self.assertEqual(original,self.s.state.accounts[self.account.name]['actions'])

    def test_live_vip_after_ss_is_160_and_never_full(self):
        self.s.template = hs.Scheduler.template.__get__(self.s)
        self.s.detector = None
        with Image.open(hs.ASSETS/'survival_score_160.bmp') as badge:
            self.frame.paste(badge, (837,878))
        self.assertEqual(160, self.s._survival_observed_score(self.frame))
        self.assertEqual(160, self.s._survival_partial_score(self.frame))
        self.assertFalse(self.s._survival_full_score_visible(self.frame))

    def test_live_two_hundred_repaint_is_partial_and_never_full(self):
        self.s.template = hs.Scheduler.template.__get__(self.s)
        self.s.detector = None
        with Image.open(hs.ASSETS/'survival_score_200.bmp') as badge:
            self.frame.paste(badge, (837,878))
        self.assertEqual(200, self.s._survival_observed_score(self.frame))
        self.assertEqual(200, self.s._survival_partial_score(self.frame))
        self.assertFalse(self.s._survival_full_score_visible(self.frame))

    def test_daily_finish_keeps_pending_battle_and_result_before_any_claim(self):
        from hyol_daily_tasks import DailyScheduler
        for action in (self.action, self.result):
            with self.subTest(action=action):
                self.slot.complete = False
                self.pending(140)
                self.s.template.return_value = (True,1,0,0)
                self.s.wait_template = Mock(return_value=True)
                self.s._survival_full_score_visible = Mock(return_value=True)
                self.s.claim_survival_rewards = Mock()
                self.s.close_survival = Mock()
                original = deepcopy(self.s.state.accounts[self.account.name])
                with self.assertRaises(hs.PendingError):
                    DailyScheduler._finish_survival_slot(self.s,self.slot)
                self.s.claim_survival_rewards.assert_not_called()
                self.assertEqual(original,self.s.state.accounts[self.account.name])

    def test_actual_write_ahead_retains_before_image_on_disk_before_input(self):
        import json
        s = self.s
        s.no_content_clicks = False
        s.check_input_pause = Mock()
        s.screen_point = Mock(return_value=(100,500))
        s.save_evidence = Mock(return_value=Path(self.folder.name)/'original.png')
        s.hwnd, s.click_count, s.content_click_count = 123, 0, 0
        def input_sent(*args, **kwargs):
            saved = json.loads(s.state.account_paths[self.account.name].read_text('utf-8'))
            receipt = saved['actions']['survival_reset']
            self.assertEqual('pending', receipt['status'])
            self.assertEqual(str(Path(self.folder.name)/'original.png'),receipt.get('before_evidence'))
        with patch.object(hs.Win32,'click',side_effect=input_sent), patch.object(hs.time,'sleep'):
            s.click_point(self.account,100,500,'survival_reset',['survival_anchor.bmp'],irreversible=True)

    def test_original_before_image_survives_pending_refresh_and_commit(self):
        s = self.s
        s.state.set_action(self.account.name,'survival_reset','pending')
        s.state.accounts[self.account.name]['actions']['survival_reset']['before_evidence'] = 'original.png'
        for status in ('pending','done'):
            s.state.set_action(self.account.name,'survival_reset',status)
            self.assertEqual('original.png', s.state.action_record(self.account.name,'survival_reset')['before_evidence'])
        s.state.set_action(self.account.name,'survival_reset','new')
        self.assertNotIn('before_evidence',s.state.action_record(self.account.name,'survival_reset'))

    def test_unresolved_reset_or_reward_blocks_start_before_selection(self):
        for receipt in ('survival_reset','survival_reset_confirm_yes','survival_claim_rewards_run_1',
                        'survival_claim_yes','survival_reward_result_confirm_run_1'):
            with self.subTest(receipt=receipt), TemporaryDirectory() as folder:
                s = self.s
                s.state = hs.StateStore([self.account.name],Path(folder),restore_history=False)
                s.state.set_action(self.account.name,receipt,'pending')
                s.content_guard.reset_mock()
                s._survival_observed_score = Mock(return_value=80)
                s._select_highest_survival_grade = Mock(side_effect=AssertionError('reached opponent selection'))
                slot = hs.CombatSlot(self.account,'survival')
                original = deepcopy(s.state.accounts[self.account.name])
                with self.assertRaises(hs.PendingError):
                    s._start_survival_slot(slot)
                s.content_guard.assert_not_called()
                self.assertEqual(original,s.state.accounts[self.account.name])

    def test_reset_recovers_only_timestamp_matched_before_image_in_active_log(self):
        s = self.s
        s.template = hs.Scheduler.template.__get__(s)
        s.detector = None
        logs = Path(self.folder.name)/'active-events'
        folder = logs/'scheduler_evidence'/s.state.day/hs.slug(self.account.name)
        folder.mkdir(parents=True)
        path = folder/'101112_000000_before_survival_reset.jpg'
        with Image.open(hs.ROOT/'tests/fixtures/survival_reset_before_20261012.png') as before:
            before.convert('RGB').save(path,quality=100,subsampling=0)
        with Image.open(hs.ROOT/'tests/fixtures/survival_partial_after_20261012.png') as after:
            frame = after.convert('RGB')
        s.state.set_action(self.account.name,'survival_reset','pending')
        receipt = s.state.accounts[self.account.name]['actions']['survival_reset']
        receipt['updated_at'] = f'{s.state.day}T10:11:12.200+08:00'
        with patch.object(hs,'LOGS',logs), patch.object(hs,'ROOT',Path(self.folder.name)):
            self.assertTrue(s.reconcile_survival_partial_reset(self.account,frame))
        self.assertEqual('done',s.state.action_status(self.account.name,'survival_reset'))
        self.assertEqual(str(path),s.state.action_record(self.account.name,'survival_reset')['before_evidence'])


class SchedulingSafetyTests(unittest.TestCase):
    def test_fast_team_starts_next_battle_during_slow_bonus_stage(self):
        owner = coordinator()
        owner.tasks = ('group_instance',)
        trace = []
        for index in owner.leaders:
            rec = owner.record['teams'][str(index)]['tasks']['group'] = {
                'status': 'running', 'phase': 'running', 'completed': 0, 'launched_battle': 1}
            driver = SimpleNamespace(account=owner.scheduler.accounts[index-1],
                                     next_poll_seconds=8 if index == 1 else .6, ticks=0,
                                     record=lambda task, rec=rec: rec)
            def tick(task, index=index, rec=rec, driver=driver):
                driver.ticks += 1
                trace.append((index, driver.ticks, owner._poll_clock()))
                if index == 4 and driver.ticks == 2:
                    rec.update(completed=1, launched_battle=2)
                elif (index == 4 and driver.ticks == 3) or (index == 1 and driver.ticks == 2):
                    rec.update(completed=2 if index == 4 else 1, status='done')
            driver.tick = tick
            owner.drivers[index] = driver
        owner.dissolve = Mock(side_effect=lambda index, slot: slot.update(status='done'))
        owner.run_teams()
        fast_second = next(t for i, n, t in trace if i == 4 and n == 2)
        slow_result = next(t for i, n, t in trace if i == 1 and n == 2)
        self.assertLess(fast_second, slow_result)
        self.assertEqual([1, 4, 4, 4, 1], [i for i, _, _ in trace])

    def test_background_gap_requires_actual_focus_change(self):
        account = hs.Account(1, 'verified-role', 180)
        s = SimpleNamespace(_tab_focus_generation=1)
        image = Image.new('RGB', (1920, 1032))
        watchdog = BattleWatchdog()
        hs.check_battle_watchdog(s, watchdog, account, image, 'battle', mode='survival', now=0)
        s._tab_focus_generation = 3  # A -> B -> A, after successful chrome input.
        hs.check_battle_watchdog(s, watchdog, account, image, 'battle', mode='survival', now=50)
        s.offline_dialog_visible = Mock(return_value=False)
        s.save_raw_evidence, s.event = Mock(), Mock()
        with self.assertRaises(hs.BattleStalled):
            hs.check_battle_watchdog(s, watchdog, account, image, 'battle', mode='survival', now=80)

    def test_bonus_tail_only_changes_poll_delay(self):
        rec = {'phase': 'running', 'completed': 0, 'launched_battle': 1,
               'battle_timer_started_at': 100, 'battle_durations': [40, 45, 50],
               'intent': {'label': 'pending-reward'}}
        driver = SimpleNamespace(next_poll_seconds=.6, record=lambda task: rec)
        original = deepcopy(rec)
        with patch.object(pipeline.time, 'time', return_value=500):
            self.assertEqual(.6, pipeline.TeamPipelineRunner._adaptive_team_poll(driver, 'group'))
        self.assertEqual(original, rec)

    def test_duration_window_does_not_truncate_total_overlap(self):
        owner = coordinator()
        rec = {'phase': 'running', 'completed': 1, 'launched_battle': 1,
               'battle_timer_started_at': 100, 'battle_result_observed_at': 140,
               'battle_durations': [40] * 20,
               'battle_timing_intervals': [{'started_at': 10 + n, 'ended_at': 20 + n} for n in range(20)]}
        driver = SimpleNamespace(account=owner.scheduler.accounts[0], record=lambda task: rec)
        with patch.object(pipeline.time, 'time', return_value=150):
            owner._observe_team_battle_timing(driver, 'group', 0)
        self.assertEqual(20, len(rec['battle_durations']))
        self.assertEqual(21, len(rec['battle_timing_intervals']))
        self.assertEqual(140, rec['battle_timing_intervals'][-1]['ended_at'])
        self.assertEqual(150, rec['battle_timing_intervals'][-1]['settled_at'])
        self.assertNotIn('battle_result_observed_at', rec)


if __name__ == '__main__':
    unittest.main()
