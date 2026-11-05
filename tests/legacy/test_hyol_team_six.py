"""Replay and validation gates for the anonymous six-tab team entry."""
from test_identity_fixtures import ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F, legacy_config
import unittest
from unittest.mock import Mock, patch
from copy import deepcopy
from pathlib import Path
import tempfile
import json

from PIL import Image

import hyol_scheduler as hs
import hyol_team_six as team


class TeamSelectionTests(unittest.TestCase):
    def test_manual_roles_are_distinct_and_task_order_is_fixed(self):
        value = team.TeamSelection(4, (2, 6), False,
                                   ("strong_approach", "group_instance", "wanted"))
        self.assertEqual(("group_instance", "wanted", "strong_approach"), value.tasks)
        with self.assertRaises(ValueError):
            team.TeamSelection(4, (4, 6))
        with self.assertRaises(ValueError):
            team.TeamSelection(4, (2, 2))
        with self.assertRaises(ValueError):
            team.TeamSelection(4, (2,), True)

    def test_yellow_top_two_replayed_from_real_friend_panel(self):
        path = (hs.LOGS / "scheduler_evidence" / "2026-08-31" / "account_c9ba2069ff"
                / "143015_943822_before_team2_invite_account_72cd26215f.jpg")
        with Image.open(path) as frame:
            rows = team.yellow_friend_rows(frame)
        self.assertEqual((391, 442), rows[:2])

    def test_friend_name_shape_has_clear_winner_on_real_assets(self):
        with Image.open(hs.ASSETS / "accounts" / "account_72cd26215f_identity.bmp") as identity, \
                Image.open(hs.ASSETS / "friend_74_row.bmp") as correct, \
                Image.open(hs.ASSETS / "friend_51_row.bmp") as wrong:
            self.assertGreater(team.name_shape_score(identity, correct), .78)
            self.assertGreater(team.name_shape_score(identity, correct)
                               - team.name_shape_score(identity, wrong), .10)

    def test_ambiguous_row_is_pending_before_invitation(self):
        with Image.open(hs.ASSETS / "accounts" / "account_72cd26215f_identity.bmp") as identity, \
                Image.open(hs.ASSETS / "friend_51_row.bmp") as row:
            with self.assertRaises(hs.PendingError):
                team.assign_friend_rows({1: identity.copy(), 2: identity.copy()},
                                        {390: row.copy()}, (1, 2))

    def test_current_top_two_friend_rows_map_to_two_other_tabs(self):
        path = (hs.LOGS / "scheduler_evidence" / "2026-09-28" / "account_00a0b00ccc"
                / "140848_482693_pending_generic_friend_panel_top.png")
        with Image.open(path) as frame:
            positions = team.yellow_friend_rows(frame)[:2]
            rows = {y: frame.crop((team.FRIEND_ROW_X, y,
                                   team.FRIEND_ROW_X + team.FRIEND_ROW_WIDTH, y + 36))
                    for y in positions}
        identities = {}
        for slot, asset in ((2, "account_3ed81e0fcf_identity.bmp"),
                            (3, "account_72cd26215f_identity.bmp"),
                            (5, "account_c9ba2069ff_identity.bmp")):
            with Image.open(hs.ASSETS / "accounts" / asset) as image:
                identities[slot] = image.copy()
        self.assertEqual({positions[0]: 2, positions[1]: 3},
                         team.assign_friend_rows(identities, rows, (2, 3, 5)))


