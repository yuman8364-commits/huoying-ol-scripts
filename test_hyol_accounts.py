import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace
from types import MethodType
from PIL import Image

import numpy as np

import hyol_accounts as accounts
import hyol_scheduler as hs
from hyol_checkpoints import encode_mask


def mask(seed):
    value = (np.random.default_rng(seed).random((32,145)) < .25).astype(np.uint8)
    value[:12] = 0
    return value


class DynamicAccountTests(unittest.TestCase):
    def test_clean_registry_enrolls_unknown_accounts_without_names_or_assets(self):
        first, second = mask(1), mask(2)
        ids = iter(('account_'+'a'*32,'account_'+'b'*32))
        mapping, refs = accounts.resolve_observations({1:[first]*3,3:[second]*3},{},id_factory=lambda:next(ids))
        self.assertEqual(mapping,{1:'account_'+'a'*32,3:'account_'+'b'*32})
        self.assertEqual(len(refs),2)
        self.assertTrue(all(row['origin']=='automatic_three_frame_observation' for row in refs.values()))

    def test_reordered_tabs_retain_durable_accounts(self):
        original = mask(3)
        key = 'account_'+'c'*32
        refs = {key:{'mask':encode_mask(original),'shape':list(original.shape)}}
        mapping, updated = accounts.resolve_observations({5:[original]*3},refs)
        self.assertEqual(mapping,{5:key})
        self.assertEqual(updated,refs)

    def test_legacy_identity_key_remains_compatible_without_hardcoded_name(self):
        original = mask(4)
        refs = {'旧安装账号':{'mask':encode_mask(original),'shape':list(original.shape)}}
        mapping, _ = accounts.resolve_observations({2:[original]*3},refs)
        self.assertEqual(mapping,{2:'旧安装账号'})

    def test_duplicate_visible_identity_never_registers_two_accounts(self):
        original = mask(5)
        with self.assertRaises(hs.PendingError):
            accounts.resolve_observations({1:[original]*3,2:[original]*3},{})

    def test_changing_or_hidden_identity_does_not_enroll(self):
        for observations in ({1:[mask(6),mask(7),mask(6)]},
                             {1:[np.zeros((32,145),dtype=np.uint8)]*3}):
            with self.assertRaises(hs.PendingError):
                accounts.resolve_observations(observations,{})

    def test_clean_state_is_per_user_and_explicit_upgrade_state_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(accounts.state_directory({},root,environ={'LOCALAPPDATA':folder}),
                             (root/'HYOLClient/state').resolve())
            self.assertEqual(accounts.state_directory({'SharedStateDirectory':'state'},root,environ={}),
                             (root/'state').resolve())

    def test_multisolo_constructs_with_empty_config_and_no_account_assets(self):
        from hyol_multi_solo import MultiSoloRunner
        with tempfile.TemporaryDirectory() as folder:
            config = {'AccountDiscovery':'automatic','SharedStateDirectory':folder}
            with patch.object(hs,'read_config',return_value=config):
                runner = MultiSoloRunner(('mail',),account_count=4)
            self.assertTrue(runner.automatic_accounts)
            self.assertEqual(len(runner.scheduler.accounts),6)
            self.assertEqual(runner.requested_account_count,4)
            self.assertEqual(runner.scheduler.account_mode,'dynamic')

    def test_first_run_discovers_arbitrary_online_roles_and_skips_offline_without_content_input(self):
        with tempfile.TemporaryDirectory() as folder:
            refs = {180:mask(11),315:mask(12)}
            selected = [180]
            def capture():
                frame = Image.new('RGB',(1920,1032))
                frame.info['tab_x'] = selected[0]
                return frame
            s = SimpleNamespace(shared_state_directory=Path(folder),no_content_clicks=False,
                config={},capture=capture,check_stop=Mock(),wait_interruptible=Mock(),
                hwnd=123,account_logs={},log=Mock(last_error=''),event_sink=None,
                chrome_click=lambda x,*args:selected.__setitem__(0,x),
                offline_dialog_visible=lambda f:f.info['tab_x']==450,
                _generic_active_tab_x=lambda f:f.info['tab_x'],
                template=lambda asset,roi,f,**kw:(asset=='team_personal_leader_core.bmp'
                          and f.info['tab_x']==180,1.,0,0),templates=None)
            s.event = MethodType(hs.Scheduler.event,s)
            with patch.object(hs,'LOGS',Path(folder)/'logs'), \
                 patch('hyol_multi_solo.tab_close_positions',return_value=(214,349,484)), \
                 patch('hyol_checkpoints.current_identity_mask',side_effect=lambda f,*args:refs[f.info['tab_x']]):
                centers,mapping,frames,unavailable = accounts.discover_accounts(s)
            self.assertEqual(centers,(180,315,450))
            self.assertEqual(set(mapping),{1,2});self.assertEqual(unavailable,{3:'offline_prompt'})
            self.assertTrue(all(key.startswith('account_') for key in mapping.values()))
            self.assertEqual(s._observed_captain_tabs,{180:True,315:False})
            saved = accounts.register_observations(s,{2:[refs[180]]*3,1:[refs[315]]*3})[0]
            self.assertEqual(saved,{2:mapping[1],1:mapping[2]})
            self.assertTrue(any((Path(folder)/'logs/accounts').glob('*.jsonl')))

    def test_current_identity_observation_can_only_select_verified_title_tabs(self):
        account = hs.Account(2,'当前窗口标签2',315)
        s = SimpleNamespace(hwnd=123,ensure_window=Mock(),event=Mock(),click_count=0,
            current_account_only='当前窗口标签1',check_input_pause=Mock(),
            automatic_accounts=True,no_content_clicks=True,_identity_observation_only=True)
        with patch.object(hs.Win32,'hit_test',return_value=1), \
             patch.object(hs.Win32,'foreground',return_value=123), \
             patch.object(hs.Win32,'click') as click:
            hs.Scheduler.chrome_click(s,315,10,'observe_account:2',account)
            click.assert_called_once_with(315,10)
            click.reset_mock()
            for label,x,y in (('observe_account:3',315,10),('refresh',315,10),
                              ('observe_account:2',316,10),('observe_account:2',315,100)):
                with self.assertRaises(hs.SafetyError):
                    hs.Scheduler.chrome_click(s,x,y,label,account)
            s.no_content_clicks = False
            with self.assertRaises(hs.SafetyError):
                hs.Scheduler.chrome_click(s,315,10,'observe_account:2',account)
            click.assert_not_called()

    def test_discovery_highlight_uses_observed_positions_before_roles_are_bound(self):
        frame = Image.new('RGB',(1920,1032),'#888888')
        frame.paste('white',(390,10,507,30))
        s = SimpleNamespace(stage='multi_solo',automatic_accounts=True,
            config={'TabXs':'180,315,450,585,720,855'},
            accounts=[hs.Account(i,f'当前窗口标签{i}',100*i) for i in range(1,7)])
        s._generic_tab_measurements = MethodType(hs.Scheduler._generic_tab_measurements,s)
        self.assertEqual(hs.Scheduler._generic_active_tab_x(s,frame),450)

    def test_unknown_battle_page_gives_actionable_reason_without_binding_or_clicking(self):
        frame = Image.new('RGB',(1920,1032))
        s = SimpleNamespace(capture=Mock(return_value=frame),check_stop=Mock(),wait_interruptible=Mock(),
            offline_dialog_visible=Mock(return_value=False),templates=None,
            _generic_active_tab_x=Mock(return_value=180),
            template=lambda asset,*args,**kw:(asset=='team_result_confirm.bmp',1.,0,0))
        with patch('hyol_checkpoints.current_identity_mask',return_value=np.zeros((32,145),dtype=np.uint8)):
            with self.assertRaisesRegex(hs.PendingError,'战斗或结算'):
                accounts.collect_current_identity(s,hs.Account(1,'未知账号',180))

    def test_failed_joint_observation_restores_single_account_content_restrictions(self):
        original = mask(13)
        s = SimpleNamespace(no_content_clicks=False)
        account = hs.Account(1,'当前窗口标签1',180)
        def fail_observation(scheduler):
            self.assertTrue(scheduler.no_content_clicks)
            self.assertTrue(scheduler._identity_observation_only)
            raise hs.PendingError('标签变化')
        with patch.object(accounts,'collect_current_identity',return_value=([original]*3,None)), \
             patch('hyol_identity_overlays.dismiss_identity_overlays',return_value=None), \
             patch.object(accounts,'dismiss_current_final_result',return_value=None), \
             patch.object(accounts,'register_observations',side_effect=hs.PendingError('身份有歧义')), \
             patch.object(accounts,'observe_accounts',side_effect=fail_observation):
            with self.assertRaises(hs.PendingError):
                accounts.bind_visible_identity(s,account)
        self.assertFalse(s.no_content_clicks)
        self.assertFalse(s._identity_observation_only)


if __name__=='__main__':
    unittest.main()
