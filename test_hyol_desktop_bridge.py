"""Focused native adapter tests. Controllers are injected; no game API is run."""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from PIL import Image
from hyol_desktop_bridge import DesktopBridge,parse_request,plain,task_codes
from hyol_runtime import RunRequest
from hyol_task_progress import ProgressReader,shared_state


class Controller:
    def __init__(self,sink,root):
        self.sink=sink
        self.busy=False
        self.identifier=''
        self.calls=[]
        self.runner=None
        self.reject=False
    def start(self,request):
        if self.busy or self.reject:
            return False
        self.identifier='20261024_180000_fixture'+str(len(self.calls))
        self.calls.append(request)
        self.busy=True
        return True
    def emit(self,event,**fields):
        self.sink(dict(event=event,run_id=self.identifier,**fields))
    def stop(self):
        if not self.busy:
            return False
        self.emit('unified_stop_requested',detail='停止已请求')
        return True
    def check_window(self):
        self.identifier='check-fixture'
        self.busy=True
        return True
    detect_team_leaders=check_window
    def finish(self,status='pending',**fields):
        self.emit('unified_finished',status=status,**fields)
        self.emit('unified_idle')
        self.busy=False


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=Path(self.folder.name)
        self.api=DesktopBridge(self.root,controller_factory=Controller,
            reader_factory=lambda root: ProgressReader(root,log_roots=[root/'logs']))
        self.control=self.api._controller
    def tearDown(self):
        self.api._close()
        self.folder.cleanup()
    def start(self,**fields):
        data=asdict(RunRequest())
        data.update(fields)
        result=self.api.start(data)
        self.assertTrue(result['ok'],result)
        return result
    def bind(self,slot=1,name='fixture-one'):
        self.control.emit('account_bound',tab_index=slot,account=name)

    def test_idle_does_not_start_controller_or_initialize_index(self):
        state=self.api.get_state()
        self.assertFalse(state['busy'])
        self.assertEqual('idle',state['status'])
        self.assertIsNone(state['snapshot'])
        self.assertEqual([],self.control.calls)
        self.assertIsNone(self.api._reader.index)
        self.assertEqual([],list(self.root.iterdir()))

    def test_valid_request_arrays_are_passed_to_existing_dataclass(self):
        payload=asdict(RunRequest(mode='multi'))
        payload['tasks']=['mail','arena']
        request=parse_request(payload)
        self.assertEqual(('mail','arena'),request.tasks)
        self.assertIsInstance(request,RunRequest)
        self.start(mode='multi',tasks=['mail','arena'])
        self.assertEqual(request,self.control.calls[0])

    def test_invalid_payload_never_reaches_controller(self):
        for bad in ({'account_count':True},{'arena_wins':float('nan')},
                    {'account_count':'6'},{'mode':'shell'},{'tasks':['arbitrary']},
                    {'tasks':'mail'},{'path':'C:/Windows'},{'leader':7}):
            with self.subTest(payload=bad):
                self.assertFalse(self.api.start(bad)['ok'])
        self.assertEqual([],self.control.calls)

    def test_busy_and_refused_start_preserve_current_run_and_logs(self):
        self.start()
        self.control.emit('unified_status',detail='现有运行')
        old=self.api.get_state()
        self.assertFalse(self.api.start(asdict(RunRequest()))['ok'])
        self.assertEqual(old['snapshot']['runId'],self.api.get_state()['snapshot']['runId'])
        self.control.busy=False
        self.control.reject=True
        self.assertFalse(self.api.start(asdict(RunRequest()))['ok'])
        old['busy']=False  # The injected controller was explicitly made idle.
        self.assertEqual(old,self.api.get_state())

    def test_stop_and_idle_never_erase_terminal_receipt(self):
        self.start()
        self.assertTrue(self.api.stop()['ok'])
        self.assertEqual('stopping',self.api.get_state()['status'])
        self.control.finish('stopped')
        self.assertEqual('stopped',self.api.get_state()['status'])
        self.assertFalse(self.api.get_state()['busy'])

    def test_account_offline_keeps_overall_run_active(self):
        self.start(mode='multi')
        self.bind()
        self.control.emit('account_offline',account='fixture-one',tab_index=1,detail='已掉线')
        state=self.api.get_state()
        self.assertEqual('running',state['status'])
        self.assertTrue(state['busy'])
        self.assertTrue(self.api.stop()['ok'])

    def test_completion_comes_only_from_current_receipt(self):
        self.start()
        self.bind()
        self.control.emit('daily_task_complete',account='fixture-one',tab_index=1,task='mail')
        self.assertEqual('running',self.api.get_state()['status'])
        self.control.sink(dict(event='unified_finished',run_id='old-run',status='complete'))
        self.assertEqual('running',self.api.get_state()['status'])
        self.control.finish('pending',detail='结果未核实')
        self.assertEqual('pending',self.api.get_state()['status'])

    def test_only_all_unavailable_receipt_uses_not_open_state(self):
        self.start()
        self.control.finish('pending',detail='组队未开放；另有未决动作')
        self.assertEqual('pending',self.api.get_state()['status'])
        self.start()
        self.control.finish('pending',detail='执行日2026-10-26：所选活动当前未开放，未执行；不计完成。')
        self.assertEqual('not_open',self.api.get_state()['status'])

    def test_new_run_drops_previous_display_and_evidence(self):
        first=self.start()['run_id']
        self.bind()
        self.control.emit('unified_status',detail='旧运行')
        self.control.finish()
        second=self.start()['run_id']
        self.assertNotEqual(first,second)
        self.assertEqual([],self.api.get_state()['snapshot']['accounts'])
        self.assertEqual([],self.api.get_state()['snapshot']['logs'])
        self.assertEqual(set(),self.api._allowed_evidence)

    def test_recent_logs_are_bounded(self):
        self.start()
        for i in range(1120):
            self.control.emit('observed',detail='日志'+str(i))
        logs=self.api.get_state()['snapshot']['logs']
        self.assertEqual(1000,len(logs))
        self.assertEqual('日志120',logs[0]['message'])

    def test_evidence_is_current_known_image_only(self):
        self.start(tasks=['mail'])
        self.bind()
        path=self.root/'logs'/'proof.png'
        path.parent.mkdir()
        Image.new('RGB',(8,8)).save(path)
        self.api._extras[('fixture-one','mail')]={**self.api._extras.get(('fixture-one','mail'),{}),
            'evidence_paths':[str(path)],'status':'pending'}
        self.api.get_state()
        self.assertTrue(self.api.read_evidence(str(path))['ok'])
        self.assertFalse(self.api.read_evidence(str(self.root/'secret.json'))['ok'])
        outside=Path(self.folder.name).parent/'outside.png'
        self.api._allowed_evidence.add(str(outside.resolve()))
        self.assertFalse(self.api.read_evidence(str(outside))['ok'])
        path.write_text('not an image','utf-8')
        self.assertFalse(self.api.read_evidence(str(path))['ok'])

    def test_group_done_requires_current_evidence_and_home(self):
        self.start(mode='team',team_tasks=['wanted'])
        self.bind()
        self.control.emit('generic_team_task_done',account='fixture-one',task='wanted',home_returned=True)
        rec=self.api.get_state()['snapshot']['accounts'][0]['tasks']['wanted']
        self.assertEqual('pending',rec['status'])
        self.control.emit('generic_team_task_done',account='fixture-one',task='wanted',home_returned=True,
                          evidence={'fixture-one':str(self.root/'logs/proof.png')})
        rec=self.api.get_state()['snapshot']['accounts'][0]['tasks']['wanted']
        self.assertEqual('done',rec['status'])

    def test_pipeline_reads_only_current_id_and_verified_records(self):
        self.start(mode='team_then_multi',team_tasks=['wanted'])
        self.bind()
        folder=shared_state(self.root)/'runs'/self.control.identifier
        folder.mkdir(parents=True)
        path=folder/'team_pipeline_latest.json'
        team={'members':['fixture-one'],'tasks':{'wanted':{'status':'done','task_completed':True,
              'home_returned':False,'evidence':str(self.root/'logs/proof.png')}}}
        path.write_text(json.dumps({'id':'old-run','teams':{'1':team}}),'utf-8')
        self.api.get_state()
        self.assertNotIn(('fixture-one','wanted'),self.api._extras)
        path.write_text(json.dumps({'id':self.control.identifier,'teams':{'1':team}}),'utf-8')
        self.api._next_read=0
        rec=self.api.get_state()['snapshot']['accounts'][0]['tasks']['wanted']
        self.assertEqual('pending',rec['status'])

    def test_check_operation_does_not_run_tasks_or_fake_completion(self):
        self.assertTrue(self.api.check_window()['ok'])
        self.assertEqual([],self.control.calls)
        self.control.emit('unified_window_check',check={'status':'not_ready','healthy':False,'content_clicks':0})
        self.control.emit('unified_idle')
        self.control.busy=False
        state=self.api.get_state()
        self.assertEqual('idle',state['status'])
        self.assertIsNone(state['snapshot'])
        self.assertFalse(state['last_check']['healthy'])

    def test_close_is_blocked_while_busy_and_reader_is_released(self):
        self.start()
        self.api.get_state()
        index=self.api._reader.index
        directory=Path(index.temporary.name)
        self.assertFalse(self.api._can_close())
        self.assertTrue(self.api.get_state()['close_notice'])
        self.control.finish('stopped')
        self.assertTrue(self.api._can_close())
        self.api._close()
        self.assertFalse(directory.exists())

    def test_stopping_a_check_keeps_previous_run_result(self):
        self.start()
        self.control.finish('pending',detail='原轮次仍有未决动作')
        run_id=self.api.get_state()['snapshot']['runId']
        self.assertTrue(self.api.check_window()['ok'])
        self.assertTrue(self.api.stop()['ok'])
        self.control.emit('unified_error',detail='检查已停止')
        self.control.emit('unified_idle')
        self.control.busy=False
        state=self.api.get_state()
        self.assertEqual('pending',state['status'])
        self.assertEqual(run_id,state['snapshot']['runId'])
        self.assertFalse(state['busy'])

    def test_export_uses_fixed_folder_and_preserves_unicode(self):
        self.start()
        self.control.emit('unified_status',detail='中文核实记录')
        result=self.api.export_logs()
        self.assertTrue(result['ok'])
        path=Path(result['path'])
        self.assertTrue(path.is_relative_to(self.root/'logs/exports'))
        self.assertIn('中文核实记录',path.read_text('utf-8-sig'))

    def test_nonfinite_observations_are_serializable(self):
        self.assertEqual({'value':None},plain({'value':float('nan')}))


if __name__=='__main__':
    unittest.main()
