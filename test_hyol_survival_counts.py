import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

import hyol_scheduler as hs
from hyol_multi_solo import MultiSoloRunner
from hyol_survival_counts import GRADES, choose_grade, grade_candidates, read_counter


class SurvivalSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = Image.open(hs.ROOT / 'fixtures/survival_highest/counters.png').convert('RGB')
        cls.templates = hs.TemplateStore(.92)
        cls.candidates = grade_candidates(cls.frame, cls.templates, hs.ASSETS)

    def test_real_page_counters(self):
        self.assertEqual([('SS', 0), ('S', 0), ('A', 2), ('B', 3), ('C', 4)],
                         [(c['grade'], c['remaining']) for c in self.candidates])

    def test_highest_available_grade_selection_replays_real_counter_screen(self):
        scheduler = object.__new__(hs.Scheduler)
        account = hs.Account(1, 'replay', 180)
        scheduler.content_guard = Mock(side_effect=[self.frame, self.frame])
        scheduler._survival_full_score_visible = Mock(return_value=False)
        scheduler.templates = self.templates
        scheduler.wait_interruptible = Mock()
        scheduler.click_template = Mock(return_value=True)
        scheduler.event = Mock()
        selected = scheduler._select_highest_survival_grade(account, 4)
        self.assertEqual(('A', 2), (selected['grade'], selected['remaining']))
        self.assertEqual('survival_grade_a_current.bmp', scheduler.click_template.call_args.args[1])
        self.assertEqual('survival_select_step_4', scheduler.click_template.call_args.args[5])

    def test_transient_grade_overlay_waits_for_two_clear_frames(self):
        blocked = Image.open(hs.LOGS / 'scheduler_evidence' / '2026-10-01' /
            'account_3ed81e0fcf' / '220503_540067_pending_survival_selection_unknown.png').convert('RGB')
        scheduler = object.__new__(hs.Scheduler)
        account = hs.Account(1, 'replay', 180)
        scheduler.content_guard = Mock(side_effect=[blocked, self.frame, self.frame])
        scheduler._survival_full_score_visible = Mock(return_value=False)
        scheduler.templates = self.templates
        scheduler.wait_interruptible = Mock()
        scheduler.click_template = Mock(return_value=True)
        scheduler.event = Mock()
        selected = scheduler._select_highest_survival_grade(account, 4)
        self.assertEqual(('A', 2), (selected['grade'], selected['remaining']))
        self.assertEqual(3, scheduler.content_guard.call_count)
        self.assertEqual(2, scheduler.wait_interruptible.call_count)

    def test_real_occluded_survival_counter_stays_pending_without_click(self):
        frame = Image.open(hs.LOGS / 'scheduler_evidence' / '2026-10-01' /
            'account_3ed81e0fcf' / '220503_540067_pending_survival_selection_unknown.png').convert('RGB')
        scheduler = object.__new__(hs.Scheduler)
        scheduler.content_guard = Mock(return_value=frame)
        scheduler._survival_full_score_visible = Mock(return_value=False)
        scheduler.templates = self.templates
        scheduler.wait_interruptible = Mock()
        scheduler.survival_selection_timeout = 0
        scheduler.save_raw_evidence = Mock(return_value='occluded.png')
        scheduler.click_template = Mock()
        with self.assertRaisesRegex(hs.PendingError, '剩余次数未确认'):
            scheduler._select_highest_survival_grade(hs.Account(1, 'replay', 180), 4)
        scheduler.click_template.assert_not_called()

    def test_exhausted_high_grades_choose_a(self):
        self.assertEqual('A', choose_grade(self.candidates)['grade'])

    def test_available_ss_precedes_every_lower_grade(self):
        candidates = copy.deepcopy(self.candidates)
        candidates[0]['remaining'] = 1
        self.assertEqual('SS', choose_grade(candidates)['grade'])

    def test_available_s_precedes_a(self):
        candidates = copy.deepcopy(self.candidates)
        candidates[1]['remaining'] = 2
        self.assertEqual('S', choose_grade(candidates)['grade'])

    def test_each_lower_grade_is_used_after_higher_counters_zero(self):
        for index in range(5):
            candidates = copy.deepcopy(self.candidates)
            for i, c in enumerate(candidates):
                c['remaining'] = 0 if i < index else 1
            self.assertEqual(GRADES[index][0], choose_grade(candidates)['grade'])

    def test_same_grade_uses_leftmost_available(self):
        left = dict(self.candidates[2], x=750, remaining=1)
        right = dict(self.candidates[2], x=1000, remaining=1)
        self.assertEqual(750, choose_grade([right, left])['x'])
        left['remaining'] = 0
        self.assertEqual(1000, choose_grade([right, left])['x'])

    def test_unknown_high_counter_blocks_lower_grade(self):
        candidates = copy.deepcopy(self.candidates)
        candidates[0]['remaining'] = None
        with self.assertRaises(ValueError):
            choose_grade(candidates)

    def test_blank_and_dimmed_counters_are_unknown(self):
        self.assertIsNone(read_counter(Image.new('RGB', self.frame.size), 862, 240, hs.ASSETS))
        dim = Image.fromarray((np.asarray(self.frame).astype(float) * .35).astype('uint8'))
        self.assertIsNone(read_counter(dim, 862, 240, hs.ASSETS))

    def test_no_available_counter_is_not_completion(self):
        self.assertIsNone(choose_grade([dict(c, remaining=0) for c in self.candidates]))

    def test_pending_later_attempt_precedes_legacy_holes(self):
        s = object.__new__(hs.Scheduler)
        s.state = SimpleNamespace(accounts={'a': {'actions': {
            'survival_battle_step_1': {'status': 'new'},
            'survival_battle_step_4': {'status': 'pending'}}}})
        self.assertEqual(4, s._next_combat_index(SimpleNamespace(name='a'), 'survival'))

    def test_new_attempt_follows_last_audited_result_without_rewriting(self):
        s = object.__new__(hs.Scheduler)
        actions = {'survival_battle_step_4': {'status': 'done'}}
        s.state = SimpleNamespace(accounts={'a': {'actions': actions}})
        self.assertEqual(5, s._next_combat_index(SimpleNamespace(name='a'), 'survival'))
        self.assertEqual({'survival_battle_step_4': {'status': 'done'}}, actions)

    def test_nonzero_checkpoint_never_infers_completed_attempts(self):
        s = object.__new__(hs.Scheduler)
        actions = {'survival_battle_step_3': {'status': 'done'}}
        s.state = SimpleNamespace(accounts={'a': {'actions': actions}},
                                  action_status=lambda name, key: actions.get(key, {}).get('status', 'new'))
        s.switch_account_for_combat = Mock(return_value=self.frame)
        s.capture = Mock(return_value=self.frame)
        s.template = Mock(side_effect=lambda name, *args, **kw: (name == 'survival_anchor.bmp', 1., 0, 0))
        s._survival_score_twenty_visible = Mock(return_value=True)
        s._survival_full_score_visible = Mock(return_value=False)
        s._migrate_survival_direct_flow_state = Mock()
        s.event = Mock()
        slot = hs.CombatSlot(SimpleNamespace(name='a'), 'survival')
        s._prepare_survival_slot(slot)
        self.assertEqual(4, slot.next_index)
        self.assertEqual({'survival_battle_step_3': {'status': 'done'}}, actions)

    def test_first_full_run_resets_and_second_full_run_closes(self):
        s = object.__new__(hs.Scheduler)
        actions = {'survival_battle_step_5': {'status': 'done'}}
        s.state = SimpleNamespace(accounts={'a': {'actions': actions}},
            action_status=lambda name, key: actions.get(key, {}).get('status', 'new'),
            set_action=lambda name, key, status, detail: actions.update({key: {'status': status, 'detail': detail}}),
            set_module=Mock())
        s.template = Mock(side_effect=lambda name, *args, **kw: (name == 'survival_anchor.bmp', 1., 0, 0))
        s.wait_template = Mock(return_value=True)
        s._survival_full_score_visible = Mock(return_value=True)
        s.capture = Mock(return_value=self.frame)
        s.claim_survival_rewards = Mock()
        s.reconcile_survival_result_confirmations = Mock()
        s._ensure_survival_reset = Mock()
        s.close_survival = Mock()
        s.event = Mock()
        slot = hs.CombatSlot(SimpleNamespace(name='a'), 'survival', next_index=6)
        s._finish_survival_slot(slot)
        self.assertFalse(slot.complete)
        self.assertEqual(1, slot.next_index)
        s._ensure_survival_reset.assert_called_once()
        s.close_survival.assert_not_called()
        s._finish_survival_slot(slot)
        self.assertTrue(slot.complete)
        s.close_survival.assert_called_once()
        self.assertEqual(2, s.claim_survival_rewards.call_count)

    def test_changed_counter_sends_no_click(self):
        s = object.__new__(hs.Scheduler)
        s.templates = self.templates
        s._survival_full_score_visible = Mock(return_value=False)
        s.content_guard = Mock(return_value=self.frame)
        s.template = Mock(return_value=(False, 0., 0, 0))
        s.wait_interruptible = Mock()
        s.survival_selection_timeout = .7
        s.save_raw_evidence = Mock(return_value=Path('pending.png'))
        s.click_template = Mock()
        changed = copy.deepcopy(self.candidates)
        changed[2]['remaining'] = 1
        with patch.object(hs, 'grade_candidates', side_effect=[self.candidates, changed]):
            with patch.object(hs.time, 'monotonic', side_effect=(0., 0., 1.)):
                with self.assertRaises(hs.PendingError):
                    s._select_highest_survival_grade(SimpleNamespace(name='a'), 1)
        s.click_template.assert_not_called()

    def test_missing_grade_sends_no_click(self):
        s = object.__new__(hs.Scheduler)
        s.templates = self.templates
        s._survival_full_score_visible = Mock(return_value=False)
        s.content_guard = Mock(return_value=self.frame)
        s.template = Mock(return_value=(False, 0., 0, 0))
        s.wait_interruptible = Mock()
        s.survival_selection_timeout = 0
        s.save_raw_evidence = Mock(return_value=Path('pending.png'))
        s.click_template = Mock()
        with patch.object(hs, 'grade_candidates', return_value=self.candidates[1:]):
            with patch.object(hs.time, 'monotonic', side_effect=(0., 1.)):
                with self.assertRaises(hs.PendingError):
                    s._select_highest_survival_grade(SimpleNamespace(name='a'), 1)
        s.click_template.assert_not_called()

    def test_pending_arena_prevents_followup_navigation(self):
        coordinator = object.__new__(MultiSoloRunner)
        coordinator.deferred = {}
        coordinator.event = Mock()
        coordinator.scheduler = SimpleNamespace(state=SimpleNamespace(accounts={
            'a': {'actions': {'arena_casual_battle_4': {'status': 'pending'}}}}))
        runner = Mock(account=SimpleNamespace(name='a'))
        runner.record.return_value = {'followup_check_due': True}
        coordinator._survival_followup(runner)
        runner.enter.assert_not_called()
        runner.ensure_formation_once.assert_not_called()

    def test_partial_second_run_resumes_without_reset_or_inferred_results(self):
        coordinator = object.__new__(MultiSoloRunner)
        account = SimpleNamespace(name='a')
        s = Mock(accounts=[account])
        s.state.accounts = {'a': {'actions': {'survival_second_run_started': {'status': 'done'}}}}
        s.state.action_status.return_value = 'done'
        s._survival_full_score_visible.return_value = False
        s._survival_score_twenty_visible.return_value = False
        s.save_raw_evidence.return_value = Path('partial.png')
        coordinator.scheduler = s
        coordinator.deferred = {}
        coordinator.event = Mock()
        runner = Mock(account=account, formation_checked=True)
        rec = {'followup_check_due': True, 'completed': 1}
        runner.record.return_value = rec
        runner.visible.side_effect = lambda name, *args: name == 'survival_anchor.bmp'
        # Finish the observation window without sleeping in this test.
        with patch('hyol_multi_solo.time.monotonic', side_effect=[0., 13.]):
            coordinator._survival_followup(runner)
        runner.survival.assert_called_once()
        s._ensure_survival_reset.assert_not_called()
        s.state.set_action.assert_not_called()
        self.assertFalse(rec['followup_check_due'])

    def test_strict_score_rejects_200_with_similar_full_template(self):
        s = object.__new__(hs.Scheduler)
        s.template = Mock(return_value=(True, .99, 800, 850))
        with Image.open(hs.ASSETS / 'survival_complete_220.bmp') as source:
            badge = source.convert('RGB')
        frame = Image.new('RGB', (1920, 1032))
        frame.paste(badge, (800, 850))
        self.assertTrue(s._survival_full_score_visible(frame))
        # Replace the middle 2 by the rightmost 0 using enrolled font pixels.
        badge.paste(badge.crop((118, 0, 131, 37)), (105, 0))
        frame.paste(badge, (800, 850))
        self.assertFalse(s._survival_full_score_visible(frame))

    def test_unknown_previous_battle_cannot_select_again(self):
        s = object.__new__(hs.Scheduler)
        s.state = Mock()
        s.state.action_status.return_value = 'pending'
        s._combat_active_visible = Mock(return_value=False)
        s._select_highest_survival_grade = Mock()
        slot = hs.CombatSlot(SimpleNamespace(name='a'), 'survival', next_index=4)
        with self.assertRaises(hs.PendingError):
            s._start_survival_slot(slot)
        s._select_highest_survival_grade.assert_not_called()

    def test_complete_score_moves_to_reward_flow_without_card_click(self):
        s = object.__new__(hs.Scheduler)
        s.content_guard = Mock(return_value=self.frame)
        s._survival_full_score_visible = Mock(return_value=True)
        s.click_template = Mock()
        self.assertIsNone(s._select_highest_survival_grade(SimpleNamespace(name='a'), 5))
        s.click_template.assert_not_called()

    def test_reviewed_return_variants_match_fault_frames(self):
        for relative in ('return_2nv.png', 'return_51.png'):
            frame = Image.open(hs.ROOT / f'fixtures/survival_highest/{relative}')
            self.assertTrue(self.templates.match(frame, hs.ASSETS / 'leaf_return_current.bmp', (1870,69,1920,125))[0])

    def test_real_collapsed_toolbar_is_enrolled(self):
        frame = Image.open(hs.ROOT / 'fixtures/survival_highest/toolbar_collapsed.png')
        self.assertTrue(self.templates.match(frame, hs.ASSETS / 'toolbar_expand_current.bmp', (1665,69,1710,135), .95)[0])
        for name, roi in (('ranking_toolbar.bmp', (980,69,1130,150)),
                          ('survival_toolbar_current_core.bmp', (1120,69,1320,169)),
                          ('arena_toolbar_core.bmp', (1201,69,1501,169))):
            self.assertFalse(self.templates.match(frame, hs.ASSETS / name, roi, .92)[0])

    def test_toolbar_expansion_requires_confirmed_destination(self):
        s = object.__new__(hs.Scheduler)
        s.capture = Mock(return_value=self.frame)
        s.template = Mock(side_effect=[(False,0,0,0), (True,1,1678,73)])
        s.main_ready = Mock(return_value=True)
        s.click_template = Mock(return_value=True)
        s.wait_absent = Mock(return_value=True)
        s.wait_template = Mock(return_value=False)
        s.event = Mock()
        with self.assertRaises(hs.PendingError):
            s.ensure_activity_toolbar(SimpleNamespace(name='a'), 'ranking_toolbar.bmp', (920,69,1060,150))
        self.assertEqual(1, s.click_template.call_count)
        s.event.assert_not_called()

    def test_activity_page_never_receives_toolbar_expand_click(self):
        s = object.__new__(hs.Scheduler)
        s.capture = Mock(return_value=self.frame)
        s.template = Mock(return_value=(False,0,0,0))
        s.main_ready = Mock(return_value=False)
        s.click_template = Mock()
        with self.assertRaises(hs.PendingError):
            s.ensure_activity_toolbar(SimpleNamespace(name='a'), 'ranking_toolbar.bmp', (920,69,1060,150))
        s.click_template.assert_not_called()


if __name__ == '__main__':
    unittest.main()
