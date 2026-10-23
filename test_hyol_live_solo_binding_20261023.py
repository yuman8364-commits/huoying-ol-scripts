"""Real DailyScheduler transitions must not depend on team-only methods."""
from pathlib import Path
from unittest.mock import Mock, patch
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

import hyol_scheduler as hs
from hyol_checkpoints import encode_mask
from hyol_daily_tasks import DailyScheduler
from hyol_run_context import RunContext


class SoloCombatBindingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.context = RunContext(Path(folder.name), Path(folder.name) / 'state')
        self.frame = Image.new('RGB', (1920, 1032), 'white')
        ImageDraw.Draw(self.frame).text((145, 10), 'bound game', fill='black')
        mask = (np.random.default_rng(23).random((32,145)) > .5).astype(np.uint8)
        self.context.remember((180,), {1:'a'}, {1:[mask]*3}, {1:self.frame.copy()},
            {'a':dict(mask=encode_mask(mask), shape=list(mask.shape))}, {})
        self.scheduler = object.__new__(DailyScheduler)
        s = self.scheduler
        s.run_context = self.context
        s.accounts = [hs.Account(1, 'a', 180)]
        s.by_name = {'a':s.accounts[0]}
        s.available_account_names = {'a'}
        s.state = hs.StateStore(['a'], directory=self.context.directory, restore_history=False)
        s.templates = {}
        s.active = None
        s.generic_current_tab_x = None
        s.current_account_only = None
        s._capture_account = None
        s.capture = Mock(side_effect=lambda:self.frame)
        s.check_stop = Mock()
        s.wait_interruptible = Mock()
        s._validate_tab_binding = Mock()
        s._generic_active_tab_x = Mock(return_value=180)
        s.offline_dialog_visible = Mock(return_value=False)
        s.healthy = Mock(return_value=False)
        s.verify_identity = Mock(return_value=(False, {}))
        s.chrome_click = Mock()
        s.click_point = Mock()
        s.event = Mock()
        s.save_raw_evidence = Mock(return_value='pending.png')
        s.template = Mock(side_effect=lambda name,*args,**kwargs:
            (name in {'map_loading_word_current.bmp','map_loading_finished_current.bmp'},1.,0,0))
        self.context.layout_matches = Mock(return_value=True)
        hidden = patch('hyol_checkpoints.current_identity_mask', return_value=np.zeros((32,145),dtype=np.uint8))
        self.identity_mask = hidden.start()
        self.addCleanup(hidden.stop)

    def observe(self, mode='ranking', running=True):
        return self.scheduler.switch_account_for_combat(self.scheduler.accounts[0], mode, running)

    def test_real_daily_scheduler_observes_loading_for_each_running_combat_mode(self):
        self.assertFalse(hasattr(self.scheduler, '_bound_role_verified'))
        self.assertFalse(hasattr(self.scheduler, '_tab_title_masks'))
        for mode in ('ranking','survival','arena'):
            with self.subTest(mode=mode):
                self.assertIs(self.observe(mode), self.frame)
        self.scheduler.chrome_click.assert_not_called()
        self.scheduler.click_point.assert_not_called()
        self.assertTrue(all(call.kwargs.get('content_authorized') is False
                            for call in self.scheduler.event.call_args_list))

    def test_hidden_battle_without_map_loading_remains_observation_only(self):
        self.scheduler.template.return_value = (False,0.,0,0)
        self.scheduler.template.side_effect = None
        self.assertIs(self.observe('arena'), self.frame)
        self.assertFalse(self.scheduler.event.call_args.kwargs['content_authorized'])
        self.scheduler.click_point.assert_not_called()

    def test_visible_conflicting_role_is_never_treated_as_hidden(self):
        self.identity_mask.return_value = np.ones((32,145),dtype=np.uint8)
        with self.assertRaises(hs.PendingError):
            self.observe()
        self.scheduler.event.assert_not_called()
        self.assertIsNone(self.scheduler.active)

    def test_changed_title_is_not_accepted_even_with_loading_art(self):
        self.frame.paste('black', (132,5,204,30))
        with self.assertRaises(hs.PendingError):
            self.observe()
        self.scheduler.event.assert_not_called()

    def test_equal_empty_tab_titles_allow_only_original_running_observation(self):
        self.frame.paste('white', (132,5,204,30))
        self.context.binding['frames'][1] = self.frame.copy()
        self.assertIs(self.observe(), self.frame)
        self.assertFalse(self.scheduler.event.call_args.kwargs['content_authorized'])
        self.scheduler.click_point.assert_not_called()

    def test_empty_reference_does_not_accept_a_new_title(self):
        self.context.binding['frames'][1].paste('white', (132,5,204,30))
        with self.assertRaises(hs.PendingError):
            self.observe()
        self.scheduler.event.assert_not_called()

    def test_missing_reference_or_mismatched_owner_stays_pending(self):
        for change in ('reference','owner'):
            with self.subTest(change=change):
                if change == 'reference':
                    self.context.binding['frames'].clear()
                else:
                    self.context.binding['frames'][1] = self.frame.copy()
                    self.context.binding['mapping'][1] = 'other'
                with self.assertRaises(hs.PendingError):
                    self.observe()
        self.scheduler.event.assert_not_called()

    def test_ready_request_cannot_take_hidden_running_observation_path(self):
        with self.assertRaises(hs.PendingError):
            self.observe(running=False)
        self.scheduler.event.assert_not_called()

    def test_ready_survival_page_allows_bound_observation_without_content_authority(self):
        self.scheduler.template.side_effect=lambda name,*args,**kwargs:(name=='survival_anchor.bmp',1.,0,0)
        self.assertIs(self.observe('survival',running=False),self.frame)
        self.assertEqual(self.scheduler.event.call_args.kwargs['phase'],'combat_ready')
        self.assertFalse(self.scheduler.event.call_args.kwargs['content_authorized'])
        self.scheduler.click_point.assert_not_called()

    def test_other_mode_ready_page_cannot_authorize_survival_selection(self):
        self.scheduler.template.side_effect=lambda name,*args,**kwargs:(
            name in ('ranking_page.bmp','ranking_counter_label.bmp'),1.,0,0)
        with self.assertRaises(hs.PendingError):
            self.observe('survival',running=False)
        self.scheduler.event.assert_not_called()

    def test_positive_ready_page_cannot_hide_a_visible_conflicting_role(self):
        self.identity_mask.return_value=np.ones((32,145),dtype=np.uint8)
        self.scheduler.template.side_effect=lambda name,*args,**kwargs:(name=='survival_anchor.bmp',1.,0,0)
        with self.assertRaises(hs.PendingError):
            self.observe('survival',running=False)
        self.scheduler.event.assert_not_called()


if __name__ == '__main__':
    unittest.main()
