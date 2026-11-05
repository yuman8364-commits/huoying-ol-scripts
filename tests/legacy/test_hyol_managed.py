import unittest
from unittest.mock import Mock, patch

from PIL import Image
import hyol_scheduler as hs
import hyol_managed as managed


class ManagedSafetyTests(unittest.TestCase):
    def exit_scheduler(self):
        s = object.__new__(hs.Scheduler)
        s.templates, s.detector = hs.TemplateStore(.90), None
        frame = Image.open(hs.ROOT/'logs/scheduler_evidence/2026-10-05/account_3ed81e0fcf/041222_640379_pending_solo_mail_pending.png').convert('RGB')
        s.capture = Mock(return_value=frame)
        s.switch_account = Mock()
        s.managed_active_score = Mock(return_value=0)
        s.state = Mock()
        s.state.accounts = {'test': {'actions': {'arena_old': {'status':'pending'}}}}
        s.click_template = Mock(return_value=True)
        s.wait_absent = Mock(return_value=True)
        s.wait_template = Mock(return_value=True)
        s.wait_until_main = Mock(return_value=True)
        s.wait_interruptible = Mock()
        s.event = Mock()
        s.ensure_assets = Mock()
        return s, frame

    def test_stopped_real_panel_is_closed_without_repeating_exit(self):
        s, frame = self.exit_scheduler()
        self.assertTrue(managed.stopped_panel_visible(s, frame))
        hs.Scheduler.exit_managed_for_market(s, hs.Account(1,'test',180))
        self.assertEqual(['managed_settings_close_current.bmp'], [call.args[1] for call in s.click_template.call_args_list])
        self.assertEqual({'arena_old': {'status':'pending'}}, s.state.accounts['test']['actions'])
        self.assertEqual('done', s.state.accounts['test']['managed_exit']['status'])

    def test_pending_exit_observes_old_request_before_closing(self):
        s, _frame = self.exit_scheduler()
        s.state.accounts['test']['managed_exit'] = {'status':'pending','phase':'exit_requested'}
        s.managed_active_score.side_effect = [1,1,0,0,0]
        hs.Scheduler.exit_managed_for_market(s, hs.Account(1,'test',180))
        self.assertEqual(['managed_settings_close_current.bmp'], [call.args[1] for call in s.click_template.call_args_list])

    def test_loading_cannot_prove_managed_stopped_or_close_a_panel(self):
        s, _frame = self.exit_scheduler()
        loading = Image.open(hs.ROOT/'logs/preflight_20261005_040859_865526_b9a474a2.png').convert('RGB')
        self.assertFalse(managed.stopped_panel_visible(s, loading))
        s.capture.return_value = loading
        s.state.accounts['test']['managed_exit'] = {'status':'pending','phase':'exit_requested'}
        s.managed_active_score.return_value = 1
        with patch.object(hs.time, 'monotonic', side_effect=[0,0,13]):
            with self.assertRaisesRegex(hs.PendingError, '退出托管尚未三帧确认'):
                hs.Scheduler.exit_managed_for_market(s, hs.Account(1,'test',180))
        self.assertNotIn('managed_settings_close_current.bmp', [call.args[1] for call in s.click_template.call_args_list])
        self.assertEqual('pending', s.state.accounts['test']['managed_exit']['status'])

    def scheduler(self, checked=True):
        s = Mock()
        s.capture.return_value = Image.new('RGB', (1920,1032))
        s.state.accounts = {'test': {'actions': {}}}
        states = {key: checked for key,_,_ in managed.OPTIONS}
        rois = {roi:key for key,roi,_ in managed.OPTIONS}
        def template(name, roi, *_args, **_kwargs):
            if name == managed.TITLE:
                return True, 1., *managed.TITLE_ORIGIN
            on = True
            if name == managed.CHECKED:
                on = states[rois[roi]]
            elif name == managed.UNCHECKED:
                on = not states[rois[roi]]
            return on, 1., roi[0], roi[1]
        s.template.side_effect = template
        def click(_account, _name, roi, *_args, **_kwargs):
            self.assertTrue(states[rois[roi]], 'Never enable an off setting')
            states[rois[roi]] = False
            return True
        s.click_template.side_effect = click
        return s, states

    def test_checked_options_are_preserved_without_settings_clicks(self):
        s, states = self.scheduler()
        with patch.object(managed, 'stopped_panel_visible', return_value=True):
            rec = managed.prepare_non_spending_options(s, hs.Account(1,'test',180))
        self.assertEqual('preserve', rec['policy'])
        self.assertEqual(0, rec['settings_clicks'])
        self.assertTrue(all(states.values()))
        s.click_template.assert_not_called()
        s.state.save_account.assert_not_called()

    def test_restart_keeps_prior_settings_record_and_all_options(self):
        s, states = self.scheduler(False)
        original = {'status':'pending','phase':'disable_market'}
        s.state.accounts['test']['managed_settings'] = original.copy()
        with patch.object(managed, 'stopped_panel_visible', return_value=True):
            managed.preserve_existing_options(s, hs.Account(1,'test',180))
        self.assertEqual(original, s.state.accounts['test']['managed_settings'])
        self.assertFalse(any(states.values()))
        s.click_template.assert_not_called()

    def test_unknown_checkbox_states_do_not_block_or_change_existing_settings(self):
        s, _ = self.scheduler()
        s.template.side_effect = AssertionError('checkboxes must not be inspected')
        with patch.object(managed, 'stopped_panel_visible', return_value=True):
            managed.preserve_existing_options(s, hs.Account(1,'test',180))
        s.click_template.assert_not_called()

    def test_unknown_start_panel_never_authorizes_start(self):
        s, _ = self.scheduler()
        with patch.object(managed, 'stopped_panel_visible', return_value=False):
            with self.assertRaisesRegex(hs.PendingError, '标题或开始按钮'):
                managed.preserve_existing_options(s, hs.Account(1,'test',180))
        s.click_template.assert_not_called()

    def test_f12_does_not_capture_or_change_settings_after_stop(self):
        s, _ = self.scheduler()
        s.capture.side_effect = KeyboardInterrupt('F12')
        with self.assertRaises(KeyboardInterrupt):
            managed.preserve_existing_options(s, hs.Account(1,'test',180))
        self.assertEqual(1, s.capture.call_count)
        s.click_template.assert_not_called()

    def test_pending_original_battle_blocks_start(self):
        s = Mock()
        s.managed_active_score.return_value = 0
        s.config = {'ManagedSafetyVerified':'1'}
        s.state.accounts = {'test': {'actions': {'arena_casual_battle_4': {'status':'pending'}}}}
        with self.assertRaisesRegex(hs.PendingError, '原操作结果尚未核实'):
            hs.Scheduler.run_managed_gate(s, hs.Account(1,'test',180))
        s.open_managed_panel_with_retry.assert_not_called()
        s.click_template.assert_not_called()

    def test_real_settings_and_foreign_page_states(self):
        s = object.__new__(hs.Scheduler)
        s.templates = hs.TemplateStore(.92)
        s.detector = None
        positive = Image.open(hs.ROOT/'logs/scheduler_evidence/2026-10-04/account_3ed81e0fcf/070108_235150_pending_managed_panel_validation.png').convert('RGB')
        negative = Image.open(hs.ROOT/'logs/preflight_20261004_063821_062492_fbdc0dc8.png').convert('RGB')
        for key, box, label in managed.OPTIONS:
            self.assertEqual('checked', managed.option_state(s, key, box, label, positive))
            with self.assertRaises(hs.PendingError):
                managed.option_state(s, key, box, label, negative)
        self.assertFalse(s.main_ready(positive), 'A managed settings overlay cannot authorize behind-dialog clicks')
        off = Image.open(hs.ROOT/'logs/scheduler_evidence/2026-10-04/account_3ed81e0fcf/070610_533475_verified_managed_spending_disabled.png').convert('RGB')
        for key, box, label in managed.OPTIONS:
            self.assertEqual('unchecked', managed.option_state(s, key, box, label, off))


if __name__ == '__main__':
    unittest.main()
