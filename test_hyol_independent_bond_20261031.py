"""Portable subset; private desktop replay fixtures remain local."""
"""Current-window scope, repeat rounds and real-result continuation regressions."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PIL import Image
import hyol_bond as bond
import hyol_runtime as runtime
from hyol_preparation import scope_for_request

class IndependentScopeTests(unittest.TestCase):

    def test_every_independent_mode_normalizes_stale_multi_account_settings(self):
        for mode in runtime.INDEPENDENT_MODES:
            with self.subTest(mode=mode):
                request = runtime.RunRequest(mode=mode, account_count=6)
                self.assertEqual(request.account_count, 1)
                self.assertEqual(scope_for_request(request), dict(scope='current', slots=[]))
        self.assertEqual(runtime.RunRequest(mode='multi', account_count=2).account_count, 2)

    def test_puzzle_and_training_dispatch_to_current_window_runner(self):
        for mode in ('puzzle', 'training_record'):
            fake = Mock(scheduler=Mock(config={}))
            with self.subTest(mode=mode), patch.object(runtime, 'DailyRunner', return_value=fake) as daily, patch.object(runtime, 'MultiSoloRunner') as multi:
                runtime.make_runner(runtime.RunRequest(mode=mode), Mock(), Path('unused'))
            self.assertEqual(daily.call_args.kwargs['selected_tasks'], (mode,))
            self.assertTrue(daily.call_args.kwargs['generic_current_window'])
            self.assertEqual(fake.scheduler.config['CurrentWindowOnly'], '1')
            multi.assert_not_called()

    def test_identity_ambiguity_never_observes_other_windows(self):
        import hyol_accounts as accounts
        s = Mock(config={'CurrentWindowOnly': '1'}, shared_state_directory=Path('unused'))
        frame = Image.new('RGB', (1920, 1032))
        s.capture.return_value = frame
        s.template.return_value = (False, 0, 0, 0)
        with patch.object(accounts, 'dismiss_current_final_result', return_value=frame), patch('hyol_identity_overlays.dismiss_identity_overlays', return_value=frame), patch.object(accounts, 'collect_current_identity', return_value=([object()] * 3, frame)), patch.object(accounts, 'register_observations', side_effect=bond.hs.PendingError('ambiguous')), patch.object(accounts, 'observe_accounts') as observe:
            with self.assertRaisesRegex(bond.hs.PendingError, '仅运行当前窗口'):
                accounts.bind_visible_identity(s, bond.hs.Account(1, 'current', 180))
        observe.assert_not_called()
        s.chrome_click.assert_not_called()

class RepeatedReadyScopeTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.shared = self.root / 'state'
        self.owner = 'verified-current-captain'
        self.session = '7:42:creation'
        key = hashlib.sha256((self.session + '|180').encode()).hexdigest()
        self.scope_path = self.shared / 'bond/scopes' / (key + '.json')
        self.request_path = self.shared / 'bond' / (bond.hs.slug(self.owner) + '.json')
        self.original = dict(run_id='original-round', owner=self.owner, status='done', phase='ready', requested=2, completed=2, isolated_legacy=[])
        self.write(self.request_path, self.original)
        self.write(self.scope_path, dict(owner=self.owner, session=self.session, tab_x=180, markers=[225, 360], run_id='original-round', lobby_rows=['captain-glyph']))
        self.runner = object.__new__(bond.BondRunner)
        self.runner.requested = 3
        self.runner.keep_ready = True
        self.runner.new_round = False
        self.runner.request = {}
        self.runner.legacy_state_paths = ()
        self.runner.scheduler = Mock(config={}, shared_state_directory=self.shared, account_logs={})
        self.runner.scheduler.main_ready.return_value = False
        self.runner.scheduler.capture.return_value = Image.new('RGB', (1920, 1032))
        self.runner.scheduler._generic_active_tab_x.return_value = 180
        self.runner.ready_visible = Mock(return_value=True)
        for target, kwargs in (('hyol_accounts.game_session_token', {'return_value': self.session}), ('hyol_multi_solo.tab_close_positions', {'return_value': (225, 360)}), ('hyol_multi_solo.active_tab_centers', {'return_value': [180, 315]}), ('hyol_bond_goals.lobby_identity', {'return_value': ['captain-glyph']}), ('hyol_bond.hs.STATE_DIR', {'new': self.shared})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_completed_round_is_idempotent_and_explicit_next_round_resets_only_new_count(self):
        self.runner.bind_repeatable_request()
        self.assertEqual(self.runner.request['run_id'], 'original-round')
        self.assertEqual(self.runner.request['completed'], 2)
        self.runner.new_round = True
        self.runner.bind_repeatable_request()
        self.assertNotEqual(self.runner.request['run_id'], 'original-round')
        self.assertEqual((self.runner.request['requested'], self.runner.request['completed']), (3, 0))
        self.runner.scheduler.chrome_click.assert_not_called()
        self.runner.scheduler.switch_account.assert_not_called()

    def test_new_round_option_cannot_replace_inflight_match_or_original_target(self):
        self.write(self.request_path, dict(self.original, status='running', phase='running', completed=1))
        self.runner.new_round = True
        self.runner.bind_repeatable_request()
        self.assertEqual((self.runner.request['run_id'], self.runner.request['requested'], self.runner.request['completed'], self.runner.request['phase']), ('original-round', 2, 1, 'running'))

    def test_changed_captain_or_layout_never_rewrites_original_request(self):
        before = self.request_path.read_bytes()
        with patch('hyol_bond_goals.lobby_identity', return_value=['different-captain']):
            with self.assertRaisesRegex(bond.hs.PendingError, '不一致'):
                self.runner.bind_repeatable_request()
        self.assertEqual(self.request_path.read_bytes(), before)
        self.runner.scheduler.click_template.assert_not_called()

    def test_first_manual_ready_page_binds_only_three_fresh_current_captain_samples(self):
        self.scope_path.unlink()
        self.runner.bind_repeatable_request()
        self.assertTrue(self.runner.scope['page_bound'])
        self.assertEqual(len(self.runner.scope['samples']), 3)
        self.assertEqual(self.runner.request['completed'], 0)
        self.runner.scheduler.bind_generic_current_window.assert_not_called()
        self.runner.scheduler.chrome_click.assert_not_called()
        self.runner.scheduler.click_template.assert_not_called()

class StopModeTests(unittest.TestCase):

    def test_five_wins_can_need_more_than_five_battles(self):
        rec = bond.prepare_request({}, 5, 'now', stop_mode='wins')
        rec.update(completed=11, verified_wins=4)
        self.assertFalse(bond.target_reached(rec))
        rec.update(completed=12, verified_wins=5, status='done')
        self.assertTrue(bond.target_reached(rec))
        self.assertEqual(bond.prepare_request(rec, 5, 'later', new_round=False), rec)

    def test_endless_has_no_ninety_nine_battle_cap_and_resume_keeps_original_mode(self):
        rec = bond.prepare_request({}, 5, 'now', stop_mode='endless')
        rec.update(completed=100, verified_wins=31)
        self.assertFalse(bond.target_reached(rec))
        resumed = bond.prepare_request(rec, 5, 'later', preserve_target=True, stop_mode='wins')
        self.assertEqual(resumed['stop_mode'], 'endless')
        self.assertEqual(resumed['completed'], 100)
        self.assertFalse(bond.target_reached(resumed))

    def test_battle_mode_counts_defeats_and_mode_is_validated_at_bridge(self):
        from hyol_desktop_bridge import parse_request
        rec = bond.prepare_request({}, 5, 'now')
        rec.update(completed=5, verified_wins=0)
        self.assertTrue(bond.target_reached(rec))
        for mode in ('endless', 'battles', 'wins'):
            self.assertEqual(parse_request({'mode': 'bond', 'bond_stop_mode': mode}).bond_stop_mode, mode)
        with self.assertRaises(ValueError):
            parse_request({'mode': 'bond', 'bond_stop_mode': 'unknown'})

    def test_stopped_endless_can_explicitly_start_another_goal_only_at_ready(self):
        original = bond.prepare_request({}, 5, 'now', stop_mode='endless')
        original.update(completed=7, verified_wins=3, last_error='F12 emergency stop')
        continued = bond.prepare_request(original, 5, 'later', new_round=False, stop_mode='wins')
        self.assertEqual(continued['run_id'], original['run_id'])
        fresh = bond.prepare_request(original, 5, 'later', new_round=True, stop_mode='wins')
        self.assertNotEqual(fresh['run_id'], original['run_id'])
        self.assertEqual((fresh['stop_mode'], fresh['completed'], fresh['verified_wins']), ('wins', 0, 0))
        inflight = bond.prepare_request(dict(original, phase='running'), 5, 'later', new_round=True, stop_mode='wins')
        self.assertEqual(inflight['run_id'], original['run_id'])

class PointsContinuationTests(unittest.TestCase):

    def test_points_popup_counts_only_owned_match_and_then_stable_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = object.__new__(bond.BondRunner)
            runner.keep_ready = True
            runner._account = bond.hs.Account(1, 'captain', 180)
            runner.request_path = Path(temporary) / 'request.json'
            runner.request = dict(run_id='round', owner='captain', phase='running', completed=0, requested=2)
            proof = Path(temporary) / 'result.png'
            frame = Image.new('RGB', (1920, 1032))
            frame.save(proof)
            stage = [0]
            s = runner.scheduler = Mock(config={})
            s.capture.return_value = frame
            s.save_raw_evidence.return_value = proof
            s.state.action_status.return_value = 'done'
            s.template.return_value = (False, 0, 0, 0)
            s._team_result_template.return_value = None
            s._team_reward_result_visible.return_value = False
            s._team_battle_start_visible.return_value = False
            s._battle_auto_on_visible.return_value = False
            runner.ready_visible = Mock(side_effect=lambda *args: stage[0] == 1)
            runner.ready_visible_stable = Mock(return_value=True)
            with patch('hyol_identity_overlays.inspect_bond_shell', side_effect=lambda *args: ('bond_points_result', {}) if stage[0] == 0 else None), patch('hyol_identity_overlays.dismiss_identity_overlays', side_effect=lambda *args, **kwargs: stage.__setitem__(0, 1)):
                runner.poll_battle(1)
            self.assertEqual(runner.request['completed'], 1)
            self.assertEqual(runner.request['phase'], 'ready')
            self.assertTrue(runner.result_was_observed(1))
            runner.ready_visible_stable.assert_called_once()
            s.chrome_click.assert_not_called()
            s.switch_account.assert_not_called()

    def test_reward_popup_is_preserved_without_any_claim_click(self):
        runner = object.__new__(bond.BondRunner)
        runner.keep_ready = True
        runner.request = dict(run_id='round', phase='running', completed=0, requested=2)
        s = runner.scheduler = Mock(config={})
        s.capture.return_value = Image.new('RGB', (1920, 1032))
        s.template.return_value = (False, 0, 0, 0)
        s._team_battle_start_visible.return_value = False
        s._team_result_template.return_value = None
        s._team_reward_result_visible.return_value = True
        s._battle_auto_on_visible.return_value = False
        runner.ready_visible = Mock(return_value=False)
        with patch('hyol_identity_overlays.inspect_bond_shell', return_value=None):
            with self.assertRaisesRegex(bond.hs.PendingError, '未领取奖励'):
                runner.poll_battle(1)
        s.click_template.assert_not_called()
        self.assertEqual(runner.request['completed'], 0)

class BondDailyNavigationTests(unittest.TestCase):

    def test_top_of_limited_list_scans_down_to_find_named_bond_row(self):
        from hyol_team_bond import TeamBondDriver
        driver = object.__new__(TeamBondDriver)
        driver._account = bond.hs.Account(1, 'captain', 180)
        driver.request = {}
        driver.save_request = Mock()
        s = driver.scheduler = Mock()
        s.template.side_effect = lambda name, *args, **kwargs: (name in ('daily_limited_tab_selected.bmp', 'daily_scroll_down_current.bmp'), 1, 0, 0)
        s.click_template.return_value = True
        with patch('hyol_team_pipeline.daily_page_present', return_value=True), patch('hyol_team_pipeline.daily_list_signature', return_value='top-of-list'):
            driver.enter_ready(object())
            self.assertEqual(s.click_template.call_args.args[1], 'daily_scroll_down_current.bmp')
            self.assertEqual(driver.request['scroll_down'], 1)
            with self.assertRaisesRegex(bond.hs.PendingError, '列表末端'):
                driver.enter_ready(object())
        self.assertEqual(s.click_template.call_count, 1)

class ReadyCompletionTests(unittest.TestCase):

    def test_runtime_completes_at_ready_without_return_or_managed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proof = root / 'ready.png'
            Image.new('RGB', (10, 10)).save(proof)
            events = []
            controller = runtime.RuntimeController(events.append, root)
            scheduler = SimpleNamespace(stop=threading.Event(), config={})
            runner = SimpleNamespace(keep_ready=True, scheduler=scheduler, run=Mock(return_value=0), _unified_owner='captain', request=dict(owner='captain', run_id='round', requested=2, completed=2, status='done', phase='ready', ready_evidence=str(proof)))
            with patch.object(runtime, 'preflight_live', return_value=dict(status='ready', healthy=True, content_clicks=0)), patch.object(runtime, 'make_runner', return_value=runner), patch.object(runtime, 'manual_pause_status', return_value={'paused': False}), patch.object(runtime.hs, 'ForegroundRuntimeLease') as lease, patch.object(controller, '_finish_bond') as finish:
                lease.return_value.acquire.return_value = True
                controller._run(runtime.RunRequest(mode='bond', bond_count=2, final_managed=False))
            receipt = next((row for row in events if row['event'] == 'unified_finished'))
            self.assertEqual(receipt['status'], 'complete')
            self.assertTrue(receipt['bond_kept_ready'])
            self.assertEqual(receipt['bond_ready_evidence'], str(proof))
            self.assertEqual(receipt['home_evidence'], '')
            self.assertEqual(receipt['final_managed_evidence'], '')
            finish.assert_not_called()
if __name__ == '__main__':
    unittest.main()
