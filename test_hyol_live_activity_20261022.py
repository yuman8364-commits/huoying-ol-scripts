"""Current rotating activity shell must never pass as an unobstructed village."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from PIL import Image
import hyol_scheduler as hs
import hyol_checkpoints as cp
import hyol_identity_overlays as overlays

TITLE='interference_recharge_title_20261022.bmp'
CLOSE='interference_recharge_close_20261022.bmp'

class ActivityShellTests(unittest.TestCase):
    def frame(self):
        frame=Image.new('RGB',(1920,1032),(10,30,55))
        frame.paste(Image.open(hs.ASSETS/TITLE),(944,265))
        frame.paste(Image.open(hs.ASSETS/CLOSE),(1306,303))
        return frame
    def scheduler(self):
        store=hs.TemplateStore(.98)
        return SimpleNamespace(template=lambda name,roi,frame,threshold=None:
            store.match(frame,hs.ASSETS/name,roi,threshold=threshold or .98))
    def test_title_and_independent_close_are_both_required(self):
        s=self.scheduler();frame=self.frame()
        found=overlays.inspect(s,frame)
        self.assertEqual(('recharge_activity',CLOSE,(1290,285,1350,350),13,14),found[:5])
        frame.paste((0,0,0),(1306,303,1332,331))
        self.assertEqual(('blocked_recharge_activity',),overlays.inspect(s,frame))
        frame=self.frame();frame.paste((0,0,0),(944,265,1176,307))
        self.assertIsNone(overlays.inspect(s,frame))
    def test_dim_glyphs_require_the_positive_shell_pair(self):
        frame=self.frame()
        glyphs=np.zeros((32,145),dtype=np.uint8)
        glyphs[12:24,5:130:2]=1
        strip=np.full((32,145,3),(10,30,55),dtype=np.uint8)
        strip[glyphs.astype(bool)]=77
        frame.paste(Image.fromarray(strip),(105,72))
        self.assertTrue(np.array_equal(glyphs,cp.current_identity_mask(frame)))
        frame.paste((0,0,0),(1306,303,1332,331))
        self.assertEqual(0,int(cp.current_identity_mask(frame).sum()))
    def test_visible_world_anchors_do_not_hide_this_modal(self):
        s=object.__new__(hs.Scheduler);frame=self.frame();present=[True]
        s.template=lambda name,*args,**kwargs:(name in ('anchor_top_menu.bmp','anchor_right_panel.bmp')
            or name==TITLE and present[0],1.,0,0)
        self.assertFalse(s.main_ready(frame))
        present[0]=False
        self.assertTrue(s.main_ready(frame))
    def test_recovery_only_uses_the_dedicated_cleanup_and_observes_its_exit(self):
        s=object.__new__(hs.Scheduler);before=self.frame();after=Image.new('RGB',(1920,1032))
        s.template=lambda name,roi,frame,**kwargs:(name==TITLE and frame is before,1.,0,0)
        account=hs.Account(1,'a',180)
        with patch.object(overlays,'dismiss_identity_overlays',return_value=after) as dismiss:
            self.assertTrue(s.recover_known_page(account,before))
        dismiss.assert_called_once_with(s,account,before,only_kind='recharge_activity')
        with patch.object(overlays,'dismiss_identity_overlays',return_value=before):
            self.assertFalse(s.recover_known_page(account,before))

class WindowBindingDiagnosticsTests(unittest.TestCase):
    def scheduler(self):
        s=object.__new__(hs.Scheduler)
        s.hwnd=0
        s.auto_foreground=False
        s.config={'AdaptiveGeometry':'1'}
        s.check_stop=Mock()
        s.event=Mock()
        return s

    def test_foreground_window_with_missing_or_invalid_surface_reports_geometry(self):
        for content, expected in ((hs.SafetyError('game content surface is ambiguous: 0 visible regions'),
                                   'game content surface is ambiguous'),
                                  ((1,69,2000,1031),'game surface is not contained')):
            with self.subTest(content=content):
                s=self.scheduler()
                with patch.object(hs.Win32,'game_windows',return_value=[123]), \
                     patch.object(hs.Win32,'rect',return_value=hs.EXPECTED_WINDOW), \
                     patch.object(hs.Win32,'foreground',return_value=123), \
                     patch.object(hs.Win32.user32,'IsIconic',return_value=False), \
                     patch.object(hs.Win32,'game_content_rect',**(
                         {'side_effect':content} if isinstance(content,Exception) else {'return_value':content})), \
                     patch.object(hs.Win32,'activate') as activate, \
                     patch.object(hs.Win32,'click') as click, \
                     patch.object(hs.time,'monotonic',side_effect=[0.,2.]), \
                     self.assertRaisesRegex(hs.SafetyError,expected) as failure:
                    s.bind_window(wait=True,timeout_seconds=1)
                self.assertNotIn('未取得前台焦点',str(failure.exception))
                self.assertEqual('window_wait_timeout',s.event.call_args.args[0])
                self.assertEqual('game_geometry_unavailable',s.event.call_args.kwargs['reason'])
                self.assertEqual(123,s.event.call_args.kwargs['foreground_hwnd'])
                self.assertIn('error_type',s.event.call_args.kwargs)
                activate.assert_not_called()
                click.assert_not_called()

    def test_real_foreground_mismatch_is_reported_as_focus_failure(self):
        s=self.scheduler()
        with patch.object(hs.Win32,'game_windows',return_value=[123]), \
             patch.object(hs.Win32,'rect',return_value=hs.EXPECTED_WINDOW), \
             patch.object(hs.Win32,'foreground',return_value=456), \
             patch.object(hs.Win32.user32,'IsIconic',return_value=False), \
             patch.object(hs.Win32,'game_content_rect',return_value=(1,69,1919,1031)), \
             patch.object(hs.time,'monotonic',side_effect=[0.,2.]), \
             self.assertRaisesRegex(hs.SafetyError,'当前前台456，游戏窗口123'):
            s.bind_window(wait=True,timeout_seconds=1)
        self.assertEqual('window_not_foreground',s.event.call_args.kwargs['reason'])

    def test_healthy_binding_is_unchanged(self):
        s=self.scheduler()
        with patch.object(hs.Win32,'game_windows',return_value=[123]), \
             patch.object(hs.Win32,'rect',return_value=hs.EXPECTED_WINDOW), \
             patch.object(hs.Win32,'foreground',return_value=123), \
             patch.object(hs.Win32.user32,'IsIconic',return_value=False), \
             patch.object(hs.Win32,'game_content_rect',return_value=(1,69,1919,1031)), \
             patch.object(hs.Win32,'pid',return_value=789):
            s.bind_window(wait=True,timeout_seconds=1)
        self.assertEqual(123,s.hwnd)
        self.assertEqual('window_bound',s.event.call_args.args[0])
        self.assertEqual(789,s.event.call_args.kwargs['pid'])

if __name__=='__main__':unittest.main()
