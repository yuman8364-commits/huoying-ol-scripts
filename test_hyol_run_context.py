"""Focused tests for atomic tab-map rebind failure handling."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import hyol_scheduler as hs
from hyol_run_context import RunContext


class RunContextRebindTests(unittest.TestCase):
    def test_event_failure_rolls_back_all_published_identity_fields(self):
        account = hs.Account(1, 'role', 180)
        old = {'centers': (180,), 'mapping': {1: 'role'}, 'refs': {}}
        context = RunContext(Path('.'), Path('.'), binding=old)
        state = SimpleNamespace(
            aliases={},
            global_state={'tab_map': {'role': 180},
                          'verified_solo_tab_map': {'role': 180}})
        scheduler = SimpleNamespace(
            accounts=[account], by_name={'role': account}, state=state,
            available_account_names={'role'}, joint_identity_bound={'role'},
            _bound_identity_names={1: 'role'},
            _bound_identity_masks={1: 'old'},
            _known_identity_materials={'old': 'unchanged'},
            _tab_title_masks={1: 'original'},
            active=account, _title_mask=Mock(return_value='new'),
            event=Mock(side_effect=RuntimeError('sink rejected event')))
        snapshots = {name: getattr(scheduler, name) for name in (
            'accounts', 'by_name', 'available_account_names',
            'joint_identity_bound', '_bound_identity_names',
            '_bound_identity_masks', '_known_identity_materials',
            '_tab_title_masks', 'active')}
        observations = {1: [np.ones((3, 3), dtype=bool)]}
        with patch('hyol_accounts.observe_accounts',
                   return_value=((315,), observations, {1: object()}, {})), \
             patch('hyol_accounts.register_observations',
                   return_value=({1: 'role'}, {'role': {'mask': 'new'}})):
            with self.assertRaisesRegex(RuntimeError, 'sink rejected event'):
                context.rebuild(scheduler)
        self.assertIs(context.binding, old)
        self.assertFalse(context.probing)
        self.assertEqual(1, context.rebind_count)
        for name, value in snapshots.items():
            self.assertIs(getattr(scheduler, name), value)
        self.assertEqual({'role': 180}, state.global_state['tab_map'])
        self.assertEqual({'role': 180}, state.global_state['verified_solo_tab_map'])
        self.assertFalse(hasattr(scheduler, '_dynamic_identity_names'))


if __name__ == '__main__':
    unittest.main()