class TeamIdentityTests(unittest.TestCase):
    def scheduler(self, directory, *, duplicate=False):
        import hyol_checkpoints as cp
        report = cp.read_checkpoint(hs.LOGS/'identity_recovery_20261004_075245_372401_61eae2c1.json')
        frames = {}
        for slot, row in enumerate(sorted(report['accounts'], key=lambda row: row['tab_x']), 1):
            with Image.open(row['evidence'][0]) as image:
                frames[slot] = image.convert('RGB')
        if duplicate:
            frames[3] = frames[1].copy()
        directory.mkdir()
        (directory/'account_identities.json').write_bytes((hs.STATE_DIR/'account_identities.json').read_bytes())
        s = object.__new__(team.SixTabScheduler)
        s.shared_state_directory = directory
        s.hwnd = 123
        s.config = {}
        s._tab_title_masks = {}
        s._session_identity = {}
        s.content_click_count = 0
        s.no_content_clicks = False
        s.bind_window = Mock()
        s.wait_interruptible = Mock()
        s.event = Mock()
        s.click_template = Mock()
        s.save_raw_evidence = Mock(return_value='identity_pending.png')
        s.current_slot = 1
        s.capture = lambda: frames[s.current_slot]
        def switch(account):
            s.current_slot = account.index
            return s.capture()
        s.switch_account = Mock(side_effect=switch)
        return s

    def bind(self, s, slots=(1,2), **kwargs):
        with patch.object(hs.Win32, 'pid', return_value=7), \
             patch.object(hs.Win32.kernel32, 'OpenProcess', return_value=1), \
             patch.object(hs.Win32.kernel32, 'GetProcessTimes', return_value=True), \
             patch.object(hs.Win32.kernel32, 'CloseHandle'), patch.object(hs, 'JsonlLogger'):
            s.bind_session(slots, **kwargs)

    def test_unrecognized_captain_does_not_disable_an_independently_verified_captain(self):
        with tempfile.TemporaryDirectory() as folder:
            s=self.scheduler(Path(folder)/'state')
            # Bright unrecognized role material, not an offline/blank frame.
            frame=s.capture().copy()
            frame.paste('white',(105,80,265,104))
            original_switch=s.switch_account.side_effect
            s.switch_account.side_effect=lambda a: frame if a.index==1 else original_switch(a)
            original_capture=s.capture
            s.capture=lambda: frame if s.current_slot==1 else original_capture()
            self.bind(s,(1,6),allow_unavailable=True,leaders_only=True)
            self.assertEqual({6:ROLE_E},s._bound_identity_names)
            self.assertEqual('identity_material_missing',s._unavailable_reasons[1])
            s.click_template.assert_not_called()

    def test_ambiguous_selected_prefix_uses_two_pass_joint_roles_and_live_centers(self):
        with tempfile.TemporaryDirectory() as folder:
            s = self.scheduler(Path(folder)/'state')
            self.bind(s)
            self.assertEqual({'当前窗口标签1': ROLE_A, '当前窗口标签2': ROLE_C}, s.state.aliases)
            self.assertEqual('180,315,450,585,720,855', s.config['TabXs'])
            self.assertEqual({'当前窗口标签1': 180, '当前窗口标签2': 315}, s.state.global_state['tab_map'])
            self.assertEqual({1, 2}, set(s._session_identity))
            self.assertEqual(6, s.switch_account.call_count)
            s.click_template.assert_not_called()

    def test_duplicate_live_role_never_binds_selected_ambiguous_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            s = self.scheduler(Path(folder)/'state', duplicate=True)
            with self.assertRaisesRegex(hs.PendingError, '联合对账'):
                self.bind(s)
            self.assertEqual({}, s.state.aliases)
            s.click_template.assert_not_called()

    def test_read_only_binding_restores_pending_only_in_memory_and_preserves_every_file(self):
        import hyol_checkpoints as cp
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)/'state'
            s = self.scheduler(directory)
            s.no_content_clicks = True
            canonical = ROLE_A
            hs.atomic_json(directory/'2026-10-03'/(hs.slug(canonical)+'.json'), {
                'date': '2026-10-03', 'account': canonical,
                'actions': {'arena_start_1': {'status': 'pending', 'detail': '原战斗结果未知'}},
            })
            today = directory/'2026-10-04'/(hs.slug(canonical)+'.json')
            hs.atomic_json(today, {'date': '2026-10-04', 'account': canonical, 'actions': {}})
            before = {path.relative_to(directory): path.read_bytes() for path in directory.rglob('*') if path.is_file()}
            self.bind(s)
            self.assertEqual('pending', s.state.action_status('当前窗口标签1', 'arena_start_1'))
            after = {path.relative_to(directory): path.read_bytes() for path in directory.rglob('*') if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual({}, cp.read_checkpoint(today)['actions'])

    def test_bound_identity_rechecks_role_when_the_browser_title_stays_the_same(self):
        with tempfile.TemporaryDirectory() as folder:
            s = self.scheduler(Path(folder)/'state')
            self.bind(s)
            account = s.accounts[0]
            original = s.switch_account(account)
            s._tab_title_masks[account.index] = s._title_mask(original, account.tab_x)
            s._generic_active_tab_x = Mock(return_value=account.tab_x)
            self.assertTrue(team.SixTabScheduler.verify_identity(s, account, original)[0])
            changed = s.switch_account(s.accounts[1]).copy()
            changed.paste(original.crop((0, 0, 1920, 42)), (0, 0))
            self.assertFalse(team.SixTabScheduler.verify_identity(s, account, changed)[0])
            self.assertEqual(1., hs.mask_f1(s._tab_title_masks[account.index], s._title_mask(changed, account.tab_x)))
            s.click_template.assert_not_called()

    def test_hidden_role_does_not_gain_identity_authority_from_the_tab_title(self):
        with tempfile.TemporaryDirectory() as folder:
            s = self.scheduler(Path(folder)/'state')
            self.bind(s)
            account = s.accounts[0]
            original = s.switch_account(account)
            s._tab_title_masks[account.index] = s._title_mask(original, account.tab_x)
            s._generic_active_tab_x = Mock(return_value=account.tab_x)
            hidden = original.copy()
            hidden.paste((0, 0, 0), (120, 65, 400, 145))
            self.assertFalse(team.SixTabScheduler.verify_identity(s, account, hidden)[0])
            s.click_template.assert_not_called()

    def test_identity_disappearing_on_second_frame_saves_both_frames_and_scores_without_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            s = self.scheduler(Path(folder)/'state')
            s.no_content_clicks = True
            actual_capture = s.capture
            s.wait_interruptible = Mock()
            after_tab_detection = 0
            def capture():
                nonlocal after_tab_detection
                after_tab_detection += 1
                image = actual_capture().copy()
                if after_tab_detection == 4:
                    image.paste((0, 0, 0), (120, 65, 400, 145))
                return image
            s.capture = capture
            with self.assertRaisesRegex(hs.PendingError, '两帧不一致'):
                self.bind(s)
            self.assertEqual(2, s.save_raw_evidence.call_count)
            event = next(call for call in s.event.call_args_list if call.args[0] == 'generic_identity_unverified')
            self.assertIn('first_scores', event.kwargs)
            self.assertIn('second_scores', event.kwargs)
            self.assertEqual({}, s.state.aliases)
            s.click_template.assert_not_called()

    def test_anonymous_pending_cannot_replace_a_different_canonical_action(self):
        import hyol_checkpoints as cp
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)/'state'
            s = self.scheduler(directory)
            s.no_content_clicks = True
            canonical = ROLE_A
            action = 'team223_create'
            mask = cp.current_identity_mask(s.capture())
            today = hs.date.today().isoformat()
            anonymous = directory/'window_7_123_0'/today/(hs.slug('当前窗口标签1')+'.json')
            hs.atomic_json(anonymous, {
                'date': today, 'account': '当前窗口标签1', 'identity_mask': cp.encode_mask(mask),
                'actions': {action: {'status': 'pending', 'detail': '匿名原动作'}},
            })
            named = directory/today/(hs.slug(canonical)+'.json')
            hs.atomic_json(named, {'date': today, 'account': canonical,
                                 'actions': {action: {'status': 'pending', 'detail': '实名原动作'}}})
            before = anonymous.read_bytes(), named.read_bytes()
            with self.assertRaisesRegex(hs.PendingError, '动作存在冲突'):
                self.bind(s)
            self.assertEqual(before, (anonymous.read_bytes(), named.read_bytes()))
            s.click_template.assert_not_called()


class TeamRoomReplayTests(unittest.TestCase):
    def references(self):
        import hyol_checkpoints as cp
        identities = cp.read_checkpoint(hs.STATE_DIR/'account_identities.json')
        return {name: cp.decode_mask(row['mask'], tuple(row['shape'])) for name, row in identities.items()}

    def test_two_member_room_replays_distinct_servers_with_the_same_nickname(self):
        with Image.open(hs.LOGS/'scheduler_evidence/2026-08-25/team1_room_readonly_after_restore.png') as frame:
            self.assertEqual((ROLE_A, ROLE_C), team.room_member_assignment(
                frame, self.references(), (ROLE_A, ROLE_C)))

    def test_three_member_room_replays_each_real_member(self):
        with Image.open(hs.LOGS/'current_team_room_after_team2_20260828.png') as frame:
            self.assertEqual((ROLE_E, ROLE_B, ROLE_D), team.room_member_assignment(
                frame, self.references(), (ROLE_B, ROLE_D, ROLE_E)))

    def test_positive_unselected_member_is_never_treated_as_an_empty_slot(self):
        with Image.open(hs.LOGS/'scheduler_evidence/2026-09-28/account_00a0b00ccc/142159_245775_pending_generic_team_room_check.png') as frame:
            with self.assertRaisesRegex(hs.PendingError, '额外或重复成员'):
                team.room_member_assignment(frame, self.references(), (ROLE_A, ROLE_C))

    def test_unknown_or_ambiguous_real_room_rejects_invitation_reconciliation(self):
        with Image.open(hs.LOGS/'current_screen_team1_room_probe_20260828.png') as frame:
            with self.assertRaisesRegex(hs.PendingError, '未知或歧义角色'):
                team.room_member_assignment(frame, self.references(), tuple(self.references()))


