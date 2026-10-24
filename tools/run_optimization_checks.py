"""Run only the affected offline checks; list unavailable baseline cases explicitly."""
import argparse
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASELINE_LIMITATIONS = {
    'test_hyol_checkpoints.CheckpointTests.test_real_managed_overlay_requires_title_and_close_for_dimmed_identity': 'Original private screenshot absent',
    'test_hyol_checkpoints.CheckpointTests.test_real_same_server_alternative_below_floor_is_not_a_credible_assignment': 'Original private screenshot absent',
    'test_hyol_checkpoints.CheckpointTests.test_real_six_tab_joint_identity_is_unique_and_rejects_duplicate_or_blank': 'Original account receipt absent',
    'test_hyol_checkpoints.CheckpointTests.test_world_and_weekend_team_badge_preserve_strict_identity_margin': 'Original private account configuration absent',
    'test_hyol_checkpoints.CheckpointTests.test_real_managed_generic_identity_below_strict_threshold_stays_unknown': 'Original identity images absent',
    'test_hyol_recognition_performance.RecognitionPerformanceTests.test_real_frame_decisions_and_coordinates_match_original_transform': 'Original private replay screenshots absent',
}
# The former seventh limitation (pause-audit locale mojibake) was fixed in this
# tree: test_hyol_infrastructure now reads the UTF-8 records with an explicit
# encoding, so it runs with the normal set instead of being excluded.

TARGETS = [
    'test_hyol_optimization', 'test_hyol_progress_repair.ReadOnlyProgressTests',
    'test_hyol_client', 'test_template_spectra', 'test_hyol_observation_lifecycle',
    'test_hyol_release', 'test_hyol_checkpoints.CheckpointTests', 'test_hyol_history.HistoryTests',
    'test_hyol_pr2_safety.SchedulingSafetyTests', 'test_hyol_infrastructure.DiagnosticsTests',
    'test_hyol_recognition_performance',
    'test_offline_full_flow.OfflineFullFlowTests.test_all_eleven_panel_commands_complete_in_offline_replay',
]


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cases = list(flatten(unittest.defaultTestLoader.loadTestsFromNames(TARGETS)))
    runnable = [case for case in cases if case.id() not in BASELINE_LIMITATIONS]
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(runnable))
    report = dict(tests_run=result.testsRun, passed=result.testsRun-len(result.errors)-len(result.failures)-len(result.skipped),
                  success=result.wasSuccessful(), skipped=[(case.id(),reason) for case,reason in result.skipped],
                  failures=[(case.id(),detail) for case,detail in result.failures],
                  errors=[(case.id(),detail) for case,detail in result.errors],
                  executed_cases=[case.id() for case in runnable],
                  separately_reproduced_baseline_limitations=BASELINE_LIMITATIONS,
                  game_input_count=0, full_suite_claimed=False)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    raise SystemExit(main())
