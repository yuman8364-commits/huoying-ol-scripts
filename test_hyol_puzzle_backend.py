"""Delegated input ownership, settings and process-tree emergency stop tests."""
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import hyol_scheduler as hs
import hyol_puzzle_backend as b


class FakeNative:
    def __init__(self):
        self.actions=[];self.members=set();self.kills=[]
        self.controls=[dict(text=label,kind='Button',hwnd=100+i,pid=11 if i>=3 else 10,
                            identifier=i,checked=int(label in ('拼图','自动识别')))
                       for i,label in enumerate(b.REQUIRED)]
        self.controls.append(dict(text='',kind='ComboBox',hwnd=200,pid=11,enabled=False,
                                  items=['4×4','5×5','6×6']))
        self.root=dict(hwnd=77,pid=10,path=str(b.backend_path().resolve()),controls=self.controls)

    def windows(self):return [self.root]
    def children(self,_):return self.controls
    def process_tree(self,_):return {10,11}
    def create_job(self):return 500
    def assign(self,job,pid):self.members.add(pid)
    def contained(self,job,pid):return pid in self.members
    def click(self,row):
        self.actions.append(row['text'])
        if row['text'] in ('拼图','自动识别'):
            row['checked']=1
    def hide(self,_):self.actions.append('hide')
    def kill(self,job):self.kills.append(job)
    def geometry(self,_):return 'fixed_geometry'
    def chrome(self,_):return 'selected_account_chrome'


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.account=hs.Account(1,'role',180)
        self.s=Mock(hwnd=123,no_content_clicks=False)
        self.s.stop=threading.Event()
        self.s.template.return_value=(True,1.,0,0)
        self.s.external_input_sessions=0
        self.s._generic_active_tab_x.return_value=180
        self.api=FakeNative()
        self.verified=patch.object(b,'verify_executable',side_effect=lambda p:Path(p).resolve())
        self.verified.start();self.addCleanup(self.verified.stop)

    def session(self):return b.PuzzleScriptSession(self.s,self.account,api=self.api)

    def test_settings_confirm_auto_and_never_start_during_configuration(self):
        with self.session():
            self.assertIn('保存设置',self.api.actions)
            self.assertNotIn('启动',self.api.actions)
            self.assertEqual({10,11},self.api.members)
        self.assertEqual([500],self.api.kills)

    def test_wrong_game_page_rejects_before_job_or_native_commands(self):
        self.s.template.return_value=(False,0.,0,0)
        with self.assertRaisesRegex(hs.PendingError,'开始拼图'):
            with self.session():pass
        self.assertEqual([],self.api.actions)
        self.assertEqual([],self.api.kills)

    def test_readonly_invocation_never_launches_or_changes_settings(self):
        self.s.no_content_clicks=True
        with self.assertRaisesRegex(hs.PendingError,'禁止内容操作'):
            with self.session():pass
        self.assertEqual([],self.api.actions)
        self.assertEqual(set(),self.api.members)

    def test_pause_before_configuration_prevents_every_command(self):
        self.s.check_input_pause.side_effect=KeyboardInterrupt('F12')
        with self.assertRaises(KeyboardInterrupt):
            with self.session():pass
        self.assertEqual([],self.api.actions)

    def test_detached_runner_is_not_taken_over(self):
        self.api.process_tree=lambda _:{10}
        with self.assertRaisesRegex(hs.PendingError,'进程归属'):
            with self.session():pass
        self.assertNotIn('中止',self.api.actions)
        self.assertNotIn('启动',self.api.actions)

    def test_failure_to_contain_child_prevents_start(self):
        self.api.contained=lambda job,pid:pid==10
        with self.assertRaisesRegex(hs.PendingError,'急停进程树'):
            with self.session():pass
        self.assertNotIn('启动',self.api.actions)
        self.assertEqual([500],self.api.kills)

    def test_enabled_manual_grid_rejects_wrong_auto_mode(self):
        self.api.controls[-1]['enabled']=True
        with self.assertRaisesRegex(hs.PendingError,'自动识别未接入'):
            with self.session():pass
        self.assertNotIn('启动',self.api.actions)

    def test_start_control_does_not_synthesize_global_function_keys(self):
        with patch.object(hs.Win32,'activate'),patch.object(hs.Win32,'foreground',return_value=123),\
             patch.object(hs.Win32.user32,'keybd_event') as keys:
            with self.session() as session:
                session.start()
                self.assertEqual(1,self.s.external_input_sessions)
                self.assertIsNone(self.s.external_input_count)
            keys.assert_not_called()
        self.assertEqual(1,self.api.actions.count('启动'))

    def test_start_page_disappearing_after_config_prevents_native_start(self):
        with self.session() as session,patch.object(hs.Win32,'activate'):
            self.s.template.return_value=(False,0.,0,0)
            with self.assertRaisesRegex(hs.PendingError,'开始拼图'):
                session.start()
        self.assertNotIn('启动',self.api.actions)

    def test_f12_terminates_job_even_without_another_capture(self):
        session=self.session()
        with session:
            self.s.stop.set()
            self.assertTrue(session.finished.wait(1))
        self.assertEqual([500],self.api.kills)
        self.s.capture.assert_not_called()

    def test_foreground_loss_aborts_delegated_drag(self):
        with patch.object(hs.Win32,'activate'),patch.object(hs.Win32,'foreground',return_value=123):
            with self.session() as session:
                session.start()
                with patch.object(hs.Win32,'foreground',return_value=999):
                    self.assertTrue(session.finished.wait(1))
        self.assertFalse(self.s.stop.is_set())
        self.assertIn('失去前台焦点',session.abort_reason)
        self.assertEqual([500],self.api.kills)

    def test_same_puzzle_on_another_account_cannot_start(self):
        self.s._generic_active_tab_x.return_value=310
        with self.assertRaisesRegex(hs.SafetyError,'账号标签'):
            with self.session():pass
        self.assertEqual([],self.api.actions)

    def test_switching_account_while_solver_runs_kills_job(self):
        with patch.object(hs.Win32,'activate'),patch.object(hs.Win32,'foreground',return_value=123):
            with self.session() as session:
                session.start()
                self.s._generic_active_tab_x.return_value=310
                self.assertTrue(session.finished.wait(1))
                with self.assertRaisesRegex(hs.SafetyError,'账号标签'):
                    session.check_running()
        self.assertEqual([500],self.api.kills)

    def test_geometry_change_kills_job_without_game_clicks(self):
        with patch.object(hs.Win32,'activate'),patch.object(hs.Win32,'foreground',return_value=123):
            with self.session() as session:
                session.start()
                self.api.geometry=lambda _:'changed_geometry'
                self.assertTrue(session.finished.wait(1))
        self.assertIn('窗口区域',session.abort_reason)
        self.assertEqual([500],self.api.kills)

    def test_unverified_process_exit_blocks_account_rotation(self):
        self.api.kill=Mock(side_effect=hs.PendingError('still_running'))
        with self.assertRaisesRegex(hs.LeaseBlocked,'停止账号轮转'):
            with self.session():pass

    def test_duplicate_original_windows_reject_ambiguous_owner(self):
        self.api.windows=lambda:[self.api.root,self.api.root]
        with self.assertRaisesRegex(hs.PendingError,'多个拼图程序'):
            with self.session():pass
        self.assertEqual([],self.api.actions)

    def test_actual_bundled_executable_matches_supplied_version(self):
        self.verified.stop()
        self.assertEqual(b.backend_path().resolve(),b.verify_executable(b.backend_path()))


if __name__=='__main__':
    unittest.main()