class _State:
    def __init__(self, accounts):
        self.global_state = {}
        self.accounts = {account.name: {"actions": {}} for account in accounts}
        self.aliases = {account.name: f"已核实角色{account.index}" for account in accounts}
        self.day = "2026-10-04"

    def action_status(self, name, action):
        return self.accounts[name]["actions"].get(action, {}).get("status", "new")

    def set_action(self, name, action, status, detail=""):
        self.accounts[name]["actions"][action] = {"status": status, "detail": detail}

    def save_global(self):
        pass


class _TeamPages:
    """Independent tab pages with a configurable one-click dissolution outcome."""
    def __init__(self, count=3):
        import threading
        self.accounts = [hs.Account(i, f"标签{i}", team.TAB_XS[i-1]) for i in range(1, count+1)]
        self.by_name = {account.name: account for account in self.accounts}
        self.state = _State(self.accounts)
        self.pages = {account.name: {"kind": "world", "teamed": True, "managed": False}
                      for account in self.accounts}
        self.active = self.accounts[0]
        self.events, self.clicks, self.managed_calls, self.home_calls = [], [], [], []
        self.dissolve_outcome = "dissolved"
        self.fail_managed = {}
        self.content_click_count = 0
        self.stop = threading.Event()

    def event(self, event, account=None, **fields):
        self.events.append((event, account, fields))

    def switch_account(self, account):
        self.active = account
        return self.capture()

    def capture(self):
        return dict(self.pages[self.active.name])

    def main_ready(self, frame=None):
        return (frame or self.capture())["kind"] == "world"

    def open_team_room(self, member, label):
        self.pages[member.name]["kind"] = "room"

    def template(self, name, roi=None, frame=None, threshold=None):
        page = frame or self.capture()
        ok = (name == "team_create_button.bmp" and page["kind"] == "room" and not page["teamed"]
              or name == "team_dissolve_button.bmp" and page["kind"] == "room" and page["teamed"]
              or name == "team_room_close.bmp" and page["kind"] == "room")
        return bool(ok), 1.0 if ok else 0.0, 0, 0

    def wait_template(self, name, roi, seconds):
        return self.template(name, roi)[0]

    def click_template(self, member, name, roi, dx, dy, action, anchors, irreversible=False):
        self.clicks.append((member.name, name, action))
        if not self.template(name, roi)[0]:
            return False
        if irreversible:
            self.state.set_action(member.name, action, "pending")
        if name == "team_dissolve_button.bmp":
            self.pages[member.name]["kind"] = "world"
            if self.dissolve_outcome == "dissolved":
                for page in self.pages.values():
                    page["teamed"] = False
            elif self.dissolve_outcome == "leader_only":
                self.pages[member.name]["teamed"] = False
        elif name == "team_room_close.bmp":
            self.pages[member.name]["kind"] = "world"
        return True

    def wait_absent(self, name, roi, seconds):
        return not self.template(name, roi)[0]

    def wait_until_main(self, seconds):
        return self.main_ready()

    def wait_interruptible(self, seconds):
        pass

    def check_stop(self):
        if self.stop.is_set():
            raise KeyboardInterrupt()

    def start_f12_monitor(self):
        pass

    def bind_session(self, slots, allow_unavailable=False):
        pass

    def save_raw_evidence(self, frame, member, action, status):
        return f"{member.name}_{action}_{status}.png"

    def commit_action(self, member, action, detail):
        self.state.set_action(member.name, action, "done", detail)

    def return_member_to_leaf(self, selected, member, task):
        self.home_calls.append((member.name, task))
        self.active = member
        self.pages[member.name]["kind"] = "world"
        return self.capture()

    def managed_active_score(self, frame):
        return 1.0 if frame["managed"] else 0.0

    def run_managed_gate(self, member):
        self.managed_calls.append(member.name)
        error = self.fail_managed.get(member.name)
        if error:
            raise error
        self.pages[member.name]["managed"] = True
        self.state.set_action(member.name, "start_managed", "done")


class TeamFinishTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = _TeamPages()
        self.runner = team.TeamRunner.__new__(team.TeamRunner)
        self.runner.scheduler = self.scheduler
        self.runner.selection = team.TeamSelection(1, (2, 3), tasks=("group_instance",))
        self.runner.legacy_wanted_unverified = False
        self.runner.fast_points = {}
        self.selected = hs.Team(223, tuple(account.name for account in self.scheduler.accounts))
        self.runner.team = self.selected
        self.record = self.runner._run_record(self.selected)
        self.record["tasks"]["group_instance"] = {
            "status": "done", "task_completed": True, "home_returned": True,
            "home_evidence": {name: f"{name}_home.png" for name in self.selected.members},
        }
        self.record["phase"] = "dissolve"
        self.action = "team223_dissolve_after_tasks"

    def dissolve(self):
        self.runner._dissolve_after_tasks(self.selected, self.scheduler.accounts[0])
        self.record["phase"] = "managed"

    def test_closed_room_without_unteamed_state_remains_pending(self):
        self.scheduler.dissolve_outcome = "closed_only"
        with self.assertRaises(hs.PendingError):
            self.dissolve()
        self.assertEqual("pending", self.scheduler.state.action_status("标签1", self.action))
        self.assertEqual([], self.scheduler.managed_calls)
        self.assertEqual(1, sum(name == "team_dissolve_button.bmp" for _, name, _ in self.scheduler.clicks))

    def test_one_member_still_teamed_prevents_commit_and_managed(self):
        self.scheduler.dissolve_outcome = "leader_only"
        with self.assertRaises(hs.PendingError):
            self.dissolve()
        self.assertEqual("pending", self.scheduler.state.action_status("标签1", self.action))
        self.assertNotIn("dissolve", self.record)
        self.assertEqual([], self.scheduler.managed_calls)

    def test_pending_dissolve_reconciles_all_members_without_another_click(self):
        self.scheduler.state.set_action("标签1", self.action, "pending")
        for page in self.scheduler.pages.values():
            page["teamed"] = False
        self.dissolve()
        self.assertFalse(any(name == "team_dissolve_button.bmp" for _, name, _ in self.scheduler.clicks))
        self.assertEqual("done", self.scheduler.state.action_status("标签1", self.action))
        self.assertEqual(set(self.selected.members), set(self.record["dissolve"]["member_evidence"]))
        self.assertTrue(self.record["dissolve"]["home_returned"])

    def test_legacy_done_without_real_dissolution_is_not_trusted(self):
        self.scheduler.state.set_action("标签1", self.action, "done", "room closed")
        with self.assertRaises(hs.PendingError):
            self.dissolve()
        self.assertFalse(any(name == "team_dissolve_button.bmp" for _, name, _ in self.scheduler.clicks))
        self.assertNotIn("dissolve", self.record)

    def test_three_positive_samples_are_required_for_each_member(self):
        self.scheduler.state.set_action("标签1", self.action, "pending")
        for page in self.scheduler.pages.values():
            page["teamed"] = False
        original = self.scheduler.capture
        seen = 0
        def capture():
            nonlocal seen
            page = original()
            if self.scheduler.active.index == 1 and page["kind"] == "room":
                seen += 1
                if seen == 2:
                    page["teamed"] = True
            return page
        self.scheduler.capture = capture
        with self.assertRaises(hs.PendingError):
            self.dissolve()
        self.assertEqual("pending", self.scheduler.state.action_status("标签1", self.action))

    def test_incomplete_task_or_missing_member_home_blocks_dissolution(self):
        self.record["tasks"]["group_instance"]["home_evidence"].pop("标签3")
        with self.assertRaises(hs.PendingError):
            self.dissolve()
        self.assertEqual([], self.scheduler.clicks)

    def test_unresolved_member_action_blocks_whole_team_managed(self):
        self.dissolve()
        self.scheduler.state.set_action("标签3", "unknown_reward_claim", "pending")
        with self.assertRaises(hs.PendingError):
            self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        self.assertEqual([], self.scheduler.managed_calls)
        self.assertEqual("managed", self.record["phase"])

    def test_all_three_members_managed_only_after_positive_dissolve_and_home(self):
        self.dissolve()
        self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        self.assertEqual(list(self.selected.members), self.scheduler.managed_calls)
        self.assertEqual("done", self.record["phase"])
        self.assertTrue(all(row["status"] == "done" and row["evidence"]
                            for row in self.record["managed"].values()))

    def test_offline_member_retains_prior_managed_evidence_and_pending_team(self):
        self.dissolve()
        self.scheduler.fail_managed["标签2"] = hs.AccountOffline(self.scheduler.accounts[1], "offline.png")
        with self.assertRaises(hs.AccountOffline):
            self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        self.assertEqual("done", self.record["managed"]["标签1"]["status"])
        self.assertEqual("pending", self.record["managed"]["标签2"]["status"])
        self.assertNotIn("标签3", self.record["managed"])
        self.assertEqual("managed", self.record["phase"])

    def test_f12_mid_managed_preserves_evidence_and_phase(self):
        self.dissolve()
        self.scheduler.fail_managed["标签2"] = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        self.assertEqual("done", self.record["managed"]["标签1"]["status"])
        self.assertEqual("pending", self.record["managed"]["标签2"]["status"])
        self.assertEqual("managed", self.record["phase"])

    def test_unknown_managed_start_is_never_reclicked(self):
        self.dissolve()
        self.scheduler.state.set_action("标签2", "start_managed", "pending")
        with self.assertRaises(hs.PendingError):
            self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        self.assertEqual([], self.scheduler.managed_calls)

    def test_resume_managed_skips_friend_invitation_team_creation_and_tasks(self):
        self.dissolve()
        self.scheduler.resolve_friend_members = lambda *args: self.fail("resume opened Friends")
        self.runner._prepare_team = lambda *args: self.fail("resume rebuilt team")
        self.runner._run_task = lambda *args: self.fail("resume repeated task")
        class Lease:
            def acquire(self): return True
            def release(self): pass
        with patch.object(hs, "ForegroundRuntimeLease", Lease), patch.object(team, "manual_pause_status", return_value={}):
            self.assertEqual(0, self.runner.run())
        self.assertEqual("done", self.record["phase"])

    def test_changed_selection_cannot_overwrite_finishing_team(self):
        self.runner.selection = team.TeamSelection(1, (2, 3), tasks=("wanted",))
        with self.assertRaises(hs.PendingError):
            self.runner._resume_finishing_team()
        self.assertEqual(["group_instance"], self.record["selected_tasks"])

    def test_two_member_final_managed_requires_both_member_evidence(self):
        self.scheduler.accounts.pop()
        self.scheduler.by_name.pop("标签3")
        self.runner.selection = team.TeamSelection(1, (2,), tasks=("group_instance",))
        selected = hs.Team(220, ("标签1", "标签2"))
        record = self.runner._run_record(selected)
        record["tasks"]["group_instance"] = {
            "status": "done", "task_completed": True, "home_returned": True,
            "home_evidence": {name: "home.png" for name in selected.members},
        }
        self.runner._dissolve_after_tasks(selected, self.scheduler.accounts[0])
        record["phase"] = "managed"
        self.runner._finish_managed(selected, self.scheduler.accounts[0])
        self.assertEqual(["标签1", "标签2"], self.scheduler.managed_calls)
        self.assertEqual("done", record["phase"])

    def test_strong_zero_requires_actual_leader_count_too(self):
        class CounterPages(_TeamPages):
            def open_strong_approach(self, selected, member, spec): pass
            def _read_team_reward_remaining(self, member, spec, frame=None): return 1 if member.index == 1 else 0
            def _close_team_activity_page(self, member, spec): pass
        s = CounterPages()
        spec = hs.TEAM_ACTIVITY_BY_KEY["strong_approach"]
        self.assertFalse(team.SixTabScheduler._strong_all_member_rewards_zero(s, self.selected, s.accounts[0], spec))
        saved = s.state.global_state["generic_strong_counts"]["223"]
        self.assertEqual({"1": 1, "2": 0, "3": 0}, saved["counts"])
        self.assertEqual({"1", "2", "3"}, set(saved["evidence"]))

    def test_managed_network_pending_is_only_reconciled_when_active(self):
        self.dissolve()
        self.scheduler.state.set_action("标签2", "start_managed_network_1", "pending")
        self.scheduler.pages["标签2"]["managed"] = True
        self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        self.assertEqual("done", self.scheduler.state.action_status("标签2", "start_managed_network_1"))
        self.assertNotIn(("标签2", "managed"), self.scheduler.home_calls)
        self.assertEqual("done", self.record["phase"])

    def test_new_selection_keeps_previous_actions_and_uses_distinct_team_cycle(self):
        self.dissolve()
        self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        previous_actions = deepcopy(self.scheduler.state.accounts)
        self.runner.selection = team.TeamSelection(1, (2, 3), tasks=("group_instance", "strong_approach"))
        self.assertIsNone(self.runner._resume_finishing_team())
        new_team = self.runner._new_team_cycle(self.scheduler.accounts[0], (3, 2))
        self.assertEqual(10223, new_team.index)
        fresh = self.runner._run_record(new_team)
        self.assertEqual(223, fresh["tasks"]["group_instance"]["carried_from_team"])
        self.assertNotIn("strong_approach", fresh["tasks"])
        self.assertEqual(previous_actions, self.scheduler.state.accounts)
        self.assertEqual(["group_instance"], self.record["selected_tasks"])

    def test_completed_cycle_cannot_start_new_request_with_unknown_action(self):
        self.dissolve()
        self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        self.scheduler.state.set_action("标签3", "arena_result_unknown", "pending")
        with self.assertRaises(hs.PendingError):
            self.runner._new_team_cycle(self.scheduler.accounts[0], (2, 3))
        self.assertEqual("pending", self.scheduler.state.action_status("标签3", "arena_result_unknown"))
        self.assertNotIn("10223", self.scheduler.state.global_state["generic_team_runs"])

    def test_yesterdays_completion_is_not_used_for_today_task_completion(self):
        self.dissolve()
        self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        self.record["source_day"] = "2026-10-03"
        self.assertIsNone(self.runner._resume_finishing_team())
        new_team = self.runner._new_team_cycle(self.scheduler.accounts[0], (2, 3))
        fresh = self.runner._run_record(new_team)
        self.assertEqual("2026-10-04", fresh["source_day"])
        self.assertEqual({}, fresh["tasks"])

    def _history(self, path, day, record):
        (path / f"{day}.json").write_text(json.dumps({"date": day, "generic_team_runs": {"223": record}}), encoding="utf-8")

    def test_cross_day_managed_phase_restores_original_date_and_binding(self):
        self.dissolve()
        old = deepcopy(self.record)
        old["source_day"] = "2026-10-03"
        with tempfile.TemporaryDirectory() as directory:
            self.scheduler.state.directory = Path(directory)
            self._history(Path(directory), "2026-10-03", old)
            self.scheduler.state.global_state["generic_team_runs"] = {}
            self.runner._restore_finishing_runs()
        restored = self.runner._run_record(self.selected)
        self.assertEqual("managed", restored["phase"])
        self.assertEqual("2026-10-03", restored["source_day"])
        self.assertEqual(old["identity_binding"], restored["identity_binding"])
        self.assertEqual(self.selected, self.runner._resume_finishing_team())

    def test_later_done_history_prevents_reviving_older_managed_phase(self):
        older = deepcopy(self.record)
        older["phase"] = "managed"
        later = dict(older, phase="done")
        with tempfile.TemporaryDirectory() as directory:
            path = self.scheduler.state.directory = Path(directory)
            self._history(path, "2026-10-02", older)
            self._history(path, "2026-10-03", later)
            self.scheduler.state.global_state["generic_team_runs"] = {}
            self.runner._restore_finishing_runs()
        self.assertEqual({}, self.scheduler.state.global_state["generic_team_runs"])

    def test_cross_day_anonymous_or_changed_identity_never_restores_phase(self):
        for binding in (None, {"标签1": "其他角色", "标签2": "已核实角色2", "标签3": "已核实角色3"}):
            with self.subTest(binding=binding), tempfile.TemporaryDirectory() as directory:
                path = self.scheduler.state.directory = Path(directory)
                old = dict(self.record, phase="managed", identity_binding=binding)
                self._history(path, "2026-10-03", old)
                self.scheduler.state.global_state["generic_team_runs"] = {}
                with self.assertRaises(hs.PendingError):
                    self.runner._restore_finishing_runs()
                self.assertEqual({}, self.scheduler.state.global_state["generic_team_runs"])

    def test_completed_same_selection_resumes_confirmation_without_new_cycle(self):
        self.dissolve()
        self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        self.assertEqual(self.selected, self.runner._resume_finishing_team())
        self.assertEqual({"223"}, set(self.scheduler.state.global_state["generic_team_runs"]))

    def test_unobserved_old_member_cannot_be_managed_from_saved_done_record(self):
        self.dissolve()
        self.scheduler._session_identity = {1: object(), 2: object()}
        with self.assertRaises(hs.PendingError):
            self.runner._finish_managed(self.selected, self.scheduler.accounts[0])
        self.assertEqual([], self.scheduler.managed_calls)

    def test_history_date_mismatch_does_not_restore_a_fresh_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.scheduler.state.directory = Path(directory)
            self._history(path, "2026-10-03", self.record)
            file = path / "2026-10-03.json"
            saved = json.loads(file.read_text(encoding="utf-8"))
            saved["date"] = "2026-10-04"
            file.write_text(json.dumps(saved), encoding="utf-8")
            self.scheduler.state.global_state["generic_team_runs"] = {}
            with self.assertRaises(hs.PendingError):
                self.runner._restore_finishing_runs()
        self.assertEqual({}, self.scheduler.state.global_state["generic_team_runs"])

    def test_pending_task_blocks_dissolution_and_final_managed_in_run(self):
        self.record["phase"] = "tasks"
        self.record["tasks"]["group_instance"].update(
            status="pending", task_completed=False, home_returned=False)
        self.scheduler.resolve_friend_members = lambda *args: (2, 3)
        self.runner._prepare_team = lambda *args: None
        def pending(*args):
            raise hs.PendingError("本轮奖励仍待核实")
        self.runner._run_task = pending
        class Lease:
            def acquire(self): return True
            def release(self): pass
        with patch.object(hs, "ForegroundRuntimeLease", Lease), patch.object(team, "manual_pause_status", return_value={}):
            self.assertEqual(2, self.runner.run())
        self.assertEqual([], self.scheduler.managed_calls)
        self.assertEqual([], self.scheduler.clicks)
        self.assertEqual("tasks", self.record["phase"])

    def test_completed_task_is_not_repeated_when_tasks_phase_resumes(self):
        self.record["phase"] = "tasks"
        self.scheduler.resolve_friend_members = lambda *args: (2, 3)
        self.runner._prepare_team = lambda *args: None
        class Lease:
            def acquire(self): return True
            def release(self): pass
        with patch.object(hs, "ForegroundRuntimeLease", Lease), \
             patch.object(team, "manual_pause_status", return_value={}), \
             patch.object(self.runner, "_run_task") as run_task:
            self.assertEqual(0, self.runner.run())
        run_task.assert_not_called()
        self.assertEqual("done", self.record["phase"])

    def test_unchecked_final_managed_requires_real_dissolve_home_and_makes_no_managed_call(self):
        self.runner.managed_enabled = False
        self.record['phase'] = 'tasks'
        self.scheduler.resolve_friend_members = lambda *args: (2, 3)
        self.runner._prepare_team = lambda *args: None
        class Lease:
            def acquire(self): return True
            def release(self): pass
        with patch.object(hs, 'ForegroundRuntimeLease', Lease), \
             patch.object(team, 'manual_pause_status', return_value={}):
            self.assertEqual(0, self.runner.run())
        self.assertTrue(self.runner._dissolve_complete(self.record['dissolve'], self.selected.members))
        self.assertFalse(self.record['managed_enabled'])
        self.assertEqual('done', self.record['phase'])
        self.assertEqual({}, self.record['managed'])
        self.assertEqual([], self.scheduler.managed_calls)

    def test_unchecking_managed_does_not_hide_an_uncertain_prior_start(self):
        self.runner.managed_enabled = False
        self.dissolve()
        self.scheduler.state.set_action('标签2', 'start_managed', 'pending')
        with self.assertRaisesRegex(hs.PendingError, '未决动作'):
            self.runner._finish_without_managed(self.selected)
        self.assertEqual('pending', self.scheduler.state.action_status('标签2', 'start_managed'))
        self.assertEqual('managed', self.record['phase'])
        self.assertEqual([], self.scheduler.managed_calls)

    def test_completed_unchecked_cycle_can_extend_selection_without_fake_managed_evidence(self):
        self.runner.managed_enabled = False
        self.dissolve()
        self.runner._finish_without_managed(self.selected)
        self.runner.selection = team.TeamSelection(1, (2, 3), tasks=('group_instance', 'wanted'))
        next_team = self.runner._new_team_cycle(self.scheduler.accounts[0], (2, 3))
        self.assertNotEqual(self.selected.index, next_team.index)
        current = self.runner._run_record(next_team)
        self.assertFalse(current['managed_enabled'])
        self.assertTrue(self.runner._task_complete(current['tasks']['group_instance'], self.selected.members))
        self.assertEqual({}, current['managed'])

    def test_pending_invites_resume_only_unaccepted_originals_before_room_proof(self):
        s = self.scheduler
        leader = s.accounts[0]
        s.state.set_action(leader.name, 'team223_create', 'pending')
        for member in s.accounts[1:]:
            s.state.set_action(leader.name, f'team223_invite_slot{member.index}', 'pending')
        s.state.set_action(s.accounts[1].name, 'team223_accept_slot2', 'pending')
        s.require_named_members = Mock()
        s.invite_member = Mock()
        s.reconcile_team_membership = Mock(side_effect=hs.PendingError('房间仍未全员核实'))
        s.create_verified_team = Mock()
        s.ensure_team_room_type = Mock()
        with self.assertRaises(hs.PendingError):
            self.runner._prepare_team(self.selected, leader)
        s.invite_member.assert_called_once_with(self.selected, leader, s.accounts[2])
        s.reconcile_team_membership.assert_called_once_with(self.selected, leader)
        s.create_verified_team.assert_not_called()
        s.ensure_team_room_type.assert_not_called()
        self.assertEqual('pending', s.state.action_status(leader.name, 'team223_create'))
        self.assertEqual('pending', s.state.action_status(s.accounts[1].name, 'team223_accept_slot2'))

    def test_pending_create_without_prior_invite_never_creates_or_sends(self):
        s = self.scheduler
        leader = s.accounts[0]
        s.state.set_action(leader.name, 'team223_create', 'pending')
        s.require_named_members = Mock()
        s.invite_member = Mock()
        s.reconcile_team_membership = Mock(side_effect=hs.PendingError('建队未决'))
        s.create_verified_team = Mock()
        with self.assertRaises(hs.PendingError):
            self.runner._prepare_team(self.selected, leader)
        s.invite_member.assert_not_called()
        s.create_verified_team.assert_not_called()

    def partial_invitation_scheduler(self):
        s = self.scheduler
        leader, member, missing = s.accounts
        s.state.set_action(leader.name, 'team223_create', 'pending')
        s.state.set_action(leader.name, 'team223_invite_slot2', 'pending')
        s.state.set_action(member.name, 'team223_accept_slot2', 'pending')
        s.require_named_members = Mock(return_value=s.state.aliases)
        s._inspect_team_room = Mock(return_value=('partial_room.png', tuple(s.state.aliases[name] for name in (leader.name, member.name))))
        s.invite_member = Mock()
        s.resume_unissued_invites = lambda *args: team.SixTabScheduler.resume_unissued_invites(s, *args)
        s.reconcile_team_membership = Mock(side_effect=[team.MembershipIncomplete('第三人尚未发出邀请'), None])
        s.ensure_team_room_type = Mock()
        s.create_verified_team = Mock()
        return s, leader, member, missing

    def test_interrupted_three_member_creation_sends_only_the_never_issued_invite_after_room_proof(self):
        s, leader, member, missing = self.partial_invitation_scheduler()
        self.runner._prepare_team(self.selected, leader)
        s._inspect_team_room.assert_any_call(self.selected, leader, owner=True, allow_partial=True)
        s._inspect_team_room.assert_any_call(self.selected, member, allow_partial=True)
        s.invite_member.assert_called_once_with(self.selected, leader, missing)
        s.create_verified_team.assert_not_called()
        self.assertEqual('pending', s.state.action_status(leader.name, 'team223_invite_slot2'))
        self.assertEqual('pending', s.state.action_status(member.name, 'team223_accept_slot2'))
        self.assertEqual(2, s.reconcile_team_membership.call_count)

    def test_missing_member_with_an_uncertain_accept_prevents_any_additional_invite(self):
        s, leader, member, missing = self.partial_invitation_scheduler()
        s._inspect_team_room.return_value = ('leader_only.png', (s.state.aliases[leader.name],))
        with self.assertRaisesRegex(team.MembershipIncomplete, '原已发送或已接受'):
            self.runner._prepare_team(self.selected, leader)
        s.invite_member.assert_not_called()
        s.create_verified_team.assert_not_called()

    def test_partial_rooms_must_agree_before_the_remaining_invite_is_sent(self):
        s, leader, member, missing = self.partial_invitation_scheduler()
        expected = tuple(s.state.aliases[name] for name in (leader.name, member.name))
        s._inspect_team_room.side_effect = [('leader.png', expected), ('member.png', (s.state.aliases[member.name],))]
        with self.assertRaisesRegex(hs.PendingError, '部分队伍'):
            self.runner._prepare_team(self.selected, leader)
        s.invite_member.assert_not_called()
        s.create_verified_team.assert_not_called()

    def test_f12_during_partial_room_proof_never_sends_the_remaining_invite(self):
        s, leader, member, missing = self.partial_invitation_scheduler()
        s._inspect_team_room.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.runner._prepare_team(self.selected, leader)
        s.invite_member.assert_not_called()
        s.create_verified_team.assert_not_called()

    def invitation_scheduler(self):
        s = self.scheduler
        leader, member = s.accounts[:2]
        s.state.set_action(leader.name, 'team223_invite_slot2', 'pending')
        s.require_named_members = Mock(return_value=s.state.aliases)
        s.invite_origin_asset = Mock(return_value='OFFLINE_OWN_ORIGIN.bmp')
        s.expected_invitation_members = set()
        s.switch_account = Mock()
        s.capture = Mock(return_value=object())
        s.wait_template = Mock(return_value=True)
        s.template = Mock(return_value=(True, 1., 0, 0))
        s.wait_absent = Mock(return_value=True)
        s.wait_interruptible = Mock()
        s.reconcile_team_membership = Mock()
        def click(actor, name, roi, dx, dy, action, anchors, irreversible=False):
            self.assertEqual('team_invite_accept_yes.bmp', name)
            self.assertEqual((member.name, 'OFFLINE_OWN_ORIGIN.bmp'), s._approved_invitation)
            s.state.set_action(actor.name, action, 'pending')
            return True
        s.click_template = Mock(side_effect=click)
        return s, leader, member

    def test_old_sent_invite_is_accepted_once_and_stays_pending_until_room_proof(self):
        s, leader, member = self.invitation_scheduler()
        team.SixTabScheduler.invite_member(s, self.selected, leader, member)
        s.click_template.assert_called_once()
        s.reconcile_team_membership.assert_not_called()
        self.assertEqual('pending', s.state.action_status(leader.name, 'team223_invite_slot2'))
        self.assertEqual('pending', s.state.action_status(member.name, 'team223_accept_slot2'))
        self.assertEqual(set(), s.expected_invitation_members)
        self.assertIsNone(s._approved_invitation)
        s.click_template.reset_mock()
        team.SixTabScheduler.invite_member(s, self.selected, leader, member)
        s.click_template.assert_not_called()
        s.reconcile_team_membership.assert_called_once()

    def test_wrong_origin_or_changed_second_frame_never_accepts_old_invite(self):
        for failed_sample in (1, 2):
            with self.subTest(sample=failed_sample):
                s, leader, member = self.invitation_scheduler()
                origins = 0
                def template(name, *args, **kwargs):
                    nonlocal origins
                    if name == 'OFFLINE_OWN_ORIGIN.bmp':
                        origins += 1
                        return origins != failed_sample, 1., 0, 0
                    return True, 1., 0, 0
                s.template.side_effect = template
                with self.assertRaises(hs.PendingError):
                    team.SixTabScheduler.invite_member(s, self.selected, leader, member)
                s.click_template.assert_not_called()
                self.assertEqual('pending', s.state.action_status(leader.name, 'team223_invite_slot2'))
                self.assertEqual('new', s.state.action_status(member.name, 'team223_accept_slot2'))
                self.assertEqual(set(), s.expected_invitation_members)

    def test_f12_before_accept_keeps_original_invite_and_clears_transient_permission(self):
        s, leader, member = self.invitation_scheduler()
        s.wait_interruptible.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            team.SixTabScheduler.invite_member(s, self.selected, leader, member)
        s.click_template.assert_not_called()
        self.assertEqual(set(), s.expected_invitation_members)
        self.assertIsNone(s._approved_invitation)


