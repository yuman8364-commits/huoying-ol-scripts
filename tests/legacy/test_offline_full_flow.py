from __future__ import annotations
from test_identity_fixtures import ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F, legacy_config

import json
import os
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import capture_rescue_template as rescue_capture
import hyol_panel as panel
import hyol_scheduler as hs
from offline_full_flow import ReplayFrameStore, SimulatedClicker, run_offline_suite


class OfflineFullFlowTests(unittest.TestCase):
    def test_all_eleven_panel_commands_complete_in_offline_replay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hyol_offline_test_") as temp_name:
            # Unified runs retain this replay's reports; standalone runs stay temporary.
            destination = Path(os.environ.get("HYOL_TEST_ACCEPTANCE_DIR", temp_name))
            summary = run_offline_suite(destination)
            self.assertEqual("passed", summary["status"])
            self.assertTrue(summary["buttons_passed"])
            self.assertTrue(summary["full_flow_passed"])
            self.assertTrue(summary["team_activity_leader_only"])
            self.assertTrue(summary["bond_duo_profile_passed"])
            self.assertTrue(summary["combat_start_after_prepare"])
            self.assertTrue(summary["market_button_effect"])
            self.assertTrue(summary["arena_rule_passed"])
            self.assertTrue(summary["visual_clicks_passed"])
            self.assertGreaterEqual(summary["visual_click_count"], 1)
            self.assertEqual(hs.ARENA_REQUIRED_WINS, summary["arena_required_wins"])
            self.assertEqual(hs.ARENA_DAILY_REWARD_CLAIMS, summary["arena_daily_reward_claims"])
            self.assertEqual(6, summary["arena_victory_account_count"])
            self.assertEqual(6, summary["arena_daily_claim_account_count"])
            self.assertTrue(summary["chinese_log_passed"])
            self.assertEqual(11, len(summary["results"]))
            self.assertEqual(set(hs.COMMAND_KINDS), set(summary["results"]))
            self.assertTrue(all(result == 0 for result in summary["results"].values()))
            self.assertEqual("离线全流程测试", summary["last_check"])
            report = Path(summary["report_path"]).read_text(encoding="utf-8")
            self.assertIn("11/11 功能键可调度：是", report)
            self.assertIn("组队活动结果与奖励次数由队长核验：是", report)
            self.assertIn("强者挑战每周7场、组队副本本日5场并以奖励次数0收尾：通过", report)
            self.assertIn("每场开战后全队成员均显示“取消”（自动战斗开启）：通过", report)

            audit_rows = [json.loads(line) for line in Path(summary["audit_path"]).read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(row.get("event") == "command_complete" for row in audit_rows))
            self.assertTrue(any(row.get("event") == "team_activity_skip_no_reward" for row in audit_rows))
            self.assertTrue(any(row.get("event") == "content_click" and row.get("target") == "black_market_npc_entry" for row in audit_rows))
            self.assertTrue(any(row.get("event") == "combat_slot_prepared" for row in audit_rows))
            self.assertTrue(any(row.get("event") == "arena_victory_confirmed" for row in audit_rows))
            self.assertEqual(
                6 * hs.ARENA_DAILY_REWARD_CLAIMS,
                sum(row.get("event") == "arena_daily_reward_claimed" for row in audit_rows),
            )
            self.assertEqual(
                2 * (hs.GROUP_INSTANCE_DAILY_REWARD_MAX + hs.STRONG_APPROACH_WEEKLY_REWARD_MAX),
                sum(row.get("event") == "team_activity_attempt_completed" for row in audit_rows),
            )
            self.assertEqual(
                2 * 3 * (hs.GROUP_INSTANCE_DAILY_REWARD_MAX + hs.STRONG_APPROACH_WEEKLY_REWARD_MAX),
                sum(row.get("event") == "team_member_auto_verified" for row in audit_rows),
            )
            zero_rows = [row for row in audit_rows if row.get("event") == "team_reward_zero_verified"]
            self.assertEqual(4, len(zero_rows))
            self.assertTrue(all(row.get("remaining") == 0 for row in zero_rows))

    def test_offline_display_lines_have_no_machine_english(self) -> None:
        events = [
            {"ts": "2026-08-29T23:00:00+08:00", "event": "command_pending", "command": "team1", "detail": "missing assets: team_activity_start.bmp"},
            {"ts": "2026-08-29T23:00:01+08:00", "event": "team_activity_done", "account": ROLE_A, "team": 1, "activity": "strong_approach", "status": "done"},
        ]
        lines = [panel.format_panel_event(event) for event in events]
        self.assertTrue(all(not re.search(r"[A-Za-z]", line) for line in lines))
        self.assertIn("待处理", lines[0])
        self.assertIn("活动：强者挑战", lines[1])
        self.assertIn("队伍：第一队", lines[1])

    def test_replay_frames_are_composed_only_from_existing_assets(self) -> None:
        replay = ReplayFrameStore()
        for scene in ("main", "arena_auto_off", "arena_result", "survival_auto_on", "survival_result"):
            frame = replay.frame(scene)
            self.assertEqual((1920, 1032), frame.size)
        self.assertGreaterEqual(len(replay.requested), 5)

    def test_visual_simulated_clicks_follow_scaled_template_locations(self) -> None:
        for scale in (0.80, 1.00, 1.25):
            with self.subTest(scale=scale):
                replay = ReplayFrameStore(scale=scale)
                clicker = SimulatedClicker()
                accepted = clicker.click_visual(
                    replay,
                    "arena_ready",
                    "arena_ranked_button_current.bmp",
                    (500, 700, 1200, 920),
                    f"arena_start_{scale}",
                    "离线缩放测试",
                )
                self.assertTrue(accepted)
                click = clicker.clicks[-1]
                self.assertEqual("template_match", click["source"])
                self.assertAlmostEqual(687.5 * scale, click["x"], delta=2)
                self.assertAlmostEqual(826.5 * scale, click["y"], delta=2)
                self.assertGreaterEqual(click["score"], 0.88)

    def test_visual_simulated_click_fails_closed_when_target_is_absent(self) -> None:
        replay = ReplayFrameStore(scale=1.25)
        clicker = SimulatedClicker()
        accepted = clicker.click_visual(
            replay,
            "main",
            "arena_result_confirm.bmp",
            (550, 350, 1300, 850),
            "absent_result",
            "离线缩放测试",
        )
        self.assertFalse(accepted)
        self.assertEqual("visual_target_not_found", clicker.clicks[-1]["reason"])
        self.assertNotIn("x", clicker.clicks[-1])

    def test_team_activity_registry_covers_all_requested_modes(self) -> None:
        self.assertEqual(
            {"wanted", "group_instance", "strong_approach", "rescue_jinchuriki", "team_pvp"},
            {spec.key for spec in hs.TEAM_ACTIVITY_SPECS},
        )
        self.assertTrue(hs.TEAM_ACTIVITY_BY_KEY["team_pvp"].reward_only)
        source = Path(hs.__file__).read_text(encoding="utf-8")
        generic = source.split("def run_generic_team_activity", 1)[1].split("def create_verified_team", 1)[0]
        self.assertNotIn("switch_account(member)", generic)
        self.assertIn("leader", generic)
        self.assertIn("free-entry status is not verified", generic)

    def test_team_profile_accepts_two_person_bond_configuration(self) -> None:
        config = legacy_config()
        config["Team1"] = f'{ROLE_A},{ROLE_C}'
        config["Team1FriendRowTemplates"] = "friend_2nv_row.bmp"
        scheduler = hs.Scheduler(config, "test", False, True)
        team = scheduler.teams[0]
        self.assertEqual(2, len(team.members))
        self.assertEqual(2, team.member_count)
        self.assertEqual("2人队伍", team.member_label)
        _leader, invitees, rows, _invite, _member_template = scheduler.verified_team_profile(team)
        self.assertEqual(1, len(invitees))
        self.assertEqual(1, len(rows))
        source = Path(hs.__file__).read_text(encoding="utf-8")
        identity = source.split("def _team_room_identity_assignment", 1)[1].split("def capture_verified_three_member_template", 1)[0]
        self.assertIn("member_count", identity)
        self.assertIn("permutations(range(member_count))", identity)

    def test_limited_team_activities_use_nested_daily_navigation_and_no_reward_gate(self) -> None:
        rescue = hs.TEAM_ACTIVITY_BY_KEY["rescue_jinchuriki"]
        pvp = hs.TEAM_ACTIVITY_BY_KEY["team_pvp"]
        self.assertEqual("daily_limited", rescue.entry_navigation)
        self.assertEqual("daily_limited", pvp.entry_navigation)
        self.assertEqual("daily_participate.bmp", pvp.no_reward_template)
        source = Path(hs.__file__).read_text(encoding="utf-8")
        generic = source.split("def run_generic_team_activity", 1)[1].split("def create_verified_team", 1)[0]
        self.assertIn('"daily_practice_toolbar.bmp"', generic)
        self.assertIn('"daily_limited_tab.bmp"', generic)
        self.assertIn("reward-state template is missing", generic)

    def test_rescue_template_capture_is_time_gated_and_only_enrolls_before_battle(self) -> None:
        self.assertFalse(rescue_capture._open_now(rescue_capture.time(17, 59)))
        self.assertTrue(rescue_capture._open_now(rescue_capture.time(18, 0)))
        self.assertTrue(rescue_capture._open_now(rescue_capture.time(22, 30)))
        self.assertFalse(rescue_capture._open_now(rescue_capture.time(22, 31)))
        self.assertFalse(rescue_capture._calendar_open(datetime(2026, 8, 31, 18, 0)))
        self.assertTrue(rescue_capture._calendar_open(datetime(2026, 9, 3, 18, 0)))
        source = Path(rescue_capture.__file__).read_text(encoding="utf-8")
        self.assertIn("rescue_template_captured", source)
        self.assertNotIn('"team_activity_start.bmp"', source)
        self.assertIn('"daily_participate.bmp"', source)
        self.assertIn("team2_rescue_jinchuriki_participate", source)


if __name__ == "__main__":
    unittest.main()
