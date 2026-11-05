"""The live diagnostic batch must never overwrite a task checkpoint."""
from test_identity_fixtures import ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F, legacy_config
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from PIL import Image

from tools import solo_acceptance_live as probe
from tools import recover_ranking_page_live as recovery
from tools import resume_treasure_live as treasure_recovery


class SoloAcceptanceToolTests(unittest.TestCase):
    def test_forest_team_badge_keeps_exact_identity_layout_and_wrong_role_rejected(self):
        s = probe.hs.Scheduler(legacy_config(),
                               'multi_solo', False, True)
        frame = Image.open(probe.hs.EVIDENCE_DIR/'2026-10-05/account_ae1662240e/'
                           '115228_293742_pending_treasure_resume.png').convert('RGB')
        self.assertFalse(s.templates.match(frame, probe.hs.ASSETS/'identity_team_badge_core_current.bmp',
                                          (106,75,139,105), .98)[0])
        self.assertTrue(s.verify_identity(s.by_name[ROLE_A], frame)[0])
        self.assertFalse(s.verify_identity(s.by_name[ROLE_B], frame)[0])
        self.assertFalse(s.verify_identity(s.by_name[ROLE_C], frame)[0])

    def test_reordered_treasure_tabs_preserve_enrolled_identity_indices(self):
        s = probe.hs.Scheduler(legacy_config(),
                               'multi_solo', False, True)
        mapping = {ROLE_A: 180, ROLE_C: 315, ROLE_B: 450,
                   ROLE_D: 585, ROLE_E: 855}
        expected = {name: account.index for name, account in s.by_name.items()}
        s.accounts = treasure_recovery.bound_tab_accounts(s, mapping, (180,315,450,585,720,855))
        for account in s.accounts:
            if account.name in mapping:
                self.assertEqual(account.index, expected[account.name])
            else:
                self.assertFalse(account.identity_path.exists())
        account = next(row for row in s.accounts if row.name == ROLE_A)
        frame = Image.open(probe.hs.EVIDENCE_DIR/'2026-10-05/account_ae1662240e/'
                           '114904_386298_pending_treasure_resume.png').convert('RGB')
        self.assertTrue(s.verify_identity(account, frame)[0])
        other = next(row for row in s.accounts if row.name == ROLE_C)
        self.assertFalse(s.verify_identity(other, frame)[0])

    def test_treasure_battle_owner_requires_every_enrolled_name_glyph(self):
        frame = Image.open(probe.hs.LOGS/'preflight_20261005_113158_523211_88105e7c.png').convert('RGB')
        runner = Mock()
        runner.account = probe.hs.Account(1, ROLE_C, 315)
        with patch.object(treasure_recovery.treasure, 'battle_visible', return_value=True):
            self.assertTrue(treasure_recovery.battle_owner_visible(runner, frame))
            changed = frame.copy()
            changed.paste('black', (518, 429, 530, 444))
            self.assertFalse(treasure_recovery.battle_owner_visible(runner, changed))
        with patch.object(treasure_recovery.treasure, 'battle_visible', return_value=False):
            self.assertFalse(treasure_recovery.battle_owner_visible(runner, frame))

    def test_original_ranking_panel_accepts_only_pixel_rounding_and_rejects_changed_power(self):
        base = probe.hs.EVIDENCE_DIR/'2026-10-05/account_ae1662240e'
        reference = Image.open(base/'110713_485796_pending_solo_ranking_pending.png').convert('RGB')
        same = Image.open(base/'111618_155662_pending_ranking_page_recovery.png').convert('RGB')
        self.assertTrue(recovery.same_owned_panel(same, reference))
        changed = same.copy()
        changed.paste('white', (375, 165, 395, 180))
        self.assertFalse(recovery.same_owned_panel(changed, reference))

    def test_arena_title_loading_waits_for_actual_controls_with_zero_input(self):
        s = Mock()
        frame = Image.new('RGB', (1920, 1032))
        s.capture.return_value = frame
        s._arena_victory_visible.return_value = False
        s._arena_defeat_visible.return_value = False
        s._arena_waiting_battle_visible.return_value = False
        s._combat_ready_visible.side_effect = [False, False, True]
        self.assertIs(probe.wait_arena_page(s, None), frame)
        self.assertEqual(s.capture.call_count, 3)
        self.assertEqual(s.wait_interruptible.call_count, 2)
        s.click_template.assert_not_called()
        s.commit_action.assert_not_called()

    def test_arena_original_result_stops_observation_without_close_or_commit(self):
        s = Mock()
        s._arena_victory_visible.return_value = True
        with self.assertRaises(probe.hs.PendingError):
            probe.wait_arena_page(s, None)
        s.click_template.assert_not_called()
        s.commit_action.assert_not_called()

    def test_multiple_accounts_keep_all_task_ledgers_and_separate_batch_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, logs = root/'state', root/'logs'
            ledgers = {}
            for name in ('first', 'second'):
                path = state/probe.hs.date.today().isoformat()/(probe.hs.slug(name)+'_tasks.json')
                ledger = {'arena': {'status': 'pending', 'phase': 'result_sent', 'before': 3}}
                probe.hs.atomic_json(path, ledger)
                ledgers[name] = (path, ledger)
            def run(task, name):
                path, ledger = ledgers[name]
                ledger[task] = {'status': 'done', 'task_completed': True, 'home_returned': True}
                probe.hs.atomic_json(path, ledger)
                return {'account': name, 'task': task, 'status': 'live_pass'}
            with patch.object(probe.hs, 'STATE_DIR', state), patch.object(probe.hs, 'LOGS', logs), \
                    patch.object(probe, 'manual_pause_status', return_value={'paused': False}), \
                    patch.object(probe, 'run', side_effect=run):
                result = probe.run_batch(('first', 'second'), ('mail', 'welfare'))
            self.assertEqual(len(result['accounts']), 4)
            self.assertEqual(Path(result['report_path']).parent, logs)
            self.assertEqual(json.loads(Path(result['report_path']).read_text(encoding='utf-8')), result)
            for path, ledger in ledgers.values():
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), ledger)
                self.assertEqual(ledger['arena']['phase'], 'result_sent')

    def test_corrupt_report_shaped_ledger_stops_before_live_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'state'/probe.hs.date.today().isoformat()/(probe.hs.slug('first')+'_tasks.json')
            original = {'accounts': [], 'report_path': 'a batch report'}
            probe.hs.atomic_json(path, original)
            with patch.object(probe.hs, 'STATE_DIR', root/'state'), \
                    patch.object(probe.hs, 'LOGS', root/'logs'), \
                    patch.object(probe, 'manual_pause_status', return_value={'paused': False}), \
                    patch.object(probe, 'run') as run:
                with self.assertRaises(probe.hs.PendingError):
                    probe.run_batch(('first',), ('mail',))
                run.assert_not_called()
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), original)


if __name__ == '__main__':
    unittest.main()
