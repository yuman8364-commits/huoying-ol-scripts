"""Irreversible continuations must survive day changes and entrance migration."""
from test_identity_fixtures import ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F, legacy_config
import json
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PIL import Image

import hyol_checkpoints as cp
import hyol_scheduler as hs


class CheckpointTests(unittest.TestCase):
    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_multiple_original_actions_do_not_nest_or_grow_on_restart(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            action = 'arena_casual_battle_1'
            sources = []
            for offset in (3, 1):
                day = (date.today()-timedelta(days=offset)).isoformat()
                source = root/day/(hs.slug('test')+'.json')
                self.write(source, {'date': day, 'account': 'test', 'actions': {
                    action: {'status': 'pending', 'updated_at': day+'T20:00:00', 'detail': 'before click'}}})
                sources.append((source, source.read_bytes()))
            state = hs.StateStore(['test'], root)
            versions = cp.action_versions(state.action_record('test', action))
            self.assertEqual(2, len(versions))
            self.assertEqual(sources[1][0].parent.name,
                             Path(state.action_record('test', action)['origin']['path']).parent.name)
            state.save_account('test')
            original = state.action_record('test', action)
            for _ in range(5):
                state = hs.StateStore(['test'], root)
                self.assertEqual(original, state.action_record('test', action))
                state.save_account('test')
            self.assertTrue(all(path.read_bytes() == content for path, content in sources))
            self.assertEqual(2, len(cp.action_versions(state.action_record('test', action))))

    def test_legacy_nested_conflicts_keep_each_origin_once(self):
        a = {'status': 'pending', 'origin': {'token': 'first'}, 'updated_at': 'first'}
        b = {'status': 'pending', 'origin': {'token': 'second'}, 'updated_at': 'second'}
        nested = dict(b, checkpoint_conflict=dict(a, checkpoint_conflict=dict(b, checkpoint_conflict=a)))
        normalized = cp.merge_pending_action(nested, a)
        self.assertEqual({'first', 'second'}, {row['origin']['token'] for row in cp.action_versions(normalized)})
        self.assertEqual(1, len(normalized['checkpoint_conflict']))
        self.assertNotIn('checkpoint_conflict', normalized['checkpoint_conflict'][0])
        self.assertEqual(normalized, cp.merge_pending_action(normalized, a))

    def test_one_result_cannot_resolve_two_distinct_pending_origins(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            action = 'arena_casual_battle_1'
            for offset in (3, 1):
                day = (date.today()-timedelta(days=offset)).isoformat()
                self.write(root/day/(hs.slug('test')+'.json'), {
                    'date': day, 'account': 'test', 'actions': {
                        action: {'status': 'pending', 'updated_at': day}}})
            state = hs.StateStore(['test'], root)
            before = state.action_record('test', action)
            with self.assertRaisesRegex(hs.PendingError, '多个未决来源'):
                state.set_action('test', action, 'done', 'one battle victory observed')
            self.assertEqual(before, state.action_record('test', action))
            self.assertFalse((root/'checkpoint_resolutions.json').exists())

    def test_network_budget_and_original_chain_survive_day_change(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            day = (date.today()-timedelta(days=1)).isoformat()
            source = root/day/(hs.slug('test')+'.json')
            self.write(source, {'date': day, 'account': 'test', 'actions': {
                'mail_claim_all_network_1': {'status': 'done'},
                'mail_claim_all_network_2': {'status': 'pending', 'updated_at': 'original'}},
                'network_retries': {'mail_claim_all': {'attempts': 2, 'verified': False}}})
            before = source.read_bytes()
            state = hs.StateStore(['test'], root)
            self.assertEqual('done', state.action_status('test', 'mail_claim_all_network_1'))
            self.assertEqual(2, state.accounts['test']['network_retries']['mail_claim_all']['attempts'])
            self.assertEqual(before, source.read_bytes())

    def test_sibling_pending_context_does_not_invent_second_original(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            day = (date.today()-timedelta(days=1)).isoformat()
            keys = [f'mail_claim_all_network_{i}' for i in range(1,4)]
            source = root/day/(hs.slug('test')+'.json')
            self.write(source,dict(account='test',date=day,actions={
                key:dict(status='pending',updated_at=f'original-{i}') for i,key in enumerate(keys)}))
            state = hs.StateStore(['test'],root)
            for key in keys:
                versions = cp.action_versions(state.action_record('test',key))
                self.assertEqual(1,len(versions))
                self.assertEqual(str(source),versions[0]['origin']['path'])

    def test_proven_legacy_context_copy_merges_into_its_source_but_other_origins_survive(self):
        original = dict(status='pending',updated_at='original',origin=dict(path='2026-09-29/account.json',token='first'))
        copy = dict(status='pending',updated_at='original',continuation_context='2026-09-29')
        merged = cp.merge_pending_action(copy,original)
        self.assertEqual(1,len(cp.action_versions(merged)))
        other = dict(status='pending',updated_at='original',origin=dict(path='2026-09-30/account.json',token='second'))
        self.assertEqual(2,len(cp.action_versions(cp.merge_pending_action(merged,other))))

    def test_switching_to_shared_state_cannot_abandon_local_anonymous_pending(self):
        with TemporaryDirectory() as folder:
            local, shared, logs = (Path(folder)/name for name in ('local','shared','logs'))
            source = local/'2026-09-22/account.json'
            self.write(source, {'account':'当前窗口标签3','actions':{
                'arena_casual_battle_2':{'status':'pending','updated_at':'2026-09-22T22:10:00'}}})
            before = source.read_bytes()
            with patch.object(hs,'STATE_DIR',local), patch.object(hs,'LOGS',logs):
                with self.assertRaisesRegex(hs.PendingError,'匿名断点'):
                    cp.guard_legacy_local_unresolved(SimpleNamespace(shared_state_directory=shared))
            self.assertEqual(before,source.read_bytes())
            report = json.loads((logs/'legacy_checkpoint_unlinked.json').read_text(encoding='utf-8'))
            self.assertEqual('arena_casual_battle_2',report['sources'][0]['actions'][0])

    def test_pending_combat_carries_original_context_and_request(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            old_day = (date.today()-timedelta(days=2)).isoformat()
            self.write(root/old_day/(hs.slug('test')+'.json'), {'date': old_day, 'account': 'test',
                'actions': {'arena_casual_battle_3': {'status': 'pending', 'updated_at': 'original'},
                            'arena_casual_battle_1': {'status': 'done'},
                            'arena_victory_1': {'status': 'done'}, 'mail_claim_all': {'status': 'done'}},
                'modules': {'mail': {'status': 'done'}}})
            self.write(root/(old_day+'.json'), {'arena_requests': {'test': {'target': 5, 'base_wins': 0}}})
            state = hs.StateStore(['test'], root)
            self.assertEqual('pending', state.action_status('test', 'arena_casual_battle_3'))
            self.assertEqual('done', state.action_status('test', 'arena_victory_1'))
            self.assertEqual('new', state.action_status('test', 'mail_claim_all'))
            self.assertEqual('new', state.module_status('test', 'mail'))
            self.assertEqual(5, state.global_state['arena_requests']['test']['target'])
            with self.assertRaises(hs.PendingError):
                state.set_action('test', 'arena_casual_battle_3', 'new')
            state.set_action('test', 'arena_casual_battle_3', 'done', 'real result verified')
            restored = hs.StateStore(['test'], root)
            self.assertEqual('done', restored.action_status('test', 'arena_casual_battle_3'))
            self.assertEqual(1, len(cp.read_checkpoint(root/'checkpoint_resolutions.json')))

    def test_corrupt_current_state_never_becomes_a_fresh_budget(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/date.today().isoformat()/(hs.slug('test')+'.json')
            path.parent.mkdir()
            path.write_text('{broken', encoding='utf-8')
            with self.assertRaises(hs.PendingError):
                hs.StateStore(['test'], Path(folder))
            self.assertEqual('{broken', path.read_text(encoding='utf-8'))

    def test_treasure_cross_day_keeps_original_deadline(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            old_day = (date.today()-timedelta(days=1)).isoformat()
            self.write(root/old_day/(hs.slug('test')+'_multi_solo.json'), {
                'treasure': {'status': 'pending', 'phase': 'battle', 'started_at': 1000, 'deadline_at': 1300},
                'mail': {'status': 'done', 'task_completed': True, 'home_returned': True}})
            state = hs.StateStore(['test'], root)
            path, records = cp.load_tasks(state, 'test')
            self.assertTrue(path.name.endswith('_tasks.json'))
            self.assertEqual(1300, records['treasure']['deadline_at'])
            self.assertNotIn('mail', records)

    def test_historical_task_ledgers_are_read_once_per_source(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            old_day = (date.today()-timedelta(days=1)).isoformat()
            multi = root/old_day/(hs.slug('test') + '_multi_solo.json')
            daily = root/old_day/(hs.slug('test') + '_daily_client.json')
            self.write(multi, {'mail': {'status': 'pending', 'phase': 'claim'}})
            self.write(daily, {'welfare': {'status': 'pending', 'phase': 'claim'}})
            state = hs.StateStore(['test'], root)
            original_reader = cp.read_checkpoint
            with patch.object(cp, 'read_checkpoint', wraps=original_reader) as read:
                _, records = cp.load_tasks(state, 'test')
            self.assertEqual({'mail', 'welfare'}, set(records))
            self.assertEqual('pending', records['mail']['status'])
            for source in (multi, daily):
                self.assertEqual(1, sum(call.args[0] == source for call in read.call_args_list))

    def test_canonical_history_does_not_open_corrupted_legacy_entrance(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            today = date.today().isoformat()
            old = (date.today()-timedelta(days=1)).isoformat()
            self.write(root/old/'test_tasks.json',
                       {'mail': {'status': 'pending', 'phase': 'verified_origin'}})
            stale = root/old/'test_multi_solo.json'
            stale.write_text('{corrupted', encoding='utf-8')
            state = SimpleNamespace(directory=root, day=today,
                                    account_paths={'test': root/today/'test.json'})
            _, records = cp.load_tasks(state, 'test')
            self.assertEqual('pending', records['mail']['status'])
            self.assertEqual(old, records['mail']['continuation_day'])

    def test_conflicting_entrances_cannot_extend_treasure_timer(self):
        result = cp.merge_records([('a', {'treasure': {'status': 'running', 'deadline_at': 1300}}),
                                   ('b', {'treasure': {'status': 'running', 'deadline_at': 1400}})])
        self.assertEqual(1300, result['treasure']['deadline_at'])
        self.assertEqual('pending', result['treasure']['status'])
        self.assertIn('checkpoint_conflict', result['treasure'])

    def test_completed_flag_without_return_is_pending(self):
        result = cp.merge_records([('a', {'arena': {'status': 'done', 'task_completed': True}})])
        self.assertEqual('pending', result['arena']['status'])

    def test_generic_labels_share_only_explicitly_verified_identity(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            real = hs.StateStore(['verified'], root)
            real.set_action('verified', 'arena_casual_battle_1', 'pending', 'original battle')
            label = hs.StateStore(['第2标签'], root, aliases={'第2标签': 'verified'})
            self.assertEqual('pending', label.action_status('第2标签', 'arena_casual_battle_1'))
            self.assertEqual(real.account_paths['verified'], label.account_paths['第2标签'])
            self.assertEqual('verified', label.accounts['第2标签']['account'])

    def test_all_pending_navigation_phases_keep_original_proof_across_days(self):
        phases = {'hot_spring_exit': ('ready', 'close_requested', 'close_confirm_requested',
                                     'home_exit_requested', 'home_confirm_requested'),
                  'return_leaf': ('return_requested',)}
        for key, values in phases.items():
            for phase in values:
                with self.subTest(key=key, phase=phase), TemporaryDirectory() as folder:
                    root = Path(folder)
                    old_day = (date.today()-timedelta(days=1)).isoformat()
                    source = root/old_day/(hs.slug('verified')+'.json')
                    record = {'status': 'pending', 'phase': phase, 'started_at': 'original-start',
                              'steps': [{'phase': phase, 'evidence': 'real-original.png'}],
                              'pending_evidence': 'real-pending.png'}
                    self.write(source, {'date': old_day, 'account': 'verified',
                                        'navigation': {key: record}})
                    before = source.read_bytes()
                    state = hs.StateStore(['verified'], root)
                    carried = state.accounts['verified']['navigation'][key]
                    self.assertEqual(phase, carried['phase'])
                    self.assertEqual(record['steps'], carried['steps'])
                    self.assertEqual('original-start', carried['started_at'])
                    self.assertEqual('real-pending.png', carried['pending_evidence'])
                    self.assertEqual(old_day, carried['source_day'])
                    self.assertEqual(str(source), carried['source_path'])
                    state.save_account('verified')
                    resumed = hs.StateStore(['verified'], root)
                    self.assertEqual(carried, resumed.accounts['verified']['navigation'][key])
                    self.assertEqual(before, source.read_bytes())

    def test_newest_done_navigation_never_revives_older_pending(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            older = (date.today()-timedelta(days=3)).isoformat()
            latest = (date.today()-timedelta(days=1)).isoformat()
            for key in ('hot_spring_exit', 'return_leaf'):
                self.write(root/older/(hs.slug('verified')+'.json'),
                           {'date': older, 'account': 'verified', 'navigation': {
                               key: {'status': 'pending', 'phase': 'close_requested'}}})
                self.write(root/latest/(hs.slug('verified')+'.json'),
                           {'date': latest, 'account': 'verified', 'navigation': {
                               key: {'status': 'done', 'phase': 'world_verified'}}})
                state = hs.StateStore(['verified'], root)
                self.assertNotIn(key, state.accounts['verified'].get('navigation', {}))

    def test_current_navigation_phase_does_not_regress_to_old_request(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            old_day = (date.today()-timedelta(days=1)).isoformat()
            self.write(root/old_day/(hs.slug('verified')+'.json'), {
                'date': old_day, 'account': 'verified', 'navigation': {
                    'hot_spring_exit': {'status': 'pending', 'phase': 'ready'}}})
            current = {'status': 'pending', 'phase': 'close_confirm_requested',
                       'started_at': 'original', 'steps': [{'evidence': 'current.png'}]}
            self.write(root/date.today().isoformat()/(hs.slug('verified')+'.json'), {
                'date': date.today().isoformat(), 'account': 'verified',
                'navigation': {'hot_spring_exit': current}})
            state = hs.StateStore(['verified'], root)
            self.assertEqual(current, state.accounts['verified']['navigation']['hot_spring_exit'])

    def test_latest_pending_navigation_uses_latest_phase_and_original_source(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            older = (date.today()-timedelta(days=3)).isoformat()
            latest = (date.today()-timedelta(days=1)).isoformat()
            for day, phase in ((older, 'close_requested'), (latest, 'home_confirm_requested')):
                self.write(root/day/(hs.slug('verified')+'.json'), {
                    'date': day, 'account': 'verified', 'navigation': {'hot_spring_exit': {
                        'status': 'pending', 'phase': phase, 'source_day': older,
                        'started_at': 'same-original-start', 'evidence': day+'.png'}}})
            state = hs.StateStore(['verified'], root)
            record = state.accounts['verified']['navigation']['hot_spring_exit']
            self.assertEqual('home_confirm_requested', record['phase'])
            self.assertEqual(older, record['source_day'])
            self.assertEqual('same-original-start', record['started_at'])
            self.assertEqual(latest+'.png', record['evidence'])

    def test_navigation_alias_must_have_one_verified_canonical_account(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            old_day = (date.today()-timedelta(days=1)).isoformat()
            self.write(root/old_day/(hs.slug('verified')+'.json'), {
                'date': old_day, 'account': 'verified', 'navigation': {
                    'return_leaf': {'status': 'pending', 'phase': 'return_requested'}}})
            alias = hs.StateStore(['第2标签'], root, aliases={'第2标签': 'verified'})
            self.assertEqual('return_requested', alias.accounts['第2标签']['navigation']['return_leaf']['phase'])
            with self.assertRaisesRegex(hs.PendingError, '映射不唯一'):
                hs.StateStore(['第1标签', '第2标签'], root,
                              aliases={'第1标签': 'verified', '第2标签': 'verified'})

    def test_anonymous_navigation_is_not_bound_across_days(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            old_day = (date.today()-timedelta(days=1)).isoformat()
            self.write(root/old_day/(hs.slug('当前窗口标签3')+'.json'), {
                'date': old_day, 'account': '当前窗口标签3', 'navigation': {
                    'return_leaf': {'status': 'pending', 'phase': 'return_requested'}}})
            state = hs.StateStore(['当前窗口标签3'], root)
            self.assertNotIn('navigation', state.accounts['当前窗口标签3'])

    def test_bad_navigation_record_remains_pending_without_touching_source(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            old_day = (date.today()-timedelta(days=1)).isoformat()
            source = root/old_day/(hs.slug('verified')+'.json')
            self.write(source, {'date': old_day, 'account': 'verified', 'navigation': []})
            before = source.read_bytes()
            with self.assertRaisesRegex(hs.PendingError, '导航记录格式异常'):
                hs.StateStore(['verified'], root)
            self.assertEqual(before, source.read_bytes())

    def test_real_managed_overlay_requires_title_and_close_for_dimmed_identity(self):
        path = hs.LOGS/'scheduler_evidence/2026-10-04/account_3ed81e0fcf/070108_235150_pending_managed_panel_validation.png'
        with Image.open(path) as source:
            frame = source.convert('RGB')
        expected = hs.identity_text_mask(frame.crop(hs.IDENTITY_ROI), threshold=50)
        np.testing.assert_array_equal(expected, cp.current_identity_mask(frame))
        for roi in ((750,190,1170,290), (1320,230,1450,330)):
            missing_anchor = frame.copy()
            missing_anchor.paste((0,0,0), roi)
            normal = hs.identity_text_mask(missing_anchor.crop(hs.IDENTITY_ROI))
            np.testing.assert_array_equal(normal, cp.current_identity_mask(missing_anchor))

    def test_real_managed_generic_identity_below_strict_threshold_stays_unknown(self):
        references = cp.read_checkpoint(hs.STATE_DIR/'account_identities.json')
        self.assertGreaterEqual(len(references), 6)
        path = hs.LOGS/'scheduler_evidence/2026-10-04/account_3ed81e0fcf/070108_235150_pending_managed_panel_validation.png'
        with Image.open(path) as frame:
            matched, _ = cp.match_identity(frame, references)
        self.assertIsNone(matched)

    def test_world_and_weekend_team_badge_preserve_strict_identity_margin(self):
        references = cp.read_checkpoint(hs.STATE_DIR/'account_identities.json')
        config = {'AccountMode':'tab6','TabAccountNames':','.join(references),
                  'TabXs':','.join(str(100+i*100) for i in range(len(references)))}
        s = hs.Scheduler(config, 'multi_solo', True, True)
        s.identity_margin = .08
        s.identity_scores = lambda frame: cp.identity_mask_scores(cp.current_identity_mask(frame),references)
        for filename, name, tab_x in (('tab_1_x180_unknown.png', ROLE_A, 180),
                                      ('tab_3_x450_unknown.png', ROLE_B, 450)):
            with self.subTest(name=name), Image.open(hs.LOGS/'identity_layout_20261004_0744'/filename) as frame:
                scores = s.identity_scores(frame)
                self.assertEqual(name, max(scores, key=scores.get))
                self.assertGreaterEqual(scores[name], .92)
                s.joint_identity_bound = set()
                account = hs.Account(1, name, tab_x)
                self.assertFalse(s.verify_identity(account, frame)[0])
                self.assertIsNone(cp.match_identity(frame, references)[0])
                s.joint_identity_bound = {name}
                self.assertTrue(s.verify_identity(account, frame)[0])
                for roi in ((850,230,1180,360), (1290,260,1420,370)):
                    missing = frame.copy()
                    missing.paste((0,0,0), roi)
                    shifted_roi = tuple(value+15 if index in (0,2) else value for index,value in enumerate(hs.IDENTITY_ROI))
                    np.testing.assert_array_equal(hs.identity_text_mask(missing.crop(shifted_roi)),
                                                  cp.current_identity_mask(missing))

    def test_real_six_tab_joint_identity_is_unique_and_rejects_duplicate_or_blank(self):
        import numpy as np
        receipt = cp.read_checkpoint(hs.LOGS/'identity_recovery_20261004_075245_372401_61eae2c1.json')
        identities = cp.read_checkpoint(hs.STATE_DIR/'account_identities.json')
        masks, expected = {}, {}
        for slot, row in enumerate(sorted(receipt['accounts'], key=lambda row: row['tab_x']), 1):
            with Image.open(row['evidence'][0]) as frame:
                masks[slot] = cp.current_identity_mask(frame)
            expected[slot] = row['name']
        assigned, matrix, gap = cp.joint_identity_assignment(masks, identities)
        self.assertEqual(expected, assigned)
        self.assertGreaterEqual(gap, .08)
        self.assertTrue(all(matrix[slot][name] >= .92 for slot, name in assigned.items()))
        self.assertEqual({1: expected[1], 2: expected[2]},
                         cp.joint_identity_assignment({1: masks[1], 2: masks[2]}, identities)[0])
        self.assertEqual({}, cp.joint_identity_assignment({**masks, 3: masks[1]}, identities)[0])
        self.assertEqual({}, cp.joint_identity_assignment({**masks, 3: np.zeros_like(masks[3])}, identities)[0])

    def test_real_same_server_alternative_below_floor_is_not_a_credible_assignment(self):
        identities = cp.read_checkpoint(hs.STATE_DIR/'account_identities.json')
        with Image.open(hs.LOGS/'scheduler_evidence/2026-10-04/account_c9ba2069ff/235909_602062_verified_identity_shell_recovery.png') as image:
            mask = cp.current_identity_mask(image.convert('RGB'))
        mapping, scores, gap = cp.joint_identity_assignment({1: mask}, identities)
        self.assertEqual({1: ROLE_A}, mapping)
        self.assertGreaterEqual(scores[1][ROLE_A], .92)
        self.assertLess(scores[1][ROLE_B], .92)
        self.assertGreaterEqual(gap, .08)
        # Two genuinely qualifying identities still block every assignment;
        # neither the .92 floor nor .08 ambiguity margin is reduced.
        ambiguous = dict(identities)
        ambiguous[ROLE_B] = identities[ROLE_A]
        self.assertEqual({}, cp.joint_identity_assignment({1: mask}, ambiguous)[0])


if __name__ == '__main__':
    unittest.main()
