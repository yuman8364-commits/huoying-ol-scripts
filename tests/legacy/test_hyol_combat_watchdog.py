from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from hyol_combat_watchdog import BattleWatchdog
import hyol_scheduler as hs
import hyol_multi_solo as multi


def frame(color=60):
    return Image.new('RGB', (1920, 1032), (color, color, color))


class BattleWatchdogTests(unittest.TestCase):
    def test_frozen_battle_stops_at_thirty_seconds_including_zero_start(self):
        monitor = BattleWatchdog()
        self.assertIsNone(monitor.observe(frame(), 0))
        self.assertIsNone(monitor.observe(frame(), 29.999))
        result = monitor.observe(frame(), 30)
        self.assertEqual('unchanged_frame', result['reason'])
        self.assertEqual((30, 30), (result['elapsed_seconds'], result['unchanged_seconds']))

    def test_chrome_chat_auto_badge_and_tiny_cursor_changes_do_not_extend_wait(self):
        original = frame()
        changed = original.copy()
        draw = ImageDraw.Draw(changed)
        draw.rectangle((0, 0, 1920, 160), fill='white')
        draw.rectangle((0, 830, 1920, 1032), fill='white')
        draw.rectangle((800, 500, 804, 504), fill='white')
        monitor = BattleWatchdog()
        monitor.observe(original, 10)
        self.assertEqual('unchanged_frame', monitor.observe(changed, 190)['reason'])

    def test_pixel_noise_does_not_extend_wait(self):
        monitor = BattleWatchdog()
        monitor.observe(frame(60), 0)
        self.assertEqual('unchanged_frame', monitor.observe(frame(63), 180)['reason'])

    def test_real_progress_extends_idle_budget_beyond_old_total_limit(self):
        monitor = BattleWatchdog()
        for moment, color in ((0, 60), (170, 140), (340, 60), (510, 140), (599, 60)):
            self.assertIsNone(monitor.observe(frame(color), moment))
        self.assertIsNone(monitor.observe(frame(140), 600))
        self.assertIsNone(monitor.observe(frame(60), 1200))
        self.assertEqual('unchanged_frame', monitor.observe(frame(60), 1230)['reason'])

    def test_missing_samples_do_not_invent_an_offline_result(self):
        monitor = BattleWatchdog(started_at=10)
        self.assertIsNone(monitor.observe(None, 609))
        self.assertIsNone(monitor.observe(None, 610))

    def test_tabs_have_independent_monotonic_deadlines(self):
        first = BattleWatchdog(started_at=0)
        second = BattleWatchdog(started_at=590)
        first.observe(frame(), 0)
        self.assertEqual('unchanged_frame', first.observe(frame(), 600)['reason'])
        self.assertIsNone(second.observe(frame(), 600))

    def test_invalid_configuration_cannot_disable_protection(self):
        for value in ('', 'nan', 'inf', '-1', '0', 'bad', None):
            with self.subTest(value=value):
                monitor = BattleWatchdog.from_config(dict(CombatStallSeconds=value, CombatMaxSeconds=value))
                self.assertEqual((30, float("inf")), (monitor.idle_seconds, monitor.max_seconds))

    def test_configuration_is_bounded(self):
        monitor = BattleWatchdog.from_config(dict(CombatStallSeconds='1', CombatMaxSeconds='999999'))
        self.assertEqual((30, float("inf")), (monitor.idle_seconds, monitor.max_seconds))

    def scheduler(self):
        s = object.__new__(hs.Scheduler)
        s.config = {}
        s.offline_dialog_visible = Mock(return_value=False)
        s.template = Mock(return_value=(False, 0, 0, 0))
        s._arena_waiting_battle_visible = Mock(return_value=False)
        s._arena_victory_visible = Mock(return_value=False)
        s._arena_defeat_visible = Mock(return_value=False)
        s._combat_ready_visible = Mock(return_value=False)
        s._combat_active_visible = Mock(return_value=True)
        s.save_raw_evidence = Mock(return_value=Path('battle_stalled.png'))
        s.event = Mock()
        s.click_template = Mock()
        s.commit_action = Mock()
        s.state = Mock()
        s.wait_interruptible = Mock()
        return s

    def test_repeated_active_markers_do_not_reset_arena_timer_or_change_results(self):
        s = self.scheduler()
        slot = hs.CombatSlot(hs.Account(1, 'example', 180), 'arena', running=True)
        with patch.object(hs.time, 'monotonic', side_effect=(0, 10, 29, 30)):
            for _ in range(3):
                s._combat_progress_watchdog(slot, frame())
            with self.assertRaises(hs.BattleStalled):
                s._combat_progress_watchdog(slot, frame())
        s.click_template.assert_not_called()
        s.commit_action.assert_not_called()
        s.state.set_action.assert_not_called()
        self.assertTrue(slot.running)
        self.assertEqual(1, slot.next_index)
        self.assertFalse(slot.complete)
        self.assertEqual('combat_watchdog_timeout', s.event.call_args.args[0])

    def test_next_battle_resets_its_own_timer(self):
        s = self.scheduler()
        slot = hs.CombatSlot(hs.Account(1, 'example', 180), 'arena', running=True)
        hs.check_combat_slot_watchdog(s, slot, frame(), now=10)
        previous = slot.battle_watchdog
        slot.next_index = 2
        hs.check_combat_slot_watchdog(s, slot, frame(), now=700)
        self.assertIsNot(previous, slot.battle_watchdog)
        self.assertEqual(700, slot.battle_watchdog.started_at)

    def test_real_result_is_processed_before_elapsed_time_guard(self):
        s = self.scheduler()
        s._arena_victory_visible.return_value = True
        slot = hs.CombatSlot(hs.Account(1, 'example', 180), 'arena', running=True,
                             battle_watchdog=BattleWatchdog(started_at=0), battle_watchdog_index=1)
        with patch.object(hs.time, 'monotonic', return_value=601):
            s._combat_progress_watchdog(slot, frame())
        s.save_raw_evidence.assert_not_called()

    def test_unhandled_result_banner_cannot_hide_a_stalled_battle_forever(self):
        s = self.scheduler()
        s.capture = Mock(return_value=frame())
        s._arena_victory_visible.return_value = True
        s._record_arena_victory = Mock()
        s._battle_auto_on_visible = Mock(return_value=True)
        slot = hs.CombatSlot(hs.Account(1, 'example', 180), 'arena', running=True,
                             battle_watchdog=BattleWatchdog(started_at=0), battle_watchdog_index=1)
        slot.battle_watchdog.observe(frame(), 0)
        with patch.object(hs.time, 'monotonic', return_value=30):
            with self.assertRaises(hs.BattleStalled):
                s._poll_arena_slot(slot)
        s.click_template.assert_not_called()

    def test_survival_battle_also_stops_before_any_retry_click(self):
        s = self.scheduler()
        slot = hs.CombatSlot(hs.Account(1, 'example', 180), 'survival', running=True)
        with patch.object(hs.time, 'monotonic', side_effect=(0, 30)):
            s._poll_survival_slot(slot, frame())
            with self.assertRaises(hs.BattleStalled):
                s._poll_survival_slot(slot, frame())
        s.click_template.assert_not_called()
        s.commit_action.assert_not_called()

    def coordinator(self, *, verified=True):
        a = hs.Account(1, 'example', 180)
        b = hs.Account(2, 'next', 315)
        s = Mock(accounts=[a, b], active=a)
        s._saved_tab_mapping.return_value = {a.name: a.tab_x} if verified else {}
        s._combat_active_visible.return_value = True
        s.ensure_account_main_before_release.return_value = False
        c = object.__new__(multi.MultiSoloRunner)
        c.scheduler = s
        c.event = Mock()
        c.save_progress = Mock()
        c.excluded = set()
        c.disconnected = set()
        c.exclusion_reasons = {}
        c.deferred = {}
        c.deferred_at = {}
        r = Mock(account=a)
        record = {'status': 'new'}
        r.record.return_value = record
        r.arena.side_effect = hs.BattleStalled('战斗卡住')
        return c, r, record

    def test_verified_stalled_account_is_skipped_without_automatic_resume(self):
        c, r, record = self.coordinator()
        c._run_task(r, 'arena')
        self.assertEqual('pending', record['status'])
        self.assertEqual({'example'}, c.excluded)
        self.assertEqual({'example': 'unchanged_30s'}, c.exclusion_reasons)
        self.assertEqual({}, c.deferred)
        c.scheduler.ensure_account_main_before_release.assert_not_called()
        c.scheduler.commit_action.assert_not_called()
        c.save_progress.assert_called_once_with('account_stalled')
        next_runner = Mock(account=c.scheduler.accounts[1])
        next_record = {'status': 'new'}
        next_runner.record.return_value = next_record
        c.scheduler.main_ready.return_value = True
        c.scheduler.template.return_value = (True, 1, 0, 0)
        c._run_task(next_runner, 'mail')
        self.assertEqual('done', next_record['status'])

    def test_unverified_stalled_tab_stops_instead_of_switching_blindly(self):
        c, r, record = self.coordinator(verified=False)
        with self.assertRaises(hs.LeaseBlocked):
            c._run_task(r, 'arena')
        self.assertEqual('pending', record['status'])
        self.assertEqual(set(), c.excluded)
        c.scheduler.click_template.assert_not_called()


if __name__ == '__main__':
    unittest.main()
