"""Portable first use must not consult the developer's accounts or screenshots."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock,patch
import json
import unittest
import numpy as np
from PIL import Image

import hyol_accounts as accounts
import hyol_checkpoints as checkpoints
import hyol_scheduler as hs
from hyol_run_context import RunContext
from tools.prepare_generic_release import clean_config,personal_asset


class PortableAccountTests(unittest.TestCase):
    def setUp(self):
        folder=TemporaryDirectory(dir=hs.ROOT/'logs');self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.state=self.root/'state';self.state.mkdir()
        self.context=RunContext(self.root,self.state,identifier='portable-first-use')
        self.s=SimpleNamespace(portable_accounts=True,automatic_accounts=True,run_context=self.context,
            identity_directory=self.state,shared_state_directory=self.context.directory,
            no_content_clicks=False,content_click_count=0,event=Mock(),
            diagnostics=SimpleNamespace(identifier='portable-first-use'))
        logs=patch.object(hs,'LOGS',self.root/'logs');logs.start();self.addCleanup(logs.stop)
        self.masks={slot:self.mask(slot) for slot in (1,3)}

    def mask(self,seed):
        value=(np.random.default_rng(seed).random((32,145))<.25).astype(np.uint8)
        value[:12]=0
        return value

    def observe(self,s=None,swapped=False):
        return accounts.register_observations(s or self.s,
            {(3 if slot==1 else 1) if swapped else slot:[mask]*3 for slot,mask in self.masks.items()})

    def test_first_use_ignores_poisoned_personal_registry_and_has_no_template_dependency(self):
        personal=self.state/'account_identities.json'
        personal.write_text('{broken personal file that must never be opened','utf-8')
        original=personal.read_bytes()
        original_read=checkpoints.read_checkpoint
        def read(path):
            self.assertNotEqual(Path(path),personal,'Portable mode opened the machine account library')
            return original_read(path)
        with patch.object(checkpoints,'read_checkpoint',side_effect=read), \
             patch.object(accounts,'_has_unresolved_work',side_effect=AssertionError('personal history read')):
            mapping,refs=self.observe()
        self.assertTrue(all(key.startswith('account_') and len(key)==40 for key in mapping.values()))
        self.assertEqual({refs[key]['display_label'] for key in mapping.values()},{'标签1','标签3'})
        self.assertTrue(all(row['scope']=='current_run' for row in refs.values()))
        self.assertEqual(personal.read_bytes(),original)
        self.assertTrue((self.context.directory/'account_identities.json').is_file())
        self.assertFalse((self.root/'assets').exists())
        self.assertEqual(self.s.content_click_count,0)

    def test_same_run_rebind_preserves_owners_and_updates_only_slot_labels(self):
        first,_refs=self.observe()
        again,refs=self.observe(swapped=True)
        self.assertEqual(again,{3:first[1],1:first[3]})
        self.assertEqual(refs[first[1]]['display_label'],'标签3')

    def test_next_start_does_not_import_another_run_or_its_pending_actions(self):
        first,_refs=self.observe()
        pending=self.context.directory/'pending.json';pending.write_text('{"status":"pending"}','utf-8')
        next_context=RunContext(self.root,self.state,identifier='portable-next-start')
        s=SimpleNamespace(**vars(self.s));s.run_context=next_context;s.shared_state_directory=next_context.directory
        second,_refs=self.observe(s)
        self.assertTrue(set(first.values()).isdisjoint(second.values()))
        self.assertEqual(pending.read_text('utf-8'),'{"status":"pending"}')

    def test_read_only_first_observation_does_not_write_a_registry(self):
        self.s.no_content_clicks=True
        mapping,_refs=self.observe()
        self.assertEqual(set(mapping),{1,3})
        self.assertFalse(self.context.directory.exists())

    def test_explicit_old_run_continuation_keeps_its_original_binding(self):
        old='preserved-original-owner'
        ref={'mask':checkpoints.encode_mask(self.masks[1]),'shape':[32,145]}
        self.context.binding={'refs':{old:ref}}
        mapping,_refs=accounts.register_observations(self.s,{1:[self.masks[1]]*3})
        self.assertEqual(mapping,{1:old})
        self.assertFalse((self.state/'account_identities.json').exists())

    def test_unbound_portable_scheduler_never_falls_back_to_personal_bitmap_files(self):
        s=object.__new__(hs.Scheduler);s.portable_accounts=True
        with patch.object(hs.Account,'identity_path',new_callable=lambda:property(lambda _s:(_ for _ in ()).throw(AssertionError('personal bitmap read')))):
            self.assertEqual(s.identity_scores(Image.new('RGB',(1920,1032))),{})

    def test_arbitrary_changed_and_duplicate_roles_keep_fail_closed_guards(self):
        self.observe()
        for rows in ({1:[self.mask(4),self.mask(5),self.mask(4)]},
                     {1:[self.masks[1]]*3,2:[self.masks[1]]*3}):
            with self.assertRaises(hs.PendingError): accounts.register_observations(self.s,rows)

    def test_non_context_portable_observations_get_one_invocation_directory(self):
        self.s.run_context=None
        path=accounts.identity_registry_path(self.s)
        self.assertEqual(path,accounts.identity_registry_path(self.s))
        self.assertTrue(path.is_relative_to(self.s.shared_state_directory/'runs'))

    def test_clean_settings_strip_account_names_teams_and_machine_paths(self):
        text=clean_config('TabAccountNames=private-role\nTeam1=private-a,private-b\n'
            'SharedStateDirectory=D:\\private\\state\nPanelCurrentAccount=private-role\nPortableAccounts=0\n')
        self.assertNotIn('private',text)
        self.assertIn('PortableAccounts=1',text)
        self.assertIn('AccountDiscovery=automatic',text)
        self.assertIn('%LOCALAPPDATA%',text)
        self.assertTrue(personal_asset(Path('accounts/role_identity.bmp')))
        self.assertFalse(personal_asset(Path('team_personal_leader_core.bmp')))

    def test_default_multisolo_and_team_entrances_enable_portable_mode(self):
        from hyol_multi_solo import MultiSoloRunner
        from hyol_team_six import SixTabScheduler
        config={'AccountDiscovery':'automatic','SharedStateDirectory':str(self.state)}
        with patch.object(hs,'read_config',return_value=config.copy()):
            solo=MultiSoloRunner(('mail',),account_count=2)
            team=SixTabScheduler()
        self.assertTrue(solo.scheduler.portable_accounts)
        self.assertTrue(team.portable_accounts)


if __name__=='__main__':unittest.main()
