"""Current-account binding uses the same identity evidence as tab discovery."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

import hyol_accounts as accounts
import hyol_checkpoints as cp
import hyol_scheduler as hs


def glyph_samples():
    rng = np.random.default_rng(42)
    reference = (rng.random((32, 145)) < .2).astype(np.uint8)
    reference[:12] = 0
    current = reference.copy()
    region = current[:, :hs.IDENTITY_SERVER_WIDTH].copy().reshape(-1)
    region[np.flatnonzero(region)[:5]] = 0
    region[np.flatnonzero(1-region)[-5:]] = 1
    current[:, :hs.IDENTITY_SERVER_WIDTH] = region.reshape(32, hs.IDENTITY_SERVER_WIDTH)
    rival = reference.copy()
    flat = rival.reshape(-1)
    foreground, background = np.flatnonzero(flat), np.flatnonzero(1-flat)
    flat[foreground[-50:]] = 0
    flat[background[-50:]] = 1
    return reference, current, rival


def frame(mask, **fields):
    image = Image.new('RGB', (1920, 1032))
    image.info.update(mask=mask, **fields)
    return image


class CurrentIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reference, self.current, self.rival = glyph_samples()
        self.refs = {key: dict(mask=cp.encode_mask(mask), shape=list(mask.shape))
                     for key, mask in (('account-a', self.reference), ('account-b', self.rival))}
        self.path = self.root/'account_identities.json'
        hs.atomic_json(self.path, self.refs)
        self.anonymous = hs.Account(1, '当前窗口标签1', 180)
        self.s = SimpleNamespace(
            state=hs.StateStore([self.anonymous.name], self.root/'window_test'),
            shared_state_directory=self.root, automatic_accounts=True,
            templates=None, no_content_clicks=False, content_click_count=0,
            capture=Mock(return_value=frame(self.current)),
            wait_interruptible=Mock(), check_stop=Mock(), event=Mock(),
            main_ready=Mock(return_value=False),
            offline_dialog_visible=lambda image: image.info.get('offline', False),
            _generic_active_tab_x=lambda image: image.info.get('tab_x', 180),
            template=lambda *args, **kwargs: (False, 0., 0, 0))
        self.addCleanup(patch.stopall)
        patch.object(cp, 'current_identity_mask', side_effect=lambda image, *args: image.info['mask']).start()
        patch.object(hs, 'LOGS', self.root/'logs').start()

    def test_below_floor_same_server_rival_does_not_block_current_account(self):
        scores = cp.identity_mask_scores(self.current, self.refs)
        self.assertGreaterEqual(scores['account-a'], .92)
        self.assertLess(scores['account-b'], .92)
        self.assertLess(scores['account-a']-scores['account-b'], .08)
        self.assertIsNone(cp.match_identity(frame(self.current), self.refs)[0])
        known = hs.StateStore(['account-a'], self.root)
        known.set_action('account-a', 'survival_battle_step_2', 'pending', 'original unknown result')
        original = known.action_record('account-a', 'survival_battle_step_2')
        bound = cp.bind_current_identity(self.s, self.anonymous)
        self.assertEqual('account-a', bound.name)
        self.assertEqual(original, self.s.state.action_record(bound.name, 'survival_battle_step_2'))
        self.assertTrue(accounts.verify_dynamic_identity(self.s, bound, frame(self.current))[0])
        self.assertEqual(0, self.s.content_click_count)

    def test_directly_recognizable_role_still_requires_three_stable_frames(self):
        hs.atomic_json(self.path, {'account-a': self.refs['account-a']})
        with patch.object(accounts, 'collect_current_identity', wraps=accounts.collect_current_identity) as collect:
            bound = cp.bind_current_identity(self.s, self.anonymous)
        self.assertEqual('account-a', bound.name)
        collect.assert_called_once()
        self.assertGreaterEqual(self.s.capture.call_count, 4)
        self.assertEqual(4, self.s.event.call_args.kwargs['observations'])

    def change_after_observation(self, changed):
        def wait(seconds):
            if seconds == 3:
                self.s.capture.return_value = changed
        self.s.wait_interruptible.side_effect = wait

    def test_two_qualifying_duplicate_roles_remain_unbound(self):
        hs.atomic_json(self.path, {'account-a': self.refs['account-a'],
                                  'account-b': self.refs['account-a']})
        before = self.path.read_bytes()
        with patch.object(accounts, 'observe_accounts', side_effect=hs.PendingError('身份有歧义')):
            with self.assertRaises(hs.PendingError):
                cp.bind_current_identity(self.s, self.anonymous)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([self.anonymous.name], list(self.s.state.accounts))
        self.assertFalse((self.root/self.s.state.day/(hs.slug('account-a')+'.json')).exists())

    def test_role_change_after_observation_preserves_original_ledgers(self):
        hs.atomic_json(self.path, {'account-a': self.refs['account-a']})
        self.change_after_observation(frame(np.zeros_like(self.current)))
        before = self.path.read_bytes()
        with self.assertRaises(hs.PendingError):
            cp.bind_current_identity(self.s, self.anonymous)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([self.anonymous.name], list(self.s.state.accounts))
        self.assertFalse((self.root/self.s.state.day/(hs.slug('account-a')+'.json')).exists())

    def test_tab_change_after_observation_never_promotes_identity(self):
        hs.atomic_json(self.path, {'account-a': self.refs['account-a']})
        self.change_after_observation(frame(self.current, tab_x=315))
        with self.assertRaises(hs.PendingError):
            cp.bind_current_identity(self.s, self.anonymous)
        self.assertEqual([self.anonymous.name], list(self.s.state.accounts))

    def test_disconnect_after_observation_never_promotes_identity(self):
        hs.atomic_json(self.path, {'account-a': self.refs['account-a']})
        self.change_after_observation(frame(self.current, offline=True))
        with self.assertRaises(hs.AccountOffline):
            cp.bind_current_identity(self.s, self.anonymous)
        self.assertEqual([self.anonymous.name], list(self.s.state.accounts))

    def test_pending_anonymous_action_without_mask_blocks_before_normalizing_pages(self):
        old = self.s.state.accounts[self.anonymous.name]
        old['actions'] = {'survival_battle_step_2': {'status': 'pending', 'operation_id': 'original'}}
        with patch.object(accounts, 'bind_visible_identity') as bind:
            with self.assertRaisesRegex(hs.PendingError, '匿名.*身份'):
                cp.bind_current_identity(self.s, self.anonymous)
        bind.assert_not_called()
        self.assertEqual('original', old['actions']['survival_battle_step_2']['operation_id'])

    def test_old_anonymous_role_change_remains_pending(self):
        old = self.s.state.accounts[self.anonymous.name]
        old['identity_mask'] = cp.encode_mask(np.zeros_like(self.current))
        old['actions'] = {'survival_battle_step_2': {'status': 'pending', 'operation_id': 'original'}}
        with self.assertRaisesRegex(hs.PendingError, '角色已变化'):
            cp.bind_current_identity(self.s, self.anonymous)
        self.assertEqual('pending', old['actions']['survival_battle_step_2']['status'])


if __name__ == '__main__':
    unittest.main()
