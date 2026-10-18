from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import hyol_scheduler as hs
import hyol_navigation as nav


class NavigationTests(unittest.TestCase):
    def test_team_toolbar_core_ignores_variable_translucent_border(self):
        original = Image.open(hs.ASSETS/'team_toolbar.bmp').convert('RGB')
        store = hs.TemplateStore(.98)
        for color in ((232,128,12),(10,35,100),(20,20,20)):
            with self.subTest(background=color):
                control = original.copy()
                for y in range(control.height):
                    for x in range(control.width):
                        if not (3<=x<17 and 5<=y<19):
                            control.putpixel((x,y),color)
                frame = Image.new('RGB',(1920,1032))
                frame.paste(control,(1704,82))
                match = store.match(frame,hs.ASSETS/'team_toolbar_core_current.bmp',
                                    (1685,70,1735,120),threshold=.98)
                self.assertTrue(match[0])
                self.assertEqual(match[2:],(1707,87))
                self.assertFalse(store.match(frame,hs.ASSETS/'team_toolbar.bmp',
                    (1685,70,1735,120),threshold=.88)[0])

    def test_team_room_prefers_strict_opaque_control(self):
        s = object.__new__(hs.Scheduler)
        s.click_template = Mock(return_value=True)
        s.open_team_room(hs.Account(1,'test',180),'open-room')
        s.click_template.assert_called_once()
        call = s.click_template.call_args
        self.assertEqual(call.args[1],'team_toolbar_core_current.bmp')
        self.assertEqual(call.args[3:5],(9,6))
        self.assertEqual(call.kwargs['threshold'],.98)

    def test_team_room_unknown_control_stays_pending_without_input(self):
        s = object.__new__(hs.Scheduler)
        s.click_template = Mock(return_value=False)
        with self.assertRaises(hs.PendingError):
            s.open_team_room(hs.Account(1,'test',180),'open-room')
        self.assertEqual(s.click_template.call_count,2)

    def test_team_room_keeps_existing_legacy_control_fallback(self):
        s = object.__new__(hs.Scheduler)
        s.click_template = Mock(side_effect=[False,True])
        s.open_team_room(hs.Account(1,'test',180),'open-room')
        self.assertEqual(s.click_template.call_args.args[1],'team_toolbar.bmp')
        self.assertEqual(s.click_template.call_args.kwargs['threshold'],.88)

    def test_unrelated_pending_allows_only_three_frame_readonly_home_confirmation(self):
        s, _ = self.scheduler('leaf')
        original = deepcopy(s.state.accounts['test']['actions'])
        self.assertTrue(nav.return_to_leaf(s, hs.Account(1, 'test', 180)))
        self.assertEqual(s.capture.call_count, 3)
        self.assertEqual(s.verify_identity.call_count, 3)
        s.click_template.assert_not_called()
        self.assertEqual(original, s.state.accounts['test']['actions'])
        self.assertEqual('leaf_verified', s.state.accounts['test']['navigation']['return_leaf']['phase'])

    def test_unrelated_pending_outside_leaf_still_forbids_navigation(self):
        s, _ = self.scheduler('world')
        original = deepcopy(s.state.accounts['test']['actions'])
        with self.assertRaisesRegex(hs.PendingError, '原任务核对结果'):
            nav.return_to_leaf(s, hs.Account(1, 'test', 180))
        s.click_template.assert_not_called()
        self.assertEqual(original, s.state.accounts['test']['actions'])

    def test_readonly_home_confirmation_cannot_accept_a_changed_identity_or_page(self):
        for changed_identity in (True, False):
            with self.subTest(changed_identity=changed_identity):
                s, state = self.scheduler('leaf')
                original = deepcopy(s.state.accounts['test']['actions'])
                if changed_identity:
                    s.verify_identity.side_effect = [(True, {}), (False, {})]
                else:
                    s.wait_interruptible.side_effect = lambda _: state.update(page='world')
                with self.assertRaises(hs.PendingError):
                    nav.return_to_leaf(s, hs.Account(1, 'test', 180))
                s.click_template.assert_not_called()
                self.assertEqual(original, s.state.accounts['test']['actions'])

    def scheduler(self, page='spring', phase=None):
        s = Mock(hwnd=None)
        s.state.accounts = {'test': {'actions': {'arena_battle': {'status':'pending'}}}}
        if phase:
            s.state.accounts['test']['navigation'] = {'hot_spring_exit': {'status':'pending','phase':phase}}
        state = {'page':page}
        s.capture.side_effect = lambda: Image.new('RGB', (1920,1032))
        s._combat_active_visible.return_value = False
        s.verify_identity.return_value = (True, {'test': 1.})
        s.main_ready.side_effect = lambda *_: state['page'] in ('world','leaf')
        def template(name,*_args,**_kwargs):
            keys = {'hot_spring_title_current.bmp':'spring','hot_spring_panel_close.bmp':'spring',
                    'friends_panel_close.bmp':'friends',
                    'hot_spring_leave_prompt_text.bmp':'prompt','hot_spring_leave_confirm.bmp':'prompt',
                    'managed_settings_title_current.bmp':'managed','managed_settings_close_current.bmp':'managed',
                    'home_leave.bmp':'home',
                    'leaf_return_current.bmp':'world','leaf_village_name_current.bmp':'leaf'}
            visible = state['page'] == keys.get(name)
            if name == 'hot_spring_leave_confirm.bmp' and state['page'] == 'unknown_prompt':
                visible = True
            return visible, 1., 0, 0
        s.template.side_effect = template
        def click(_account,name,*_args,**_kwargs):
            state['page'] = {'hot_spring_panel_close.bmp':'prompt',
                             'friends_panel_close.bmp':'leaf',
                             'hot_spring_leave_confirm.bmp':'world',
                             'managed_settings_close_current.bmp':'leaf',
                             'home_leave.bmp':'leaf',
                             'leaf_return_current.bmp':'leaf'}[name]
            return True
        s.click_template.side_effect = click
        s.save_raw_evidence.return_value = 'evidence.png'
        return s, state

    def test_friend_overlay_closes_once_and_confirms_leaf(self):
        s,_ = self.scheduler('friends')
        s.state.accounts['test']['actions'] = {}
        self.assertTrue(nav.return_to_leaf(s,hs.Account(1,'test',180)))
        s.click_template.assert_called_once()
        self.assertEqual(s.click_template.call_args.args[1],'friends_panel_close.bmp')
        self.assertEqual(s.click_template.call_args.args[2],(1870,260,1920,350))
        self.assertEqual(s.state.accounts['test']['navigation']['return_leaf']['phase'],'leaf_verified')

    def test_friend_overlay_close_never_repeats_without_confirmation(self):
        s,_ = self.scheduler('friends')
        s.state.accounts['test']['actions'] = {}
        s.click_template.side_effect = lambda *_args,**_kwargs:True
        with patch.object(nav.time,'monotonic',side_effect=[0,1,2,61]):
            with self.assertRaises(hs.PendingError):
                nav.return_to_leaf(s,hs.Account(1,'test',180))
        s.click_template.assert_called_once()

    def test_unrelated_pending_friend_overlay_keeps_zero_input(self):
        s,_ = self.scheduler('friends')
        original = deepcopy(s.state.accounts['test']['actions'])
        with self.assertRaisesRegex(hs.PendingError,'原任务核对结果'):
            nav.return_to_leaf(s,hs.Account(1,'test',180))
        s.click_template.assert_not_called()
        self.assertEqual(original,s.state.accounts['test']['actions'])

    def test_known_home_exit_preserves_combat_pending_and_verifies_leaf(self):
        s, _ = self.scheduler('home')
        original = deepcopy(s.state.accounts['test']['actions'])
        self.assertTrue(nav.return_to_leaf(s, hs.Account(1,'test',180), allow_pending_navigation=True))
        self.assertEqual(['home_leave.bmp'], [call.args[1] for call in s.click_template.call_args_list])
        self.assertEqual(original, s.state.accounts['test']['actions'])
        self.assertEqual('leaf_verified', s.state.accounts['test']['navigation']['return_leaf']['phase'])

    def test_home_exit_pending_never_repeats_and_unidentified_home_has_zero_input(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                s, _ = self.scheduler('home')
                if pending:
                    s.state.accounts['test']['navigation'] = {'return_leaf': {'status':'pending','phase':'home_exit_requested'}}
                else:
                    s.verify_identity.return_value = (False, {'test': .1})
                with patch.object(nav.time, 'monotonic', side_effect=[0, 1, 61]):
                    with self.assertRaises(hs.PendingError):
                        nav.return_to_leaf(s, hs.Account(1,'test',180), allow_pending_navigation=True)
                s.click_template.assert_not_called()

    def test_full_x_prompt_then_leaf_and_original_actions_preserved(self):
        s, _ = self.scheduler()
        original = deepcopy(s.state.accounts['test']['actions'])
        self.assertTrue(nav.return_to_leaf(s, hs.Account(1,'test',180), allow_pending_navigation=True))
        self.assertEqual(['hot_spring_panel_close.bmp','hot_spring_leave_confirm.bmp','leaf_return_current.bmp'],
                         [call.args[1] for call in s.click_template.call_args_list])
        self.assertEqual(original, s.state.accounts['test']['actions'])
        self.assertEqual('leaf_verified', s.state.accounts['test']['navigation']['return_leaf']['phase'])

    def test_pending_close_observes_prompt_and_never_repeats_x(self):
        s, _ = self.scheduler('prompt', 'close_requested')
        self.assertTrue(nav.leave_hot_spring(s, hs.Account(1,'test',180)))
        self.assertEqual(['hot_spring_leave_confirm.bmp'], [call.args[1] for call in s.click_template.call_args_list])

    def test_prior_spring_ready_survives_external_settings_shell(self):
        s, _ = self.scheduler('managed', 'ready')
        self.assertTrue(nav.return_to_leaf(s, hs.Account(1,'test',180), allow_pending_navigation=True))
        self.assertEqual(['managed_settings_close_current.bmp'], [call.args[1] for call in s.click_template.call_args_list])
        self.assertEqual('observed_external_exit', s.state.accounts['test']['navigation']['hot_spring_exit']['outcome'])

    def test_unknown_confirm_does_not_authorize_click(self):
        s, _ = self.scheduler('unknown_prompt', 'close_requested')
        with self.assertRaisesRegex(hs.PendingError, '正文尚未识别'):
            nav.leave_hot_spring(s, hs.Account(1,'test',180))
        s.click_template.assert_not_called()
        self.assertEqual('close_requested', s.state.accounts['test']['navigation']['hot_spring_exit']['phase'])

    def test_f12_keeps_requested_phase_without_new_capture(self):
        s, _ = self.scheduler()
        s.click_template.side_effect = KeyboardInterrupt('F12')
        with self.assertRaises(KeyboardInterrupt):
            nav.leave_hot_spring(s, hs.Account(1,'test',180))
        self.assertEqual(2, s.capture.call_count)
        self.assertEqual('close_requested', s.state.accounts['test']['navigation']['hot_spring_exit']['phase'])

    def test_external_world_observation_is_distinguished_from_script_exit(self):
        s, _ = self.scheduler('world', 'ready')
        self.assertTrue(nav.leave_hot_spring(s, hs.Account(1,'test',180)))
        rec = s.state.accounts['test']['navigation']['hot_spring_exit']
        self.assertEqual('observed_external_exit', rec['outcome'])
        s.click_template.assert_not_called()
        self.assertEqual(3, s.main_ready.call_count-1)  # Initial ready probe plus three confirmations.

    def test_requested_return_is_observed_without_duplicate_click(self):
        s, _ = self.scheduler('world')
        s.state.accounts['test']['actions'] = {}
        s.state.accounts['test']['navigation'] = {'return_leaf': {'status':'pending','phase':'return_requested'}}
        with patch.object(nav.time, 'monotonic', side_effect=[0, 1, 61]):
            with self.assertRaises(hs.PendingError):
                nav.return_to_leaf(s, hs.Account(1,'test',180))
        s.click_template.assert_not_called()
        self.assertEqual('pending', s.state.accounts['test']['navigation']['return_leaf']['status'])

    def test_actual_static_spring_title_rejects_leaf_frame(self):
        s = object.__new__(hs.Scheduler)
        s.templates = hs.TemplateStore(.92)
        s.detector = None
        spring = Image.open(hs.ROOT/'logs/preflight_20261003_232527_624315_2febdf4a.png').convert('RGB')
        leaf = Image.open(hs.ROOT/'logs/preflight_20261004_063821_062492_fbdc0dc8.png').convert('RGB')
        self.assertTrue(nav.hot_spring_visible(s, spring))
        self.assertFalse(nav.hot_spring_visible(s, leaf))

    def test_identity_change_during_return_preserves_pending_and_failure_frame(self):
        s, _ = self.scheduler('leaf')
        s.state.accounts['test']['actions'] = {}
        s.verify_identity.return_value = (False, {'test': .1})
        with self.assertRaisesRegex(hs.PendingError, '账号身份未确认'):
            nav.return_to_leaf(s, hs.Account(1, 'test', 180))
        s.click_template.assert_not_called()
        rec = s.state.accounts['test']['navigation']['return_leaf']
        self.assertEqual('pending', rec['status'])
        self.assertEqual('evidence.png', rec['pending_evidence'])
        self.assertEqual({'anchor_top_menu.bmp', 'anchor_right_panel.bmp'}, set(rec['anchor_scores']))

    def test_f12_during_return_saves_last_frame_without_recapture(self):
        s, _ = self.scheduler('leaf')
        s.state.accounts['test']['actions'] = {}
        s.wait_interruptible.side_effect = KeyboardInterrupt('F12')
        with self.assertRaises(KeyboardInterrupt):
            nav.return_to_leaf(s, hs.Account(1, 'test', 180))
        self.assertEqual(2, s.capture.call_count)
        self.assertEqual('pending', s.state.accounts['test']['navigation']['return_leaf']['status'])
        s.click_template.assert_not_called()


if __name__ == '__main__':
    unittest.main()
