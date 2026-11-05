import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import hyol_network_retry as retry
from hyol_scheduler import PendingError


class NetworkRetryTests(unittest.TestCase):
    def exercise(self, success_at=None, prior=0, ready=True, stop=False):
        s = Mock()
        s.state.accounts = {'a': {'network_retries': {'action': {'attempts': prior}}}}
        s.state.action_status.return_value = 'new'
        clock = [0.0]
        s.wait_interruptible.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        clicks = []
        def click(label):
            clicks.append(clock[0])
            return True
        if stop:
            s.check_stop.side_effect = KeyboardInterrupt()
        with patch.object(retry.time, 'monotonic', side_effect=lambda: clock[0]):
            try:
                retry.run(s, SimpleNamespace(name='a'), 'action', '测试',
                          lambda: success_at is not None and clock[0] >= success_at,
                          lambda: ready, click)
                error = None
            except (PendingError, KeyboardInterrupt) as exc:
                error = exc
        return clicks, error, s

    def test_three_clicks_ten_seconds_apart_then_game_error(self):
        clicks, error, _ = self.exercise()
        self.assertEqual(clicks, [0,10,20])
        self.assertIn('游戏报错', str(error))

    def test_delayed_success_prevents_second_click(self):
        clicks, error, _ = self.exercise(success_at=9.5)
        self.assertEqual(clicks, [0])
        self.assertIsNone(error)

    def test_second_attempt_success_stops_third(self):
        clicks, error, _ = self.exercise(success_at=12)
        self.assertEqual(clicks, [0,10])
        self.assertIsNone(error)

    def test_restart_preserves_total_three_attempt_limit(self):
        clicks, error, _ = self.exercise(prior=2)
        self.assertEqual(clicks, [10])
        self.assertIsInstance(error, PendingError)

    def test_already_successful_has_no_click(self):
        clicks, error, _ = self.exercise(success_at=0)
        self.assertEqual(clicks, [])
        self.assertIsNone(error)

    def test_missing_button_and_f12_have_no_click(self):
        self.assertEqual(self.exercise(ready=False)[0], [])
        clicks, error, _ = self.exercise(stop=True)
        self.assertEqual(clicks, [])
        self.assertIsInstance(error, KeyboardInterrupt)

    def test_legacy_pending_attempts_cannot_restart_three_click_budget(self):
        s = Mock()
        s.state.action_status.return_value = 'new'
        s.state.accounts = {'a': {'actions': {
            'action_network_1': {'status': 'pending'},
            'action_network_2': {'status': 'pending'},
            'action_network_3': {'status': 'pending'}}}}
        now = [0.]
        s.wait_interruptible.side_effect = lambda seconds: now.__setitem__(0, now[0]+seconds)
        clicked = Mock()
        with patch.object(retry.time, 'monotonic', side_effect=lambda: now[0]):
            with self.assertRaises(PendingError):
                retry.run(s, SimpleNamespace(name='a'), 'action', '测试', lambda: False, lambda: True, clicked)
        clicked.assert_not_called()
        self.assertEqual(3, s.state.accounts['a']['network_retries']['action']['attempts'])

    def test_conflicting_originals_block_retry_and_commit_before_budget_change(self):
        from copy import deepcopy
        for key in ('action', 'action_network_1'):
            s = Mock()
            original = {'actions': {key: {'status': 'pending', 'checkpoint_conflict': [
                {'status': 'pending', 'source': 'other-original'}]}},
                'network_retries': {'action': {'attempts': 2, 'verified': False}}}
            s.state.accounts = {'a': deepcopy(original)}
            succeeded, ready, click = Mock(return_value=True), Mock(return_value=True), Mock()
            with self.assertRaisesRegex(PendingError, '重试前停止'):
                retry.run(s, SimpleNamespace(name='a'), 'action', '测试', succeeded, ready, click)
            self.assertEqual(original, s.state.accounts['a'])
            succeeded.assert_not_called()
            ready.assert_not_called()
            click.assert_not_called()
            s.commit_action.assert_not_called()
            s.state.save_account.assert_not_called()


if __name__ == '__main__':
    unittest.main()
