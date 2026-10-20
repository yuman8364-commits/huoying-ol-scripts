"""Regression for real interrupted runs; identities remain generic fixtures."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import hyol_navigation as nav
import hyol_scheduler as hs


class InterruptionRecoveryTests(unittest.TestCase):
    def battle_scheduler(self, visible):
        scheduler = object.__new__(hs.Scheduler)
        scheduler._arena_waiting_battle_visible = Mock(return_value=False)
        scheduler.template = Mock(side_effect=lambda name, *_args, **_kwargs:
                                  (name in visible, 1. if name in visible else 0., 0, 0))
        return scheduler

    def test_arena_and_survival_recognize_both_battle_controls_when_auto_is_covered(self):
        names = {'survival_battle_hud_left_current.bmp', 'survival_battle_hud_right_current.bmp'}
        frame = Image.new('RGB', (1920, 1032))
        for mode in ('arena', 'survival'):
            with self.subTest(mode=mode):
                scheduler = self.battle_scheduler(names)
                self.assertTrue(scheduler._combat_active_visible(mode, frame))
                self.assertFalse(scheduler._battle_auto_on_visible(frame))
                for call in scheduler.template.call_args_list:
                    if call.args[0] in names:
                        self.assertEqual(.98, call.kwargs['threshold'])

    def test_one_battle_control_cannot_authorize_an_unknown_page(self):
        frame = Image.new('RGB', (1920, 1032))
        for visible in ({'survival_battle_hud_left_current.bmp'},
                        {'survival_battle_hud_right_current.bmp'}, set()):
            with self.subTest(visible=visible):
                scheduler = self.battle_scheduler(visible)
                self.assertFalse(scheduler._combat_active_visible('arena', frame))
                self.assertFalse(scheduler._combat_active_visible('survival', frame))

    def guarded_battle_scheduler(self, visible):
        scheduler = self.battle_scheduler(visible)
        account = hs.Account(1, 'example', 180)
        scheduler.active = account
        scheduler.generic_current_tab_x = None
        scheduler.ensure_window = Mock()
        scheduler.capture = Mock(return_value=Image.new('RGB',(1920,1032)))
        scheduler._saved_tab_mapping = Mock(return_value={account.name:account.tab_x})
        scheduler.verify_identity = Mock(return_value=(False,{}))
        scheduler.click_point = Mock()
        return scheduler, account

    def test_skill_guard_accepts_two_live_battle_controls_without_the_old_arena_patch(self):
        scheduler,account=self.guarded_battle_scheduler(
            {'survival_battle_hud_left_current.bmp','survival_battle_hud_right_current.bmp'})
        frame=scheduler._content_guard_once(account,['arena_battle_frame_anchor.bmp'])
        self.assertIs(frame,scheduler.capture.return_value)
        scheduler.click_point.assert_not_called()

    def test_skill_guard_rejects_one_control_and_never_authorizes_an_unknown_page(self):
        for visible in ({'survival_battle_hud_left_current.bmp'},
                        {'survival_battle_hud_right_current.bmp'},set()):
            with self.subTest(visible=visible):
                scheduler,account=self.guarded_battle_scheduler(visible)
                with self.assertRaises(hs.SafetyError):
                    scheduler._content_guard_once(account,['arena_battle_frame_anchor.bmp'])
                scheduler.click_point.assert_not_called()

    def chrome_scheduler(self):
        return SimpleNamespace(hwnd=123, ensure_window=Mock(), event=Mock(), click_count=0,
                               check_input_pause=Mock(), wait_interruptible=Mock())

    def chrome(self, scheduler, hits, foreground=123):
        return (patch.object(hs.Win32, 'hit_test', side_effect=hits),
                patch.object(hs.Win32, 'foreground', return_value=foreground),
                patch.object(hs.Win32, 'click'))

    def test_one_transient_hit_test_timeout_rechecks_then_clicks_once(self):
        scheduler = self.chrome_scheduler()
        hit, focus, input_patch = self.chrome(scheduler, [hs.SafetyError('window hit test timed out or failed'), 1])
        with hit as probe, focus, input_patch as click:
            hs.Scheduler.chrome_click(scheduler, 180, 10, 'observe_account:1')
            self.assertEqual(2, probe.call_count)
            click.assert_called_once_with(180, 10, expected_hwnd=scheduler.hwnd,
                                          before_down=scheduler.check_input_pause)
        scheduler.ensure_window.assert_called()
        self.assertEqual(1, scheduler.click_count)

    def test_persistent_hit_test_timeout_has_bounded_observation_and_zero_input(self):
        scheduler = self.chrome_scheduler()
        hit, focus, input_patch = self.chrome(scheduler, [hs.SafetyError('window hit test timed out or failed')]*3)
        with hit as probe, focus, input_patch as click:
            with self.assertRaises(hs.SafetyError):
                hs.Scheduler.chrome_click(scheduler, 180, 10, 'observe_account:1')
            self.assertEqual(3, probe.call_count)
            click.assert_not_called()

    def test_timeout_followed_by_caption_hit_never_clicks_or_retries_caption(self):
        scheduler = self.chrome_scheduler()
        hit, focus, input_patch = self.chrome(scheduler, [hs.SafetyError('window hit test timed out or failed'), 2, 1])
        with hit as probe, focus, input_patch as click:
            with self.assertRaises(hs.SafetyError):
                hs.Scheduler.chrome_click(scheduler, 180, 10, 'observe_account:1')
            self.assertEqual(2, probe.call_count)
            click.assert_not_called()

    def test_focus_change_during_timeout_recheck_has_zero_input(self):
        scheduler = self.chrome_scheduler()
        hit, focus, input_patch = self.chrome(scheduler, [hs.SafetyError('window hit test timed out or failed'), 1], 456)
        with hit as probe, focus, input_patch as click:
            with self.assertRaises(hs.SafetyError):
                hs.Scheduler.chrome_click(scheduler, 180, 10, 'observe_account:1')
            self.assertEqual(1, probe.call_count)
            click.assert_not_called()

    def market_scheduler(self, page='market', pending=False):
        state = {'page': page, 'title': True, 'close': True}
        scheduler = Mock(hwnd=None)
        scheduler.state.accounts = {'example': {'actions': {'arena_battle_1': {'status': 'pending', 'id': 'original'}}}}
        if pending:
            scheduler.state.accounts['example']['navigation'] = {
                'black_market_exit': {'phase': 'close_requested', 'status': 'pending'}}
        scheduler.capture.side_effect = lambda: Image.new('RGB', (1920, 1032))
        scheduler.verify_identity.return_value = True, {'example': 1.}
        scheduler._combat_active_visible.return_value = False
        scheduler.main_ready.side_effect = lambda *_: state['page'] == 'leaf'
        scheduler.save_raw_evidence.return_value = 'evidence.png'
        def template(name, *_args, **_kwargs):
            visible = ((name == 'market_anchor.bmp' and state['page'] == 'market' and state['title'])
                       or (name == 'market_close.bmp' and state['page'] == 'market' and state['close'])
                       or (name == 'leaf_village_name_current.bmp' and state['page'] == 'leaf'))
            return visible, 1. if visible else 0., 1400, 250
        scheduler.template.side_effect = template
        def close(*_args, **_kwargs):
            state['page'] = 'leaf'
            return True
        scheduler.click_template.side_effect = close
        return scheduler, hs.Account(1, 'example', 180), state

    def test_market_x_returns_to_verified_main_and_preserves_unknown_actions(self):
        scheduler, account, _ = self.market_scheduler()
        original = deepcopy(scheduler.state.accounts[account.name]['actions'])
        self.assertTrue(nav.close_black_market(scheduler, account))
        scheduler.click_template.assert_called_once()
        self.assertEqual('market_close.bmp', scheduler.click_template.call_args.args[1])
        self.assertEqual(.98, scheduler.click_template.call_args.kwargs['threshold'])
        self.assertEqual(original, scheduler.state.accounts[account.name]['actions'])
        self.assertEqual('done', scheduler.state.accounts[account.name]['navigation']['black_market_exit']['status'])

    def test_pending_market_x_observes_without_repeating_input(self):
        scheduler, account, _ = self.market_scheduler(pending=True)
        with patch.object(nav.time, 'monotonic', side_effect=[0, 1, 13]):
            with self.assertRaises(hs.PendingError):
                nav.close_black_market(scheduler, account)
        scheduler.click_template.assert_not_called()

    def test_resume_after_market_x_requires_main_identity_without_another_click(self):
        scheduler, account, _ = self.market_scheduler(page='leaf', pending=True)
        self.assertTrue(nav.close_black_market(scheduler, account))
        scheduler.click_template.assert_not_called()
        self.assertGreaterEqual(scheduler.verify_identity.call_count, 3)

    def test_unrelated_pending_can_close_verified_market_without_settling_old_battle(self):
        scheduler, account, _ = self.market_scheduler()
        original = deepcopy(scheduler.state.accounts[account.name]['actions'])
        self.assertTrue(nav.return_to_leaf(scheduler, account))
        scheduler.click_template.assert_called_once()
        self.assertEqual(original, scheduler.state.accounts[account.name]['actions'])

    def test_market_wrong_identity_or_missing_x_or_combat_has_zero_input(self):
        for failure in ('identity', 'close', 'combat'):
            with self.subTest(failure=failure):
                scheduler, account, state = self.market_scheduler()
                if failure == 'identity':
                    scheduler.verify_identity.return_value = False, {}
                elif failure == 'close':
                    state['close'] = False
                else:
                    scheduler._combat_active_visible.return_value = True
                with self.assertRaises(hs.PendingError):
                    nav.close_black_market(scheduler, account)
                scheduler.click_template.assert_not_called()

    def test_market_without_title_or_an_unrecognized_page_cannot_close_a_generic_x(self):
        for page in ('market', 'unknown'):
            scheduler, account, state = self.market_scheduler(page=page)
            state['title'] = False
            self.assertFalse(nav.close_black_market(scheduler, account))
            scheduler.click_template.assert_not_called()

    def test_market_close_cannot_succeed_on_an_unknown_post_click_page(self):
        scheduler, account, state = self.market_scheduler()
        scheduler.click_template.side_effect = lambda *_args, **_kwargs: state.update(page='unknown') or True
        with patch.object(nav.time, 'monotonic', side_effect=[0, 1, 13]):
            with self.assertRaises(hs.PendingError):
                nav.close_black_market(scheduler, account)
        scheduler.click_template.assert_called_once()
        self.assertEqual('pending', scheduler.state.accounts[account.name]['navigation']['black_market_exit']['status'])


if __name__ == '__main__':
    unittest.main()
