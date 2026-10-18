from __future__ import annotations
from test_identity_fixtures import ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F, legacy_config

import queue
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import hyol_panel as panel
import hyol_scheduler as hs


class PanelTests(unittest.TestCase):
    def test_live_failure_details_are_actionable_inside_client(self) -> None:
        self.assertIn("当前账号身份未确认", panel._translate_detail(f'visible account does not positively match {ROLE_E}: {{}}'))
        self.assertIn("1920", panel._translate_detail("game geometry changed before capture"))
        self.assertIn("停止", panel._translate_detail("game geometry changed before capture"))
        self.assertIn("自动战斗", panel._translate_detail("arena auto-battle control disappeared"))

    def test_all_panel_commands_map_to_scheduler_commands(self) -> None:
        self.assertEqual(set(hs.COMMAND_KINDS), set(panel.COMMAND_LABELS))
        self.assertEqual(1, hs.SchedulerCommand("team1").team_index)
        self.assertEqual(2, hs.SchedulerCommand("team2").team_index)
        with self.assertRaises(ValueError):
            hs.SchedulerCommand("reset_state")

    def test_scheduler_event_sink_receives_display_payload(self) -> None:
        received: list[dict[str, object]] = []
        scheduler = hs.Scheduler(legacy_config(), "panel", False, True, event_sink=received.append)
        scheduler.event("panel_test", detail="ok")
        self.assertEqual("panel_test", received[-1]["event"])
        self.assertEqual("ok", received[-1]["detail"])
        self.assertIn("ts", received[-1])

    def test_controller_rejects_a_second_concurrent_task(self) -> None:
        events: queue.Queue[dict[str, object]] = queue.Queue()
        entered = threading.Event()
        release = threading.Event()

        class FakeScheduler:
            def __init__(self, *_args, **_kwargs):
                pass

            def execute_command(self, _command: hs.SchedulerCommand) -> int:
                entered.set()
                release.wait(2)
                return 0

            def request_pause_after_module(self) -> None:
                pass

        controller = panel.PanelTaskController(events)
        with mock.patch.object(panel.hs, "Scheduler", FakeScheduler):
            self.assertTrue(controller.start(hs.SchedulerCommand("recognize")))
            self.assertTrue(entered.wait(1))
            self.assertFalse(controller.start(hs.SchedulerCommand("mail")))
            release.set()
            deadline = time.monotonic() + 2
            while controller.busy and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertFalse(controller.busy)

    def test_panel_has_no_state_reset_or_purchase_command(self) -> None:
        source = Path(panel.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("reset_state", source)
        self.assertNotIn("allowmarketpurchase=1", source)
        self.assertIn("self.iconify()", source)

    def test_full_command_order_is_fixed(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def _run_full_order", 1)[1].split("def run_full", 1)[0]
        needles = [
            "self.recognize_two_passes()",
            "self.run_market_all()",
            "self.run_managed_then_main()",
            'self.run_accounts_module("mail"',
            'self.run_accounts_module("welfare"',
            "for team_index in (1, 2)",
            "self.run_team_selection(team_index)",
            'self.run_combat_interleaved("arena")',
            'self.run_combat_interleaved("survival")',
        ]
        positions = [body.index(needle) for needle in needles]
        self.assertEqual(positions, sorted(positions))

    def test_panel_button_order_has_exactly_all_scheduler_commands(self) -> None:
        self.assertEqual(11, len(panel.PANEL_COMMAND_ORDER))
        self.assertEqual(set(hs.COMMAND_KINDS), set(panel.PANEL_COMMAND_ORDER))
        self.assertEqual(("mail", "welfare", "team1", "team2", "arena", "survival", "market", "managed", "all_day"), panel.MODULE_COLUMNS)

    def test_panel_exposes_selectable_combat_counts(self) -> None:
        source = Path(panel.__file__).read_text(encoding="utf-8")
        for label in ("竞技场胜利次数", "组队副本场数", "强者挑战场数"):
            self.assertIn(label, source)
        self.assertIn("arena_victory_target=self.arena_wins.get()", source)
        self.assertIn("group_instance_count=self.group_instance_count.get()", source)
        self.assertIn("strong_approach_count=self.strong_approach_count.get()", source)

    def test_panel_exposes_all_day_status_in_account_table(self) -> None:
        source = Path(panel.__file__).read_text(encoding="utf-8")
        self.assertIn('"all_day": "全天值守"', source)
        self.assertIn('self.tree.heading("market", text=f"黑市[{_label_cycle(cycle)}]")', source)
        self.assertIn('all_day_status = "done"', source)
        self.assertIn('module_values.append("待处理" if account_all_day_pending else _label_status(all_day_status))', source)

    def test_panel_event_is_chinese_and_hides_internal_codes(self) -> None:
        line = panel.format_panel_event(
            {
                "ts": "2026-08-29T22:00:00",
                "event": "module_pending",
                "command": "team1",
                "module": "team1_strong_approach",
                "status": "pending",
                "detail": "strong_approach page timeout",
                "target": "team1_strong_approach_start",
                "template": "strong_approach_entry.bmp",
            }
        )
        self.assertIn("模块待处理", line)
        self.assertIn("第一队强者挑战", line)
        self.assertNotRegex(line, r"[A-Za-z]")
        self.assertNotIn("module_pending", line)
        self.assertNotIn("strong_approach", line)

    def test_panel_arena_and_bond_events_are_chinese(self) -> None:
        arena_line = panel.format_panel_event(
            {
                "ts": "2026-08-30T09:00:00",
                "event": "arena_daily_rewards_verified",
                "account": ROLE_A,
                "mode": "arena",
                "claims": 2,
                "target": "arena_daily_rewards",
            }
        )
        bond_line = panel.format_panel_event(
            {
                "ts": "2026-08-30T09:01:00",
                "event": "team_three_member_template_enrolled",
                "team": 1,
                "member_count": 2,
                "target": "team1_member_template",
            }
        )
        for line in (arena_line, bond_line):
            self.assertNotRegex(line, r"[A-Za-z]")
        self.assertIn("竞技场每日奖励已核对", arena_line)
        self.assertIn("领取数：2", arena_line)
        self.assertIn("已登记队伍成员模板", bond_line)
        self.assertIn("成员数：2", bond_line)

    def test_panel_team_counter_and_auto_events_are_fully_chinese(self) -> None:
        counter = panel.format_panel_event(
            {
                "ts": "2026-08-31T19:00:00",
                "event": "team_reward_counter_verified",
                "account": ROLE_B,
                "team": 2,
                "activity": "strong_approach",
                "remaining": 6,
                "maximum": 7,
            }
        )
        auto = panel.format_panel_event(
            {
                "ts": "2026-08-31T19:01:00",
                "event": "team_member_auto_verified",
                "account": ROLE_E,
                "team": 2,
                "activity": "group_instance",
                "attempt": 1,
                "display": "取消",
            }
        )
        for line in (counter, auto):
            self.assertNotRegex(line, r"[A-Za-z]")
        self.assertIn("剩余奖励次数：6", counter)
        self.assertIn("次数上限：7", counter)
        self.assertIn("界面状态：取消", auto)

    def test_panel_display_cycle_follows_earliest_unsettled_batch(self) -> None:
        config = {"MarketRefreshTimes": "08:00,11:00,14:00,17:00,20:00,23:00"}
        self.assertEqual("PRE08", panel.display_market_cycle(config, {}, now="07:59"))
        self.assertEqual("0800", panel.display_market_cycle(config, {}, now="11:30"))
        state = {"all_day_cycles": {"0800": {"status": "done"}}}
        self.assertEqual("1100", panel.display_market_cycle(config, state, now="11:30"))
        state["all_day_cycles"]["1100"] = {"status": "done"}
        self.assertEqual("1400", panel.display_market_cycle(config, state, now="14:30"))

    def test_panel_hides_machine_batch_codes(self) -> None:
        line = panel.format_panel_event(
            {
                "ts": "2026-08-31T07:59:00",
                "event": "market_wait",
                "cycle": "PRE08",
                "latest_cycle": "PRE08",
            }
        )
        self.assertNotRegex(line, r"[A-Za-z]")
        self.assertIn("批次：未开放", line)
        self.assertIn("最新批次：未开放", line)


if __name__ == "__main__":
    unittest.main()
