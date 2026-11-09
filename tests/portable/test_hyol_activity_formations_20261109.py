"""Activity preset priorities, no-change fallback, and one fresh group entry."""
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest
from PIL import Image
import hyol_scheduler as hs
from hyol_daily_tasks import DailyRunner,PAGE_ANCHORS


class FormationPriorityTests(unittest.TestCase):
    def runner(self,available,active=None,full=True,fast_label='速刷'):
        state={'active':active}
        frame=Image.new('RGB',(1920,1032));frame.paste((170,120,35),(490,320,595,349))
        r=object.__new__(DailyRunner);r.account=hs.Account(2,'owner',315);r.records={};r.current='group'
        r.record=lambda task:r.records.setdefault(task,{})
        r.save=Mock();r.event=Mock()
        r.scheduler=SimpleNamespace(capture=Mock(return_value=frame),main_ready=Mock(return_value=True),
            wait_template=Mock(return_value=True),wait_interruptible=Mock(),
            wait_main_after_close=Mock(return_value=True),save_raw_evidence=Mock(return_value='preset-proof.png'))
        def visible(name,*args):
            if name in ('ninja_page_current.bmp','ninja_close_current.bmp'):return True
            if name=='ninja_four_deployed_current.bmp':return full
            if name=='ninja_main_selected_current.bmp':return state['active']=='main'
            if name=='ninja_main_unselected_current.bmp':return 'main' in available
            if name=='ninja_fast_unselected_current.bmp':return 'fast' in available
            return False
        r.visible=Mock(side_effect=visible)
        def click(*args,**kw):
            if '_select_fast_formation' in args[4]:state['active']='fast'
            if '_select_main_formation' in args[4]:state['active']='main'
        r.click=Mock(side_effect=click)
        def ocr(*args):
            return {'lines':[dict(text=fast_label,words=[dict(y=30)])]} if state['active']=='fast' else {'lines':[]}
        return r,ocr

    def test_exact_activity_priorities_and_missing_presets_keep(self):
        cases=(('strong',{'main','fast'},'main'),('strong',{'fast'},'keep'),
               ('group',{'main','fast'},'fast'),('group',{'main'},'main'),('group',set(),'keep'))
        for activity,available,expected in cases:
            with self.subTest(activity=activity,available=available):
                r,ocr=self.runner(available)
                with patch('hyol_ocr.recognize',side_effect=ocr):result=r.ensure_activity_formation(activity)
                self.assertEqual(expected,result['preset'])
                selections=[call.args[4] for call in r.click.call_args_list if '_select_' in call.args[4]]
                self.assertEqual(0 if expected=='keep' else 1,len(selections))
                self.assertEqual(1,r.scheduler.wait_main_after_close.call_count)

    def test_named_selected_fast_is_kept_but_shark_is_not_fast(self):
        for label,expected in (('速刷','fast'),('鲨鱼','main')):
            with self.subTest(label=label):
                r,ocr=self.runner({'main'},active='fast',fast_label=label)
                with patch('hyol_ocr.recognize',side_effect=ocr):result=r.ensure_activity_formation('group')
                self.assertEqual(expected,result['preset'])
                self.assertEqual(label!='速刷',result['switched'])

    def test_unverified_selection_blocks_battle_instead_of_claiming_kept(self):
        r,ocr=self.runner({'main'},full=False)
        with patch('hyol_ocr.recognize',side_effect=ocr),self.assertRaises(hs.PendingError):
            r.ensure_activity_formation('strong')
        self.assertNotIn('strong',r.__dict__.get('_activity_formations',{}))
        self.assertFalse(any('_close_formation' in call.args[4] for call in r.click.call_args_list))

    def test_missing_safe_toolbar_keeps_without_opening_any_panel(self):
        r,ocr=self.runner({'main'});r.visible.return_value=False;r.visible.side_effect=None
        with patch('hyol_ocr.recognize',side_effect=ocr):result=r.ensure_activity_formation('strong')
        self.assertEqual('keep',result['preset']);r.click.assert_not_called()

    def test_each_activity_checks_once_and_group_can_choose_after_strong(self):
        r,ocr=self.runner({'main','fast'})
        with patch('hyol_ocr.recognize',side_effect=ocr):
            self.assertEqual('main',r.ensure_activity_formation('strong')['preset'])
            self.assertEqual('fast',r.ensure_activity_formation('group')['preset'])
            count=r.click.call_count;r.ensure_activity_formation('group');self.assertEqual(count,r.click.call_count)


class GroupEntryTests(unittest.TestCase):
    def test_fresh_group_prepares_formation_then_enters_once(self):
        r=object.__new__(DailyRunner);r.account=hs.Account(2,'owner',315);r.group_count=0
        rec=dict(phase='ready',completed=0,reward_exhausted=False)
        r.record=lambda task:rec;r.save=Mock();r.event=Mock();r.group_selection_cancelled=False
        page={'group':False};order=[]
        r.visible=Mock(side_effect=lambda name,*args:page['group'] if name==PAGE_ANCHORS['group'] else False)
        r.ensure_activity_formation=Mock(side_effect=lambda activity:order.append('formation'))
        def enter(task):page['group']=True;order.append('enter')
        def close(task):page['group']=False;order.append('close')
        r.enter=Mock(side_effect=enter);r.close_page=Mock(side_effect=close)
        r.once=Mock();r.poll_group=Mock();r.friendship_settled=Mock(return_value=False)
        r.scheduler=SimpleNamespace(capture=Mock(return_value=Image.new('RGB',(1920,1032))),
            _team_battle_start_visible=Mock(return_value=False),_battle_auto_on_visible=Mock(return_value=False),
            _team_result_template=Mock(return_value=False),_team_reward_result_visible=Mock(return_value=False),
            _read_team_reward_remaining=Mock(side_effect=[1,1,0,0]))
        r.group()
        self.assertEqual(['formation','enter','close'],order)
        r.enter.assert_called_once_with('group');self.assertEqual(1,rec['completed'])
        self.assertTrue(rec['reward_exhausted'])


if __name__=='__main__':unittest.main()
