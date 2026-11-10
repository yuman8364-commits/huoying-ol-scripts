"""Skipping only dismisses the guide; it never rewrites settings or starts tasks."""
import ast
import json
import os
import tempfile
import time
from datetime import datetime
from types import ModuleType
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from hyol_onboarding import Onboarding, STEPS


class OnboardingSkipTests(TestCase):
    def setUp(self):
        # Load only the actual persistence helpers, not the Windows game runtime.
        source = Path(__file__).resolve().parents[2] / 'client/hyol_scheduler.py'
        tree = ast.parse(source.read_text('utf-8-sig'))
        helpers = ast.Module(body=[node for node in tree.body
                                  if isinstance(node, ast.FunctionDef)
                                  and node.name in ('atomic_json', 'now_iso')], type_ignores=[])
        scheduler = ModuleType('hyol_scheduler')
        scheduler.__dict__.update(Path=Path, json=json, os=os, tempfile=tempfile,
                                  time=time, datetime=datetime)
        exec(compile(helpers, str(source), 'exec'), scheduler.__dict__)
        module_patch = patch.dict('sys.modules', hyol_scheduler=scheduler)
        module_patch.start()
        self.addCleanup(module_patch.stop)
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.guide = Onboarding(self.root)

    def test_skip_from_every_step_survives_reload_without_touching_settings(self):
        settings = self.root / 'desktop_settings.json'
        settings.write_text('{"keep":"my choices"}', encoding='utf-8')
        for count in range(6):
            with self.subTest(step=count + 1):
                self.guide._save(dict(version=1, steps=list(STEPS[:count]), completed_at=''))
                result = self.guide.skip()
                self.assertFalse(result['required'])
                self.assertFalse(Onboarding(self.root).load()['required'])
                self.assertEqual(list(STEPS[:count]), result['steps'])
                self.assertFalse(result['completed_at'])
                self.assertEqual('{"keep":"my choices"}', settings.read_text('utf-8'))

    def test_skipped_guide_can_be_revisited_and_completed(self):
        self.guide.skip()
        for step in STEPS:
            self.assertFalse(self.guide.acknowledge(step)['required'])
        self.assertTrue(self.guide.finish()['completed_at'])

    def test_write_failure_is_not_reported_as_success(self):
        with patch.object(self.guide, '_save', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                self.guide.skip()
        self.assertTrue(self.guide.load()['required'])

    def test_corrupt_record_is_backed_up_when_skipping(self):
        self.guide.path.parent.mkdir(parents=True, exist_ok=True)
        self.guide.path.write_text('broken', encoding='utf-8')
        self.guide.skip()
        self.assertFalse(self.guide.load()['required'])
        backups = list(self.guide.path.parent.glob('desktop_onboarding_损坏备份_*.json'))
        self.assertEqual(1, len(backups))
        self.assertEqual('broken', backups[0].read_text('utf-8'))