class _SettlementPages(_TeamPages, team.SixTabScheduler):
    def require_named_members(self, names):
        return {name: self.state.aliases[name] for name in names}

    def _team_result_template(self, frame):
        return 'team_result_confirm.bmp' if frame['kind'] == 'result' else None

    def _team_reward_result_visible(self, frame):
        return frame['kind'] == 'reward'

    def _battle_auto_on_visible(self, frame):
        return frame['kind'] == 'battle'

    def template(self, name, roi=None, frame=None, threshold=None):
        page = frame or self.capture()
        if name == 'team_result_confirm.bmp':
            return page['kind'] == 'result', 1., 0, 0
        if name == hs.TEAM_REWARD_RESULT_TEMPLATE:
            return page['kind'] == 'reward', 1., 0, 0
        return super().template(name, roi, frame, threshold)

    def click_template(self, member, name, roi, dx, dy, action, anchors, irreversible=False):
        if name in ('team_result_confirm.bmp', hs.TEAM_REWARD_RESULT_TEMPLATE):
            self.clicks.append((member.name, name, action))
            self.state.set_action(member.name, action, 'pending')
            self.pages[member.name]['kind'] = 'reward' if name == 'team_result_confirm.bmp' else 'world'
            return True
        return super().click_template(member, name, roi, dx, dy, action, anchors, irreversible)

    def return_member_to_leaf(self, selected, member, task, *, attempt_index=None):
        return team.SixTabScheduler.return_member_to_leaf(self, selected, member, task, attempt_index=attempt_index)


