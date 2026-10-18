"""Offline model evidence and gate tests. All input methods are replaced by mocks."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

import hyol_scheduler as hs
from hyol_recognition import Detection, OnnxDetector, fuse
from tools.verify_model_gate import (ROOT, audit_dataset, audit_model, audit_training, audit_validation, authorization_cases,
                                    detector_stub, fusion_cases, read_json)
from tools.write_model_evaluation import write_report


class ModelEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = audit_model()

    def test_evidence_hashes_labels_sessions_and_frozen_thresholds(self):
        self.assertEqual('PASS', self.result['offline_result'])
        self.assertEqual(dict(train=24, val=24, test=24), self.result['splits']['images'])
        self.assertEqual(72, self.result['splits']['unique_images'])
        self.assertEqual(dict(tp=22, fp=0, fn=15), self.result['total'])
        self.assertEqual(100, self.result['completed_epochs'])
        self.assertEqual(5, len(self.result['disabled']))
        self.assertEqual(9, len(self.result['pending_classes']))

    def test_offline_pass_does_not_authorize_clicks_or_live_pass(self):
        self.assertFalse(self.result['live_pass'])
        self.assertFalse(self.result['model_quality_pass'])
        self.assertFalse(self.result['activation_changed'])
        self.assertEqual(0, self.result['input_operations'])
        self.assertEqual(dict(candidate=[], shipped=[]), self.result['real_model_authorizations'])
        self.assertEqual(264, self.result['gate_cases']['real_held_out_model_only'])
        self.assertEqual([], self.result['gate_cases']['failures'])

    def test_date_groups_are_not_promoted_to_verified_acquisition_sessions(self):
        self.assertFalse(self.result['capture_session_identity_verified'])
        self.assertIn('exact_acquisition_session_ids_missing', self.result['model_quality_blockers'])
        self.assertEqual('val', self.result['training_provenance']['validation_split'])
        self.assertFalse(self.result['validation_replay']['replayed'])

    def test_unreviewed_truth_is_rejected_before_evaluation(self):
        dataset = ROOT / 'datasets/multiclass-20261003/source_manifest.json'
        altered = read_json(dataset)
        altered['samples'][0]['reviewed'] = False
        with patch('tools.verify_model_gate.read_json', return_value=altered):
            with self.assertRaisesRegex(ValueError, 'unreviewed'):
                audit_dataset(dataset)

    def test_calibration_threshold_drift_is_rejected(self):
        real = read_json
        def altered(path):
            value = real(path)
            if Path(path).name == 'detector.fixed.json':
                value['thresholds']['daily_practice_toolbar.bmp'] = .95
            return value
        with patch('tools.verify_model_gate.read_json', side_effect=altered):
            with self.assertRaisesRegex(ValueError, 'thresholds changed'):
                audit_model()

    def test_training_split_swap_is_rejected(self):
        dataset = ROOT/'datasets/multiclass-20261003/source_manifest.json'
        classes = read_json(dataset)['classes']
        config = read_json(dataset.parent/'data.yaml')
        config['train'] = 'images/test'
        with patch('tools.verify_model_gate.read_json', return_value=config):
            with self.assertRaisesRegex(ValueError, 'training split path drift: train'):
                audit_training(ROOT, dataset, classes)


class ValidationReplayTests(unittest.TestCase):
    def fixture(self, folder):
        image = Path(folder)/'validation.png'
        Image.new('RGB', (40, 40)).save(image)
        data = dict(classes=['fixture'], samples=[dict(image=image.name, split='val',
                                                     boxes=[dict(target='fixture', xyxy=[10, 10, 30, 30])])])
        grid = (.50, .55, .60, .65, .70, .75, .80, .85, .90, .95, .99, 1.0)
        trials = [dict(threshold=value, tp=int(value <= .95), fp=0, fn=int(value > .95)) for value in grid]
        calibration = dict(fixture=dict(chosen=trials[0], trials=trials))
        manifest = Path(folder)/'manifest.json'
        manifest.write_text(json.dumps(dict(mode='shadow', click_validated_targets=[], thresholds={})), encoding='utf-8')
        detector = Mock(session=object(), error='', manifest=read_json(manifest))
        detector.predict.return_value = [Detection('fixture', (10, 10, 30, 30), .95, 'yolo', True)]
        return data, calibration, manifest, detector

    def test_validation_replay_checks_all_frozen_trials_and_preserves_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            data, calibration, manifest, detector = self.fixture(folder)
            before = manifest.read_bytes()
            with patch('tools.verify_model_gate.OnnxDetector', return_value=detector):
                result = audit_validation(data, Path(folder)/'dataset.json', manifest, calibration, replay=True)
            self.assertTrue(result['replayed'])
            self.assertEqual(12, result['trial_count'])
            self.assertEqual(1, result['images'])
            self.assertFalse(result['thresholds_changed'])
            self.assertEqual(before, manifest.read_bytes())
            self.assertEqual('shadow', detector.manifest['mode'])
            self.assertEqual([], detector.manifest['click_validated_targets'])

    def test_tampered_validation_trial_is_rejected_by_actual_predictions(self):
        with tempfile.TemporaryDirectory() as folder:
            data, calibration, manifest, detector = self.fixture(folder)
            calibration['fixture']['trials'][0]['fn'] = 4
            with patch('tools.verify_model_gate.OnnxDetector', return_value=detector):
                with self.assertRaisesRegex(ValueError, 'validation replay count drift'):
                    audit_validation(data, Path(folder)/'dataset.json', manifest, calibration, replay=True)


class ModelReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = audit_model()
        cls.result['current_cpu_latency_ms'] = dict(mean=123., max=150.)

    def test_existing_evidence_is_preserved_without_rerunning_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'old.json'
            output.write_text('prior evidence', encoding='utf-8')
            report = Path(folder)/'MODEL_EVALUATION.md'
            report.write_text('prior report', encoding='utf-8')
            with patch('tools.write_model_evaluation.audit_model') as audit:
                with self.assertRaisesRegex(ValueError, 'preserve prior evidence'):
                    write_report(Path(folder), evidence_path=output)
            audit.assert_not_called()
            self.assertEqual('prior evidence', output.read_text(encoding='utf-8'))
            self.assertEqual('prior report', report.read_text(encoding='utf-8'))

    def test_failed_audit_keeps_prior_report_and_creates_no_pass_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder)/'MODEL_EVALUATION.md'
            report.write_text('prior report', encoding='utf-8')
            output = Path(folder)/'failed.json'
            with patch('tools.write_model_evaluation.audit_model', return_value=dict(offline_result='FAIL')):
                with self.assertRaisesRegex(RuntimeError, 'audit failed'):
                    write_report(Path(folder), evidence_path=output)
            self.assertFalse(output.exists())
            self.assertEqual('prior report', report.read_text(encoding='utf-8'))

    def test_new_reports_keep_both_machine_records_and_describe_session_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            from datetime import datetime
            same_instant = datetime.fromisoformat('2026-10-13T16:00:00+08:00')
            with patch('tools.write_model_evaluation.datetime') as clock, \
                 patch('tools.write_model_evaluation.audit_model', side_effect=lambda *a, **k: copy.deepcopy(self.result)):
                clock.now.return_value.astimezone.return_value = same_instant
                first, second = write_report(Path(folder)), write_report(Path(folder))
            self.assertNotEqual(first['evidence'], second['evidence'])
            self.assertTrue(Path(first['evidence']).exists())
            self.assertTrue(Path(second['evidence']).exists())
            text = Path(second['report']).read_text(encoding='utf-8')
            self.assertIn('跨午夜', text)
            self.assertIn('不计入上述纯审计项', text)
            self.assertIn(Path(second['evidence']).name, text)
            self.assertFalse(second['model_quality_pass'])
            self.assertFalse(second['live_pass'])


class ModelAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classes = read_json(ROOT / 'models/candidates/multiclass-20261003/detector.fixed.json')['classes']

    def test_all_classes_require_health_live_proof_hash_threshold_and_allowlist(self):
        cases = authorization_cases(self.classes)
        self.assertEqual(187, len(cases))
        for case in cases:
            with self.subTest(target=case['target'], case=case['case']):
                self.assertEqual(case['expected'], case['actual'])

    def test_missing_disabled_ambiguous_conflicting_and_invalid_detections_fail_closed(self):
        cases = fusion_cases(self.classes)
        self.assertEqual(121, len(cases))
        for case in cases:
            with self.subTest(target=case['target'], case=case['case']):
                self.assertFalse(case['actual'])

    def test_missing_model_and_hash_failure_never_authorize(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = OnnxDetector(Path(folder) / 'missing.json')
            self.assertFalse(missing.enabled_for(self.classes[0]))
            path = Path(folder) / 'detector.json'
            path.write_text(json.dumps(dict(mode='validated', file='missing.onnx', sha256='a'*64,
                                            format='yolo11_raw_detection', classes=[self.classes[0]],
                                            click_validated_targets=[self.classes[0]])), encoding='utf-8')
            bad = OnnxDetector(path)
            self.assertIsNone(bad.session)
            self.assertTrue(bad.error)
            self.assertFalse(bad.enabled_for(self.classes[0]))

    def test_disabled_class_score_one_still_produces_no_prediction(self):
        target = self.classes[0]
        detector = detector_stub(dict(classes=[target], image_size=32, thresholds={target: 1.0},
                                      disabled_calibration_targets=[target]))
        detector.cached_frame, detector.cached_predictions = None, []
        # A score equal to one would pass a numeric threshold alone.
        detector.session = Mock()
        detector.session.get_inputs.return_value = [Mock(name='input')]
        detector.session.get_inputs.return_value[0].name = 'images'
        detector.session.run.return_value = [np.array([[[16], [16], [8], [8], [1.0]]], dtype=np.float32)]
        self.assertEqual([], detector.predict(Image.new('RGB', (32, 32))))


class SchedulerModelGateTests(unittest.TestCase):
    def make_scheduler(self, detector, frames, store=None):
        scheduler = object.__new__(hs.Scheduler)
        scheduler.detector = detector
        scheduler.diagnostics = None
        scheduler.config = {'RecognitionRetrySeconds': '0'}
        scheduler.templates = store or hs.TemplateStore(.90)
        scheduler.content_guard = Mock(side_effect=frames)
        scheduler.wait_interruptible = Mock()
        scheduler.event = Mock()
        scheduler.click_point = Mock()
        return scheduler

    def test_all_real_held_out_model_candidates_cannot_click_without_template(self):
        manifest = read_json(ROOT / 'models/candidates/multiclass-20261003/detector.fixed.json')
        report = read_json(ROOT / 'logs/multiclass_test_20261003.json')
        class MissingTemplateMatch:
            last_match_size = (5, 5)
            last_match_source = 'template'
            def match(self, *args, **kwargs):
                return False, .1, -1, -1
        checked = 0
        for row in report['rows']:
            with Image.open(row['image']) as source:
                frame = source.convert('RGB')
            predictions = [Detection(**value) for value in row['predictions']]
            detector = detector_stub(manifest)
            detector.predict = Mock(return_value=predictions)
            for target in manifest['classes']:
                with self.subTest(image=row['sha256'], target=target):
                    scheduler = self.make_scheduler(detector, [frame], MissingTemplateMatch())
                    self.assertFalse(scheduler.click_template(hs.Account(1, 'offline_replay', 180), target,
                                                            (0, 0, frame.width, frame.height), 1, 1, 'offline', ()))
                    scheduler.click_point.assert_not_called()
                    checked += 1
        self.assertEqual(264, checked)

    def test_confirmed_disabled_second_frame_cannot_use_model_only_override(self):
        folder = ROOT / 'logs/scheduler_evidence/2026-08-21'
        with Image.open(folder / 'mail_warehouse_current_probe.png') as image:
            before = image.convert('RGB')
        with Image.open(folder / 'mail_after_claim_settled.png') as image:
            after = image.convert('RGB')
        target, disabled, roi = 'mail_claim_all.bmp', 'mail_claim_all_disabled.bmp', (1001, 689, 1251, 919)
        real_store = hs.TemplateStore(.90)
        found, score, x, y = real_store.match(before, hs.ASSETS / target, roi, .995)
        self.assertTrue(found, 'real enabled mail button missing from evidence')
        height, width = real_store.load(hs.ASSETS / target).shape
        self.assertTrue(real_store.match(after, hs.ASSETS / disabled, roi, .995)[0])
        candidate = Detection(target, (x, y, x+width, y+height), .999, 'yolo', True)
        sha = 'a'*64
        detector = detector_stub(dict(mode='validated', classes=[target], sha256=sha,
                                      thresholds={target: .9}, click_validated_targets=[target],
                                      disabled_calibration_targets=[],
                                      click_validation_evidence={target: dict(status='live_pass', model_sha256=sha,
                                                                            evidence='OFFLINE_FIXTURE_ONLY')}))
        detector.predict = Mock(return_value=[candidate])
        class Store:
            last_match_size = (height, width)
            last_match_source = 'template'
            def match(self, frame, path, bounds, threshold=None):
                if frame is after and path.name == target:
                    # Force the model-only fallback while retaining the real disabled classifier.
                    self.last_match_size, self.last_match_source = (height, width), 'template'
                    return False, .1, -1, -1
                result = real_store.match(frame, path, bounds, threshold)
                self.last_match_size, self.last_match_source = real_store.last_match_size, real_store.last_match_source
                return result
            def load(self, path):
                return real_store.load(path)
        scheduler = self.make_scheduler(detector, [before, after], Store())
        self.assertFalse(scheduler.click_template(hs.Account(1, 'offline_replay', 180), target, roi,
                                                10, 5, 'offline', (), threshold=.995))
        self.assertEqual(2, scheduler.content_guard.call_count)
        self.assertEqual('disabled_control', scheduler.last_detection.reason)
        scheduler.click_point.assert_not_called()

    def test_second_frame_missing_control_stops_before_input(self):
        frame = Image.new('RGB', (1920, 1032))
        scheduler = self.make_scheduler(None, [frame, frame])
        accepted = Detection('daily_practice_toolbar.bmp', (10., 10., 30., 30.), .99, 'yolo', True)
        def match(*args, **kwargs):
            if scheduler.content_guard.call_count == 1:
                scheduler.last_detection = accepted
                return True, .99, 10, 10
            scheduler.last_detection = Detection(accepted.target, None, 0, 'template', False, 'missing_template')
            return False, 0, -1, -1
        scheduler.active_template = Mock(side_effect=match)
        self.assertFalse(scheduler.click_template(hs.Account(1, 'offline_replay', 180), accepted.target,
                                                (0, 0, 100, 100), 2, 2, 'offline', ()))
        self.assertEqual(2, scheduler.active_template.call_count)
        scheduler.click_point.assert_not_called()


if __name__ == '__main__':
    unittest.main()
