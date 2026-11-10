"""Optional recognition failures must not bypass identity or send blind input."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from hyol_errors import PendingError, SafetyError, TaskUnavailable

ROOT = Path(__file__).resolve().parents[2] / 'client'


class OptionalDisplayTests(TestCase):
    def setUp(self):
        self.hs = SimpleNamespace(PendingError=PendingError, now_iso=lambda: 'test-time')
        self.nav = SimpleNamespace(LEAF_NAME_ROI=(), preparation_blocked=lambda s, f: False,
                                  organization_world_visible=lambda s, f: False, move_cursor_clear=Mock())
        modules = patch.dict('sys.modules', hyol_scheduler=self.hs, hyol_navigation=self.nav)
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location('isolated_player_display', ROOT/'hyol_player_display.py')
        self.display = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.display)
        self.account = SimpleNamespace(name='one', tab_x=100)
        self.row = {}
        self.s = SimpleNamespace(state=SimpleNamespace(accounts={'one': {'player_display': self.row}}, save_account=Mock()),
            capture=Mock(return_value='frame'), content_click_count=0, check_stop=Mock(),
            _generic_active_tab_x=Mock(return_value=100), verify_identity=Mock(return_value=(True, {})),
            main_ready=Mock(return_value=True), wait_interruptible=Mock(), click_template=Mock(),
            save_raw_evidence=Mock(return_value='evidence.png'), event=Mock())
        self.s.template=Mock(side_effect=lambda name,*args,**kw: (name=='leaf_village_name_current.bmp',1,0,0))

    def test_absent_optional_control_has_bounded_retry_and_no_click(self):
        result=self.display.ensure_hidden(self.s,self.account)
        self.assertTrue(result['skipped'])
        self.assertFalse(result['hidden'])
        self.assertEqual('skipped',self.row['status'])
        self.assertEqual(4,self.s.wait_interruptible.call_count)
        self.s.click_template.assert_not_called()

    def test_transient_absence_can_recover_without_lowering_threshold(self):
        with patch.object(self.display,'observe',side_effect=[None,None,('closed',1,2),('closed',1,2),('closed',1,2)]):
            result=self.display.ensure_hidden(self.s,self.account)
        self.assertTrue(result['hidden'])
        self.assertEqual(3,len(result['samples']))
        self.s.click_template.assert_not_called()

    def test_wrong_identity_or_focus_still_stops(self):
        for field,value in [('verify_identity',(False,{})),('_generic_active_tab_x',200)]:
            with self.subTest(field=field), patch.object(self.s,field,return_value=value):
                with self.assertRaises(PendingError):self.display.ensure_hidden(self.s,self.account)
        self.s.click_template.assert_not_called()

    def test_original_unconfirmed_click_is_not_discarded(self):
        self.row.update(status='pending',phase='click_requested')
        with self.assertRaises(PendingError):self.display.ensure_hidden(self.s,self.account)
        self.assertEqual('click_requested',self.row['phase'])
        self.s.click_template.assert_not_called()

    def test_preparation_does_not_claim_hidden_after_skip(self):
        import hyol_preparation
        self.s.config={}
        row={}
        with patch.dict('sys.modules',hyol_player_display=self.display):
            hyol_preparation.hide_first_world(self.s,self.account,'frame',row)
        self.assertTrue(row['player_display_first']['skipped'])
        self.assertEqual('preparation_player_display_skipped',self.s.event.call_args.args[0])


class OptionalDailyEntryTests(TestCase):
    def setUp(self):
        # Execute the real entry method without importing the Windows scheduler.
        source=ROOT/'hyol_daily_tasks.py'
        tree=ast.parse(source.read_text('utf-8-sig'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DailyRunner')
        enter=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='enter')
        namespace=dict(hs=SimpleNamespace(PendingError=PendingError),TaskUnavailable=TaskUnavailable,
                       PAGE_ANCHORS={'group':'group_anchor'},PAGE_ROIS={'group':()},DAILY_ROI=(),ROW_ROI=(),
                       LABELS={'group':'组队副本'})
        exec(compile(ast.Module(body=[enter],type_ignores=[]),str(source),'exec'),namespace)
        self.enter=namespace['enter']
        self.s=SimpleNamespace(main_ready=Mock(return_value=True),wait_template=Mock(return_value=False),wait_interruptible=Mock())
        self.runner=SimpleNamespace(scheduler=self.s,visible=Mock(return_value=False),click=Mock(),wait=Mock(),event=Mock())
        self.runner.visible.side_effect=lambda name,*args,**kw: name=='daily_practice_anchor.bmp'

    def test_missing_list_row_skips_before_participation(self):
        with self.assertRaises(TaskUnavailable):self.enter(self.runner,'group')
        self.runner.click.assert_not_called()

    def test_disabled_participation_skips_without_starting(self):
        self.runner.visible.side_effect=lambda name,*a,**k: name in ('daily_practice_anchor.bmp','daily_group_current_row.bmp','daily_participate_disabled_current.bmp')
        with self.assertRaises(TaskUnavailable):self.enter(self.runner,'group')
        self.assertEqual(['daily_group_current_row.bmp'],[c.args[0] for c in self.runner.click.call_args_list])

    def test_unverified_page_still_raises_blocking_pending_error(self):
        self.runner.visible.side_effect=None
        self.runner.visible.return_value=False
        self.s.main_ready.return_value=False
        with self.assertRaises(PendingError) as caught:self.enter(self.runner,'group')
        self.assertNotIsInstance(caught.exception,TaskUnavailable)
        self.runner.click.assert_not_called()