class TeamAttemptSettlementTests(unittest.TestCase):
    def setUp(self):
        self.s = _SettlementPages()
        self.selected = hs.Team(223, tuple(account.name for account in self.s.accounts))
        self.leader = self.s.accounts[0]
        self.spec = hs.TEAM_ACTIVITY_BY_KEY['group_instance']
        self.pause = patch('hyol_navigation.return_to_leaf', side_effect=self.home)
        self.pause.start()
        self.addCleanup(self.pause.stop)

    def home(self, scheduler, member):
        if scheduler.pages[member.name]['kind'] != 'world':
            raise hs.PendingError('未确认队员回村')

    def completed_leader_attempt(self, attempt):
        base = self.s._team_attempt_base(self.selected, self.spec, attempt)
        self.s.state.set_action(self.leader.name, base+'_start', 'done')
        self.s.state.set_action(self.leader.name, base+'_complete', 'done')
        for member in self.s.accounts:
            self.s.state.set_action(member.name, base+'_auto_'+hs.slug(member.name), 'done')
            self.s.pages[member.name]['kind'] = 'world' if member == self.leader else 'result'

    def test_next_attempt_blocks_until_both_other_members_finish_result_reward_and_home(self):
        self.completed_leader_attempt(1)
        with self.assertRaisesRegex(hs.PendingError, '不开始下一场'):
            self.s._next_team_attempt_index(self.selected, self.leader, self.spec)
        self.assertEqual([], self.s.clicks)
        self.s.settle_member_attempt(self.selected, self.leader, self.spec, 1)
        self.assertEqual(2, self.s._next_team_attempt_index(self.selected, self.leader, self.spec))
        self.assertEqual(4, len(self.s.clicks))
        record = self.s._member_attempt_record(self.selected, self.spec, 1)
        self.assertEqual(set(self.selected.members), set(record['home_evidence']))
        self.assertEqual('done', record['status'])

    def test_second_attempt_has_distinct_result_and_reward_actions_and_resume_is_idempotent(self):
        for attempt in (1, 2):
            self.completed_leader_attempt(attempt)
            self.s.settle_member_attempt(self.selected, self.leader, self.spec, attempt)
        self.assertEqual(8, len(self.s.clicks))
        self.assertEqual(8, len({row[2] for row in self.s.clicks}))
        self.s.settle_member_attempt(self.selected, self.leader, self.spec, 2)
        self.assertEqual(8, len(self.s.clicks))
        self.assertEqual(3, self.s._next_team_attempt_index(self.selected, self.leader, self.spec))

    def test_unknown_member_reward_remains_pending_and_prevents_the_next_leader_battle(self):
        self.completed_leader_attempt(1)
        member = self.s.accounts[1]
        self.s.pages[member.name]['kind'] = 'reward'
        action = 'team223_group_instance_attempt_1_member2_reward'
        self.s.state.set_action(member.name, action, 'pending')
        with self.assertRaises(hs.PendingError):
            self.s.settle_member_attempt(self.selected, self.leader, self.spec, 1)
        self.assertEqual([], self.s.clicks)
        self.assertEqual('pending', self.s.state.action_status(member.name, action))
        with self.assertRaisesRegex(hs.PendingError, '不开始下一场'):
            self.s._next_team_attempt_index(self.selected, self.leader, self.spec)

    def test_f12_between_members_keeps_verified_member_home_without_new_clicks_on_resume(self):
        self.completed_leader_attempt(1)
        original_switch = self.s.switch_account
        def switch(member):
            if member.index == 3:
                raise KeyboardInterrupt()
            return original_switch(member)
        self.s.switch_account = switch
        with self.assertRaises(KeyboardInterrupt):
            self.s.settle_member_attempt(self.selected, self.leader, self.spec, 1)
        self.assertEqual(2, len(self.s.clicks))
        record = self.s._member_attempt_record(self.selected, self.spec, 1)
        self.assertEqual({'标签1', '标签2'}, set(record['home_evidence']))
        self.s.switch_account = original_switch
        self.s.settle_member_attempt(self.selected, self.leader, self.spec, 1)
        self.assertEqual(4, len(self.s.clicks))

    def runner(self):
        runner = team.TeamRunner.__new__(team.TeamRunner)
        runner.scheduler, runner.team = self.s, self.selected
        runner.selection = team.TeamSelection(1, (2, 3), tasks=('group_instance',))
        runner.fast_points, runner.legacy_wanted_unverified = {}, False
        return runner

    def membership(self):
        self.s.state.set_action(self.leader.name, 'team223_create', 'done')
        for member in self.s.accounts[1:]:
            self.s.state.set_action(self.leader.name, f'team223_invite_slot{member.index}', 'done')
            self.s.state.set_action(member.name, f'team223_accept_slot{member.index}', 'done')
        self.s.state.global_state['generic_membership_evidence'] = {'223': {
            'members': list(self.selected.members), 'identity_binding': dict(self.s.state.aliases),
            'evidence': {name: name+'_room.png' for name in self.selected.members},
        }}

    def test_original_in_flight_battle_does_not_open_rooms_or_change_formation_before_observation(self):
        self.membership()
        self.s.state.set_action(self.leader.name, 'team223_group_instance_attempt_1_start', 'done')
        runner = self.runner()
        self.s.reconcile_team_membership = Mock()
        self.s.ensure_team_room_type = Mock()
        self.s.create_verified_team = Mock()
        runner._prepare_team(self.selected, self.leader)
        self.s.reconcile_team_membership.assert_not_called()
        self.s.create_verified_team.assert_not_called()
        runner._formation = Mock()
        self.s.run_verified_group_instance = Mock(side_effect=hs.PendingError('正常原战斗仍进行中'))
        with self.assertRaisesRegex(hs.PendingError, '正常原战斗'):
            runner._run_task('group_instance', self.selected, self.leader)
        runner._formation.assert_not_called()
        self.s.run_verified_group_instance.assert_called_once()

    def test_missing_original_membership_evidence_does_not_bypass_room_reconciliation(self):
        self.membership()
        self.s.state.global_state['generic_membership_evidence']['223']['evidence'].pop('标签3')
        self.s.state.set_action(self.leader.name, 'team223_group_instance_attempt_1_start', 'done')
        runner = self.runner()
        self.s.create_verified_team, self.s.invite_member = Mock(), Mock()
        self.s.reconcile_team_membership = Mock(side_effect=hs.PendingError('原同队证据不足'))
        with self.assertRaisesRegex(hs.PendingError, '原同队证据不足'):
            runner._prepare_team(self.selected, self.leader)
        self.s.reconcile_team_membership.assert_called_once()

    def test_rescue_cannot_complete_from_only_the_leaders_auto_or_result(self):
        runner = self.runner()
        runner.selection = team.TeamSelection(1, (2, 3), tasks=('rescue_jinchuriki',))
        runner._formation = Mock()
        self.s.run_generic_team_activity = Mock()
        self.s._team_members_auto_complete = Mock(return_value=False)
        self.s._team_activity_status = Mock(return_value='done')
        with patch.object(hs, 'rescue_activity_open_now', return_value=True):
            with self.assertRaisesRegex(hs.PendingError, '逐成员自动战斗'):
                runner._run_task('rescue_jinchuriki', self.selected, self.leader)
        self.assertTrue(self.s.run_generic_team_activity.call_args.args[2].require_all_member_auto)
        self.assertEqual([], self.s.home_calls)

    def test_normal_member_battle_is_observed_past_sixty_seconds_even_with_catalogue_anchor(self):
        member = self.s.accounts[1]
        self.s.switch_account(member)
        frames = [{'kind': 'battle'}, {'kind': 'battle'}, {'kind': 'reward'}]
        self.s.capture = Mock(side_effect=frames)
        original_template = self.s.template
        def template(name, *args, **kwargs):
            if name == self.spec.page_anchor:
                return True, 1., 0, 0
            return original_template(name, *args, **kwargs)
        self.s.template = template
        clock = [0.]
        self.s.wait_interruptible = Mock(side_effect=lambda seconds: clock.__setitem__(0, clock[0]+61))
        with patch.object(team.time, 'monotonic', side_effect=lambda: clock[0]):
            self.s._wait_member_result_page(member, self.spec)
        self.assertEqual(3, self.s.capture.call_count)
        self.assertEqual(2, self.s.wait_interruptible.call_count)
        self.assertEqual([], self.s.clicks)


class TeamManagedGuiTests(unittest.TestCase):
    def test_real_checkbox_default_and_runner_parameter(self):
        with patch('hyol_client_tools.add_diagnostic_controls'):
            app = team.TeamClient()
        app.withdraw()
        self.addCleanup(app.destroy)
        self.assertTrue(app.managed_enabled.get())
        self.assertFalse(app.managed_control.instate(('disabled',)))
        app.managed_enabled.set(False)
        with patch.object(team,'TeamRunner') as runner,patch.object(team.threading,'Thread'):
            app.start()
        self.assertIs(False,runner.call_args.kwargs['managed_enabled'])

    def test_invalid_managed_type_never_creates_scheduler(self):
        selection = team.TeamSelection(1,(2,),False,('wanted',))
        with patch.object(team,'SixTabScheduler') as create,self.assertRaises(ValueError):
            team.TeamRunner(selection,managed_enabled='false')
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
