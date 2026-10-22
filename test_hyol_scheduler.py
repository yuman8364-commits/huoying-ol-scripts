from __future__ import annotations
from test_identity_fixtures import ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F, legacy_config

import json
import os
import subprocess
import sys
import threading
import tempfile
import unittest
from unittest.mock import ANY, Mock, patch
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

import hyol_scheduler as hs


class SchedulerUnitTests(unittest.TestCase):
    def test_wanted_empty_ignores_green_map_left_border(self):
        frame = Image.new('RGB',(1920,1032),(70,70,70))
        for x in range(1715,1725):
            for y in range(528,564):
                frame.putpixel((x,y),(15,150,10))
        self.assertTrue(hs.wanted_task_list_is_empty(frame))

    def test_wanted_green_text_inside_panel_still_blocks_empty(self):
        frame = Image.new('RGB',(1920,1032),(70,70,70))
        for x in range(1730,1737):
            for y in range(500,505):
                frame.putpixel((x,y),(15,150,10))
        self.assertFalse(hs.wanted_task_list_is_empty(frame))

    def test_survival_finish_requires_delayed_220_and_claim_result(self) -> None:
        account = hs.Account(1, 'replay', 180)
        slot = SimpleNamespace(account=account, complete=False, running=False, next_index=5)
        scheduler = object.__new__(hs.Scheduler)
        scheduler.template = Mock(return_value=(True, 1.0, 0, 0))
        scheduler.wait_template = Mock(side_effect=[True])
        scheduler.capture = Mock(return_value=Image.new('RGB', (1920, 1032)))
        scheduler._survival_full_score_visible = Mock(return_value=True)
        scheduler.claim_survival_rewards = Mock()
        scheduler.reconcile_survival_result_confirmations = Mock()
        scheduler._ensure_survival_reset = Mock()
        scheduler.close_survival = Mock()
        scheduler.state = Mock()
        scheduler.state.accounts = {account.name: {'actions': {}}}
        scheduler.state.action_status.return_value = 'done'
        scheduler.event = Mock()
        hs.Scheduler._finish_survival_slot(scheduler, slot)
        scheduler.wait_template.assert_called_once()
        scheduler.claim_survival_rewards.assert_called_once_with(account)
        scheduler.close_survival.assert_called_once_with(account)
        self.assertTrue(slot.complete)

    def test_survival_finish_unverified_score_saves_frame_without_claim(self) -> None:
        account = hs.Account(1, 'replay', 180)
        slot = SimpleNamespace(account=account, complete=False)
        scheduler = object.__new__(hs.Scheduler)
        frame = Image.new('RGB', (1920, 1032))
        scheduler.template = Mock(side_effect=[(True, 1.0, 0, 0), (False, .72, 0, 0)])
        scheduler.wait_template = Mock(return_value=False)
        scheduler.capture = Mock(return_value=frame)
        scheduler.save_raw_evidence = Mock(return_value=Path('score_pending.png'))
        scheduler._survival_full_score_visible = Mock(return_value=True)
        scheduler.claim_survival_rewards = Mock()
        scheduler.event = Mock()
        with self.assertRaises(hs.PendingError):
            hs.Scheduler._finish_survival_slot(scheduler, slot)
        scheduler.save_raw_evidence.assert_called_once()
        scheduler.claim_survival_rewards.assert_not_called()
        self.assertFalse(slot.complete)

    def test_survival_finish_unverified_claim_stays_pending(self) -> None:
        account = hs.Account(1, 'replay', 180)
        slot = SimpleNamespace(account=account, complete=False)
        scheduler = object.__new__(hs.Scheduler)
        scheduler.template = Mock(return_value=(True, 1.0, 0, 0))
        scheduler.wait_template = Mock(return_value=True)
        scheduler._survival_full_score_visible = Mock(return_value=True)
        scheduler.claim_survival_rewards = Mock(side_effect=hs.PendingError('claim unknown'))
        scheduler.state = SimpleNamespace(accounts={account.name: {'actions': {}}})
        scheduler.reconcile_survival_result_confirmations = Mock()
        scheduler.close_survival = Mock()
        with self.assertRaises(hs.PendingError):
            hs.Scheduler._finish_survival_slot(scheduler, slot)
        scheduler.close_survival.assert_not_called()
        self.assertFalse(slot.complete)

    def test_current_window_mode_requires_no_account_names_or_teams(self) -> None:
        config = {
            "AccountMode": "current",
            "TabXs": "180,310,445,590,730,860",
            "AllowMarketPurchase": "0",
        }
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(hs, "STATE_DIR", Path(folder) / "state"), \
             patch.object(hs, "LOGS", Path(folder) / "logs"):
            scheduler = hs.Scheduler(config, "panel", False, False)
        self.assertEqual(["当前窗口"], [account.name for account in scheduler.accounts])
        self.assertEqual([], scheduler.teams)

    def test_generic_current_window_selects_unique_highlight_without_identity(self) -> None:
        frame = Image.new("RGB", hs.EXPECTED_WINDOW[2:], (211, 211, 211))
        pixels = np.asarray(frame).copy()
        pixels[12:28, 578:603] = 255
        frame = Image.fromarray(pixels)
        scheduler = object.__new__(hs.Scheduler)
        scheduler.config = {"TabXs": "180,310,445,590,730,860"}
        self.assertEqual(590, hs.Scheduler._generic_active_tab_x(scheduler, frame))
        scheduler.generic_current_tab_x = 590
        self.assertTrue(hs.Scheduler._generic_current_tab_is_active(scheduler, frame))
        scheduler.generic_current_tab_x = 445
        self.assertFalse(hs.Scheduler._generic_current_tab_is_active(scheduler, frame))

    def test_multi_solo_selected_tab_uses_verified_live_centers(self) -> None:
        frame = Image.new('RGB', hs.EXPECTED_WINDOW[2:], (211,211,211))
        pixels = np.asarray(frame).copy()
        pixels[12:28, 843:868] = 255
        frame = Image.fromarray(pixels)
        scheduler = object.__new__(hs.Scheduler)
        scheduler.stage = 'multi_solo'
        scheduler.config = {'TabXs':'100,200,300,400,500,600'}
        scheduler.accounts = [hs.Account(1,'a',180),hs.Account(2,'b',855)]
        self.assertEqual(855,hs.Scheduler._generic_active_tab_x(scheduler,frame))

    def test_arena_client_release_contains_no_configured_account_identity(self) -> None:
        source = Path("hyol_arena.py").read_text(encoding="utf-8")
        config = Path("ARENA_Client.ini").read_text(encoding="utf-8")
        for token in ("TabAccountNames", "Team1", "Team2", "HYOL_Daily.ini"):
            self.assertNotIn(token, source + config)
        for account_name in (ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F):
            self.assertNotIn(account_name, source + config)
        self.assertIn("generic_current_window=True", source)

    def test_generic_current_window_is_arena_eligible_without_identity_templates(self) -> None:
        scheduler = hs.Scheduler.__new__(hs.Scheduler)
        current = hs.Account(1, "当前窗口", 590)
        scheduler.accounts = [current]
        scheduler.available_account_names = set()
        scheduler.generic_current_tab_x = 590
        self.assertEqual([current], scheduler.combat_eligible_accounts())
        scheduler.generic_current_tab_x = None
        self.assertEqual([], scheduler.combat_eligible_accounts())

    def test_generic_current_window_can_resume_an_active_battle(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        binding = source.split("def bind_generic_current_window", 1)[1].split("def prepare_additional_arena_request", 1)[0]
        self.assertIn('self._combat_active_visible("arena", frame)', binding)

    def test_generic_content_guard_uses_selected_tab_instead_of_identity_asset(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        guard = source.split("def content_guard", 1)[1].split("def save_evidence", 1)[0]
        self.assertIn("if self.generic_current_tab_x is not None", guard)
        self.assertIn("ok, scores = True, {account.name: 1.0}", guard)
        self.assertLess(guard.index("_generic_current_tab_is_active"), guard.index("ok, scores = True"))

    def test_old_daily_victory_marker_does_not_block_additional_target(self) -> None:
        account = hs.Account(2, ROLE_E, 180)
        scheduler = SimpleNamespace(
            state=SimpleNamespace(
                accounts={account.name: {"actions": {
                    "arena_casual_battle_1": {"status": "done"},
                    "arena_casual_battle_2": {"status": "done"},
                    "arena_attempt_victory_1": {"status": "done"},
                    "arena_attempt_victory_2": {"status": "done"},
                    "arena_daily_victory": {"status": "done"},
                    "arena_daily_reward_claim_1": {"status": "done"},
                    "arena_daily_reward_claim_2": {"status": "done"},
                }}},
                action_status=lambda name, key: scheduler.state.accounts[name]["actions"].get(key, {}).get("status", "new"),
            ),
            arena_victory_target=7,
            arena_attempt_limit=12,
            _combat_limit=lambda _mode, _account=None: 12,
            _combat_action_name=lambda _mode, index: f"arena_casual_battle_{index}",
            _arena_victory_count=lambda _account: 2,
            _arena_victory_confirmed=lambda _account: False,
            _arena_daily_rewards_complete=lambda _account: True,
        )
        self.assertEqual(3, hs.Scheduler._next_combat_index(scheduler, account, "arena"))

    def test_visible_account_binding_requires_unique_identity_without_tab_click(self) -> None:
        moon = hs.Account(2, ROLE_E, 860)
        other = hs.Account(1, ROLE_C, 310)
        frame = object()
        scheduler = SimpleNamespace(
            capture=Mock(return_value=frame), template=Mock(return_value=(False, 0, 0, 0)),
            identity_scores=Mock(return_value={moon.name: 0.97, other.name: 0.70}),
            identity_assignment_threshold=0.80, identity_margin=0.08,
            by_name={moon.name: moon, other.name: other}, accounts=[moon, other],
            event=Mock(), active=None, current_account_only=None, joint_identity_bound=set(),
            state=SimpleNamespace(global_state={}, save_global=Mock()),
        )
        bound = hs.Scheduler.bind_visible_account_only(scheduler)
        self.assertEqual(moon.name, bound.name)
        self.assertEqual(moon.name, scheduler.current_account_only)
        self.assertEqual(0, scheduler.event.call_args.kwargs["tab_clicks"])

    def test_visible_account_binding_accepts_main_page_with_strong_margin(self) -> None:
        moon = hs.Account(2, ROLE_E, 860)
        other = hs.Account(1, ROLE_C, 310)
        scheduler = SimpleNamespace(
            capture=Mock(return_value=object()), template=Mock(return_value=(False, 0, 0, 0)),
            identity_scores=Mock(return_value={moon.name: 0.775, other.name: 0.60}),
            identity_assignment_threshold=0.80, identity_margin=0.08, main_ready=Mock(return_value=True),
            by_name={moon.name: moon, other.name: other}, accounts=[moon, other],
            event=Mock(), active=None, current_account_only=None, joint_identity_bound=set(),
            state=SimpleNamespace(global_state={}, save_global=Mock()),
        )
        bound = hs.Scheduler.bind_visible_account_only(scheduler)
        self.assertEqual(moon.name, bound.name)
        self.assertEqual("visible_current_main_with_strong_margin", scheduler.event.call_args.kwargs["detection"])

    def test_single_bound_account_tab_activity_has_no_division_by_zero(self) -> None:
        account = hs.Account(1, ROLE_F, 445)
        scheduler = SimpleNamespace(accounts=[account])
        frame = Image.new("RGB", (1920, 1032), "black")
        self.assertTrue(hs.Scheduler.tab_is_visually_active(scheduler, account, frame))

    def test_bounded_window_wait_reports_geometry_instead_of_hanging(self) -> None:
        scheduler = SimpleNamespace(check_stop=Mock(), hwnd=0, event=Mock(), validate_geometry=Mock(return_value=False))
        with patch.object(hs.Win32, "game_windows", return_value=[123]), \
             patch.object(hs.Win32, "rect", return_value=(537, 190, 1939, 1060)), \
             patch.object(hs.Win32.user32, "IsIconic", return_value=False), \
             patch.object(hs.time, "monotonic", side_effect=[0.0, 2.0]), \
             self.assertRaisesRegex(hs.SafetyError, "位置或尺寸不符"):
            hs.Scheduler.bind_window(scheduler, wait=True, timeout_seconds=1)

    def test_additional_arena_request_resumes_then_starts_new_round(self) -> None:
        account = hs.Account(2, ROLE_E, 180)
        scheduler = SimpleNamespace(
            state=SimpleNamespace(global_state={}, accounts={account.name: {"actions": {"arena_casual_battle_12": {"status": "done"}}}}, save_global=Mock()),
            _arena_victory_count=Mock(return_value=11), event=Mock(),
        )
        hs.Scheduler.prepare_additional_arena_request(scheduler, account, 2)
        self.assertEqual(13, scheduler.arena_victory_target)
        self.assertEqual(24, scheduler.arena_attempt_limit)
        scheduler._arena_victory_count.return_value = 12
        hs.Scheduler.prepare_additional_arena_request(scheduler, account, 3)
        self.assertEqual(14, scheduler.arena_victory_target)
        self.assertEqual(3, scheduler.state.global_state["arena_requests"][account.name]["requested_wins"])
        scheduler.state.global_state["arena_requests"][account.name]["status"] = "done"
        scheduler._arena_victory_count.return_value = 13
        hs.Scheduler.prepare_additional_arena_request(scheduler, account, 1)
        self.assertEqual(14, scheduler.arena_victory_target)


    def test_ticket_exhaustion_requires_three_positive_zero_count_frames(self) -> None:
        account = hs.Account(2, ROLE_E, 180)
        scheduler = object.__new__(hs.Scheduler)
        scheduler.capture = Mock(side_effect=[object(), object(), object()])
        scheduler._combat_ready_visible = Mock(return_value=True)
        scheduler._arena_token_count = Mock(return_value=0)
        scheduler.wait_interruptible = Mock()
        scheduler.event = Mock()
        self.assertTrue(hs.Scheduler._arena_tickets_exhausted_on_page(scheduler, account))
        self.assertEqual(3, scheduler.capture.call_count)
        scheduler._arena_token_count = Mock(return_value=None)
        with self.assertRaisesRegex(hs.PendingError, '无法唯一识别'):
            hs.Scheduler._arena_tickets_exhausted_on_page(scheduler, account)

    def test_ticket_mode_ignores_win_target_until_tickets_are_verified_exhausted(self) -> None:
        account = hs.Account(2, ROLE_E, 180)
        scheduler = object.__new__(hs.Scheduler)
        request = {'target_mode': 'tickets', 'status': 'running', 'target': 1, 'tickets_exhausted': False}
        scheduler.state = SimpleNamespace(global_state={'arena_requests': {account.name: request}})
        scheduler.arena_victory_target = 1
        scheduler._arena_victory_count = Mock(return_value=20)
        self.assertFalse(hs.Scheduler._arena_victory_confirmed(scheduler, account))
        self.assertFalse(hs.Scheduler._arena_goal_complete(scheduler, account))
        request.update(status='done', tickets_exhausted=True)
        self.assertTrue(hs.Scheduler._arena_goal_complete(scheduler, account))

    def test_ticket_request_has_bounded_attempt_limit_and_does_not_use_win_count_as_stop(self) -> None:
        account = hs.Account(2, ROLE_E, 180)
        scheduler = SimpleNamespace(
            state=SimpleNamespace(global_state={}, accounts={account.name: {'actions': {}}}, save_global=Mock()),
            _arena_victory_count=Mock(return_value=0), event=Mock(),
        )
        hs.Scheduler.prepare_additional_arena_request(scheduler, account, 5, target_mode='tickets')
        request = scheduler.state.global_state['arena_requests'][account.name]
        self.assertEqual('tickets', request['target_mode'])
        self.assertEqual(hs.ARENA_MAX_ATTEMPTS, request['attempt_limit'])
        self.assertFalse(request['tickets_exhausted'])
        self.assertEqual(5, request['requested_wins'])

    def test_ticket_mode_never_clicks_casual_when_tokens_are_zero(self) -> None:
        source = Path(hs.__file__).read_text(encoding='utf-8')
        selector = source.split('def _click_arena_mode_when_stable', 1)[1].split('def _combat_progress_watchdog', 1)[0]
        self.assertIn("self._arena_target_mode_for(account) == 'tickets'", selector)
        self.assertIn("return 'tickets_exhausted'", selector)
        start = source.split('def _start_arena_slot', 1)[1].split('def _arena_opening_once', 1)[0]
        self.assertIn('combat_battle_start_requested', start)
        self.assertNotIn('transition_deadline', start)

    def test_running_battle_waits_through_transient_missing_anchor(self) -> None:
        account = hs.Account(2, ROLE_E, 180)
        scheduler = SimpleNamespace(
            chrome_click=Mock(), capture=Mock(return_value=object()), healthy=Mock(return_value=True),
            tab_is_visually_active=Mock(return_value=True), template=Mock(return_value=(False, 0, 0, 0)),
            _combat_active_visible=Mock(side_effect=[False, False, True]), _combat_ready_visible=Mock(return_value=False),
            verify_identity=Mock(return_value=(False, {})), wait_interruptible=Mock(), event=Mock(),
            _validate_tab_binding=Mock(), _record_tab_binding=Mock(),
            state=SimpleNamespace(global_state={}, save_global=Mock()),
        )
        with patch.object(hs.time, "sleep"), patch.object(hs.Win32, "click") as click:
            hs.Scheduler.switch_account_for_combat(scheduler, account, "arena", True)
            click.assert_not_called()
        self.assertEqual([1.0, .4, .4], [call.args[0] for call in scheduler.wait_interruptible.call_args_list])
        self.assertEqual(account, scheduler.active)

    def test_running_same_selected_tab_does_not_click_chrome_each_poll(self) -> None:
        account = hs.Account(2, ROLE_E, 180)
        scheduler = SimpleNamespace(
            active=account, chrome_click=Mock(), capture=Mock(return_value=object()),
            _generic_active_tab_x=Mock(return_value=180), healthy=Mock(return_value=True),
            tab_is_visually_active=Mock(return_value=True),
            template=Mock(return_value=(False,0,0,0)),
            _combat_active_visible=Mock(return_value=True), _combat_ready_visible=Mock(),
            event=Mock(), _validate_tab_binding=Mock(),
            _saved_tab_mapping=lambda: {ROLE_E: 180},
            state=SimpleNamespace(global_state={'tab_map':{account.name:180}}),
        )
        hs.Scheduler.switch_account_for_combat(scheduler,account,'arena',True)
        scheduler.chrome_click.assert_not_called()
        scheduler.event.assert_called_once()

    def test_arena_matching_and_loading_share_bounded_stall_budget(self) -> None:
        account = hs.Account(2, ROLE_E, 180)
        slot = hs.CombatSlot(account,'arena',running=True)
        frame = Image.new('RGB',(1920,1032))
        phase = ['matching']
        def template(name,*args,**kwargs):
            return (name == ('arena_matching_anchor.bmp' if phase[0]=='matching'
                            else 'arena_loading_anchor.bmp'), 1.0, 0, 0)
        scheduler = SimpleNamespace(template=template,_arena_waiting_battle_visible=Mock(return_value=False),
                                    _arena_victory_visible=Mock(return_value=False), _arena_defeat_visible=Mock(return_value=False),
                                    save_raw_evidence=Mock(return_value='stalled.png'),event=Mock())
        with patch.object(hs.time,'monotonic',side_effect=[100.0,129.0,130.0]):
            hs.Scheduler._combat_progress_watchdog(scheduler,slot,frame)
            phase[0]='loading'
            hs.Scheduler._combat_progress_watchdog(scheduler,slot,frame)
            with self.assertRaisesRegex(hs.BattleStalled,'页面连续30秒无变化'):
                hs.Scheduler._combat_progress_watchdog(scheduler,slot,frame)
        self.assertEqual(slot.battle_watchdog.changed_at,100.0)
        scheduler.save_raw_evidence.assert_called_once()

    def test_single_account_command_rejects_incomplete_or_team_scope(self) -> None:
        for kwargs in ({"current_account": ROLE_E}, {"current_tab_x": 180}, {"current_account": ROLE_E, "current_tab_x": -1}):
            with self.assertRaises(ValueError):
                hs.SchedulerCommand("arena", **kwargs)
        with self.assertRaises(ValueError):
            hs.SchedulerCommand("team2", current_account=ROLE_E, current_tab_x=180)
        with self.assertRaises(ValueError):
            hs.SchedulerCommand("team2", generic_current_window=True)
        with self.assertRaises(ValueError):
            hs.SchedulerCommand("arena", current_account=ROLE_E, current_tab_x=180, generic_current_window=True)

    def test_single_account_mode_never_clicks_any_tab(self) -> None:
        account = hs.Account(2, ROLE_E, 180)
        scheduler = SimpleNamespace(hwnd=123, ensure_window=Mock(), event=Mock(), click_count=0, current_account_only=account.name, check_input_pause=Mock())
        with patch.object(hs.Win32, "click") as click:
            hs.Scheduler.chrome_click(scheduler, 180, 10, f'combat_switch:arena:{ROLE_E}', account)
            hs.Scheduler.chrome_click(scheduler, 180, 10, f'switch_tab:{ROLE_E}', account)
            for other, label in ((hs.Account(1, ROLE_C, 310), f'switch_tab:{ROLE_C}'), (None, "rebind_probe:1"), (account, "refresh")):
                with self.assertRaises(hs.SafetyError):
                    hs.Scheduler.chrome_click(scheduler, 180, 10, label, other)
            click.assert_not_called()
        self.assertEqual(0, scheduler.click_count)

    def test_single_account_binding_rejects_wrong_visible_identity(self) -> None:
        account = hs.Account(2, ROLE_E, 860)
        scheduler = SimpleNamespace(by_name={account.name: account}, capture=Mock(return_value=object()), verify_identity=Mock(return_value=(False, {account.name: 0.2})), template=Mock(return_value=(False, 0, 0, 0)))
        with patch.object(hs.Win32, "click") as click:
            with self.assertRaises(hs.PendingError):
                hs.Scheduler.bind_current_account_only(scheduler, account.name, 180)
            click.assert_not_called()

    def test_delayed_click_rejects_focus_loss_without_sending_mouse_down(self) -> None:
        user = Mock()
        user.SetCursorPos.return_value = True
        with patch.object(hs.Win32, 'user32', user), \
             patch.object(hs.Win32, 'foreground', return_value=456), \
             patch.object(hs.time, 'sleep'):
            with self.assertRaisesRegex(hs.SafetyError, 'lost foreground'):
                hs.Win32.click(180, 200, expected_hwnd=123)
        user.mouse_event.assert_not_called()

    def test_delayed_click_checks_pause_immediately_before_mouse_down(self) -> None:
        user = Mock()
        user.SetCursorPos.return_value = True
        guard = Mock(side_effect=KeyboardInterrupt('paused'))
        with patch.object(hs.Win32, 'user32', user), patch.object(hs.time, 'sleep'):
            with self.assertRaises(KeyboardInterrupt):
                hs.Win32.click(180, 200, expected_hwnd=123, before_down=guard)
        guard.assert_called_once_with()
        user.mouse_event.assert_not_called()

    def test_f12_during_hover_prevents_mouse_down(self) -> None:
        user = Mock()
        user.SetCursorPos.return_value = True
        with patch.object(hs.Win32, 'user32', user), \
             patch.object(hs.Win32, 'foreground', return_value=123), \
             patch.object(hs.Win32, 'f12_down', return_value=True), \
             patch.object(hs.time, 'sleep'):
            with self.assertRaises(KeyboardInterrupt):
                hs.Win32.click(180, 200, expected_hwnd=123)
        user.mouse_event.assert_not_called()

    def test_interrupted_mouse_hold_still_releases_left_button(self) -> None:
        user = Mock()
        user.SetCursorPos.return_value = True
        with patch.object(hs.Win32, 'user32', user), \
             patch.object(hs.time, 'sleep', side_effect=[None, KeyboardInterrupt('stop')]):
            with self.assertRaises(KeyboardInterrupt):
                hs.Win32.click(180, 200)
        self.assertEqual([(hs.Win32.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0),
                          (hs.Win32.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)],
                         [call.args for call in user.mouse_event.call_args_list])

    def test_chrome_click_rejects_nonclient_targets_without_input(self) -> None:
        scheduler = SimpleNamespace(hwnd=123, ensure_window=Mock(), event=Mock(), click_count=0, check_input_pause=Mock())
        for hit in (-2, -1, 0, 2, 3, 8, 9, 10, 12, 20):
            with self.subTest(hit=hit), patch.object(hs.Win32, "hit_test", return_value=hit), patch.object(hs.Win32, "click") as click:
                with self.assertRaises(hs.SafetyError):
                    hs.Scheduler.chrome_click(scheduler, 180, 10, "probe")
                click.assert_not_called()
        self.assertEqual(0, scheduler.click_count)

    def test_chrome_click_rechecks_window_after_hit_test(self) -> None:
        scheduler = SimpleNamespace(hwnd=123, ensure_window=Mock(), event=Mock(), click_count=0, check_input_pause=Mock())
        with patch.object(hs.Win32, "hit_test", return_value=1), patch.object(hs.Win32, "foreground", return_value=456), patch.object(hs.Win32, "click") as click:
            with self.assertRaises(hs.SafetyError):
                hs.Scheduler.chrome_click(scheduler, 180, 10, "probe")
            click.assert_not_called()

    def test_chrome_click_accepts_verified_client_target(self) -> None:
        scheduler = SimpleNamespace(hwnd=123, ensure_window=Mock(), event=Mock(), click_count=0, check_input_pause=Mock())
        with patch.object(hs.Win32, "hit_test", return_value=1), patch.object(hs.Win32, "foreground", return_value=123), patch.object(hs.Win32, "rect", return_value=hs.EXPECTED_WINDOW), patch.object(hs.Win32, "click") as click:
            hs.Scheduler.chrome_click(scheduler, 180, 10, "probe")
            click.assert_called_once_with(180, 10, expected_hwnd=123,
                                          before_down=scheduler.check_input_pause)
        self.assertEqual(1, scheduler.click_count)

    def test_runtime_lease_serializes_foreground_workers(self) -> None:
        original_name = hs.ForegroundRuntimeLease.NAME
        hs.ForegroundRuntimeLease.NAME = f"Local\\HYOL_Test_{os.getpid()}"
        first = hs.ForegroundRuntimeLease()
        child_code = (
            "from hyol_scheduler import ForegroundRuntimeLease\n"
            "import sys\n"
            "ForegroundRuntimeLease.NAME = sys.argv[1]\n"
            "lease = ForegroundRuntimeLease()\n"
            "acquired = lease.acquire()\n"
            "if acquired:\n"
            "    lease.release()\n"
            "raise SystemExit(0 if acquired == (sys.argv[2] == '1') else 1)\n"
        )
        try:
            self.assertTrue(first.acquire())
            blocked = subprocess.run(
                [sys.executable, "-c", child_code, hs.ForegroundRuntimeLease.NAME, "0"],
                cwd=str(hs.ROOT),
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(0, blocked.returncode, blocked.stderr)
            first.release()
            acquired = subprocess.run(
                [sys.executable, "-c", child_code, hs.ForegroundRuntimeLease.NAME, "1"],
                cwd=str(hs.ROOT),
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(0, acquired.returncode, acquired.stderr)
        finally:
            first.release()
            hs.ForegroundRuntimeLease.NAME = original_name

    def test_config_declares_exactly_six_accounts_and_two_complete_teams(self) -> None:
        config = legacy_config()
        accounts = hs.parse_csv(config["TabAccountNames"])
        team1 = hs.parse_csv(config["Team1"])
        team2 = hs.parse_csv(config["Team2"])
        self.assertEqual("tab6", config["AccountMode"])
        self.assertEqual("6", config["AccountCount"])
        self.assertEqual(6, len(accounts))
        self.assertEqual(6, len(set(accounts)))
        self.assertEqual(set(accounts), set(team1 + team2))
        self.assertEqual("0", config["AllowMarketPurchase"])
        self.assertEqual("0", config["MarketDirectBuyVerified"])
        self.assertEqual("coupon_only", config["MarketCurrency"])
        self.assertEqual("none", config["MarketMaxPrice"])
        self.assertEqual("all_day", config["MarketMonitorMode"])
        self.assertEqual("group_instance", config["Team1Type"])
        self.assertEqual("group_instance", config["Team2Type"])
        self.assertEqual("0", config["RescueJinchurikiEnabled"])

    def test_deferred_rescue_keeps_verified_team_scope_without_dissolution(self) -> None:
        config = legacy_config()
        scheduler = hs.Scheduler(config, "test", False, True)
        self.assertFalse(scheduler.rescue_enabled)
        self.assertNotIn("rescue_jinchuriki", {spec.key for spec in scheduler._enabled_team_activity_specs()})
        self.assertIn("rescue_jinchuriki", {spec.key for spec in scheduler._deferred_team_activity_specs()})
        source = Path(hs.__file__).read_text(encoding="utf-8")
        self.assertIn('"status": "deferred_activities"', source)
        self.assertIn("team_preserved=True", source)

    def test_disabled_participate_shape_is_not_an_active_control(self) -> None:
        config = legacy_config()
        scheduler = hs.Scheduler(config, "test", False, True)
        active = Image.new("RGB", (1920, 1032), "black")
        with Image.open(hs.ASSETS / "daily_participate.bmp") as button:
            active.paste(button.convert("RGB"), (1167, 669))
            disabled = Image.new("RGB", (1920, 1032), "black")
            disabled.paste(button.convert("L").convert("RGB"), (1167, 669))
        roi = (1050, 620, 1350, 740)
        self.assertTrue(scheduler.active_template("daily_participate.bmp", roi, active)[0])
        self.assertFalse(scheduler.active_template("daily_participate.bmp", roi, disabled)[0])

    def test_leader_is_highest_power_and_ties_follow_config_order(self) -> None:
        members = ("a", "b", "c")
        self.assertEqual("b", hs.choose_leader(members, {"a": 10, "b": 30, "c": 20}))
        self.assertEqual("a", hs.choose_leader(members, {"a": 30, "b": 30, "c": 30}))
        self.assertEqual("b", hs.choose_leader(members, {"a": 10, "b": 30, "c": 30}))

    def test_wanted_empty_gate_rejects_task_text(self) -> None:
        root = hs.ROOT / "logs" / "scheduler_evidence" / "2026-08-21"
        self.assertTrue(hs.wanted_task_list_is_empty(Image.open(root / "team2_wanted_completion_probe.png")))
        self.assertFalse(hs.wanted_task_list_is_empty(Image.open(root / "team2_wanted_poll_55s.png")))

    def test_real_wanted_chain_prefix_is_visible_and_absent_after_completion(self):
        folder = hs.LOGS/'scheduler_evidence/2026-08-21'
        store = hs.TemplateStore(.98)
        roi = (1715,480,1910,550)
        with Image.open(folder/'team2_wanted_poll_55s.png') as running,Image.open(folder/'team2_wanted_completion_probe.png') as done:
            self.assertTrue(store.match(running,hs.ASSETS/'wanted_task_prefix_live.bmp',roi,.98)[0])
            self.assertFalse(store.match(done,hs.ASSETS/'wanted_task_prefix_live.bmp',roi,.98)[0])

    def test_pending_wanted_stops_long_combat_without_reopening_or_restarting_chain(self):
        account = hs.Account(1,'verified-role',180)
        team = hs.Team(1,('verified-role','member'))
        s = object.__new__(hs.Scheduler)
        s.active = account
        s.state = Mock()
        s.state.action_status.return_value = 'pending'
        s._team_activity_terminal = Mock(return_value=False)
        s._team_activity_status = Mock(return_value='pending')
        s._team_members_auto_complete = Mock(return_value=True)
        s._ensure_team_members_auto = Mock()
        s.prepare_main = Mock()
        s.click_template = Mock()
        s.event = Mock()
        s.save_raw_evidence = Mock(return_value='wanted_stalled.png')
        s.check_stop = Mock()
        battle_frame = Image.new('RGB',(1920,1032))
        battle_frame.info['battle'] = True
        frames = [battle_frame]*405
        s.offline_dialog_visible = Mock(return_value=False)
        s.capture = Mock(side_effect=frames)
        s._combat_active_visible = lambda kind,frame: frame.info['battle']
        s._team_result_template = lambda frame: None
        s._team_reward_result_visible = lambda frame: False
        s.template = lambda *args,**kw: (False,0.,0,0)
        s.main_ready = lambda frame: not frame.info['battle']
        s.verify_identity = Mock(return_value=(True,{}))
        s.commit_action = Mock()
        s._set_team_activity_status = Mock()
        now = [0.]
        s.wait_interruptible = lambda seconds: now.__setitem__(0,now[0]+seconds)
        with patch.object(hs.time,'monotonic',side_effect=lambda:now[0]),patch.object(hs,'wanted_task_list_is_empty',return_value=True):
            with self.assertRaises(hs.BattleStalled):
                s.run_verified_wanted(team,account)
        self.assertEqual(now[0],30)
        s.prepare_main.assert_not_called()
        s.click_template.assert_not_called()
        s.commit_action.assert_not_called()
        s.verify_identity.assert_not_called()

    def test_wanted_empty_gate_accepts_live_panel_over_textured_map(self) -> None:
        frame = Image.open(hs.LOGS / "wanted_live_20260910_2246.png")
        self.assertTrue(hs.wanted_task_list_is_empty(frame))

    def test_wanted_empty_gate_ignores_map_edge_density_after_task_row_disappears(self) -> None:
        frame = np.zeros((1032, 1920, 3), dtype=np.uint8)
        yy, xx = np.indices((110, 195))
        texture = ((xx + yy) % 2 * 80 + 40).astype(np.uint8)
        frame[480:590, 1715:1910, 0] = texture
        frame[480:590, 1715:1910, 1] = texture
        frame[480:590, 1715:1910, 2] = texture
        self.assertTrue(hs.wanted_task_list_is_empty(Image.fromarray(frame)))

    def test_multiplayer_wanted_requires_stable_disappearance_before_done(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_verified_wanted", 1)[1].split("def run_verified_wanted_and_dissolve", 1)[0]
        self.assertIn("empty_stable >= 5", body)
        self.assertIn('"wanted_task_row_disappeared"', body)
        self.assertIn("self.main_ready(frame) and wanted_task_list_is_empty(frame)", body)

    def test_wanted_row_search_covers_current_daily_list_position(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        wanted = source.split("def run_verified_wanted", 1)[1].split("def run_verified_wanted_and_dissolve", 1)[0]
        self.assertIn('"daily_wanted_row.bmp", (451, 300, 1101, 800)', wanted)

    def test_team2_verified_profile_assets_are_complete(self) -> None:
        config = legacy_config()
        names = hs.parse_csv(config["Team2FriendRowTemplates"])
        names.extend((config["Team2InviteLeaderTemplate"], config["Team2ThreeMemberTemplate"]))
        self.assertTrue(all((hs.ASSETS / name).exists() for name in names))

    def test_team_preflight_precedes_create_and_has_no_placeholder(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_verified_team_daily", 1)[1].split("def run_team_selection", 1)[0]
        self.assertLess(body.index("self.ensure_assets(required)"), body.index("self.create_verified_team"))
        selection = source.split("def run_team_selection", 1)[1].split("def run_team", 1)[0]
        self.assertNotIn("awaits first positive replay", selection)

    def test_team_activity_assets_are_gated_per_activity_after_team_creation(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_verified_team_daily", 1)[1].split("def run_team_selection", 1)[0]
        loop = body.split("for spec in active_specs:", 1)[1]
        self.assertIn("self.ensure_assets(self._team_assets_for_spec(spec))", loop)
        self.assertIn('"team_activity_pending_missing_assets"', loop)
        self.assertIn("队伍保持不解散", loop)
        common = source.split("def _team_activity_required_assets", 1)[1].split("def _team_assets_for_spec", 1)[0]
        self.assertNotIn('"strong_approach_entry.bmp"', common)

    def test_team_rebuild_resets_only_membership_ledger_after_positive_loss(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        rebuild = source.split("def _rebuild_team_after_membership_loss", 1)[1].split("def run_verified_group_instance", 1)[0]
        self.assertIn("team_dissolve_button.bmp", rebuild)
        self.assertIn("team_rebuild_after_membership_loss", rebuild)
        self.assertIn("旧队伍成员缺失，允许重新邀请", rebuild)
        daily = source.split("def run_verified_team_daily", 1)[1].split("def run_team_selection", 1)[0]
        self.assertIn("self._rebuild_team_after_membership_loss(team, leader, invitees)", daily)
        self.assertLess(daily.index("self._rebuild_team_after_membership_loss"), daily.index("self.invite_verified_member"))
        self.assertIn("解散按钮仍可见，确认上次点击未生效，允许一次重试", rebuild)
        self.assertIn('wait_absent("team_dissolve_button.bmp"', rebuild)
        self.assertIn('wait_absent("team_room_close.bmp"', rebuild)

    def test_known_recovery_closes_only_verified_member_reduction_notice(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        recovery = source.split("def recover_known_page", 1)[1].split("def enroll_identities", 1)[0]
        self.assertIn('"team_member_reduced_confirm.bmp"', recovery)
        self.assertIn("team_member_reduced_notice_closed", recovery)

    def test_wanted_catalogue_modal_recovery_uses_its_task_card_and_close_x(self) -> None:
        screenshot_path = (
            hs.LOGS
            / "scheduler_evidence"
            / "2026-08-31"
            / "account_c9ba2069ff"
            / "144028_161992_pending_team2_after_wanted_before_instance_retry.png"
        )
        if not screenshot_path.exists():
            self.skipTest("wanted catalogue replay evidence is unavailable")
        screenshot = Image.open(screenshot_path).convert("RGB")
        store = hs.TemplateStore(0.90)
        self.assertTrue(store.match(screenshot, hs.ASSETS / "wanted_claim_task.bmp", (600, 650, 1350, 780))[0])
        self.assertFalse(store.match(screenshot, hs.ASSETS / "daily_practice_close.bmp", (1300, 280, 1400, 390))[0])
        self.assertTrue(store.match(screenshot, hs.ASSETS / "daily_practice_close.bmp", (1300, 280, 1400, 390), 0.86)[0])
        source = Path(hs.__file__).read_text(encoding="utf-8")
        recovery = source.split("def recover_known_page", 1)[1].split("def enroll_identities", 1)[0]
        self.assertIn('"wanted_claim_task.bmp"', recovery)
        self.assertIn('"recover_close_wanted_task_catalogue"', recovery)
        self.assertIn("threshold=0.86", recovery)

    def test_team_battle_start_replay_enables_auto_for_every_member(self) -> None:
        screenshot_path = (
            hs.LOGS
            / "scheduler_evidence"
            / "2026-08-31"
            / "account_c9ba2069ff"
            / "144529_921484_pending_team2_leader_page_anchor_pending.png"
        )
        if not screenshot_path.exists():
            self.skipTest("team battle-start replay evidence is unavailable")
        screenshot = Image.open(screenshot_path).convert("RGB")
        store = hs.TemplateStore(0.90)
        matched, score, x, y = store.match(
            screenshot,
            hs.ASSETS / "team_battle_start_current.bmp",
            hs.TEAM_BATTLE_START_ROI,
            0.90,
        )
        self.assertTrue(matched)
        self.assertGreaterEqual(score, 0.90)
        self.assertEqual((897, 598), (x, y))
        source = Path(hs.__file__).read_text(encoding="utf-8")
        helper = source.split("def _resume_team_instance_battle", 1)[1].split("def _team_activity_visible", 1)[0]
        self.assertIn("TEAM_BATTLE_START_TEMPLATE", helper)
        self.assertIn("_ensure_team_members_auto", helper)
        self.assertIn("_team_members_auto_complete", helper)
        self.assertIn('f"{base}_battle_start"', helper)

    def test_group_instance_requires_daily_reward_zero_and_distinct_battle_start(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_verified_group_instance", 1)[1].split("def run_verified_wanted", 1)[0]
        self.assertIn("_read_team_reward_remaining", body)
        self.assertIn("remaining == 0", body)
        self.assertIn("_finish_team_activity_at_zero", body)
        self.assertIn("GROUP_INSTANCE_DAILY_REWARD_MAX = 5", source)
        self.assertNotIn("hard_instance_once", body)
        resume_call = body.split("self._resume_team_instance_battle(", 1)[1].split(")", 1)[0]
        self.assertNotIn("start_action=action", resume_call)

    def test_live_reward_counter_templates_match_only_their_enrolled_digits(self) -> None:
        group_frame = Image.open(
            hs.LOGS
            / "scheduler_evidence"
            / "2026-08-31"
            / "account_c9ba2069ff"
            / "153003_870919_before_team2_hard_instance_once.jpg"
        )
        strong_frame = Image.open(hs.LOGS / "live_team2_strong_page_20260831.png")
        store = hs.TemplateStore(0.90)
        self.assertTrue(store.match(group_frame, hs.ASSETS / "group_instance_reward_digit_5.bmp", hs.GROUP_INSTANCE_DAILY_REWARD_ROI, 0.88)[0])
        self.assertFalse(store.match(group_frame, hs.ASSETS / "group_instance_reward_digit_0.bmp", hs.GROUP_INSTANCE_DAILY_REWARD_ROI, 0.88)[0])
        self.assertTrue(store.match(strong_frame, hs.ASSETS / "strong_approach_reward_digit_6.bmp", hs.STRONG_APPROACH_WEEKLY_REWARD_ROI, 0.88)[0])

    def test_strong_approach_runs_weekly_seven_until_reward_zero(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_verified_strong_approach", 1)[1].split("def run_generic_team_activity", 1)[0]
        self.assertIn("STRONG_APPROACH_WEEKLY_REWARD_MAX = 7", source)
        self.assertIn("_read_team_reward_remaining", body)
        self.assertIn("remaining == 0", body)
        self.assertIn("_finish_team_activity_at_zero", body)
        self.assertIn("_poll_generic_team_activity", body)

    def test_selected_group_and_strong_counts_stop_at_verified_home_boundary(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        strong = source.split("def run_verified_strong_approach", 1)[1].split("def run_generic_team_activity", 1)[0]
        group = source.split("def run_verified_group_instance", 1)[1].split("def run_verified_wanted", 1)[0]
        for body in (group, strong):
            self.assertIn("first_attempt + target_count", body)
            self.assertIn("attempt_index >= selected_stop_attempt", body)
            self.assertIn('"team_selected_activity_target_complete"', body)
            self.assertIn("home_returned=True", body)
        self.assertIn("wait_until_main(8)", group)
        self.assertIn("_close_team_activity_page(leader, spec)", strong)

    def test_repeated_team_battles_verify_cancel_on_every_member_tab(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        helper = source.split("def _ensure_team_members_auto", 1)[1].split("def _team_members_auto_complete", 1)[0]
        self.assertIn("for member_name in team.members", helper)
        self.assertIn("self.switch_account(member)", helper)
        self.assertIn("self._battle_auto_on_visible(frame)", helper)
        self.assertIn('display="取消"', helper)
        self.assertIn('"battle_auto_off.bmp"', helper)

    def test_team_battle_start_is_a_main_page_overlay_and_team_asset_gate(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        main = source.split("def main_ready", 1)[1].split("def _activity_notice_close_match", 1)[0]
        required = source.split("def _team_activity_required_assets", 1)[1].split("def _team_assets_for_spec", 1)[0]
        self.assertIn("TEAM_BATTLE_START_TEMPLATE", main)
        self.assertIn("TEAM_BATTLE_START_TEMPLATE", required)
        self.assertIn("def _resume_team_instance_battle", source)
        self.assertIn("self._resume_team_instance_battle(team, leader", source)

    def test_identity_rebind_preserves_mapping_for_team_room_pages(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        rebind = source.split("def rebind_tabs_by_identity", 1)[1].split("def recognize_two_passes", 1)[0]
        self.assertIn('"team_room_close.bmp"', rebind)
        self.assertIn('mode="team_room"', rebind)
        self.assertIn('mapping_source="last_verified_tab_map"', rebind)

    def test_identity_rebind_can_use_one_trusted_white_tab_refresh(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        rebind = source.split("def rebind_tabs_by_identity", 1)[1].split("def recognize_two_passes", 1)[0]
        self.assertIn('self.config.get("AutoRefreshWhiteTab", "0") == "1"', rebind)
        self.assertIn('self.recover_white_tab(expected)', rebind)
        self.assertIn('identity_rebind_white_tab_refresh', rebind)
        self.assertIn("offline_after_refresh.png", rebind)

    def test_team_command_rebinds_only_its_configured_scope_after_complete_map(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        rebind = source.split("def rebind_tabs_by_identity", 1)[1].split("def recognize_two_passes", 1)[0]
        execute = source.split("def execute_command", 1)[1].split("def run", 1)[0]
        self.assertIn("scope_accounts: Iterable[str] | None = None", rebind)
        self.assertIn("scoped identity rebind requires a complete previously verified tab map", rebind)
        self.assertIn("Account(configured.index, configured.name, tab_x)", rebind)
        self.assertIn("self.rebind_tabs_by_identity(team.members)", execute)
        self.assertIn("unrelated white tab", execute)

    def test_content_guard_allows_only_mapped_team_room_cleanup(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        guard = source.split("def content_guard", 1)[1].split("def save_evidence", 1)[0]
        self.assertIn('"team_room_close.bmp"', guard)
        self.assertIn("not anchors", guard)
        self.assertIn("caller", guard)

    def test_team_invite_accept_precedes_notification_cleanup(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        invite = source.split("def _invite_verified_member_once", 1)[1].split("def verify_three_member_team", 1)[0]
        self.assertLess(invite.index('member_frame = self.capture()'), invite.index("self.clear_transient_notifications(member, member_frame)"))
        self.assertIn('team_invite_accept_yes.bmp', invite)

    def test_team_invite_wait_is_bounded_and_saves_missing_dialog_evidence(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        invite = source.split("def _invite_verified_member_once", 1)[1].split("def verify_three_member_team", 1)[0]
        self.assertIn('TeamInviteWaitSeconds', invite)
        self.assertIn('min(max(float', invite)
        self.assertIn('team_invite_not_visible_after_wait', invite)
        self.assertIn('invite_not_visible_after_wait', invite)

    def test_team_create_selects_group_instance_before_irreversible_confirm(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        create = source.split("def create_verified_team", 1)[1].split("def invite_verified_member", 1)[0]
        selector = source.split("def select_team_create_type", 1)[1].split("def create_verified_team", 1)[0]
        self.assertIn("self.select_team_create_type(team, leader)", create)
        self.assertLess(create.index("self.select_team_create_type(team, leader)"), create.index("irreversible=True"))
        for asset in ("team_create_type_dropdown.bmp", "team_create_group_option.bmp", "team_create_group_type.bmp"):
            self.assertIn(asset, selector)
        self.assertIn("team_create_group_type.bmp", selector)
        self.assertIn("team-create 组队副本 selection was not verified", selector)

    def test_team_group_type_assets_match_live_create_and_room_replays(self) -> None:
        create = Image.open(hs.LOGS / "scheduler_evidence" / "2026-08-28" / "account_c9ba2069ff" / "014955_579212_before_team2_create.jpg")
        dropdown = Image.open(hs.LOGS / "current_team_type_dropdown_20260828.png")
        selected = Image.open(hs.LOGS / "current_team_type_group_selected_20260828.png")
        store = hs.TemplateStore(0.90)
        self.assertTrue(store.match(create, hs.ASSETS / "team_create_type_dropdown.bmp", (850, 480, 1120, 560), 0.90)[0])
        self.assertTrue(store.match(dropdown, hs.ASSETS / "team_create_group_option.bmp", (850, 800, 1250, 940), 0.90)[0])
        self.assertTrue(store.match(selected, hs.ASSETS / "team_create_group_type.bmp", (900, 740, 1250, 830), 0.90)[0])
        self.assertTrue(store.match(dropdown, hs.ASSETS / "team_room_type_dropdown.bmp", (1000, 740, 1250, 830), 0.90)[0])
        self.assertTrue(store.match(selected, hs.ASSETS / "team_room_group_type.bmp", (1000, 740, 1250, 830), 0.90)[0])

    def test_existing_team_type_is_reconciled_before_group_instance(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        selector = source.split("def ensure_team_room_type", 1)[1].split("def create_verified_team", 1)[0]
        workflow = source.split("def run_verified_team_daily", 1)[1].split("def run_team_selection", 1)[0]
        for asset in ("team_room_type_dropdown.bmp", "team_create_group_option.bmp", "team_room_group_type.bmp"):
            self.assertIn(asset, selector)
        self.assertIn("team{team.index}_select_existing_group_instance", selector)
        self.assertIn("team-room X missing after type reconciliation", selector)
        self.assertLess(workflow.index("ensure_team_room_type"), workflow.index("run_verified_group_instance"))

    def test_missing_team_member_template_is_enrollable_after_team_creation(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        workflow = source.split("def run_verified_team_daily", 1)[1].split("def run_team_selection", 1)[0]
        self.assertIn("capture_verified_three_member_template", workflow)
        self.assertLess(workflow.index("self.create_verified_team"), workflow.index("capture_verified_three_member_template"))
        self.assertLess(workflow.index("capture_verified_three_member_template"), workflow.index("self.verify_three_member_team"))
        required = workflow.split("required = [", 1)[1].split("]\n        try:", 1)[0]
        self.assertNotIn("three_member_template, *row_templates", required)

    def test_team_room_identity_assignment_replays_three_member(self) -> None:
        config = legacy_config()
        scheduler = hs.Scheduler(config, "test", False, True)
        team = hs.Team(2, tuple(hs.parse_csv(config["Team2"])))
        leader = scheduler.by_name[config["Team2VerifiedLeader"]]
        invitees = [scheduler.by_name[name] for name in team.members if name != leader.name]
        frame = Image.open(hs.LOGS / "current_team_room_after_team2_20260828.png")
        result = scheduler._team_room_identity_assignment(frame, [leader, *invitees])
        self.assertIsNotNone(result)
        assignment, origin = result
        self.assertEqual(set(team.members), {account.name for account in assignment})
        self.assertLessEqual(abs(origin[0] - hs.TEAM_ROOM_MEMBER_ORIGIN[0]), 4)
        self.assertLessEqual(abs(origin[1] - hs.TEAM_ROOM_MEMBER_ORIGIN[1]), 4)

    def test_team_room_identity_assignment_rejects_wrong_three_members(self) -> None:
        config = legacy_config()
        scheduler = hs.Scheduler(config, "test", False, True)
        team = hs.Team(1, tuple(hs.parse_csv(config["Team1"])))
        leader = scheduler.by_name[config["Team1VerifiedLeader"]]
        invitees = [scheduler.by_name[name] for name in team.members if name != leader.name]
        frame = Image.open(hs.LOGS / "current_team_room_after_team2_20260828.png")
        self.assertIsNone(scheduler._team_room_identity_assignment(frame, [leader, *invitees]))

    def test_completed_team_invite_is_never_sent_again(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def invite_verified_member", 1)[1].split("def verify_three_member_team", 1)[0]
        done_branch = body.split('if invite_status == "done":', 1)[1].split("else:", 1)[0]
        send_branch = body.split("else:", 1)[1]
        self.assertIn("invite_already_sent", done_branch)
        self.assertNotIn("friend_invite_team.bmp", done_branch)
        self.assertIn("friend_invite_team.bmp", send_branch)
        self.assertIn("prior invitation is not repeated", body)

    def test_team_friend_toolbar_allows_observed_render_drift_only_in_bounded_roi(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def _invite_verified_member_once", 1)[1].split("def verify_three_member_team", 1)[0]
        self.assertIn('"friends_toolbar.bmp"', body)
        self.assertIn("threshold=0.88", body)
        self.assertIn("(1715, 70, 1770, 120)", body)
        evidence = hs.LOGS / "current_screen_team1_retry_20260828.png"
        if evidence.exists():
            frame = Image.open(evidence)
            store = hs.TemplateStore(0.90)
            score = store.match(frame, hs.ASSETS / "friends_toolbar.bmp", (1715, 70, 1770, 120))[1]
            self.assertGreaterEqual(score, 0.88)
            self.assertLess(score, 0.90)

    def test_team_friend_row_waits_for_async_list_paint(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def _invite_verified_member_once", 1)[1].split("def verify_three_member_team", 1)[0]
        self.assertIn('self.wait_template(row_template, (1715, 320, 1920, 900), 4)', body)

    def test_team_friend_row_failure_closes_panel_and_prepare_main_recovers_it(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        invite = source.split("def _invite_verified_member_once", 1)[1].split("def verify_three_member_team", 1)[0]
        prepare = source.split("def prepare_main", 1)[1].split("def run_module", 1)[0]
        self.assertIn("team{team.index}_close_friends_missing_row", invite)
        self.assertIn('"friends_panel_close_recover"', prepare)
        self.assertIn('self.wait_absent("friends_panel_close.bmp"', prepare)

    def test_missing_team_member_probe_keeps_raw_evidence(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def capture_verified_three_member_template", 1)[1].split("def create_verified_team", 1)[0]
        self.assertIn('save_raw_evidence(frame, leader, f"team{team.index}_three_member_probe", "probe")', body)
        self.assertIn('evidence=str(evidence)', body)

    def test_ncc_finds_exact_template(self) -> None:
        image = np.zeros((40, 60), dtype=np.float64)
        template = np.arange(63, dtype=np.float64).reshape(7, 9)
        image[13:20, 21:30] = template
        score, x, y = hs.ncc(image, template)
        self.assertGreater(score, 0.999)
        self.assertEqual((21, 13), (x, y))

    def test_template_find_all_keeps_repeated_digits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template_path = Path(temp_dir) / "digit.bmp"
            tile = np.array([[0, 255, 0], [255, 0, 255], [0, 255, 0]], dtype=np.uint8)
            frame = np.zeros((30, 50), dtype=np.uint8)
            frame[5:8, 10:13] = tile
            frame[5:8, 24:27] = tile
            Image.fromarray(tile).save(template_path)
            store = hs.TemplateStore(0.99)
            matches = store.find_all(Image.fromarray(frame), template_path, (0, 0, 50, 30))
            self.assertEqual([10, 24], sorted(x for _score, x, _y in matches))

    def test_managed_edge_signature_survives_map_change(self) -> None:
        active = Image.open(Path(r"C:\Users\Administrator\AppData\Local\Temp\current_live.png"))
        enrolled = Image.open(Path(r"C:\Users\Administrator\AppData\Local\Temp\managed_after_start.png"))
        normal = Image.open(hs.ROOT / "logs" / "account_tab_2.png")
        ref = Image.open(hs.ASSETS / "managed_active.bmp")
        active_crop = active.crop((895, 145, 980, 185))
        enrolled_crop = enrolled.crop((895, 145, 980, 185))
        normal_crop = normal.crop((895, 145, 980, 185))
        self.assertGreaterEqual(hs.edge_f1(ref, active_crop), 0.70)
        self.assertGreaterEqual(hs.edge_f1(ref, enrolled_crop), 0.70)
        self.assertLess(hs.edge_f1(ref, normal_crop), 0.20)

    def test_managed_completion_requires_ten_consecutive_samples_even_when_already_active(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_managed_gate", 1)[1].split("def exit_managed_for_market", 1)[0]
        self.assertIn("for sample in range(10)", body)
        self.assertIn("managed active was positively reconciled across ten consecutive samples", body)
        self.assertIn("managed active recognition was not stable for ten consecutive samples; no start click made", body)
        self.assertNotIn("across three samples", body)

    def test_identity_scoring_separates_similar_server_labels(self) -> None:
        scheduler = hs.Scheduler(legacy_config(), "test", False, True)
        one = Image.open(hs.ROOT / "logs" / "scheduler_evidence" / "2026-08-25" / "current_tabs" / "tab_1_180.png")
        two = Image.open(hs.ROOT / "logs" / "scheduler_evidence" / "2026-08-25" / "current_tabs" / "tab_2_310.png")
        one_scores = scheduler.identity_scores(one)
        two_scores = scheduler.identity_scores(two)
        self.assertGreater(one_scores[ROLE_A], one_scores[ROLE_C] + 0.07)
        self.assertGreater(two_scores[ROLE_C], two_scores[ROLE_A] + 0.07)

    def test_managed_exit_opens_panel_before_exit_control(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def exit_managed_for_market", 1)[1].split("def run_full", 1)[0]
        self.assertLess(body.index('self.ensure_assets(["managed_mode_button.bmp"])'), body.index('self.wait_template("managed_exit.bmp", exit_roi, 6)'))
        self.assertIn('self.wait_template("managed_exit.bmp", exit_roi, 6)', body)
        self.assertIn('"managed_panel_for_exit"', body)

    def test_prepare_main_closes_verified_team_room_without_dissolving(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def prepare_main", 1)[1].split("def run_module", 1)[0]
        self.assertIn('self.template("team_room_close.bmp", (1200, 260, 1280, 340), frame)', body)
        self.assertIn('"team_room_close_recover"', body)
        self.assertIn('self.wait_absent("team_room_close.bmp", (1200, 260, 1280, 340), 5)', body)
        self.assertLess(body.index('"team_room_close_recover"'), body.index('raise PendingError("main anchors missing and no known popup is present")'))
        self.assertNotIn("dissolve_team_after_wanted", body)

    def test_market_purchase_preflights_before_slot_loop(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_market_read_only", 1)[1].split("def managed_active_score", 1)[0]
        slot_body = source.split("def process_market_slot", 1)[1].split("def run_market_read_only", 1)[0]
        self.assertLess(body.index("if purchase:\n            # Fail closed"), body.index("for slot_index, slot in enumerate(self.market_slot_rois()"))
        self.assertIn("self.market_purchase_prerequisites()", body)
        self.assertIn('"item": "体力药水（小）"', slot_body)
        self.assertIn('"currency": "coupon"', slot_body)

    def test_market_navigation_has_verified_npc_fallback_for_expired_notice(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_market_read_only", 1)[1].split("def managed_active_score", 1)[0]
        self.assertIn('self.ensure_assets(["market_npc_label.bmp"])', body)
        self.assertIn('npc_roi = MARKET_NPC_ROI', body)
        self.assertIn('MARKET_NPC_ROI = (200, 100, 1919, 600)', source)
        self.assertIn('self.wait_template("market_npc_label.bmp", npc_roi, 8, threshold=0.60)', body)
        self.assertIn('self.template("market_npc_label.bmp", npc_roi, frame, threshold=0.60)', body)
        self.assertIn('anchor_threshold=0.60', body)
        self.assertIn('"black_market_npc_entry"', body)
        self.assertIn('"market_npc_label.bmp"]', body)
        self.assertIn('raise PendingError("black-market notification and NPC entry are both unavailable")', body)

    def test_live_exchange_store_notification_is_separately_classified(self) -> None:
        config = legacy_config()
        scheduler = hs.Scheduler(config, "test", False, True)
        positives = (
            hs.LOGS / "current_after_stop_20260829.png",
            hs.LOGS / "imagegrab_x310_20260829.png",
            hs.LOGS / "imagegrab_x590_20260829.png",
        )
        for path in positives:
            self.assertTrue(path.exists(), path)
            result = scheduler.find_exchange_store_notification(Image.open(path).convert("RGB"))
            self.assertIsNotNone(result, path)
            assert result is not None
            self.assertEqual("market_entry_icon_current.bmp", result["anchor"])
            self.assertEqual("market_entry_view_current.bmp", result["view"])
            self.assertGreaterEqual(float(result["anchor_score"]), 0.88)
            self.assertGreaterEqual(float(result["view_score"]), 0.88)
            self.assertLessEqual(abs(int(result["view_x"]) - int(result["anchor_x"]) - 8), 55)
            self.assertTrue(90 <= int(result["view_y"]) - int(result["anchor_y"]) <= 145)
        negative = hs.LOGS / "current_live_20260828_0134.png"
        if negative.exists():
            self.assertIsNone(scheduler.find_exchange_store_notification(Image.open(negative).convert("RGB")))

    def test_live_exchange_store_dialog_replays_first_option_only(self) -> None:
        config = legacy_config()
        scheduler = hs.Scheduler(config, "test", False, True)
        for path in (
            hs.LOGS / "current_dialog_after_probe_20260829.png",
            hs.LOGS / "market_probe_after_view_20260829.png",
        ):
            self.assertTrue(path.exists(), path)
            result = scheduler.find_exchange_store_dialog_view(Image.open(path).convert("RGB"))
            self.assertIsNotNone(result, path)
            assert result is not None
            self.assertEqual("market_dialog_close_current.bmp", result["close"])
            self.assertEqual("market_dialog_view_option_current.bmp", result["option"])
            self.assertGreaterEqual(float(result["close_score"]), 0.88)
            self.assertGreaterEqual(float(result["option_score"]), 0.88)
            self.assertTrue(int(result["close_x"]) - 500 <= int(result["option_x"]) <= int(result["close_x"]) - 200)
            self.assertTrue(int(result["close_y"]) + 100 <= int(result["option_y"]) <= int(result["close_y"]) + 210)
        negative = hs.LOGS / "current_live_20260828_0134.png"
        if negative.exists():
            self.assertIsNone(scheduler.find_exchange_store_dialog_view(Image.open(negative).convert("RGB")))

    def test_exchange_store_probe_is_not_used_by_coupon_market_flow(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_market_read_only", 1)[1].split("def managed_active_score", 1)[0]
        self.assertNotIn("find_exchange_store_notification", body)
        self.assertNotIn("market_entry_icon_current.bmp", body)
        self.assertNotIn("market_dialog_view_option_current.bmp", body)
        self.assertIn('"market_anchor.bmp", (651, 69, 1251, 319), 90', body)

    def test_market_notification_source_keeps_two_positive_gates_bounded(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def find_exchange_store_notification", 1)[1].split("def record_market_offer", 1)[0]
        self.assertIn('"market_entry_icon_current.bmp"', body)
        self.assertIn('"market_entry_view_current.bmp"', body)
        self.assertIn("button_roi = (", body)
        self.assertIn("abs(button_x - (icon_x + 8)) <= 55", body)
        self.assertIn("organization-points shop", body)
        flow = source.split("def run_market_read_only", 1)[1].split("def managed_active_score", 1)[0]
        self.assertNotIn("find_exchange_store_notification", flow)
        self.assertNotIn('"market_notification_match"', flow)

    def test_market_npc_path_does_not_click_leaf_map_after_npc_entry(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_market_read_only", 1)[1].split("def managed_active_score", 1)[0]
        npc_branch = body.split("if npc_ok:", 1)[1].split("elif not self.template", 1)[0]
        self.assertNotIn("leaf_map_south", npc_branch)
        self.assertIn("if not npc_ok:", body)

    def test_market_read_only_probe_does_not_hide_later_purchase(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_market_read_only", 1)[1].split("def managed_active_score", 1)[0]
        self.assertIn("if not purchase or detail == \"coupon-only offers settled\":", body)
        self.assertIn("A read-only probe is useful evidence", body)

    def test_market_offer_ledger_contains_required_quote_and_delta_fields(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def record_market_offer", 1)[1].split("def process_market_slot", 1)[0]
        for field in ("date", "cycle", "slot", "item", "currency", "price", "coupon_before", "coupon_after", "ingot_before", "ingot_after", "status", "evidence"):
            self.assertIn(f'"{field}"', body)
        slot_body = source.split("def process_market_slot", 1)[1].split("def run_market_read_only", 1)[0]
        self.assertIn('"status": "purchased"', slot_body)
        self.assertIn('"quote_evidence":', slot_body)

    def test_market_balance_roi_covers_full_coupon_and_ingot_numbers(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        self.assertIn("roi = (220, 154, 300, 184) if coupon else (301, 154, 380, 184)", source)
        self.assertNotIn("(312, 154, 380, 184)", source)

    def test_market_balance_digit_reader_uses_live_variants_and_lower_edge_cutoff(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def read_template_digits", 1)[1].split("def edge_binary", 1)[0]
        self.assertIn('asset_dir.glob(f"{prefix}{digit}_*.bmp")', body)
        self.assertIn('edge_threshold = 30 if prefix == "market_balance_digit_" else 70', body)

    def test_live_market_quote_balance_and_settlement_replay(self) -> None:
        before_path = hs.LOGS / "market_live_modal_before_trial_20260828.png"
        confirm_path = hs.LOGS / "market_live_after_buy_trial_20260828.png"
        after_path = hs.LOGS / "market_live_after_confirm_trial_20260828_3.png"
        for path in (before_path, confirm_path, after_path):
            self.assertTrue(path.exists(), path)
        before = Image.open(before_path)
        confirm = Image.open(confirm_path)
        after = Image.open(after_path)
        store = hs.TemplateStore(0.65)
        self.assertTrue(store.match(before, hs.ASSETS / "market_anchor.bmp", (651, 69, 1251, 319), 0.90)[0])
        self.assertTrue(store.match(before, hs.ASSETS / "market_stamina_potion.bmp", (621, 369, 886, 454), 0.90)[0])
        self.assertTrue(store.match(before, hs.ASSETS / "market_coupon_icon.bmp", (621, 369, 886, 544), 0.90)[0])
        self.assertEqual(32, hs.read_template_digits(before, store, hs.ASSETS, "market_price_digit_", (758, 438, 796, 465), 0.65, 1, 5))
        self.assertEqual(4678, hs.read_template_digits(before, store, hs.ASSETS, "market_balance_digit_", (220, 154, 300, 184), 0.65, 1, 8))
        self.assertEqual(110, hs.read_template_digits(before, store, hs.ASSETS, "market_balance_digit_", (301, 154, 380, 184), 0.65, 1, 8))
        self.assertTrue(store.match(confirm, hs.ASSETS / "market_purchase_confirm_coupon.bmp", (551, 319, 1301, 869), 0.78)[0])
        self.assertTrue(store.match(after, hs.ASSETS / "market_sold_out.bmp", (621, 369, 886, 544), 0.90)[0])
        self.assertFalse(store.match(before, hs.ASSETS / "market_sold_out.bmp", (621, 369, 886, 544), 0.90)[0])
        self.assertEqual(4646, hs.read_template_digits(after, store, hs.ASSETS, "market_balance_digit_", (220, 154, 300, 184), 0.65, 1, 8))
        self.assertEqual(110, hs.read_template_digits(after, store, hs.ASSETS, "market_balance_digit_", (301, 154, 380, 184), 0.65, 1, 8))

    def test_live_market_balance_variants_replay_for_other_accounts(self) -> None:
        store = hs.TemplateStore(0.65)
        cases = (
            ("account_72cd26215f/011543_799877_raw_market_2300.png", 25740, 20),
            ("account_a802d1815a/011557_096506_raw_market_2300.png", 44612, 2),
        )
        for relative, coupon, ingot in cases:
            frame = Image.open(hs.LOGS / "scheduler_evidence" / "2026-08-28" / relative)
            self.assertTrue(store.match(frame, hs.ASSETS / "market_anchor.bmp", (651, 69, 1251, 319), 0.90)[0])
            self.assertEqual(coupon, hs.read_template_digits(frame, store, hs.ASSETS, "market_balance_digit_", (220, 154, 300, 184), 0.65, 1, 8))
            self.assertEqual(ingot, hs.read_template_digits(frame, store, hs.ASSETS, "market_balance_digit_", (301, 154, 380, 184), 0.65, 1, 8))

    def test_live_market_npc_label_edge_position_is_inside_fallback_roi(self) -> None:
        path = hs.LOGS / "current_live_20260828_0134.png"
        self.assertTrue(path.exists(), path)
        frame = Image.open(path)
        store = hs.TemplateStore(0.90)
        ok, score, x, y = store.match(frame, hs.ASSETS / "market_npc_label.bmp", (200, 100, 1919, 800), 0.60)
        self.assertTrue(ok)
        self.assertGreaterEqual(score, 0.60)
        self.assertGreater(x, 1700)
        self.assertGreaterEqual(y, 100)

    def test_live_exchange_store_npc_label_current_variant_is_distinct(self) -> None:
        config = legacy_config()
        scheduler = hs.Scheduler(config, "test", False, True)
        for path in (
            hs.LOGS / "current_after_dialog_close_20260829.png",
            hs.LOGS / "current_dialog_after_probe_20260829.png",
        ):
            self.assertTrue(path.exists(), path)
            frame = Image.open(path).convert("RGB")
            ok, score, x, y = scheduler.template("market_npc_label_current.bmp", (200, 100, 1500, 600), frame, threshold=0.88)
            self.assertTrue(ok, (path, score, x, y))
            self.assertGreaterEqual(score, 0.88)
            self.assertGreaterEqual(x, 800)
            self.assertGreaterEqual(y, 250)
        negative = hs.LOGS / "current_live_20260828_0134.png"
        if negative.exists():
            frame = Image.open(negative).convert("RGB")
            self.assertFalse(scheduler.template("market_npc_label_current.bmp", (200, 100, 1500, 600), frame, threshold=0.88)[0])

    def test_coupon_market_flow_does_not_use_exchange_store_npc_label(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_market_read_only", 1)[1].split("def managed_active_score", 1)[0]
        self.assertNotIn('"market_npc_label_current.bmp"', body)
        self.assertNotIn("advance_exchange_store_dialog", body)

    def test_market_confirmation_uses_bounded_tolerant_threshold(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def process_market_slot", 1)[1].split("def run_market_read_only", 1)[0]
        self.assertIn("confirm_threshold = 0.78", body)
        self.assertIn("threshold=confirm_threshold", body)
        self.assertIn("coupon/ingot delta was not verified", body)

    def test_market_missing_confirmation_has_only_bounded_uncharged_retry(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        helper = source.split("def market_preconfirm_is_uncharged", 1)[1].split("def process_market_slot", 1)[0]
        slot = source.split("def process_market_slot", 1)[1].split("def run_market_read_only", 1)[0]
        self.assertIn("purchase confirmation is visible but was not positively settled", helper)
        self.assertIn("coupon or ingot balance changed after the click", helper)
        self.assertIn('"phase": "awaiting_confirm"', slot)
        self.assertIn('"phase": "awaiting_settlement"', slot)
        self.assertIn("remaining_attempts = 2 - dialog_retry_count", slot)
        self.assertIn("balance unchanged and bounded retries exhausted", slot)

    def test_white_tab_pending_keeps_lossless_evidence(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def recover_white_tab", 1)[1].split("def recover_known_page", 1)[0]
        self.assertIn('save_raw_evidence(frame, account, "white_tab_confirmed", "pending")', body)
        self.assertIn('evidence=str(evidence)', body)

    def test_market_and_all_day_commands_fail_closed_when_purchase_disabled(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        market = source.split("def run_market_all", 1)[1].split("def run_managed_all", 1)[0]
        all_day = source.split("def run_all_day_once", 1)[1].split("def run_all_day", 1)[0]
        self.assertIn("market purchase is disabled until coupon-only live verification completes", market)
        self.assertIn("action=\"read_only_navigation\"", market)
        self.assertIn("self.run_market_read_only(account, purchase=purchase)", market)
        self.assertIn("the command remains resumable and fail-closed", market)
        self.assertIn("all-day requires verified coupon-only market purchase configuration", all_day)

    def test_full_flow_reuses_gated_market_command(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        full = source.split("def _run_full_order", 1)[1].split("def run_full", 1)[0]
        self.assertIn("self.run_market_all()", full)
        complete = source.split("def command_is_complete", 1)[1].split("def _finish_command", 1)[0]
        self.assertIn("self.allow_purchase and self.market_direct_buy_verified", complete)

    def test_full_flow_stops_at_each_unresolved_module_boundary(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        full = source.split("def _run_full_order", 1)[1].split("def run_full", 1)[0]
        checks = (
            '"full flow halted after unresolved mail module"',
            '"full flow halted after unresolved welfare module"',
            '"full flow halted after unresolved Team{team_index}"',
            '"full flow halted after unresolved arena module"',
            '"full flow halted after unresolved survival module"',
            '"full flow halted after unresolved market module"',
            '"full flow halted after unresolved managed module"',
        )
        for marker in checks:
            self.assertIn(marker, full)

    def test_execute_command_dispatches_every_panel_command(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def execute_command", 1)[1].split("def run", 1)[0]
        for kind in ("recognize", "mail", "welfare", "team_index", "arena", "survival", "market", "managed", "all_day", "full"):
            self.assertIn(kind, body)
        self.assertIn("self.run_team_selection(command.team_index)", body)
        self.assertIn('self.run_accounts_module("mail", self.module_mail)', body)
        self.assertIn('self.run_accounts_module("welfare", self.module_welfare)', body)
        self.assertIn('self.run_combat_interleaved("arena")', body)
        self.assertIn('self.run_combat_interleaved("survival")', body)

    def test_market_completion_requires_verified_coupon_settlement(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        completion = source.split('if command.kind == "market":', 1)[1].split('if command.kind == "managed":', 1)[0]
        self.assertIn("not (self.allow_purchase and self.market_direct_buy_verified)", completion)
        self.assertIn('"coupon-only offers settled"', source.split("def market_cycle_settled", 1)[1].split("def managed_active_score", 1)[0])

    def test_insufficient_coupon_remains_pending_and_blocks_cycle_completion(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        slot = source.split("def process_market_slot", 1)[1].split("def run_market_read_only", 1)[0]
        self.assertIn('self.state.set_action(account.name, action, "pending", detail)', slot)
        self.assertIn('raise PendingError(detail)', slot)
        self.assertIn('"pending_insufficient_coupon"', slot)
        self.assertIn('known_no_click_pending = pending_detail.startswith("coupon insufficient for ")', slot)
        self.assertIn('status == "pending" and pending_quote is None and not known_no_click_pending', slot)
        self.assertIn("def market_cycle_settled", source)
        completion = source.split('if command.kind == "market":', 1)[1].split('if command.kind == "managed":', 1)[0]
        self.assertIn("self.market_cycle_settled(account, cycle)", completion)

    def test_unknown_market_pending_stays_isolated_when_card_changes(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        slot = source.split("def process_market_slot", 1)[1].split("def run_market_read_only", 1)[0]
        guard = slot.split('if status == "pending" and pending_quote is None and not known_no_click_pending:', 1)[1].split('if status == "done":', 1)[0]
        self.assertIn('"pending_unknown_sold_out"', guard)
        self.assertIn('"pending_unknown_offer_missing"', guard)
        self.assertIn("raise PendingError(f\"market offer {action} has unknown prior outcome; refusing duplicate\")", guard)
        self.assertIn('known_no_click_pending = pending_detail.startswith("coupon insufficient for ")', slot)
        self.assertLess(slot.index('if status == "pending" and pending_quote is None and not known_no_click_pending:'), slot.index('if status == "done":'))

    def test_market_modal_keeps_lossless_raw_evidence_for_digit_enrollment(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_market_read_only", 1)[1].split("def managed_active_score", 1)[0]
        self.assertIn("self.save_raw_evidence(frame, account, f\"market_{cycle}\", \"raw\")", body)

    def test_full_team_reconciliation_commits_missing_accepts_without_resend(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def reconcile_three_member_team", 1)[1].split("def run_verified_group_instance", 1)[0]
        self.assertIn("exact three-member room proves invitation delivered", body)
        self.assertIn("exact three-member room proves", body)
        self.assertNotIn("invite_verified_member(", body)
        workflow = source.split("def run_verified_team_daily", 1)[1].split("def run_team_selection", 1)[0]
        self.assertLess(workflow.index("reconcile_three_member_team"), workflow.index("self.verify_three_member_team"))

    def test_all_day_runs_each_refresh_for_all_accounts_and_finishes_at_2300(self) -> None:
        config = legacy_config()
        scheduler = object.__new__(hs.Scheduler)
        scheduler.allow_purchase = True
        scheduler.market_direct_buy_verified = True
        scheduler.accounts = [SimpleNamespace(name=name) for name in hs.parse_csv(config["TabAccountNames"])]
        modules: dict[tuple[str, str], str] = {}
        scheduler.state = SimpleNamespace(
            global_state={},
            module_status=lambda name, module: modules.get((name, module), "new"),
            add_pending=lambda *args, **kwargs: None,
            resolve_pending=lambda *args, **kwargs: 0,
            save_global=lambda: None,
        )
        scheduler.event = lambda *args, **kwargs: None
        scheduler.pause_after_module = threading.Event()
        calls: list[tuple[str, str, bool | None]] = []
        scheduler.exit_managed_for_market = lambda account: calls.append(("exit", account.name, None))

        current_cycle = {"value": ""}

        def market(account, purchase=False, cycle=None):
            self.assertEqual(current_cycle["value"], cycle)
            modules[(account.name, f"market_{cycle}")] = "done"
            calls.append(("market", account.name, bool(purchase)))

        def managed(account):
            modules[(account.name, "managed")] = "done"
            calls.append(("managed", account.name, None))

        scheduler.run_market_read_only = market
        scheduler.run_managed_gate = managed
        cycles = iter(("0800", "1100", "1400", "1700", "2000", "2300"))

        def next_cycle():
            current_cycle["value"] = next(cycles)
            return current_cycle["value"]

        scheduler.current_market_cycle = next_cycle
        for _ in range(5):
            self.assertFalse(scheduler.run_all_day_once())
        self.assertTrue(scheduler.run_all_day_once())
        self.assertEqual(6 * len(scheduler.accounts), sum(1 for kind, _name, _flag in calls if kind == "market"))
        self.assertTrue(all(flag for kind, _name, flag in calls if kind == "market"))
        self.assertEqual({"0800", "1100", "1400", "1700", "2000", "2300"}, set(scheduler.state.global_state["all_day_cycles"]))
        self.assertIn("all_day_completed_at", scheduler.state.global_state)

    def test_all_day_cycle_requires_current_account_success_not_stale_managed_status(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_all_day_once", 1)[1].split("def run_all_day", 1)[0]
        self.assertIn("account_success: dict[str, bool] = {}", body)
        self.assertIn("account_success[account.name] = True", body)
        self.assertIn("account_success[account.name] = False", body)
        self.assertIn("all(account_success.get(account.name, False) for account in self.accounts)", body)

    def test_all_day_failure_recovers_map_before_switching_tabs(self) -> None:
        scheduler = object.__new__(hs.Scheduler)
        scheduler.allow_purchase = True
        scheduler.market_direct_buy_verified = True
        account_a = SimpleNamespace(name="账号A")
        account_b = SimpleNamespace(name="账号B")
        scheduler.accounts = [account_a, account_b]
        scheduler.config = {"MarketRefreshTimes": "08:00,11:00,14:00,17:00,20:00,23:00"}
        scheduler.state = SimpleNamespace(
            global_state={},
            module_status=lambda _name, _module: "new",
            add_pending=lambda *args, **kwargs: None,
            resolve_pending=lambda *args, **kwargs: 0,
            save_global=lambda: None,
        )
        scheduler.event = lambda *args, **kwargs: None
        scheduler.pause_after_module = threading.Event()
        scheduler.current_market_cycle = lambda: "0800"
        scheduler.active = account_a
        seen: list[str] = []
        scheduler.exit_managed_for_market = lambda account: seen.append(f"exit:{account.name}")

        def failed_market(account, purchase=False, cycle=None):
            seen.append(f"market:{account.name}")
            raise hs.PendingError("leaf-village submap timeout")

        scheduler.run_market_read_only = failed_market
        scheduler.run_managed_gate = lambda account: seen.append(f"managed:{account.name}")
        scheduler.ensure_account_main_before_release = lambda account, module: seen.append(f"recover:{account.name}") or False
        with self.assertRaises(hs.PendingError):
            scheduler.run_all_day_once()
        self.assertEqual(["exit:账号A", "market:账号A", "recover:账号A"], seen)

    def test_recovery_source_has_verified_map_toggles_and_quarantine_gate(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        recovery = source.split("def recover_known_page", 1)[1].split("def enroll_identities", 1)[0]
        self.assertIn('"recover_close_leaf_map"', recovery)
        self.assertIn('"recover_close_world_map"', recovery)
        self.assertIn('"world_map_button.bmp"', recovery)
        all_day = source.split("def run_all_day_once", 1)[1].split("def run_all_day", 1)[0]
        self.assertIn("ensure_account_main_before_release(account, f\"all_day_{cycle}\")", all_day)
        self.assertIn('"all_day_account_quarantined"', all_day)

    def test_all_day_honors_pause_after_account_cycle(self) -> None:
        scheduler = object.__new__(hs.Scheduler)
        scheduler.allow_purchase = True
        scheduler.market_direct_buy_verified = True
        scheduler.accounts = [SimpleNamespace(name="账号A"), SimpleNamespace(name="账号B")]
        scheduler.state = SimpleNamespace(
            global_state={},
            module_status=lambda _name, _module: "new",
            add_pending=lambda *args, **kwargs: None,
            resolve_pending=lambda *args, **kwargs: 0,
            save_global=lambda: None,
        )
        scheduler.event = lambda *args, **kwargs: None
        scheduler.pause_after_module = threading.Event()
        scheduler.pause_after_module.set()
        cycle = "0800"
        scheduler.current_market_cycle = lambda: cycle
        scheduler.exit_managed_for_market = lambda _account: None
        scheduler.run_market_read_only = lambda _account, purchase=False, cycle=None: None
        scheduler.run_managed_gate = lambda _account: None
        self.assertFalse(scheduler.run_all_day_once())
        self.assertNotIn(cycle, scheduler.state.global_state.get("all_day_cycles", {}))

    def test_all_day_catches_up_earliest_unsettled_refresh_before_later_batch(self) -> None:
        scheduler = object.__new__(hs.Scheduler)
        scheduler.allow_purchase = True
        scheduler.market_direct_buy_verified = True
        scheduler.config = {"MarketRefreshTimes": "08:00,11:00,14:00,17:00,20:00,23:00"}
        scheduler.accounts = [SimpleNamespace(name="账号A"), SimpleNamespace(name="账号B")]
        modules: dict[tuple[str, str], str] = {}
        scheduler.state = SimpleNamespace(
            global_state={"all_day_cycles": {"0800": {"status": "pending"}}},
            module_status=lambda name, module: modules.get((name, module), "new"),
            add_pending=lambda *args, **kwargs: None,
            resolve_pending=lambda *args, **kwargs: 0,
            save_global=lambda: None,
        )
        scheduler.event = lambda *args, **kwargs: None
        scheduler.pause_after_module = threading.Event()
        scheduler.current_market_cycle = lambda: "1100"
        scheduler.exit_managed_for_market = lambda _account: None
        selected: list[str] = []

        def market(account, purchase=False, cycle=None):
            selected.append(cycle)
            modules[(account.name, f"market_{cycle}")] = "done"

        scheduler.run_market_read_only = market
        scheduler.run_managed_gate = lambda account: modules.__setitem__((account.name, "managed"), "done")
        self.assertFalse(scheduler.run_all_day_once())
        self.assertEqual(["0800", "0800"], selected)
        self.assertEqual("done", scheduler.state.global_state["all_day_cycles"]["0800"]["status"])

    def test_offline_login_template_is_distinct_from_live_main(self) -> None:
        template = hs.ASSETS / "offline_login_prompt.bmp"
        self.assertTrue(template.exists())
        offline = Image.open(Path(r"C:\Users\Administrator\AppData\Local\Temp\account_ae1662240e_welfare_probe.png"))
        live = Image.open(Path(r"C:\Users\Administrator\AppData\Local\Temp\current_live.png"))
        store = hs.TemplateStore(0.98)
        self.assertTrue(store.match(offline, template, (780, 450, 1140, 660), 0.98)[0])
        self.assertFalse(store.match(live, template, (780, 450, 1140, 660), 0.98)[0])

    def test_per_account_module_skips_unavailable_account(self) -> None:
        scheduler = hs.Scheduler.__new__(hs.Scheduler)
        online = hs.Account(1, "online", 180)
        offline = hs.Account(2, "offline", 310)
        scheduler.accounts = [online, offline]
        scheduler.available_account_names = {"online"}
        scheduler.check_pause_boundary = Mock(return_value=False)
        scheduler.run_module = Mock()
        scheduler.event = Mock()
        fn = Mock()
        scheduler.run_accounts_module("mail", fn)
        scheduler.run_module.assert_called_once_with(online, "mail", fn)
        scheduler.event.assert_called_once_with(
            "module_unavailable_accounts_skipped",
            module="mail",
            accounts=["offline"],
            eligible=["online"],
        )

    def test_completion_scope_excludes_unavailable_account(self) -> None:
        scheduler = hs.Scheduler.__new__(hs.Scheduler)
        online = hs.Account(1, "online", 180)
        offline = hs.Account(2, "offline", 310)
        scheduler.accounts = [online, offline]
        scheduler.available_account_names = {"online"}
        scheduler._account_module_complete = Mock(side_effect=lambda account, module: account.name == "online")
        self.assertTrue(scheduler._accounts_module_complete("mail"))
        scheduler._account_module_complete.assert_called_once_with(online, "mail")

    def test_current_offline_login_skin_is_detected_without_click(self) -> None:
        screenshot = hs.LOGS / "scheduler_evidence" / "2026-09-19" / "identity_rebind" / "tab_2_x310_unknown.png"
        if not screenshot.exists():
            self.skipTest("current offline evidence is unavailable")
        scheduler = hs.Scheduler.__new__(hs.Scheduler)
        scheduler.templates = hs.TemplateStore(0.90)
        scheduler.detector = None
        scheduler.capture = Mock(side_effect=AssertionError("provided frame must be reused"))
        result = scheduler.template(
            "offline_login_prompt.bmp",
            (780, 450, 1140, 660),
            Image.open(screenshot).convert("RGB"),
            threshold=0.98,
        )
        self.assertTrue(result[0])
        live = screenshot.with_name("tab_1_x180.png")
        self.assertFalse(scheduler.template(
            "offline_login_prompt.bmp",
            (780, 450, 1140, 660),
            Image.open(live).convert("RGB"),
            threshold=0.98,
        )[0])
        scheduler.capture.assert_not_called()

    def test_october_offline_prompt_text_anchor_blocks_main_ready(self) -> None:
        offline = Image.open(hs.LOGS / "scheduler_evidence" / "2026-10-01" /
            "account_c9ba2069ff" / "214000_653728_pending_multi_solo_start_before_return.png").convert("RGB")
        village = Image.open(hs.LOGS / "scheduler_evidence" / "2026-10-01" /
            "account_ae1662240e" / "214059_449056_pending_multi_solo_start_before_return.png").convert("RGB")
        scheduler = hs.Scheduler.__new__(hs.Scheduler)
        scheduler.templates = hs.TemplateStore(.92)
        scheduler.detector = None
        self.assertTrue(scheduler.template(
            "offline_login_prompt.bmp", (780, 450, 1140, 660), offline, threshold=.98)[0])
        self.assertFalse(scheduler.main_ready(offline))
        self.assertFalse(scheduler.template(
            "offline_login_prompt.bmp", (780, 450, 1140, 660), village, threshold=.98)[0])
        self.assertTrue(scheduler.main_ready(village))

    def test_missing_sixth_tab_is_occupied_by_account_multi_control(self) -> None:
        screenshot = hs.LOGS / "scheduler_evidence" / "2026-09-19" / "identity_rebind" / "tab_6_x860.png"
        if not screenshot.exists():
            self.skipTest("current five-tab evidence is unavailable")
        store = hs.TemplateStore(0.90)
        ok, score, x, _y = store.match(
            Image.open(screenshot).convert("RGB"),
            hs.ASSETS / "microclient_account_multi.bmp",
            (0, 0, 1100, 35),
            0.92,
        )
        self.assertTrue(ok, score)
        with Image.open(hs.ASSETS / "microclient_account_multi.bmp") as asset:
            self.assertLessEqual(x, 860)
            self.assertGreaterEqual(x + asset.width, 860)

    def test_welfare_snapshot_has_three_disjoint_claim_card_matches(self) -> None:
        screenshot_path = hs.LOGS / "scheduler_evidence" / "2026-08-29" / "account_3ed81e0fcf" / "190739_829513_before_welfare_claim_1.jpg"
        if not screenshot_path.exists():
            self.skipTest("live welfare evidence is unavailable")
        screenshot = Image.open(screenshot_path).convert("RGB")
        store = hs.TemplateStore(0.98)
        self.assertEqual(3, len(hs.WELFARE_CLAIM_ROIS))
        for index, roi in enumerate(hs.WELFARE_CLAIM_ROIS, 1):
            self.assertTrue(store.match(screenshot, hs.ASSETS / f"welfare_claim_{index}.bmp", roi, 0.98)[0])
            self.assertFalse(store.match(screenshot, hs.ASSETS / f"welfare_claimed_{index}.bmp", roi, 0.98)[0])

    def test_welfare_completion_requires_all_three_action_records(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        helper = source.split("def welfare_claims_complete", 1)[1].split("def module_mail", 1)[0]
        self.assertIn('for index in range(1, 4)', helper)
        welfare = source.split("def module_welfare", 1)[1].split("def module_arena", 1)[0]
        self.assertIn("daily online welfare did not positively verify all three claims", welfare)
        self.assertIn("welfare_claims_complete", source)

    def test_welfare_done_flag_is_repaired_when_claim_two_or_three_is_missing(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        run_module = source.split("def run_module", 1)[1].split("def ensure_account_main_before_release", 1)[0]
        self.assertIn('name == "welfare"', run_module)
        self.assertIn("module_repair_required", run_module)
        completion = source.split("def command_is_complete", 1)[1].split("def _finish_command", 1)[0]
        self.assertIn("_accounts_module_complete(command.kind)", completion)
        self.assertIn("_account_module_complete(account, module)", completion)

    def test_interleaved_combat_uses_single_foreground_round_robin(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_combat_interleaved", 1)[1].split("def module_arena", 1)[0]
        self.assertIn("cooperative six-tab round robin", body)
        self.assertIn("_start_arena_slot(slot)", body)
        self.assertIn("_poll_arena_slot(slot)", body)
        self.assertIn("_start_survival_slot(slot)", body)
        self.assertIn("_poll_survival_slot(slot)", body)
        self.assertIn("combat_interleaved_complete", body)
        self.assertNotIn("threading.Thread", body)

    def test_arena_skips_unavailable_tabs_and_continues_online_accounts(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        execute = source.split("def execute_command", 1)[1].split("def run", 1)[0]
        rebind = source.split("def rebind_tabs_by_identity", 1)[1].split("def recognize_two_passes", 1)[0]
        combat = source.split("def run_combat_interleaved", 1)[1].split("def module_arena", 1)[0]
        completion = source.split("def command_is_complete", 1)[1].split("def _finish_command", 1)[0]
        self.assertIn("rebind_tabs_by_identity(skip_unavailable=True)", execute)
        self.assertIn("identity_rebind_unavailable_skipped", rebind)
        self.assertIn("arena_unavailable_accounts_skipped", combat)
        self.assertIn("eligible_accounts", combat)
        self.assertIn("self.arena_eligible_accounts", completion)

    def test_foreground_activation_has_non_clicking_alt_fallback(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def activate", 1)[1].split("def click", 1)[0]
        self.assertIn("keybd_event(cls.VK_MENU", body)
        self.assertIn("SetForegroundWindow(hwnd)", body)
        self.assertNotIn("SetWindowPos", body)
        self.assertNotIn("ShowWindow", body)

    def test_blocking_or_leave_confirm_clicks_visual_button_center(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split('if self.template("team_member_reduced_confirm.bmp"', 1)[1].split('if self.template("wanted_claim_task.bmp"', 1)[0]
        self.assertIn('"team_member_reduced_confirm.bmp",\n                (750, 450, 1180, 700),\n                51,\n                20,', body)
        self.assertNotIn("\n                160,\n", body)

        execute = source.split("def execute_command", 1)[1].split("def execute", 1)[0]
        self.assertIn('elif command.kind == "arena":\n                self.run_combat_interleaved("arena")', execute)
        self.assertIn('elif command.kind == "survival":\n                self.run_combat_interleaved("survival")', execute)

    def test_arena_completion_is_one_victory_and_two_left_daily_claims(self) -> None:
        scheduler = hs.Scheduler(legacy_config(), "test", False, True)
        self.assertEqual(1, hs.ARENA_REQUIRED_WINS)
        self.assertEqual(hs.ARENA_MAX_ATTEMPTS, scheduler._combat_limit("arena"))
        self.assertEqual(20, scheduler._combat_limit("survival"))
        source = Path(hs.__file__).read_text(encoding="utf-8")
        combat = source.split("def _combat_action_name", 1)[1].split("def _combat_active_visible", 1)[0]
        self.assertIn("ARENA_REQUIRED_WINS", combat)
        self.assertIn("ARENA_DAILY_REWARD_CLAIMS", source)
        self.assertIn("arena_victory_1", source)
        self.assertIn("arena_daily_reward_claim_", source)
        completion = source.split("def _finish_arena_slot", 1)[1].split("def _prepare_survival_slot", 1)[0]
        self.assertIn("_arena_victory_confirmed", completion)
        self.assertIn("_arena_daily_rewards_complete", completion)

    def test_panel_commands_validate_selectable_battle_counts(self) -> None:
        command = hs.SchedulerCommand("arena", arena_victory_target=3)
        self.assertEqual(3, command.arena_victory_target)
        team = hs.SchedulerCommand("team1", group_instance_count=2, strong_approach_count=4)
        self.assertEqual((2, 4), (team.group_instance_count, team.strong_approach_count))
        with self.assertRaises(ValueError):
            hs.SchedulerCommand("arena", arena_victory_target=0)
        with self.assertRaises(ValueError):
            hs.SchedulerCommand("team1", group_instance_count=6)
        with self.assertRaises(ValueError):
            hs.SchedulerCommand("team2", strong_approach_count=8)

    def test_arena_without_positive_victory_cannot_complete_from_result_page(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        poll = source.split("def _poll_arena_slot", 1)[1].split("def _finish_arena_slot", 1)[0]
        self.assertIn("arena returned without a positively confirmed victory or defeat", poll)
        self.assertIn('victory=self._arena_attempt_victory_confirmed(account, completed_index)', poll)
        self.assertIn('"arena_defeat_confirmed"', poll)
        self.assertIn("arena_rankdown_banner.bmp", poll)
        self.assertIn("arena_rankdown_confirm.bmp", poll)

    def test_combat_flow_has_no_fixed_arena_close_click(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        self.assertNotIn('click_point(account, 1636, 137, "arena_close"', source)
        self.assertIn('account, "survival_close.bmp", (1550, 60, 1700, 200)', source)
        self.assertIn('"arena_close", ["arena_ready_anchor.bmp", "survival_close.bmp"]', source)

    def test_arena_free_count_reconciles_from_live_dialog_and_never_pays(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def _lower_arena_daily_tasks", 1)[1].split("def _next_arena_result_index", 1)[0]
        self.assertIn("arena_free_count_reconciled", body)
        self.assertIn("arena_free_difficulty_exhausted", body)
        self.assertIn("arena_free_difficulty_unavailable", body)
        self.assertIn("actual_free < expected_free", body)
        self.assertNotIn("purchase", body.lower())

    def test_generic_post_battle_confirmation_is_not_victory_proof(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        poll = source.split("def _poll_arena_slot", 1)[1].split("def _finish_arena_slot", 1)[0]
        rankup = poll.split("for rankup_index in range(1, 9)", 1)[1].split('if self.template("battle_auto_off.bmp"', 1)[0]
        self.assertNotIn("_record_arena_victory", rankup)
        self.assertNotIn("slot.victory_confirmed = True", rankup)
        self.assertIn("not used as victory proof", rankup)
        self.assertTrue((hs.ASSETS / "arena_rankdown_banner.bmp").is_file())
        self.assertTrue((hs.ASSETS / "arena_rankdown_confirm.bmp").is_file())

    def test_interleaved_combat_starts_each_slot_before_preparing_next_slot(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def run_combat_interleaved", 1)[1].split("def module_arena", 1)[0]
        first_round = body.split("while not all(slot.complete for slot in slots):", 1)[0]
        self.assertLess(first_round.index("self._prepare_arena_slot(slot)"), first_round.index("self._start_arena_slot(slot)"))
        self.assertLess(first_round.index("self._prepare_survival_slot(slot)"), first_round.index("self._start_survival_slot(slot)"))
        self.assertIn("identity/template failure could leave every earlier tab", first_round)

    def test_survival_selected_fight_page_is_resumable_and_start_is_audited(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        active = source.split("def _combat_active_visible", 1)[1].split("def _combat_ready_visible", 1)[0]
        prepare = source.split("def _prepare_survival_slot", 1)[1].split("def _start_survival_slot", 1)[0]
        start = source.split("def _start_survival_slot", 1)[1].split("def _poll_survival_slot", 1)[0]
        poll = source.split("def _poll_survival_slot", 1)[1].split("def _finish_survival_slot", 1)[0]
        self.assertIn('("survival_fight_start.bmp", (751, 169, 1151, 419))', active)
        self.assertTrue((hs.ASSETS / "survival_fight_start_core.bmp").is_file())
        self.assertTrue((hs.ASSETS / "survival_toolbar_current_core.bmp").is_file())
        self.assertIn('wait_template("survival_fight_start_core.bmp", (751, 169, 1151, 419), 30', start)
        self.assertIn("self._open_verified_survival(account)", prepare)
        self.assertLess(prepare.index("self._open_verified_survival(account)"), prepare.index("self._migrate_survival_direct_flow_state(account)"))
        self.assertNotIn("daily_survival_row.bmp", prepare)
        self.assertLess(prepare.index('self.template("survival_reset_confirm_yes.bmp"'), prepare.index("self.prepare_main(account)"))
        self.assertIn("def _click_survival_fight_start", start)
        self.assertIn("irreversible=True", start)
        self.assertIn('self._click_survival_fight_start(slot)', poll)
        self.assertIn('"survival_auto_enable_recheck"', poll)
        self.assertNotIn("survival auto-battle was not positively enabled", poll)

    def test_survival_direct_entry_requires_real_page_before_state_migration(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        opener = source.split("def _open_verified_survival", 1)[1].split("def _migrate_survival_direct_flow_state", 1)[0]
        migration = source.split("def _migrate_survival_direct_flow_state", 1)[1].split("def _start_survival_slot", 1)[0]
        self.assertIn('"survival_toolbar_current_core.bmp"', opener)
        self.assertIn('wait_template("survival_anchor.bmp"', opener)
        self.assertIn('marker = "survival_direct_entry_v2"', migration)
        self.assertNotIn('"new"', migration)

    def test_survival_reset_requires_modal_confirmation_before_commit(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        reset = source.split("def _ensure_survival_reset", 1)[1].split("def _open_verified_survival", 1)[0]
        self.assertTrue((hs.ASSETS / "survival_reset_confirm_yes.bmp").is_file())
        self.assertIn('"survival_reset_confirm_yes.bmp"', reset)
        self.assertIn("irreversible=True", reset)
        self.assertLess(reset.index('wait_absent("survival_reset_confirm_yes.bmp"'), reset.rindex("self.commit_action(account, reset_action"))
        self.assertIn("still-visible confirmation button proves", reset)
        self.assertIn('wait_template("survival_score_0.bmp", (751, 769, 1151, 1031), 8', reset)
        self.assertIn('"survival_reset_committed_without_modal"', reset)
        rebind = source.split("def rebind_tabs_by_identity", 1)[1].split("def recognize_two_passes", 1)[0]
        self.assertIn('occluded_positions[tab_x] = "survival_reset_confirm"', rebind)
        self.assertLess(rebind.index('"survival_reset_confirm_yes.bmp"'), rebind.index("recover_known_page(expected, frame)"))

    def test_survival_runs_once_then_resets_then_runs_again(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        prepare = source.split("def _prepare_survival_slot", 1)[1].split("def _start_survival_slot", 1)[0]
        finish = source.split("def _finish_survival_slot", 1)[1].split("def run_combat_interleaved", 1)[0]
        self.assertNotIn("self._ensure_survival_reset(account)\n        self.event(\"combat_slot_prepared\"", prepare)
        self.assertTrue((hs.ASSETS / "survival_reset_confirm_no.bmp").is_file())
        self.assertIn("def _cancel_premature_survival_reset", prepare)
        self.assertLess(finish.index("self.claim_survival_rewards(account)"), finish.index("self._ensure_survival_reset(account)"))
        self.assertIn('"survival_second_run_started"', finish)
        self.assertIn("slot.next_index = 1", finish)
        self.assertIn('self.template("survival_complete_220.bmp"', prepare)
        self.assertIn('self.template("survival_score_0.bmp"', prepare)
        self.assertIn('self.commit_action(account, "survival_reset", "重置后页面稳定为0/220")', prepare)
        self.assertIn('"survival_reset_reconciled_from_zero"', prepare)
        self.assertTrue((hs.ASSETS / "survival_score_80.bmp").is_file())
        self.assertIn('"survival_score_observed"', prepare)
        self.assertNotIn("completed_steps", prepare)
        self.assertTrue((hs.ASSETS / "survival_score_140.bmp").is_file())
        self.assertIn('threshold=0.98', prepare)
        self.assertNotIn("survival actions are complete but the ready page is not visible", prepare)
        claim = source.split("def claim_survival_rewards", 1)[1].split("def close_survival", 1)[0]
        self.assertIn("anchor_threshold=0.98", claim)
        self.assertIn("produced neither confirmation nor reward result", claim)
        self.assertIn("220分奖励箱已确认领取", claim)
        self.assertLess(claim.index('reward_result_visible = self.template("survival_reward_result_confirm.bmp"'), claim.index('if not reward_result_visible and self.template("survival_final_claimed.bmp"'))
        self.assertIn('reward_result_was_visible', prepare)
        self.assertIn('"survival_reset_reconciled_blocked_by_reward_modal"', prepare)
        self.assertIn('"奖励结果弹窗遮挡了重置，页面仍为220/220"', prepare)
        active = source.split("def _combat_active_visible", 1)[1].split("def _combat_ready_visible", 1)[0]
        self.assertIn('"survival_reward_result_confirm.bmp"', active)
        modal_branch = prepare.split('if self.template("survival_reset_confirm_yes.bmp"', 1)[1].split("slot.next_index =", 1)[0]
        self.assertIn('self.template("survival_complete_220.bmp"', modal_branch)
        self.assertIn('self.template("survival_score_0.bmp"', modal_branch)

    def test_known_page_recovery_never_clicks_behind_survival_reward_result(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        recovery = source.split("def recover_known_page", 1)[1].split("def enroll_identities", 1)[0]
        self.assertIn('self.template("survival_reward_result_confirm.bmp"', recovery)
        self.assertIn("reward result blocks every control", recovery)

    def test_combat_rebind_waits_without_clicking_the_same_tab_again(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def switch_account_for_combat", 1)[1].split("def _claim_arena_tasks", 1)[0]
        self.assertIn('self.event("combat_identity_wait"', body)
        self.assertIn("repeated_tab_click=False", body)
        self.assertEqual(2, body.count('self.chrome_click(account.tab_x, 10, f"combat_switch:'))
        self.assertNotIn("return self.switch_account(account, verify=True)", body)

    def test_identity_rebind_preserves_last_verified_map_for_active_combat_pages(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def rebind_tabs_by_identity", 1)[1].split("def recognize_two_passes", 1)[0]
        self.assertIn("saved_map_valid", body)
        self.assertIn("saved_map = self._saved_tab_mapping()", body)
        self.assertNotIn('global_state.get("tab_map"', body)
        self.assertIn("identity_rebind_occluded_page", body)
        self.assertIn('mapping_source=\"last_verified_tab_map\"', body)
        self.assertIn("occluded_positions={str(tab_x): mode", body)

    def test_full_workflow_market_managed_warmup_then_combat(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        full = source.split("def _run_full_order", 1)[1].split("def run_full", 1)[0]
        self.assertLess(full.index("self.run_market_all()"), full.index("self.run_managed_then_main()"))
        self.assertLess(full.index("self.run_managed_then_main()"), full.index('self.run_accounts_module("mail"'))
        self.assertLess(full.index('self.run_accounts_module("welfare"'), full.index("self.run_team_selection(team_index)"))
        self.assertLess(full.index("self.run_team_selection(team_index)"), full.index('self.run_combat_interleaved("arena")'))
        warmup = source.split("def run_managed_then_main", 1)[1].split("def _run_full_order", 1)[0]
        self.assertIn("ManagedWarmupSeconds", warmup)
        self.assertIn("managed_warmup_complete", warmup)

    def test_arena_result_variants_and_auto_control_are_replay_verified(self) -> None:
        screenshot = Image.open(Path(r"C:\Users\Administrator\AppData\Local\Temp\arena_after_battle5_pending.png"))
        store = hs.TemplateStore(0.90)
        self.assertTrue(store.match(screenshot, hs.ASSETS / "arena_defeat_confirm.bmp", (801, 580, 1101, 760), 0.90)[0])
        self.assertTrue(store.match(screenshot, hs.ASSETS / "battle_auto_on.bmp", (1120, 820, 1320, 980), 0.90)[0])
        with Image.open(hs.ASSETS / "arena_rankup_confirm.bmp") as rankup_asset:
            self.assertGreater(rankup_asset.size[0], 0)

    def test_arena_rankup_confirmation_drains_replaced_prompts(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def module_arena", 1)[1].split("def module_survival", 1)[0]
        self.assertIn("for rankup_index in range(1, 9)", body)
        self.assertIn("arena rank-up confirmation sequence did not settle", body)
        self.assertIn("f\"arena_rankup_confirm_{battle_index}_{rankup_index}\"", body)

    def test_interruptible_wait_clamps_deadline_race(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def wait_interruptible", 1)[1].split("def ensure_assets", 1)[0]
        self.assertIn("remaining = deadline - time.monotonic()", body)
        self.assertIn("if remaining <= 0", body)

    def test_jsonl_logging_retries_transient_windows_share_lock(self) -> None:
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "run.jsonl"
            logger = hs.JsonlLogger(path)
            original = Path.open
            calls = []
            def transient(candidate, *args, **kwargs):
                calls.append(candidate)
                if len(calls) == 1:
                    raise PermissionError("reader holds share lock")
                return original(candidate, *args, **kwargs)
            with patch.object(Path, "open", transient), patch.object(hs.time, "sleep"):
                logger.write("verified", value=7)
            self.assertEqual(len(calls), 2)
            self.assertEqual(json.loads(path.read_text())["value"], 7)

    def test_identity_rebind_recovers_occluding_activity_page_only_with_content_enabled(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def rebind_tabs_by_identity", 1)[1].split("def recognize_two_passes", 1)[0]
        self.assertIn("identity_rebind_recovered_page", body)
        self.assertIn("if not self.no_content_clicks", body)
        self.assertIn("self.recover_known_page(expected, frame)", body)

    def test_arena_result_dialog_precedes_background_auto_badge(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def _poll_arena_slot", 1)[1].split("def _finish_arena_slot", 1)[0]
        self.assertLess(body.index("_arena_victory_visible"), body.index('arena_result_confirm.bmp'))
        self.assertLess(body.index('arena_result_confirm.bmp'), body.rindex('_battle_auto_on_visible'))

    def test_current_auto_on_skin_is_supported_without_duplicate_click(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        helper = source.split("def _battle_auto_on_visible", 1)[1].split("def _wait_battle_auto_on", 1)[0]
        poll = source.split("def _poll_arena_slot", 1)[1].split("def _finish_arena_slot", 1)[0]
        self.assertTrue((hs.ASSETS / "battle_auto_on_current.bmp").is_file())
        self.assertTrue((hs.ASSETS / "battle_auto_on_current_core.bmp").is_file())
        self.assertIn('battle_auto_on.bmp', helper)
        self.assertIn('battle_auto_on_current.bmp', helper)
        self.assertIn('battle_auto_on_current_core.bmp', helper)
        self.assertIn('_battle_auto_on_visible(frame)', poll)

    def test_unknown_arena_attempt_on_verified_main_preserves_pending_at_any_age(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        recovery = source.split("def recover_known_page", 1)[1].split("def enroll_identities", 1)[0]
        switch = source.split("def switch_account_for_combat", 1)[1].split("def _arena_pending_attempt_is_stale", 1)[0]
        self.assertIn('"friends_panel_close.bmp"', recovery)
        self.assertIn('"recover_close_friends_panel"', recovery)
        self.assertIn('self.wait_absent("friends_panel_close.bmp"', recovery)
        self.assertIn("for _ in range(4)", switch)
        from copy import deepcopy
        account = hs.Account(1, 'test', 180)
        for stale in (False, True):
            scheduler = Mock()
            action = 'arena_casual_battle_1'
            original = {'status': 'pending', 'updated_at': '2026-09-30T23:59:00+08:00',
                        'conflicts': [{'status': 'pending', 'source': 'older'}]}
            scheduler.state.accounts = {account.name: {'actions': {action: deepcopy(original)}}}
            scheduler.state.action_status.return_value = 'pending'
            scheduler._next_combat_index.return_value = 1
            scheduler._combat_limit.return_value = 10
            scheduler._combat_action_name.return_value = action
            scheduler._recover_arena_victory_from_evidence.return_value = False
            scheduler._recover_arena_defeat_from_evidence.return_value = False
            scheduler._combat_active_visible.return_value = False
            scheduler._combat_ready_visible.return_value = False
            scheduler.recover_known_page.return_value = False
            scheduler.main_ready.return_value = True
            scheduler._arena_pending_attempt_is_stale.return_value = stale
            slot = hs.CombatSlot(account, 'arena')
            with self.assertRaisesRegex(hs.PendingError, '不作为结算依据'):
                hs.Scheduler._prepare_arena_slot(scheduler, slot)
            self.assertEqual(original, scheduler.state.accounts[account.name]['actions'][action])
            scheduler.commit_action.assert_not_called()
            scheduler.click_template.assert_not_called()
            scheduler._prepare_arena_slot.assert_not_called()
            scheduler.save_raw_evidence.assert_called_once()
            self.assertEqual(1, slot.next_index)
            self.assertFalse(slot.running)

    def test_arena_main_with_positive_old_result_can_reconcile(self) -> None:
        account = hs.Account(1, 'test', 180)
        for victory in (True, False):
            scheduler = Mock()
            scheduler.state.action_status.return_value = 'pending'
            scheduler._next_combat_index.return_value = 1
            scheduler._combat_limit.return_value = 10
            scheduler._combat_action_name.return_value = 'arena_casual_battle_1'
            scheduler._recover_arena_victory_from_evidence.return_value = victory
            scheduler._recover_arena_defeat_from_evidence.return_value = not victory
            scheduler._combat_active_visible.return_value = False
            scheduler._combat_ready_visible.return_value = False
            scheduler.recover_known_page.return_value = False
            scheduler.main_ready.return_value = True
            hs.Scheduler._prepare_arena_slot(scheduler, hs.CombatSlot(account, 'arena'))
            scheduler.commit_action.assert_called_once()
            scheduler._prepare_arena_slot.assert_called_once()
            scheduler.click_template.assert_not_called()

    def test_rebind_accepts_only_the_unique_medium_identity_winner(self) -> None:
        scheduler = hs.Scheduler(legacy_config(), "panel", False, False)
        account = next(item for item in scheduler.accounts if item.name == ROLE_F)
        frame = Image.new("RGB", (1920, 1032), "black")
        scheduler.identity_scores = lambda _frame: {
            ROLE_F: 0.632,
            ROLE_D: 0.502,
            ROLE_C: 0.38,
        }
        scheduler.tab_is_visually_active = lambda _account, _frame: True
        self.assertTrue(scheduler.identity_visible_for_rebind(account, 860, frame))
        self.assertTrue(scheduler.identity_visible_for_rebind(None, 860, frame))
        scheduler.identity_scores = lambda _frame: {ROLE_F: 0.632, ROLE_D: 0.58}
        self.assertFalse(scheduler.identity_visible_for_rebind(account, 860, frame))

    def test_jointly_rebound_unique_medium_identity_passes_runtime_gate(self) -> None:
        scheduler = hs.Scheduler(legacy_config(), "panel", False, False)
        account = hs.Account(5, ROLE_F, 860)
        frame = Image.new("RGB", (1920, 1032), "black")
        scheduler.joint_identity_bound = {account.name}
        scheduler.identity_scores = lambda _frame: {ROLE_F: 0.632, ROLE_D: 0.502}
        scheduler.tab_is_visually_active = lambda _account, _frame: True
        verified, _scores = scheduler.verify_identity(account, frame)
        self.assertTrue(verified)

    def test_filtered_offline_role_remains_a_real_identity_competitor(self) -> None:
        scheduler = object.__new__(hs.Scheduler)
        config = legacy_config()
        scheduler.identity_candidates = tuple(hs.Account(i+1, name, 0) for i, name in enumerate(
            hs.parse_csv(config['TabAccountNames'])))
        wrong = next(a for a in scheduler.identity_candidates if a.name == ROLE_B)
        scheduler.accounts = [wrong]
        scheduler.templates = hs.TemplateStore(.98)
        scheduler.identity_threshold = .82
        scheduler.identity_margin = .08
        scheduler.identity_assignment_threshold = .80
        scheduler.joint_identity_bound = {wrong.name}
        scheduler.tab_is_visually_active = lambda *_: True
        with Image.open(hs.LOGS/'scheduler_evidence/2026-10-04/account_c9ba2069ff/235909_602062_verified_identity_shell_recovery.png') as image:
            frame = image.convert('RGB')
        valid, scores = scheduler.verify_identity(wrong, frame)
        self.assertGreater(scores[wrong.name], .80)
        self.assertGreater(scores[ROLE_A], scores[wrong.name])
        self.assertFalse(valid)
        self.assertFalse(scheduler.identity_visible_for_rebind(wrong, 180, frame))

    def test_joint_binding_does_not_authorize_a_lower_scoring_role(self) -> None:
        scheduler = object.__new__(hs.Scheduler)
        wrong = hs.Account(1, 'wrong', 180)
        scheduler.identity_scores = lambda _: {'wrong': .91, 'observed': .99}
        scheduler.identity_threshold = .82
        scheduler.identity_margin = .08
        scheduler.identity_assignment_threshold = .80
        scheduler.joint_identity_bound = {'wrong'}
        scheduler.tab_is_visually_active = lambda *_: True
        self.assertFalse(scheduler.verify_identity(wrong, Image.new('RGB', (1920, 1032)))[0])

    def test_arena_missing_battle_modes_waits_instead_of_forcing_completion(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        stable = source.split("def _click_arena_mode_when_stable", 1)[1].split("def _combat_progress_watchdog", 1)[0]
        start = source.split("def _start_arena_slot", 1)[1].split("def _poll_arena_slot", 1)[0]
        self.assertIn("deadline = time.monotonic() + seconds", stable)
        self.assertIn("arena_mode_revalidation_wait", stable)
        self.assertIn("current_fight_frames >= 3", stable)
        self.assertIn("ranked/casual battle button did not stabilize", start)
        self.assertNotIn('slot.next_index = self._combat_limit("arena") + 1', start)

    def test_live_top_menu_arena_entry_replay_targets_arena_not_theme_menu(self) -> None:
        frame = Image.open(Path(hs.ROOT) /
                           "logs/preflight_20261002_000330_063683_31a478d9.png").convert("RGB")
        store = hs.TemplateStore(.92)
        matched, score, x, y = store.match(
            frame, hs.ASSETS / "arena_toolbar_core.bmp", (1201, 69, 1501, 169))
        self.assertTrue(matched)
        self.assertGreaterEqual(score, .92)
        self.assertEqual((1386, 78), (x, y))
        self.assertFalse(store.match(frame, hs.ASSETS / "arena_ready_anchor.bmp", (851, 199, 1451, 449))[0])

    def test_october_arena_battle_button_replays_four_live_fault_frames(self) -> None:
        template = hs.ASSETS / "arena_battle_button_current.bmp"
        self.assertTrue(template.is_file())
        sources = (
            "logs/scheduler_evidence/2026-10-01/account_3ed81e0fcf/232553_034287_pending_arena_mode_unstable.png",
            "logs/scheduler_evidence/2026-10-01/account_72cd26215f/232700_030495_pending_arena_mode_unstable.png",
            "logs/scheduler_evidence/2026-10-01/account_a802d1815a/232807_081152_pending_arena_mode_unstable.png",
            "logs/scheduler_evidence/2026-10-01/account_b76494cf2c/232928_270596_pending_arena_mode_unstable.png",
        )
        store = hs.TemplateStore(.92)
        scheduler = object.__new__(hs.Scheduler)
        scheduler.templates = store
        scheduler.detector = None
        roi = (650, 770, 920, 875)
        for source in sources:
            with self.subTest(source=source), Image.open(Path(hs.ROOT) / source) as image:
                matched, score, _x, _y = store.match(image.convert("RGB"), template, roi, threshold=.98)
                self.assertTrue(matched, source)
                self.assertGreaterEqual(score, .98)
                self.assertTrue(scheduler._combat_ready_visible("arena", image.convert("RGB")))
        with Image.open(Path(hs.ROOT) / "logs/preflight_20261001_225944_050542_1ade4e4a.png") as image:
            matched, score, _x, _y = store.match(image.convert("RGB"), template, roi, threshold=.98)
            self.assertFalse(scheduler._combat_ready_visible("arena", image.convert("RGB")))
        self.assertFalse(matched)
        self.assertLess(score, .98)

    @patch("hyol_scheduler.time.monotonic", side_effect=[0, 1, 2, 3])
    def test_arena_current_page_fight_button_requires_three_stable_anchored_frames(self, _clock: Mock) -> None:
        account = hs.Account(1, "a", 180)
        def match(name, *_args, **_kwargs):
            if name == "arena_ready_anchor.bmp":
                return True, .99, 850, 200
            if name == "arena_battle_button_current.bmp":
                return True, 1.0, 688, 795
            return False, .1, -1, -1
        scheduler = SimpleNamespace(
            _arena_token_count=Mock(return_value=0),
            check_stop=Mock(), capture=Mock(return_value=Image.new("RGB", (1920, 1032))),
            template=Mock(side_effect=match), click_template=Mock(return_value=True),
            event=Mock(), wait_interruptible=Mock(),
        )
        selected = hs.Scheduler._click_arena_mode_when_stable(scheduler, account, "arena_casual_battle_1")
        self.assertEqual("current_arena_page", selected)
        self.assertEqual(3, scheduler.capture.call_count)
        scheduler.click_template.assert_called_once_with(
            account,
            "arena_battle_button_current.bmp",
            (650, 770, 920, 875),
            95,
            36,
            "arena_casual_battle_1",
            ["arena_ready_anchor.bmp", "arena_battle_button_current.bmp"],
            irreversible=True,
            threshold=.98,
            frame_predicate=ANY,
        )

    @patch("hyol_scheduler.time.monotonic", side_effect=[0, 1, 2, 3])
    def test_arena_current_page_button_without_page_anchor_never_clicks(self, _clock: Mock) -> None:
        account = hs.Account(1, "a", 180)
        def match(name, *_args, **_kwargs):
            if name == "arena_battle_button_current.bmp":
                return True, 1.0, 688, 795
            return False, .1, -1, -1
        scheduler = SimpleNamespace(
            _arena_token_count=Mock(return_value=0),
            check_stop=Mock(), capture=Mock(return_value=Image.new("RGB", (1920, 1032))),
            template=Mock(side_effect=match), click_template=Mock(),
            save_raw_evidence=Mock(return_value="unanchored.png"),
            event=Mock(), wait_interruptible=Mock(),
        )
        selected = hs.Scheduler._click_arena_mode_when_stable(scheduler, account, "attempt_1", seconds=3)
        self.assertIsNone(selected)
        scheduler.click_template.assert_not_called()

    @patch("hyol_scheduler.time.monotonic", side_effect=[0, 1, 2])
    def test_arena_ranked_click_retries_when_guarded_recapture_repaints(self, _clock: Mock) -> None:
        account = hs.Account(5, ROLE_F, 860)
        scheduler = SimpleNamespace(
            _arena_token_count=Mock(return_value=3),
            check_stop=Mock(),
            capture=Mock(return_value=Image.new('RGB', (1920, 1032))),
            template=Mock(return_value=(True, 0.99, 798, 798)),
            click_template=Mock(side_effect=[False, True]),
            event=Mock(),
            wait_interruptible=Mock(),
        )
        selected = hs.Scheduler._click_arena_mode_when_stable(scheduler, account, "arena_casual_battle_2")
        self.assertEqual("ranked", selected)
        self.assertEqual(2, scheduler.click_template.call_count)
        self.assertEqual(
            scheduler.event.call_args_list[0].args,
            ("arena_mode_revalidation_wait", account),
        )
        scheduler.event.assert_any_call(
            "arena_battle_mode_selected",
            account,
            battle_mode="ranked",
            target="arena_casual_battle_2",
        )

    @patch("hyol_scheduler.time.monotonic", side_effect=[0, 1, 2, 3])
    def test_arena_uses_casual_after_three_stable_ranked_unavailable_frames(self, _clock: Mock) -> None:
        account = hs.Account(1, 'a', 180)
        scheduler = SimpleNamespace(
            _arena_token_count=Mock(return_value=0),
            check_stop=Mock(), capture=Mock(return_value=Image.new('RGB', (1920, 1032))),
            template=Mock(side_effect=lambda name, *_args, **_kwargs: (
                name != 'arena_ranked_button_current.bmp', 1.0, 0, 0)),
            click_template=Mock(return_value=True),
            save_raw_evidence=Mock(return_value='ranked_unavailable.png'),
            event=Mock(), wait_interruptible=Mock(),
        )
        selected = hs.Scheduler._click_arena_mode_when_stable(scheduler, account, 'attempt_1')
        self.assertEqual('casual', selected)
        self.assertEqual(3, scheduler.capture.call_count)
        scheduler.save_raw_evidence.assert_called_once()
        scheduler.click_template.assert_called_once()

    def test_arena_result_has_priority_over_once_only_opening_inputs(self) -> None:
        source = Path(hs.__file__).read_text(encoding='utf-8')
        body = source.split('def _poll_arena_slot',1)[1].split('def _finish_arena_slot',1)[0]
        self.assertLess(body.index('_arena_victory_visible(frame)'),body.index('_arena_opening_once'))
        self.assertLess(body.index('arena_result_confirm.bmp'),body.index('_arena_opening_once'))
        self.assertIn('_combat_ready_visible("arena", frame)',body)

    def test_arena_auto_click_waits_when_cinematic_covers_second_guard(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        poll = source.split("def _poll_arena_slot", 1)[1].split("def _finish_arena_slot", 1)[0]
        self.assertIn("except SafetyError", poll)
        self.assertIn("arena_auto_control_temporarily_occluded", poll)
        self.assertIn("_combat_active_visible('arena', frame)", poll)

    def test_arena_victory_banner_is_positive_and_recoverable_evidence(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        recovery = source.split("def _recover_arena_victory_from_evidence", 1)[1].split("def _next_combat_index", 1)[0]
        self.assertTrue((hs.ASSETS / "arena_victory_banner.bmp").is_file())
        self.assertIn("*_before_arena_result_confirm_", recovery)
        self.assertIn("_arena_victory_visible", recovery)
        self.assertIn("_record_arena_victory", recovery)

    def test_arena_defeat_banner_is_positive_and_recoverable_evidence(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        recovery = source.split("def _recover_arena_defeat_from_evidence", 1)[1].split("def _next_combat_index", 1)[0]
        poll = source.split("def _poll_arena_slot", 1)[1].split("def _finish_arena_slot", 1)[0]
        self.assertTrue((hs.ASSETS / "arena_defeat_banner.bmp").is_file())
        self.assertIn("*_before_arena_result_confirm_", recovery)
        self.assertIn("_arena_defeat_visible", recovery)
        self.assertIn("arena_attempt_defeat_", poll)

    def test_arena_two_free_difficulty_reductions_precede_new_battle(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        lowering = source.split("def _lower_arena_daily_tasks", 1)[1].split("def _next_arena_result_index", 1)[0]
        start = source.split("def _start_arena_slot", 1)[1].split("def _poll_arena_slot", 1)[0]
        self.assertIn("arena_free_remaining_", lowering)
        self.assertIn("arena_lower_cancel.bmp", lowering)
        self.assertIn("arena_lower_confirm.bmp", lowering)
        self.assertIn("Win32.move(*self.screen_point(440, hover_y))", lowering)
        self.assertIn("Win32.move(*self.screen_point(100, 500))", lowering)
        self.assertLess(lowering.index("Win32.move(*self.screen_point(100, 500))"), lowering.index("self.content_guard"))
        self.assertIn("arena_task_hovered", lowering)
        self.assertIn("irreversible=True", lowering)
        self.assertLess(start.index("_lower_arena_daily_tasks"), start.index("_click_arena_mode_when_stable"))
        self.assertIn("Win32.move(*self.screen_point(100, 500))", start)
        self.assertGreaterEqual(hs.ARENA_MAX_ATTEMPTS, 2)

    def test_arena_rechecks_victory_target_before_starting_another_battle(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        start = source.split("def _start_arena_slot", 1)[1].split("def _poll_arena_slot", 1)[0]
        self.assertLess(start.index("_claim_arena_tasks"), start.index("_arena_victory_confirmed"))
        self.assertLess(start.index("_arena_victory_confirmed"), start.index("_lower_arena_daily_tasks"))
        self.assertLess(start.index("_arena_victory_confirmed"), start.index("_click_arena_mode_when_stable"))
        self.assertIn("self._finish_arena_slot(slot)", start)

    def test_pending_arena_attempt_precedes_completion_skip(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def _next_combat_index", 1)[1].split("def _combat_active_visible", 1)[0]
        self.assertLess(body.index('== "pending"'), body.index("_arena_daily_rewards_complete"))
        self.assertIn("pending battle page", body)

    def test_pending_arena_outcome_reconciles_after_identity_rebind_closed_page(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def _prepare_arena_slot", 1)[1].split("def _start_arena_slot", 1)[0]
        self.assertIn("self.main_ready(frame) and (slot.victory_confirmed or slot.defeat_confirmed)", body)
        self.assertIn("combat_battle_reconciled_from_main", body)
        self.assertIn("return self._prepare_arena_slot(slot)", body)

    def test_arena_loading_screen_remains_an_active_pending_battle(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        active = source.split("def _combat_active_visible", 1)[1].split("def _combat_ready_visible", 1)[0]
        poll = source.split("def _poll_arena_slot", 1)[1].split("def _finish_arena_slot", 1)[0]
        self.assertTrue((hs.ASSETS / "arena_loading_anchor.bmp").is_file())
        self.assertTrue((hs.ASSETS / "arena_battle_frame_anchor.bmp").is_file())
        self.assertIn('("arena_loading_anchor.bmp", ARENA_LOADING_ROI)', active)
        self.assertIn('("arena_battle_frame_anchor.bmp", (250, 80, 500, 300))', active)
        self.assertIn('self.template("arena_loading_anchor.bmp", ARENA_LOADING_ROI, frame)', poll)
        self.assertNotIn('if self.template("arena_battle_frame_anchor.bmp"', poll)

    def test_arena_ready_fallback_survives_hover_tooltip_occlusion(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        active = source.split("def _combat_active_visible", 1)[1].split("def _combat_ready_visible", 1)[0]
        ready = source.split("def _combat_ready_visible", 1)[1].split("def switch_account_for_combat", 1)[0]
        poll = source.split("def _poll_arena_slot", 1)[1].split("def _finish_arena_slot", 1)[0]
        for body in (active, ready):
            self.assertIn("arena_ranked_button_current.bmp", body)
            self.assertIn("arena_casual_button_current.bmp", body)
            self.assertIn("arena_condition_unmet.bmp", body)
            self.assertIn("arena_reward_claim.bmp", body)
        self.assertIn("((ranked or casual) and (title or task_state)) or (title and current_fight)", ready)
        self.assertIn('if self._combat_ready_visible("arena", frame)', poll)

    def test_arena_prioritizes_ranked_then_casual_and_prioritizes_auto(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        prepare = source.split("def _prepare_arena_slot", 1)[1].split("def _start_arena_slot", 1)[0]
        stable = source.split("def _click_arena_mode_when_stable", 1)[1].split("def _combat_progress_watchdog", 1)[0]
        start = source.split("def _start_arena_slot", 1)[1].split("def _poll_arena_slot", 1)[0]
        poll = source.split("def _poll_arena_slot", 1)[1].split("def _finish_arena_slot", 1)[0]
        self.assertIn("prepare_main(account, clear_notifications=False)", prepare)
        self.assertNotIn("clear_transient_notifications", prepare)
        self.assertLess(stable.index('"ranked"'), stable.index('"casual"'))
        self.assertIn('"arena_ranked_button_current.bmp"', stable)
        self.assertIn('"arena_casual_button_current.bmp"', stable)
        self.assertIn("_click_arena_mode_when_stable", start)
        self.assertNotIn('if self.template("arena_battle_frame_anchor.bmp"', poll.split('if self.template("battle_auto_off.bmp"', 1)[0])

    def test_arena_entry_uses_the_same_image_watchdog_for_every_phase(self) -> None:
        source = Path(hs.__file__).read_text(encoding='utf-8')
        watchdog = source.split('def _combat_progress_watchdog',1)[1].split('def _start_arena_slot',1)[0]
        self.assertIn('check_combat_slot_watchdog(self, slot, frame)',watchdog)
        self.assertNotIn('mark_manual_pause',watchdog)
        self.assertNotIn('>= 90',watchdog)
        self.assertNotIn('stop_event.set',watchdog)

    def test_command_completion_requires_every_account(self) -> None:
        names = hs.parse_csv(legacy_config()["TabAccountNames"])
        scheduler = object.__new__(hs.Scheduler)
        scheduler.accounts = [SimpleNamespace(name=name) for name in names]
        complete = {name: "new" for name in names}
        scheduler.state = SimpleNamespace(module_status=lambda name, module: complete[name])
        command = hs.SchedulerCommand("mail")
        self.assertFalse(scheduler.command_is_complete(command))
        complete.update({name: "done" for name in names})
        self.assertTrue(scheduler.command_is_complete(command))

    def test_invite_overlay_retry_is_iterative_and_positive_only(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def invite_verified_member", 1)[1].split("def verify_three_member_team", 1)[0]
        self.assertIn("invite_obstructed_retry", body)
        self.assertIn("while True", body)
        self.assertNotIn("return self.invite_verified_member(", body)

    def test_atomic_json_never_leaves_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            hs.atomic_json(path, {"account": "测试账号", "done": True})
            self.assertEqual({"account": "测试账号", "done": True}, json.loads(path.read_text(encoding="utf-8")))
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_pending_resolution_preserves_audit_and_reopens_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = object.__new__(hs.StateStore)
            state.path = Path(temp_dir) / "state.json"
            state.global_state = {"date": hs.date.today().isoformat(), "pending": []}
            state.add_pending("account", "mail", "first attempt")
            self.assertEqual(1, state.unresolved_pending_count())
            self.assertEqual(1, state.resolve_pending("account", "mail"))
            self.assertEqual(0, state.unresolved_pending_count())
            self.assertEqual("resolved", state.global_state["pending"][0]["status"])
            state.add_pending("account", "mail", "first attempt")
            self.assertEqual(2, len(state.global_state["pending"]))
            self.assertEqual(1, state.unresolved_pending_count())

    def test_python_candidate_refuses_purchase_enable(self) -> None:
        config = legacy_config()
        config["AllowMarketPurchase"] = "1"
        config["MarketDirectBuyVerified"] = "0"
        with self.assertRaises(hs.SafetyError):
            hs.Scheduler(config, "recognize", False, True)

    def test_source_has_no_injection_or_background_message_input(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("writeprocessmemory", "createremotethread", "postmessagew", "sendmessagew", "socket."):
            self.assertNotIn(forbidden, source)

    def test_shared_main_gate_does_not_require_character_portrait(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def main_ready", 1)[1].split("def prepare_main", 1)[0]
        executable = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn('self.template("anchor_bottom_menu.bmp"', executable)
        self.assertIn('"wanted_team_anchor.bmp"', body)
        self.assertIn('"team_room_close.bmp"', body)

    def test_window_activation_never_moves_or_resizes_client(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def activate", 1)[1].split("def click", 1)[0]
        self.assertNotIn("ShowWindow", body)
        self.assertNotIn("SetWindowPos", body)
        self.assertNotIn("MoveWindow", body)

    def test_window_activation_recovers_foreground_focus_without_geometry_change(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def activate", 1)[1].split("def click", 1)[0]
        self.assertIn("AttachThreadInput", body)
        self.assertIn("BringWindowToTop", body)
        self.assertIn("SetForegroundWindow", body)
        self.assertIn("finally:", body)
        self.assertIn("AttachThreadInput(current_thread, foreground_thread, False)", body)

    def test_click_is_stationary_and_long_enough_for_flash(self) -> None:
        user = Mock()
        user.SetCursorPos.return_value = True
        with patch.object(hs.Win32, 'user32', user), patch.object(hs.time, 'sleep') as sleep:
            hs.Win32.click(180, 200)
        user.SetCursorPos.assert_called_once_with(180, 200)
        self.assertEqual([(.15,), (.06,)], [call.args for call in sleep.call_args_list])
        self.assertEqual([(hs.Win32.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0),
                          (hs.Win32.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)],
                         [call.args for call in user.mouse_event.call_args_list])

    def test_desktop_capture_rejects_foreign_foreground(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        body = source.split("def capture(self)", 1)[1].split("def healthy", 1)[0]
        self.assertIn("Win32.foreground() != self.hwnd", body)
        self.assertIn("capture_focus_reacquire", body)
        self.assertIn("self.validate_geometry(activate=True)", body)
        self.assertIn('reason="game_not_foreground"', body)
        self.assertLess(body.index("self.validate_geometry(activate=True)"), body.index("ImageGrab.grab"))

    def test_module_lease_requires_verified_main_before_next_tab(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        self.assertIn("ensure_account_main_before_release", source)
        self.assertIn("no_further_tab_switch", source)
        config = legacy_config()
        self.assertEqual("0", config["AutoRefreshWhiteTab"])
        self.assertEqual("0", config["AllowStartGameRecovery"])
        self.assertIn("start_game_button.bmp", source)

    def test_auto_stage_stops_after_lease_quarantine(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        independent = source.split("def run_independent", 1)[1].split("def run_accounts_module", 1)[0]
        post_team = source.split("def run_post_team_combat", 1)[1].split("def dissolve_team_after_wanted", 1)[0]
        self.assertIn("automatic stage stopped", independent)
        self.assertIn("post-team stage stopped", post_team)
        self.assertIn("raise PendingError", independent)
        self.assertIn("raise PendingError", post_team)

    def test_direct_non_recognize_stage_rebinds_tabs_before_work(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        run = source.split("def run(self)", 1)[1].split("def build_parser", 1)[0]
        self.assertIn('if self.stage not in ("recognize", "auto"):', run)
        self.assertIn("self.rebind_tabs_by_identity()", run)
        self.assertLess(run.index("self.rebind_tabs_by_identity()"), run.index('if self.stage in ("recognize", "auto")'))

    def test_quarantine_runtime_does_not_advance_stage(self) -> None:
        scheduler = object.__new__(hs.Scheduler)
        scheduler.accounts = [SimpleNamespace(name="隔离账号")]
        scheduler.state = SimpleNamespace(global_state={})
        scheduler.event = lambda *args, **kwargs: None
        scheduler.pause_after_module = threading.Event()
        scheduler.run_module = lambda *args, **kwargs: (_ for _ in ()).throw(hs.LeaseBlocked("verified main missing"))
        with self.assertRaises(hs.PendingError):
            scheduler.run_independent()
        self.assertNotIn("stage", scheduler.state.global_state)
        with self.assertRaises(hs.PendingError):
            scheduler.run_post_team_combat()

    def test_pending_independent_stage_does_not_advance(self) -> None:
        scheduler = object.__new__(hs.Scheduler)
        scheduler.accounts = [SimpleNamespace(name="账号A")]
        scheduler.state = SimpleNamespace(
            global_state={},
            module_status=lambda _name, _module: "pending",
            add_pending=lambda *args, **kwargs: None,
            save_global=lambda: None,
        )
        scheduler.event = lambda *args, **kwargs: None
        scheduler.pause_after_module = threading.Event()
        scheduler.run_module = lambda *args, **kwargs: None
        with self.assertRaises(hs.PendingError):
            scheduler.run_independent()
        self.assertNotIn("stage", scheduler.state.global_state)

    def test_stage_guards_require_team_and_full_completion(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        team = source.split("def run_team(self)", 1)[1].split("def current_market_cycle", 1)[0]
        full = source.split("def _run_full_order", 1)[1].split("def run_full", 1)[0]
        self.assertIn("team stage unresolved", team)
        self.assertIn("full flow halted after unresolved Team{team_index}", full)
        self.assertIn("full flow halted after unresolved managed module", full)
        self.assertIn("self.run_managed_then_main()", full)
        self.assertIn('self.run_combat_interleaved("arena")', full)
        self.assertIn('self.event("market_wait", cycle=cycle, reason="full_flow_before_first_refresh")', full)
        self.assertIn("if self.check_pause_boundary():", source.split("def run_independent", 1)[1].split("def run_accounts_module", 1)[0])
        self.assertIn('raise PendingError("independent stage paused before completion")', source)
        self.assertIn('raise PendingError("team stage paused before completion")', source)
        self.assertIn('raise PendingError("all-day paused after account cycle")', source)

    def test_clicked_navigation_is_never_retried_blindly(self) -> None:
        source = Path(hs.__file__).read_text(encoding="utf-8")
        self.assertIn("module_no_repeat_after_click", source)
        self.assertIn("welfare_close.bmp", source)
        self.assertIn("paid page left untouched", source)

    def test_welfare_paid_page_has_verified_x_and_no_false_main_match(self) -> None:
        before_path = hs.LOGS / "scheduler_evidence" / "2026-08-21" / "welfare_paid_page_before_close.png"
        after_path = hs.LOGS / "scheduler_evidence" / "2026-08-21" / "welfare_paid_page_after_close.png"
        if not before_path.exists() or not after_path.exists():
            self.skipTest("live welfare evidence is unavailable")
        store = hs.TemplateStore(0.90)
        before = Image.open(before_path).convert("RGB")
        after = Image.open(after_path).convert("RGB")
        self.assertTrue(store.match(before, hs.ASSETS / "welfare_close.bmp", (1301, 219, 1451, 369))[0])
        self.assertFalse(store.match(after, hs.ASSETS / "welfare_close.bmp", (1301, 219, 1451, 369))[0])
        self.assertTrue(store.match(before, hs.ASSETS / "welfare_online_tab.bmp", (501, 319, 721, 449))[0])

    def test_current_welfare_paid_page_uses_exact_inactive_online_tab(self) -> None:
        screenshot_path = (
            hs.LOGS
            / "scheduler_evidence"
            / "2026-08-29"
            / "account_3ed81e0fcf"
            / "204753_142717_raw_welfare_probe.png"
        )
        if not screenshot_path.exists():
            self.skipTest("current live welfare evidence is unavailable")
        screenshot = Image.open(screenshot_path).convert("RGB")
        store = hs.TemplateStore(0.98)
        matched, score, x, y = store.match(
            screenshot,
            hs.ASSETS / "welfare_online_tab_inactive.bmp",
            (501, 319, 721, 449),
            0.98,
        )
        self.assertTrue(matched)
        self.assertGreaterEqual(score, 0.98)
        self.assertEqual((557, 353), (x, y))

    def test_content_click_rejects_coordinates_outside_client(self) -> None:
        scheduler = object.__new__(hs.Scheduler)
        scheduler.no_content_clicks = False
        account = hs.Account(0, "bounds-test", 0)
        with self.assertRaisesRegex(hs.SafetyError, "outside trusted client"):
            scheduler.click_point(account, 1925, 1002, "outside", [])

    def test_daily_practice_overlay_requires_verified_close_x(self) -> None:
        folder = hs.LOGS / "scheduler_evidence" / "2026-08-21"
        before_path = folder / "daily_practice_left_open.png"
        after_path = folder / "daily_practice_verified_closed.png"
        if not before_path.exists() or not after_path.exists():
            self.skipTest("live daily-practice evidence is unavailable")
        store = hs.TemplateStore(0.90)
        before = Image.open(before_path).convert("RGB")
        after = Image.open(after_path).convert("RGB")
        roi = (1360, 190, 1470, 290)
        self.assertTrue(store.match(before, hs.ASSETS / "daily_practice_close.bmp", roi)[0])
        self.assertFalse(store.match(after, hs.ASSETS / "daily_practice_close.bmp", roi)[0])
        source = Path(hs.__file__).read_text(encoding="utf-8")
        recovery = source.split("def recover_known_page", 1)[1].split("def enroll_identities", 1)[0]
        self.assertIn('"daily_practice_close.bmp"', recovery)

    def test_lower_right_activity_notice_has_verified_x_and_cleanup_gate(self) -> None:
        folder = hs.LOGS / "scheduler_evidence" / "2026-08-25"
        before_path = folder / "activity_notice_before_close.png"
        after_path = folder / "activity_notice_after_close.png"
        if not before_path.exists() or not after_path.exists():
            self.skipTest("live activity-notice evidence is unavailable")
        store = hs.TemplateStore(0.90)
        before = Image.open(before_path).convert("RGB")
        after = Image.open(after_path).convert("RGB")
        self.assertTrue(store.match(before, hs.ASSETS / "activity_notice_anchor.bmp", (1680, 730, 1919, 930))[0])
        self.assertTrue(store.match(before, hs.ASSETS / "activity_notice_close.bmp", (1865, 700, 1919, 780))[0])
        self.assertFalse(store.match(after, hs.ASSETS / "activity_notice_close.bmp", (1865, 700, 1919, 780))[0])
        source = Path(hs.__file__).read_text(encoding="utf-8")
        close_helper = source.split("def _activity_notice_close_match", 1)[1].split("def clear_transient_notifications", 1)[0]
        self.assertIn('"activity_notice_close.bmp"', close_helper)
        self.assertIn('"activity_notice_close_current.bmp"', close_helper)
        cleanup = source.split("def clear_transient_notifications", 1)[1].split("def prepare_main", 1)[0]
        self.assertIn('"activity_notice_anchor.bmp"', cleanup)
        self.assertIn("not notice_after and not close_after", cleanup)
        self.assertIn("notice and not close_name", cleanup)
        self.assertIn('"activity_notice_retry"', cleanup)
        self.assertIn("verified_notice_and_close_remained_after_settle", cleanup)
        self.assertIn("for _ in range(12)", cleanup)
        self.assertIn("after 12 verified closes", cleanup)
        self.assertNotIn("drag", cleanup.lower())

    def test_current_activity_notice_replay_uses_bounded_relaxed_anchor_gate(self) -> None:
        screenshot_path = (
            hs.LOGS
            / "scheduler_evidence"
            / "2026-08-31"
            / "account_c9ba2069ff"
            / "141213_733514_pending_team2_notice_after_first_close.png"
        )
        if not screenshot_path.exists():
            self.skipTest("current activity-notice replay evidence is unavailable")
        screenshot = Image.open(screenshot_path).convert("RGB")
        store = hs.TemplateStore(0.90)
        notice_roi = (1680, 730, 1919, 930)
        close_roi = (1865, 700, 1919, 780)
        self.assertFalse(store.match(screenshot, hs.ASSETS / "activity_notice_anchor.bmp", notice_roi)[0])
        self.assertTrue(store.match(screenshot, hs.ASSETS / "activity_notice_anchor.bmp", notice_roi, 0.60)[0])
        low_anchor_path = (
            hs.LOGS
            / "scheduler_evidence"
            / "2026-08-31"
            / "account_72cd26215f"
            / "142416_339730_pending_team2_invite_not_visible_after_send.png"
        )
        if low_anchor_path.exists():
            low_anchor = Image.open(low_anchor_path).convert("RGB")
            self.assertFalse(store.match(low_anchor, hs.ASSETS / "activity_notice_anchor.bmp", notice_roi, 0.60)[0])
            self.assertTrue(store.match(low_anchor, hs.ASSETS / "activity_notice_anchor.bmp", notice_roi, 0.50)[0])
        self.assertFalse(store.match(screenshot, hs.ASSETS / "activity_notice_close.bmp", close_roi)[0])
        scheduler = object.__new__(hs.Scheduler)
        scheduler.templates = store
        close_match = scheduler._activity_notice_close_match(screenshot)
        self.assertIsNotNone(close_match)
        assert close_match is not None
        self.assertEqual("activity_notice_close.bmp", close_match[0])
        self.assertGreaterEqual(close_match[1], 0.90)
        self.assertAlmostEqual(1.20, close_match[4], places=2)
        source = Path(hs.__file__).read_text(encoding="utf-8")
        cleanup = source.split("def clear_transient_notifications", 1)[1].split("def prepare_main", 1)[0]
        self.assertIn("notice_threshold = 0.50", cleanup)
        self.assertIn("threshold=notice_threshold", cleanup)
        self.assertIn("notice and not close_name", cleanup)

    def test_current_mail_states_are_strictly_separable(self) -> None:
        folder = hs.LOGS / "scheduler_evidence" / "2026-08-21"
        before_path = folder / "mail_warehouse_current_probe.png"
        after_path = folder / "mail_after_claim_settled.png"
        if not before_path.exists() or not after_path.exists():
            self.skipTest("live mail evidence is unavailable")
        store = hs.TemplateStore(0.90)
        before = Image.open(before_path).convert("RGB")
        after = Image.open(after_path).convert("RGB")
        roi = (1001, 689, 1251, 919)
        self.assertTrue(store.match(before, hs.ASSETS / "mail_claim_all.bmp", roi, 0.995)[0])
        self.assertFalse(store.match(after, hs.ASSETS / "mail_claim_all.bmp", roi, 0.995)[0])
        self.assertFalse(store.match(before, hs.ASSETS / "mail_claim_all_disabled.bmp", roi, 0.995)[0])
        self.assertTrue(store.match(after, hs.ASSETS / "mail_claim_all_disabled.bmp", roi, 0.995)[0])
        stamp_roi = (701, 329, 1251, 719)
        self.assertFalse(store.match(before, hs.ASSETS / "mail_claimed_stamp.bmp", stamp_roi)[0])
        self.assertTrue(store.match(after, hs.ASSETS / "mail_claimed_stamp.bmp", stamp_roi)[0])


class TabMappingTests(unittest.TestCase):
    def make_scheduler(self):
        moon = hs.Account(1, ROLE_E, 720)
        qinqin = hs.Account(2, ROLE_F, 585)
        scheduler = object.__new__(hs.Scheduler)
        scheduler.command_kind = 'multi_solo'
        scheduler.accounts = [moon, qinqin]
        scheduler.state = SimpleNamespace(
            global_state={
                'tab_map': {ROLE_E: 720, ROLE_F: 720},
                'verified_solo_tab_map': {ROLE_E: 720, ROLE_F: 585},
            },
            save_global=Mock(),
        )
        scheduler.event = Mock()
        scheduler._last_frame = Image.new('RGB', (1920, 1032))
        scheduler.save_raw_evidence = Mock(return_value=Path('mapping_conflict.png'))
        return scheduler, moon, qinqin

    def test_multi_solo_uses_verified_map_and_keeps_legacy_map_isolated(self):
        scheduler, _moon, qinqin = self.make_scheduler()
        self.assertEqual('verified_solo_tab_map', scheduler._tab_mapping_key())
        self.assertEqual(585, scheduler._saved_tab_mapping()[qinqin.name])
        scheduler._record_tab_binding(qinqin)
        self.assertEqual(585, scheduler.state.global_state['verified_solo_tab_map'][qinqin.name])
        self.assertEqual(720, scheduler.state.global_state['tab_map'][qinqin.name])
        scheduler.state.save_global.assert_called_once()

    def test_duplicate_verified_mapping_stops_and_saves_evidence(self):
        scheduler, moon, _qinqin = self.make_scheduler()
        scheduler.state.global_state['verified_solo_tab_map']['stale'] = moon.tab_x
        with self.assertRaisesRegex(hs.PendingError, '坐标冲突'):
            scheduler._validate_tab_binding(moon)
        scheduler.save_raw_evidence.assert_called_once()
        scheduler.event.assert_called_once()
        self.assertEqual('tab_mapping_conflict', scheduler.event.call_args.args[0])


class ArenaModeEvidenceTests(unittest.TestCase):
    def test_unstable_mode_page_saves_last_frame_and_match_scores(self):
        scheduler = object.__new__(hs.Scheduler)
        frame = Image.new('RGB', (1920, 1032))
        scheduler.check_stop = Mock()
        scheduler.capture = Mock(return_value=frame)
        scheduler.template = Mock(return_value=(False, .42, -1, -1))
        scheduler.wait_interruptible = Mock()
        scheduler.save_raw_evidence = Mock(return_value=Path('arena_mode_unstable.png'))
        scheduler.event = Mock()
        with patch.object(hs.time, 'monotonic', side_effect=(0., 0., 2.)):
            selected = scheduler._click_arena_mode_when_stable(
                hs.Account(1, 'replay', 180), 'arena_casual_battle_4', seconds=1)
        self.assertIsNone(selected)
        scheduler.save_raw_evidence.assert_called_once_with(
            frame, hs.Account(1, 'replay', 180), 'arena_mode_unstable', 'pending')
        scheduler.event.assert_called_once()
        self.assertEqual('arena_mode_unstable', scheduler.event.call_args.args[0])
        self.assertEqual(5, len(scheduler.event.call_args.kwargs['observations']))
        self.assertIsNone(scheduler.event.call_args.kwargs['observations']['tokens'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
