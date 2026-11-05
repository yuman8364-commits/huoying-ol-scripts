import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock

import hyol_multi_solo_gui as gui


class MultiSoloGuiTests(unittest.TestCase):
    def setUp(self):
        clearer=patch('hyol_diagnostics.clear_manual_pause')
        clearer.start();self.addCleanup(clearer.stop)
        lease=patch.object(gui.hs,'ForegroundRuntimeLease')
        lease.start();self.addCleanup(lease.stop)

    def test_worker_error_has_traceback_for_pythonw(self):
        app = object.__new__(gui.MultiSoloApp)
        app.events = queue.Queue()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(gui.hs, 'LOGS', Path(folder)), \
             patch.object(gui, 'preflight_live', side_effect=RuntimeError('preflight failed')):
            app.run_worker(2, ('ranking',))
            diagnostic = Path(folder) / 'multi_solo_gui_errors.jsonl'
            self.assertTrue(diagnostic.exists())
            self.assertIn('RuntimeError', diagnostic.read_text(encoding='utf-8'))
            self.assertIn(str(diagnostic), app.events.get_nowait()[1])

    def test_account_count_input_is_bounded_by_configured_identities(self):
        self.assertEqual(3, gui.parse_account_count(' 3 ', 6))
        for value in ('0', '7', 'abc'):
            with self.assertRaises(ValueError):
                gui.parse_account_count(value, 6)

    def test_unhealthy_preflight_never_starts_runner(self):
        app = object.__new__(gui.MultiSoloApp)
        app.events = queue.Queue()
        with patch.object(gui, 'preflight_live', return_value={
                'status': 'unhealthy', 'healthy': False, 'main_ready': False,
                'content_clicks': 0}), patch.object(gui, 'MultiSoloRunner') as runner:
            app.run_worker(3, ('mail',))
        runner.assert_not_called()
        self.assertIn('未执行任务点击', app.events.get_nowait()[1])

    def test_valid_preflight_passes_selected_count_and_tasks(self):
        app = object.__new__(gui.MultiSoloApp)
        app.events = queue.Queue()
        with patch.object(gui, 'preflight_live', return_value={
                'status': 'ready', 'healthy': True, 'main_ready': False,
                'content_clicks': 0}), patch.object(gui, 'MultiSoloRunner') as runner:
            runner.return_value.run.return_value = 0
            app.run_worker(3, ('mail', 'welfare', 'arena'), 7)
        self.assertEqual(3, runner.call_args.kwargs['account_count'])
        self.assertEqual(7, runner.call_args.kwargs['arena_wins'])
        self.assertEqual('keep', runner.call_args.kwargs['formation'])
        self.assertEqual(('mail', 'welfare', 'arena'), runner.call_args.args[0])
        self.assertIn('完成', app.events.get_nowait()[1] + app.events.get_nowait()[1])

    def test_arena_wins_input_is_bounded(self):
        for value in ('1', '5', '10'):
            self.assertEqual(int(value), gui.parse_arena_wins(value))
        for value in ('0', '11', 'abc'):
            with self.assertRaises(ValueError):
                gui.parse_arena_wins(value)

    def test_actual_checkbox_default_and_worker_arguments(self):
        app = gui.MultiSoloApp()
        app.withdraw()
        self.addCleanup(app.destroy)
        self.assertTrue(app.managed_enabled.get())
        self.assertFalse(app.managed_control.instate(('disabled',)))
        app.managed_enabled.set(False)
        with patch.object(gui.threading,'Thread') as thread:
            app.start()
        self.assertFalse(thread.call_args.kwargs['args'][3])

    def test_worker_forwards_disabled_managed_option(self):
        app = object.__new__(gui.MultiSoloApp)
        app.events = queue.Queue()
        with patch.object(gui,'preflight_live',return_value=dict(status='ready',healthy=True,content_clicks=0)),patch.object(gui,'MultiSoloRunner') as runner:
            runner.return_value.run.return_value = 0
            app.run_worker(2,('mail',),5,False)
        self.assertIs(False,runner.call_args.kwargs['managed_enabled'])


if __name__ == '__main__':
    unittest.main()
