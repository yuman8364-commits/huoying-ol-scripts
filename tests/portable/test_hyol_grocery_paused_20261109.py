"""The deferred purchase entry must not even bind a window or mutate its old ledger."""
from unittest.mock import Mock
import unittest
from hyol_errors import TaskUnavailable
from hyol_grocery import run

class PurchasePausedTests(unittest.TestCase):
    def test_public_entry_stops_before_any_runner_or_game_access(self):
        runner=Mock()
        with self.assertRaisesRegex(TaskUnavailable,'已按用户要求暂缓'):run(runner)
        self.assertEqual([],runner.mock_calls)

if __name__=='__main__':unittest.main()
