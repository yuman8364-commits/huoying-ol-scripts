"""Observed startup/navigation faults and non-overlapping timing accounting."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PIL import Image

import hyol_accounts as accounts
import hyol_scheduler as hs
import hyol_performance as performance
import hyol_checkpoints as checkpoints
import numpy as np


class PartialIdentityTests(unittest.TestCase):
    def test_known_modal_scales_brightness_and_saturation_without_weakening_identity(self):
        normal=Image.new('RGB',(1920,1032))
        strip=np.full((32,145,3),(60,140,200),dtype=np.uint8)
        strip[12:20,20:80]=255
        strip[22:29,30:100]=150
        normal.paste(Image.fromarray(strip),(105,72))
        with patch.object(checkpoints,'_identity_layout',return_value=(hs.IDENTITY_ROI,120)):
            expected=checkpoints.current_identity_mask(normal)
        for factor in (.3,.5,.7):
            frame=Image.new('RGB',(1920,1032))
            frame.paste(Image.fromarray((strip*factor).astype(np.uint8)),(105,72))
            with patch.object(checkpoints,'_identity_layout',return_value=(hs.IDENTITY_ROI,56)):
                np.testing.assert_array_equal(expected,checkpoints.current_identity_mask(frame))
            with patch.object(checkpoints,'_identity_layout',return_value=(hs.IDENTITY_ROI,120)):
                self.assertLess(int(checkpoints.current_identity_mask(frame).sum()),int(expected.sum()))

    def test_same_server_different_name_cannot_make_exact_online_role_ambiguous(self):
        first=np.zeros((32,145),dtype=np.uint8)
        first[12:, :18]=1
        first[12:, 18:118]=1
        other=first.copy()
        other[:,18:52]=0
        refs={key:dict(mask=checkpoints.encode_mask(value),shape=list(value.shape))
              for key,value in (('online',first),('absent_same_server',other))}
        scores=checkpoints.identity_mask_scores(first,refs)
        old_score=.6*hs.mask_f1(first[:,:18],other[:,:18])+.4*hs.mask_f1(first,other)
        self.assertGreaterEqual(old_score,.92)
        self.assertLess(1-old_score,.08)
        self.assertLess(scores['absent_same_server'],.92)
        mapping,_,gap=checkpoints.joint_identity_assignment({1:first},refs)
        self.assertEqual({1:'online'},mapping)
        self.assertGreaterEqual(gap,.08)
        # Real duplicate names and duplicate visible accounts still fail closed.
        duplicate=dict(refs,duplicate=refs['online'])
        self.assertEqual({},checkpoints.joint_identity_assignment({1:first},duplicate)[0])
        self.assertEqual({},checkpoints.joint_identity_assignment({1:first,2:first},refs)[0])
        self.assertEqual({},checkpoints.joint_identity_assignment({1:np.zeros_like(first)},refs)[0])


class QueueRecognitionTests(unittest.TestCase):
    def test_dark_green_world_is_not_a_queue_but_enrolled_title_is(self):
        frame = Image.new('RGB', (1920,1032), (45,90,20))
        s = object.__new__(hs.Scheduler)
        self.assertFalse(s._arena_waiting_battle_visible(frame))
        frame.paste(Image.open(hs.ASSETS/'arena_waiting_title_current.bmp'),(820,472))
        self.assertTrue(s._arena_waiting_battle_visible(frame))
        frame.paste(Image.new('RGB',(245,57),(45,90,20)),(820,472))
        self.assertFalse(s._arena_waiting_battle_visible(frame))


class StartupTabTests(unittest.TestCase):
    def scheduler(self):
        frame=Image.new('RGB',(1920,1032))
        clock={'now':0.0}
        s=SimpleNamespace(check_stop=Mock(), capture=Mock(return_value=frame),
            _generic_active_tab_x=Mock(side_effect=[hs.PendingError('redraw'),180,180]),
            click_template=Mock(),chrome_click=Mock(),
            wait_interruptible=lambda seconds: clock.update(now=clock['now']+seconds))
        return s, frame, clock

    def test_repaint_is_observed_before_role_collection_without_reclick(self):
        s,frame,clock=self.scheduler()
        with patch('hyol_multi_solo.tab_close_positions',return_value=(214,)), \
             patch.object(accounts.time,'monotonic',side_effect=lambda:clock['now']):
            self.assertIs(frame,accounts.wait_observed_tab(s,hs.Account(1,'a',180),frame,(214,)))
        self.assertGreaterEqual(clock['now'],.4)
        s.click_template.assert_not_called()
        s.chrome_click.assert_not_called()

    def test_layout_change_and_unselected_timeout_never_collect_role(self):
        s,frame,clock=self.scheduler()
        with patch('hyol_multi_solo.tab_close_positions',return_value=(349,)):
            with self.assertRaises(hs.PendingError):
                accounts.wait_observed_tab(s,hs.Account(1,'a',180),frame,(214,))
        s._generic_active_tab_x=Mock(return_value=315)
        with patch('hyol_multi_solo.tab_close_positions',return_value=(214,)), \
             patch.object(accounts.time,'monotonic',side_effect=lambda:clock['now']):
            with self.assertRaises(hs.PendingError):
                accounts.wait_observed_tab(s,hs.Account(1,'a',180),frame,(214,))
        self.assertGreaterEqual(clock['now'],3)
        s.click_template.assert_not_called()
        s.chrome_click.assert_not_called()


class TimingTests(unittest.TestCase):
    def test_replaced_ledger_is_measured_once_by_current_worker_and_restored(self):
        clock={'now':0.0}
        class Store:
            def save_account(self,*args):clock['now']+=.2
            def save_global(self,*args):clock['now']+=.3
        original=Store.save_account
        def scheduler(name):
            s=SimpleNamespace(active=SimpleNamespace(name=name),state=Store(),
                templates=SimpleNamespace(match=Mock()),_performance_task='identity_binding')
            for method in ('capture','template','switch_account','switch_account_for_combat',
                           'wait_interruptible','click_template','click_point','save_raw_evidence'):
                setattr(s,method,Mock())
            return s
        a,b=scheduler('a'),scheduler('b')
        with patch.object(performance.time,'perf_counter',side_effect=lambda:clock['now']):
            first=performance.PerformanceMeter(a)
            try:
                a.state=Store();a.state.save_account('a')
                second=performance.PerformanceMeter(b)
                try:
                    b.state=Store();b.state.save_global()
                    self.assertEqual(1,first.snapshot()['save_account']['calls'])
                    self.assertNotIn('save_global',first.snapshot())
                    self.assertAlmostEqual(.3,second.snapshot()['save_global']['exclusive_seconds'])
                finally:second.close()
                a.state.save_global()
                self.assertAlmostEqual(.5,sum(row['exclusive_seconds'] for row in first.snapshot().values()))
            finally:first.close()
        self.assertIs(original,Store.save_account)

    def test_nested_time_is_exclusive_and_scoped_to_account_task(self):
        clock={'now':0.0}
        def spend(seconds):clock['now']+=seconds
        s=SimpleNamespace(active=SimpleNamespace(name='a'),_performance_task='survival',
                          templates=SimpleNamespace(match=lambda:spend(.3)))
        def template():spend(.2);return s.templates.match()
        def capture():spend(.1);return s.template()
        s.template,s.capture=template,capture
        for name in ('switch_account','switch_account_for_combat','wait_interruptible',
                     'click_template','click_point','save_raw_evidence'):
            setattr(s,name,Mock())
        with patch.object(performance.time,'perf_counter',side_effect=lambda:clock['now']):
            meter=performance.PerformanceMeter(s)
            try:
                s.capture()
                rows=meter.snapshot()
                self.assertAlmostEqual(.6,sum(row['exclusive_seconds'] for row in rows.values()))
                self.assertGreater(sum(row['seconds'] for row in rows.values()),.6)
                scope=meter.scope_snapshot()[0]
                self.assertEqual(('a','survival'),(scope['account'],scope['task']))
                s.active=SimpleNamespace(name='b');s._performance_task='ranking';s.capture()
                self.assertEqual({('a','survival'),('b','ranking')},
                                 {(row['account'],row['task']) for row in meter.scope_snapshot()})
            finally:meter.close()
        self.assertIs(capture,s.capture)
        self.assertIs(template,s.template)


if __name__=='__main__':unittest.main()
