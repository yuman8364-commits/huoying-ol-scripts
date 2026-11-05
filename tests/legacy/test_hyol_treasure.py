"""Timing and recovery contracts for irreversible treasure starts."""
from test_identity_fixtures import ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F, legacy_config
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import numpy as np

import hyol_daily_tasks as daily
import hyol_scheduler as hs
import hyol_treasure as treasure


class TimedTreasureTests(unittest.TestCase):
    def transformation_frame(self):
        return Image.open(hs.EVIDENCE_DIR/'2026-10-05/account_a802d1815a/'
                          '122727_918614_pending_treasure_ground_unverified.png').convert('RGB')

    def use_real_templates(self, runner, frame):
        store = hs.TemplateStore(.98)
        runner.visible = lambda name, roi, image=None, threshold=.92: store.match(
            image if image is not None else frame, hs.ASSETS/name, roi, threshold)[0]

    def test_real_transformation_is_modal_and_never_ground_after_deadline(self):
        frame, r = self.transformation_frame(), self.runner()
        self.use_real_templates(r, frame)
        r.scheduler.capture.return_value = frame
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        slot = treasure.TreasureRun(r, clock=lambda: 1400)
        self.assertTrue(treasure.transformation_visible(r, frame))
        self.assertTrue(treasure.central_modal_visible(r, frame))
        self.assertFalse(treasure.world_visible(r, frame))
        store = hs.TemplateStore(.98)
        ok, score, x, y = store.match(frame, hs.ASSETS/'treasure_transformation_confirm_current.bmp',
                                    (880,565,1040,625), .98)
        self.assertTrue(ok)
        self.assertEqual((x+50, y+16), (960,594))
        with self.assertRaisesRegex(hs.PendingError, '变身确认后页面'):
            slot.step()
        r.scheduler.click_point.assert_not_called()
        r.scheduler.commit_action.assert_not_called()
        self.assertEqual(slot.record['deadline_at'], 1300)
        self.assertEqual(slot.record['awaiting']['kind'], 'transformations')

    def test_unknown_central_modal_blocks_ground_and_all_content_inputs(self):
        frame, r = self.transformation_frame(), self.runner()
        frame.paste((30,40,50), (850,507,1066,526))
        self.use_real_templates(r, frame)
        r.scheduler.capture.return_value = frame
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        self.assertTrue(treasure.central_modal_visible(r, frame))
        self.assertFalse(treasure.transformation_visible(r, frame))
        self.assertFalse(treasure.world_visible(r, frame))
        with self.assertRaisesRegex(hs.PendingError, '当前画面未知'):
            treasure.TreasureRun(r, clock=lambda:1400).step()
        r.click.assert_not_called()
        r.scheduler.click_point.assert_not_called()

    def test_pending_transformation_confirmation_is_never_repeated(self):
        frame, r = self.transformation_frame(), self.runner()
        self.use_real_templates(r, frame)
        r.scheduler.capture.return_value = frame
        r.scheduler.state.accounts[r.account.name]['actions']['treasure_transformation_1'] = {'status':'pending'}
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        with self.assertRaisesRegex(hs.PendingError, '变身提示确认已发送'):
            treasure.TreasureRun(r, clock=lambda:1400).step()
        r.click.assert_not_called()
        r.scheduler.click_point.assert_not_called()

    def test_real_dimmed_transformation_keeps_name_confidence_and_competitors(self):
        frame = self.transformation_frame()
        scheduler = object.__new__(hs.Scheduler)
        scheduler.templates = hs.TemplateStore(.98)
        scheduler.accounts = [hs.Account(i+1, name, 180+i*135) for i,name in enumerate(
            (ROLE_D,ROLE_A,ROLE_C,ROLE_B,ROLE_E,ROLE_F))]
        scheduler.identity_candidates = tuple(scheduler.accounts)
        scheduler.joint_identity_bound = set()
        scheduler.identity_threshold = .82
        scheduler.identity_margin = .1
        scheduler.identity_assignment_threshold = .80
        for account in scheduler.accounts:
            ok, scores = scheduler.verify_identity(account, frame)
            self.assertEqual(ok, account.name == ROLE_D)
        self.assertGreaterEqual(scores[ROLE_D], scheduler.identity_threshold)
        self.assertGreaterEqual(scores[ROLE_D]-scores[ROLE_F], scheduler.identity_margin)
        from hyol_checkpoints import _identity_layout
        altered = frame.copy()
        altered.paste((30,40,50), (850,507,1066,526))
        self.assertEqual(_identity_layout(altered, scheduler.templates)[1], hs.IDENTITY_TEXT_THRESHOLD)

    def test_ground_changed_during_guard_is_not_clicked(self):
        r, frame = self.pause_runner()
        r.scheduler.content_guard.return_value = Image.new('RGB', frame.size, 'black')
        slot = treasure.TreasureRun(r, clock=lambda:1400)
        self.assertFalse(slot._pause(frame))
        self.assertFalse(slot.record.get('stop_requested', False))
        r.scheduler.click_point.assert_not_called()

    def test_live_village_background_uses_strict_icon_interior_for_treasure_entry(self):
        frame = Image.open(hs.EVIDENCE_DIR/'2026-10-05/account_a802d1815a/'
                           '121737_818729_pending_solo_treasure_pending.png').convert('RGB')
        store = hs.TemplateStore(.98)
        roi = (651,69,1670,170)
        self.assertFalse(store.match(frame,hs.ASSETS/'treasure_toolbar_core_current.bmp',roi,.98)[0])
        ok,score,x,y = store.match(frame,hs.ASSETS/'treasure_toolbar_icon_core_current.bmp',roi,.98)
        self.assertTrue(ok)
        self.assertEqual((x+12,y+12),(1525,97))
        self.assertGreaterEqual(score,.98)

    def test_real_forest_return_icon_has_strict_core_and_safe_click_center(self):
        frame = Image.open(hs.EVIDENCE_DIR/'2026-10-05/account_72cd26215f/'
                           '120945_592087_pending_treasure_resume.png').convert('RGB')
        store = hs.TemplateStore(.98)
        self.assertFalse(store.match(frame, hs.ASSETS/'leaf_return_current.bmp', (1870,69,1920,125),.92)[0])
        ok, score, x, y = store.match(frame, hs.ASSETS/'leaf_return_core_current.bmp', (1870,69,1920,125), .98)
        self.assertTrue(ok)
        self.assertEqual((x+10, y+10),(1908,95))
        self.assertGreaterEqual(score, .98)

    def test_second_forest_background_requires_its_own_reviewed_return_glyph(self):
        frame = Image.open(hs.EVIDENCE_DIR/'2026-10-05/account_a802d1815a/'
                           '124202_647818_pending_treasure_resume.png').convert('RGB')
        store = hs.TemplateStore(.98)
        roi = (1870,69,1920,125)
        self.assertFalse(store.match(frame, hs.ASSETS/'leaf_return_core_current.bmp', roi, .98)[0])
        ok, score, x, y = store.match(frame, hs.ASSETS/'leaf_return_core_forest2_current.bmp', roi, .98)
        self.assertTrue(ok)
        self.assertGreaterEqual(score, .98)
        self.assertEqual((x+10,y+10),(1908,95))

    def test_result_auto_dismissed_before_input_does_not_leave_false_pending_or_credit(self):
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        visible = [True, False]
        r.visible.side_effect = lambda name, *args, **kwargs: visible.pop(0) if name == 'treasure_battle_victory.bmp' else False
        r.click.side_effect = hs.SafetyError('page anchor guard failed: original result disappeared')
        slot = treasure.TreasureRun(r, clock=lambda:1400)
        with patch.object(treasure, 'known_frame', return_value=True):
            self.assertTrue(slot._confirm_battle_result(r.scheduler.capture()))
        self.assertIsNone(slot.record['awaiting'])
        self.assertEqual(slot.record.get('battle_results',0),0)
        r.scheduler.commit_action.assert_not_called()
        self.assertEqual(r.scheduler.state.action_status(r.account.name,'treasure_battle_result_1'),'new')

    def test_result_guard_failure_after_pending_write_keeps_original_intent(self):
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        r.visible.side_effect = lambda name, *args, **kwargs: name == 'treasure_battle_victory.bmp'
        def dispatched(*args, **kwargs):
            r.scheduler.state.accounts[r.account.name]['actions']['treasure_battle_result_1'] = {'status':'pending'}
            raise hs.SafetyError('focus changed after sending input')
        r.click.side_effect = dispatched
        slot = treasure.TreasureRun(r,clock=lambda:1400)
        with self.assertRaises(hs.SafetyError):
            slot._confirm_battle_result(r.scheduler.capture())
        self.assertIsNotNone(slot.record['awaiting'])
        r.scheduler.commit_action.assert_not_called()

    def test_real_loading_finished_is_observed_without_start_or_pause(self):
        frame = Image.open(hs.EVIDENCE_DIR/'2026-10-05/account_ae1662240e/'
                           '114149_910985_pending_solo_treasure_pending.png').convert('RGB')
        store = hs.TemplateStore(.98)
        r = self.runner()
        r.scheduler.capture.return_value = frame
        r.visible = lambda name, roi, image=None, threshold=.92: store.match(
            image if image is not None else frame, hs.ASSETS/name, roi, threshold)[0]
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        slot = treasure.TreasureRun(r, clock=lambda: 1400)
        self.assertTrue(treasure.loading_visible(r, frame))
        self.assertFalse(slot.step())
        r.click.assert_not_called()
        r.scheduler.click_point.assert_not_called()
        self.assertEqual(slot.record['deadline_at'], 1300)

    def test_missing_auto_path_asset_refuses_pause_and_stop_credit(self):
        r, frame = self.pause_runner()
        slot = treasure.TreasureRun(r, clock=lambda: 1400)
        with patch('pathlib.Path.exists', return_value=False):
            with self.assertRaisesRegex(hs.PendingError, '素材缺失'):
                slot._pause(frame)
        r.scheduler.click_point.assert_not_called()
        r.scheduler.commit_action.assert_not_called()

    def test_positive_auto_path_with_unchanged_position_cannot_prove_stop(self):
        source = hs.EVIDENCE_DIR/'2026-10-05/account_3ed81e0fcf/113621_206871_pending_treasure_resume.png'
        frame = Image.open(source).convert('RGB')
        stopped = Image.open(hs.EVIDENCE_DIR/'2026-10-05/account_3ed81e0fcf/'
                             '113910_040021_verified_treasure_auto_stopped.png').convert('RGB')
        store = hs.TemplateStore(.98)
        self.assertTrue(store.match(frame, hs.ASSETS/'treasure_auto_path_running.bmp', hs.VIEW, .96)[0])
        self.assertFalse(store.match(stopped, hs.ASSETS/'treasure_auto_path_running.bmp', hs.VIEW, .96)[0])
        r = self.runner()
        r.scheduler.capture.return_value = frame
        r.visible = lambda name, roi, image=None, threshold=.92: store.match(
            image if image is not None else frame, hs.ASSETS/name, roi, threshold)[0]
        r.record('treasure').update(started_at=1000, deadline_at=1300,
            stop_requested=True, stop_requested_at=1400, stop_stable_frames=2)
        slot = treasure.TreasureRun(r, clock=lambda: 1403)
        self.assertFalse(slot._pause(frame))
        self.assertEqual(slot.record['stop_stable_frames'], 0)
        r.scheduler.commit_action.assert_not_called()
        r.scheduler.click_point.assert_not_called()

    def test_real_battle_opening_waits_after_deadline_without_pause_or_result_input(self):
        frame = Image.open(hs.EVIDENCE_DIR/'2026-10-05/account_3ed81e0fcf/'
                           '112801_457366_pending_solo_treasure_pending.png').convert('RGB')
        store = hs.TemplateStore(.98)
        r = self.runner()
        r.scheduler.capture.return_value = frame
        r.scheduler.main_ready.return_value = False
        r.visible = lambda name, roi, image=None, threshold=.92: store.match(
            image if image is not None else frame, hs.ASSETS/name, roi, threshold)[0]
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        slot = treasure.TreasureRun(r, clock=lambda: 1400)
        self.assertTrue(treasure.battle_visible(r, frame))
        self.assertFalse(treasure.world_visible(r, frame))
        self.assertFalse(slot.step())
        self.assertEqual(slot.record['phase'], 'battle')
        self.assertEqual(slot.record['deadline_at'], 1300)
        self.assertTrue(slot.record['stop_due'])
        r.scheduler.click_point.assert_not_called()
        r.click.assert_not_called()
        r.scheduler.commit_action.assert_not_called()

    def test_one_battle_corner_cannot_classify_unknown_scene(self):
        r = self.runner()
        r.visible.side_effect = lambda name, *a, **kw: name == 'normal_battle_left_corner_current.bmp'
        self.assertFalse(treasure.battle_visible(r, r.scheduler.capture()))

    def test_real_black_battle_loading_is_observation_only_after_deadline(self):
        frame = Image.open(hs.EVIDENCE_DIR/'2026-10-05/account_c9ba2069ff/'
                           '124816_801829_pending_solo_treasure_pending.png').convert('RGB')
        r = self.runner()
        r.scheduler.capture.return_value = frame
        r.scheduler.main_ready.return_value = False
        self.use_real_templates(r, frame)
        r.record('treasure').update(started_at=1000,deadline_at=1300)
        self.assertTrue(treasure.battle_visible(r,frame))
        self.assertFalse(treasure.world_visible(r,frame))
        self.assertFalse(treasure.TreasureRun(r,clock=lambda:1400).step())
        r.click.assert_not_called()
        r.scheduler.click_point.assert_not_called()
        r.scheduler.commit_action.assert_not_called()

    def test_corners_from_different_battle_variants_cannot_classify_unknown_scene(self):
        r = self.runner()
        r.visible.side_effect = lambda name, *a, **kw: name in (
            'normal_battle_left_corner_current.bmp','normal_battle_black_right_corner_current.bmp')
        self.assertFalse(treasure.battle_visible(r,r.scheduler.capture()))

    def test_real_map_loading_is_observed_with_original_deadline_and_zero_input(self):
        frame = Image.open(hs.EVIDENCE_DIR/'2026-10-05/account_3ed81e0fcf/'
                           '112332_243586_pending_solo_treasure_pending.png').convert('RGB')
        store = hs.TemplateStore(.98)
        r = self.runner()
        r.scheduler.capture.return_value = frame
        r.visible = lambda name, roi, image=None, threshold=.92: store.match(
            image if image is not None else frame, hs.ASSETS/name, roi, threshold)[0]
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        slot = treasure.TreasureRun(r, clock=lambda: 1400)
        self.assertTrue(treasure.loading_visible(r, frame))
        self.assertFalse(slot.step())
        self.assertEqual(slot.record['deadline_at'], 1300)
        self.assertTrue(slot.record['stop_due'])
        self.assertEqual(slot.record['phase'], 'loading')
        r.scheduler.click_point.assert_not_called()
        r.click.assert_not_called()

    def test_map_loading_timeout_preserves_deadline_without_start_or_ground_click(self):
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300, transition_started_at=1100)
        slot = treasure.TreasureRun(r, clock=lambda: 1200)
        with patch.object(treasure, 'loading_visible', return_value=True):
            with self.assertRaisesRegex(hs.PendingError, '加载60秒'):
                slot.step()
        self.assertEqual(slot.record['deadline_at'], 1300)
        r.scheduler.click_point.assert_not_called()
        r.click.assert_not_called()

    def test_live_expanded_toolbar_uses_exact_core_and_keeps_strict_threshold(self):
        frame = Image.open(hs.EVIDENCE_DIR/'2026-10-05/account_3ed81e0fcf/'
                           '055729_237309_pending_solo_treasure_inventory_pending.png').convert('RGB')
        store = hs.TemplateStore(.98)
        self.assertFalse(store.match(frame, hs.ASSETS/'treasure_toolbar_current.bmp',
                                     (1380,69,1530,170), .96)[0])
        matched, score, x, y = store.match(frame, hs.ASSETS/'treasure_toolbar_core_current.bmp',
                                         (651,69,1670,170), .98)
        self.assertTrue(matched)
        self.assertGreaterEqual(score, .98)
        self.assertEqual((x+22, y+25), (1526, 107))

    def runner(self, name='test'):
        r = object.__new__(daily.DailyRunner)
        r.account = hs.Account(1, name, 180)
        r.records, r.path = {}, None
        r.scheduler = Mock()
        r.scheduler.generic_current_tab_x = 180
        r.scheduler.state.accounts = {name: {'actions': {}}}
        actions = r.scheduler.state.accounts[name]['actions']
        r.scheduler.state.action_status.side_effect = lambda who, key: actions.get(key, {}).get('status', 'new')
        r.scheduler.commit_action.side_effect = lambda who, key, detail: actions.update({key: {'status': 'done'}})
        r.scheduler.capture.return_value = Image.new('RGB', (1920, 1032), 'black')
        r.scheduler._combat_active_visible.return_value = False
        r.scheduler._battle_auto_on_visible.return_value = False
        r.scheduler.main_ready.return_value = True
        r.scheduler.wait_absent.return_value = True
        r.visible = Mock(side_effect=lambda name, *args, **kwargs: name in (
            'treasure_map_item.bmp', 'treasure_auto_dig.bmp'))
        r.click = Mock()
        return r

    def test_start_persists_original_five_minute_deadline(self):
        r = self.runner()
        with patch.object(treasure, 'open_page'):
            slot = treasure.TreasureRun(r, clock=lambda: 1000)
            slot.begin()
        self.assertEqual(1000, slot.record['started_at'])
        self.assertEqual(1300, slot.record['deadline_at'])
        self.assertEqual(300, slot.record['duration_seconds'])
        treasure.TreasureRun(r, clock=lambda: 1250).begin()
        r.click.assert_called_once()
        self.assertEqual(1300, slot.record['deadline_at'])

    def test_reward_before_deadline_does_not_end_run(self):
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        r.visible.side_effect = lambda name, *args, **kwargs: name == 'treasure_result_anchor.bmp'
        slot = treasure.TreasureRun(r, clock=lambda: 1100)
        with patch.object(slot, '_pause') as pause:
            self.assertFalse(slot.step())
        pause.assert_not_called()
        self.assertEqual(1, slot.record['rewards'])
        self.assertEqual(1300, slot.record['deadline_at'])

    def test_expired_battle_waits_without_ground_click(self):
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        r.scheduler._combat_active_visible.return_value = True
        slot = treasure.TreasureRun(r, clock=lambda: 1400)
        with patch.object(slot, '_pause') as pause:
            self.assertFalse(slot.step())
        pause.assert_not_called()
        r.scheduler.click_point.assert_not_called()
        self.assertTrue(slot.record['stop_due'])

    def test_expired_resume_stops_then_returns_without_another_map(self):
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        slot = treasure.TreasureRun(r, clock=lambda: 1400)
        with patch.object(slot, '_pause', return_value=True) as pause, \
             patch('hyol_navigation.return_to_leaf', return_value=True) as returned:
            slot.begin()
            self.assertTrue(slot.step())
        r.click.assert_not_called()
        pause.assert_called_once()
        returned.assert_called_once_with(r.scheduler, r.account)
        self.assertTrue(slot.record['task_completed'])
        self.assertTrue(slot.record['home_returned'])

    def test_verified_treasure_stop_can_return_while_unrelated_old_battle_stays_pending(self):
        r = self.runner()
        old = {'status': 'pending', 'updated_at': 'old battle'}
        r.scheduler.state.accounts[r.account.name]['actions']['arena_casual_battle_1'] = old
        r.record('treasure').update(started_at=1000, deadline_at=1300, stop_verified=True)
        slot = treasure.TreasureRun(r, clock=lambda: 1400)
        with patch('hyol_navigation.return_to_leaf', return_value=True) as returned:
            self.assertTrue(slot.step())
        returned.assert_called_once_with(r.scheduler, r.account, allow_pending_navigation=True)
        self.assertEqual(r.scheduler.state.accounts[r.account.name]['actions']['arena_casual_battle_1'], old)
        r.click.assert_not_called()

    def test_pending_treasure_result_still_blocks_return_after_stop(self):
        r = self.runner()
        r.scheduler.state.accounts[r.account.name]['actions']['daily_treasure_reward_1'] = {'status': 'pending'}
        r.record('treasure').update(started_at=1000, deadline_at=1300, stop_verified=True)
        slot = treasure.TreasureRun(r, clock=lambda: 1400)
        with patch('hyol_navigation.return_to_leaf') as returned:
            with self.assertRaisesRegex(hs.PendingError, '挖宝原动作'):
                slot.step()
        returned.assert_not_called()
        r.click.assert_not_called()
        self.assertFalse(slot.record.get('task_completed', False))
        self.assertFalse(slot.record.get('home_returned', False))

    def test_unknown_page_keeps_deadline_and_never_pauses(self):
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        r.scheduler.main_ready.return_value = False
        slot = treasure.TreasureRun(r, clock=lambda: 1400)
        with self.assertRaises(hs.PendingError):
            slot.step()
        r.scheduler.click_point.assert_not_called()
        self.assertEqual(1300, slot.record['deadline_at'])

    def test_unsettled_start_page_does_not_loop_or_restart(self):
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300, phase='start_pending')
        r.visible.side_effect = lambda name, *args, **kwargs: name == 'treasure_page_anchor.bmp'
        with self.assertRaises(hs.PendingError):
            treasure.TreasureRun(r, clock=lambda: 1400).step()
        r.click.assert_not_called()

    def test_legacy_pending_without_timestamp_never_starts_again(self):
        r = self.runner()
        r.scheduler.state.accounts[r.account.name]['actions'][treasure.START_ACTION] = {'status': 'pending'}
        with self.assertRaises(hs.PendingError):
            treasure.TreasureRun(r).begin()
        r.click.assert_not_called()

    def test_f12_preserves_started_budget_and_sends_no_new_input(self):
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        r.scheduler.check_stop.side_effect = KeyboardInterrupt('F12')
        with self.assertRaises(KeyboardInterrupt):
            treasure.TreasureRun(r, clock=lambda: 1400).step()
        r.click.assert_not_called()
        r.scheduler.click_point.assert_not_called()
        self.assertEqual(1300, r.record('treasure')['deadline_at'])

    def test_two_accounts_start_before_polling_and_have_independent_deadlines(self):
        a, b = self.runner('a'), self.runner('b')
        b.account = hs.Account(2, 'b', 310)
        coordinator = Mock()
        coordinator.scheduler.accounts = [a.account, b.account]
        coordinator.scheduler.active = None
        coordinator.startup_returned = {'a', 'b'}
        coordinator.startup_pending, coordinator.deferred = {}, {}
        coordinator.runner_for.side_effect = lambda account: {'a': a, 'b': b}[account.name]
        coordinator.check_account.return_value = True
        clock = iter((1000, 1010))
        observed = []
        def finished(slot):
            observed.append((a.record('treasure').get('deadline_at'), b.record('treasure').get('deadline_at')))
            return True
        with patch.object(treasure, 'open_page'), patch.object(treasure, 'switch_to'), \
             patch.object(treasure.time, 'time', side_effect=lambda: next(clock)), \
             patch.object(treasure.TreasureRun, 'step', finished):
            treasure.run_multi(coordinator)
        self.assertEqual([(1300, 1310), (1300, 1310)], observed)
        self.assertEqual(1, a.click.call_count)
        self.assertEqual(1, b.click.call_count)

    def pause_runner(self):
        r = self.runner()
        frame = Image.new('RGB', (1920, 1032), (128,128,128))
        width = treasure.POSITION_ROI[2]-treasure.POSITION_ROI[0]
        height = treasure.POSITION_ROI[3]-treasure.POSITION_ROI[1]
        stripe = np.tile((np.arange(width)%2*255).astype('uint8'), (height,1))
        frame.paste(Image.fromarray(stripe), treasure.POSITION_ROI[:2])
        r.scheduler.capture.return_value = frame
        r.scheduler.content_guard.return_value = frame
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        return r, frame

    def test_pause_requires_three_observations_and_restart_never_clicks_again(self):
        r, frame = self.pause_runner()
        now = [1400]
        slot = treasure.TreasureRun(r, clock=lambda: now[0])
        self.assertFalse(slot._pause(frame))
        now[0] += 3
        self.assertFalse(slot._pause(frame))
        r.scheduler.click_point.assert_called_once()
        restored = treasure.TreasureRun(r, clock=lambda: now[0])
        now[0] += 3
        self.assertTrue(restored._pause(frame))
        r.scheduler.click_point.assert_called_once()
        self.assertEqual('done', r.scheduler.state.action_status(r.account.name, treasure.PAUSE_ACTION))
        self.assertEqual(1300, restored.record['deadline_at'])

    def test_intervening_battle_invalidates_previous_pause_observations(self):
        r, frame = self.pause_runner()
        now = [1400]
        slot = treasure.TreasureRun(r, clock=lambda: now[0])
        slot._pause(frame)
        now[0] += 3
        slot._pause(frame)
        r.scheduler._combat_active_visible.return_value = True
        self.assertFalse(slot.step())
        self.assertEqual(0, slot.record['stop_stable_frames'])
        r.scheduler._combat_active_visible.return_value = False
        now[0] += 3
        self.assertFalse(slot._pause(frame))
        self.assertEqual(1, slot.record['stop_stable_frames'])
        r.scheduler.click_point.assert_called_once()

    def test_original_reward_confirmation_is_reconciled_without_duplicate(self):
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300,
            awaiting={'action':'daily_treasure_reward_1','kind':'rewards','index':1,
                      'anchor':'treasure_result_anchor.bmp','roi':list(treasure.RESULT_ROI)})
        r.scheduler.state.accounts[r.account.name]['actions']['daily_treasure_reward_1'] = {'status':'pending'}
        slot = treasure.TreasureRun(r, clock=lambda:1400)
        self.assertFalse(slot.step())
        self.assertEqual(1, slot.record['rewards'])
        self.assertIsNone(slot.record['awaiting'])
        r.click.assert_not_called()
        r.scheduler.click_point.assert_not_called()


if __name__ == '__main__':
    unittest.main()
