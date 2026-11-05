"""Publishing after a failed team binding still requires a terminal receipt."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from tools.publish_stop_cleanup_clients import runtime_ended


class EndedTeamRunTests(unittest.TestCase):
    def test_failed_team_binding_has_terminal_controller_receipt(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/'unified_latest.json'
            p.write_text(json.dumps(dict(status='pending', result=2, at=now.isoformat())), encoding='utf-8')
            self.assertTrue(runtime_ended(folder, [dict(
                event='generic_team_checkpoints_bound', ts=(now-timedelta(milliseconds=2)).isoformat())]))

    def test_later_game_activity_invalidates_previous_receipt(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/'unified_latest.json'
            p.write_text(json.dumps(dict(status='pending', result=2, at=now.isoformat())), encoding='utf-8')
            self.assertFalse(runtime_ended(folder, [dict(
                event='content_click', ts=(now+timedelta(seconds=1)).isoformat())]))

    def test_missing_or_nonterminal_controller_receipt_cannot_allow_publication(self):
        now = datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as folder:
            events = [dict(event='generic_team_checkpoints_bound', ts=now)]
            self.assertFalse(runtime_ended(folder, events))
            p = Path(folder)/'unified_latest.json'
            for receipt in (dict(status='running', result=2, at=now),
                            dict(status='pending', result='2', at=now), dict(status='pending')):
                p.write_text(json.dumps(receipt), encoding='utf-8')
                self.assertFalse(runtime_ended(folder, events))


if __name__ == '__main__':
    unittest.main()
