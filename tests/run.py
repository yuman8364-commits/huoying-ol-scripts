"""Single offline test runner; PowerShell selects Python and forwards options."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SUPPORT_MODULES = {"test_identity_fixtures"}
VALIDATORS = (
    ("黑市安全静态检查", "test_market_safety_static.py"),
    ("多账号静态检查", "validate_multi_static.py"),
    ("模板离线验证", "validate_templates.py"),
)
ACCEPTANCE_TEST = "test_offline_full_flow.OfflineFullFlowTests.test_all_eleven_panel_commands_complete_in_offline_replay"
ACCEPTANCE_ENV = "HYOL_TEST_ACCEPTANCE_DIR"


def now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def unique_suite(suite):
    """Imported classes and overlapping selectors execute each test once."""
    cases, duplicates = {}, []
    for case in flatten(suite):
        key = case.id()
        if key in cases:
            duplicates.append(key)
        else:
            cases[key] = case
    if not cases:
        raise ValueError("未找到测试，不能记为通过。")
    return unittest.TestSuite(cases.values()), sorted(set(duplicates))


def normalize_target(target: str) -> str:
    target = target.replace("\\", "/")
    if target.endswith(".py"):
        target = target[:-3]
    target = target.removeprefix("./").replace("/", ".")
    if not target or target.startswith("."):
        raise ValueError(f"无效测试名称：{target!r}")
    return target


def collect_tests(targets=(), *, root=ROOT):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    if targets:
        for target in targets:
            suite.addTests(loader.loadTestsFromName(normalize_target(target)))
    else:
        excluded = SUPPORT_MODULES | {Path(name).stem for _, name in VALIDATORS}
        for path in sorted(root.glob("test_*.py")):
            if path.stem not in excluded:
                suite.addTests(loader.loadTestsFromName(path.stem))
        suite.addTests(loader.discover(str(root / "tests"), pattern="test_*.py", top_level_dir=str(root)))
    collected, duplicates = unique_suite(suite)
    return collected, duplicates, list(loader.errors)


def source_files(root=ROOT):
    """Only active source; exclude historical releases, packages and logs."""
    paths = set(root.glob("*.py"))
    for directory in (root / "tools", root / "tests"):
        paths.update(path for path in directory.rglob("*.py")
                     if "__pycache__" not in path.parts and "fixtures" not in path.parts)
    return sorted(paths)


def check_syntax(paths):
    failures = []
    for path in paths:
        try:
            # No import of live tools and no .pyc writes.
            compile(path.read_bytes(), str(path), "exec")
        except (OSError, SyntaxError) as exc:
            failures.append({"path": str(path), "error": str(exc)})
    return failures


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self._started = 0.0
        self._status = "passed"

    def startTest(self, test):
        self._started = time.perf_counter()
        self._status = "passed"
        super().startTest(test)

    def addFailure(self, test, err):
        self._status = "failed"
        super().addFailure(test, err)

    def addError(self, test, err):
        self._status = "error"
        super().addError(test, err)

    def addSkip(self, test, reason):
        self._status = "skipped"
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self._status = "expected_failure"
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self._status = "unexpected_success"
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self._status = "failed" if issubclass(err[0], test.failureException) else "error"
        super().addSubTest(test, subtest, err)

    def stopTest(self, test):
        self.records.append({"id": test.id(), "status": self._status,
                             "seconds": round(time.perf_counter() - self._started, 4)})
        super().stopTest(test)


def result_summary(result, collected):
    return {
        "collected": collected, "executed": result.testsRun, "successful": result.wasSuccessful(),
        "failures": [{"id": test.id(), "traceback": detail} for test, detail in result.failures],
        "errors": [{"id": test.id(), "traceback": detail} for test, detail in result.errors],
        "skipped": [{"id": test.id(), "reason": reason} for test, reason in result.skipped],
        "expected_failures": [test.id() for test, _ in result.expectedFailures],
        "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
        "tests": result.records,
    }


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, value):
        for stream in self.streams:
            stream.write(value)
        self.flush()
        return len(value)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def validator_step(name, filename):
    began = time.perf_counter()
    print(f"\n[{name}]")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    with subprocess.Popen([sys.executable, "-u", str(ROOT / filename)], cwd=ROOT,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace", env=env) as process:
        for line in process.stdout:
            print(line, end="")
        code = process.wait()
    return {"name": name, "script": filename, "exit_code": code,
            "seconds": round(time.perf_counter() - began, 3)}


def acceptance_artifacts(directory: Path, *, publish: bool):
    """Reuse the full-flow test's output; never rerun the eleven commands."""
    summary_path = directory / "offline_full_flow_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") != "passed" or len(summary.get("results", {})) != 11:
        raise ValueError("11功能键离线报告未全部通过。")
    if not all(value == 0 for value in summary["results"].values()):
        raise ValueError("11功能键离线报告包含失败。")
    files = [summary_path, Path(summary["report_path"]), Path(summary["audit_path"])]
    if not all(path.is_file() and path.resolve().is_relative_to(directory.resolve()) for path in files):
        raise ValueError("本次离线报告不完整或引用了其他轮次的文件。")
    if publish:
        stable = ROOT / "logs" / "offline_acceptance"
        stable.mkdir(parents=True, exist_ok=True)
        for path in files:
            shutil.copy2(path, stable / path.name)
        shutil.copy2(files[1], ROOT / "logs" / "ACCEPTANCE_REPORT.md")
    return {"status": "passed", "commands": 11, "summary": str(summary_path),
            "report": str(files[1]), "audit": str(files[2]), "published": publish}


def absolute_path(raw):
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def execute(args, run_directory):
    started = time.perf_counter()
    report = {"schema_version": 1, "started_at": now(),
              "scope": "selected" if args.targets else "full", "status": "failed",
              "python": sys.executable, "root": str(ROOT), "targets": args.targets,
              "run_directory": str(run_directory), "stages": []}
    paths = source_files()
    syntax_errors = check_syntax(paths)
    report["syntax"] = {"files": len(paths), "errors": syntax_errors}
    print(f"[语法检查] {len(paths)} 个当前源码文件，{len(syntax_errors)} 个错误。")
    for error in syntax_errors:
        print(f"ERROR: {error['path']}: {error['error']}")
    if syntax_errors:
        report.update(finished_at=now(), seconds=round(time.perf_counter() - started, 3))
        return 1, report

    suite, duplicates, collection_errors = collect_tests(args.targets)
    collected = suite.countTestCases()
    ids = [case.id() for case in flatten(suite)]
    report["collection"] = {"count": collected, "duplicates_removed": duplicates, "errors": collection_errors,
                            "modules": dict(sorted(Counter(key.rsplit(".", 2)[0] for key in ids).items()))}
    print(f"\n[单元与回放] 自动收集 {collected} 项；重复编号去除 {len(duplicates)} 项。")
    destination = run_directory / "offline_acceptance"
    previous_acceptance = os.environ.get(ACCEPTANCE_ENV)
    os.environ[ACCEPTANCE_ENV] = str(destination)
    try:
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1 if args.quiet else 2,
                                        failfast=args.failfast, resultclass=RecordingResult).run(suite)
    finally:
        if previous_acceptance is None:
            os.environ.pop(ACCEPTANCE_ENV, None)
        else:
            os.environ[ACCEPTANCE_ENV] = previous_acceptance
    report["unit"] = result_summary(result, collected)
    successful = result.wasSuccessful() and not collection_errors and result.testsRun == collected
    acceptance_ran = any(row["id"] == ACCEPTANCE_TEST and row["status"] == "passed" for row in result.records)
    if acceptance_ran:
        try:
            report["acceptance"] = acceptance_artifacts(destination, publish=not args.targets and successful)
            print("[11功能键报告] 复用上述用例产物；完整流程只执行一次。")
        except (OSError, ValueError, KeyError) as exc:
            successful = False
            report["acceptance"] = {"status": "failed", "error": str(exc)}
            print(f"ERROR: {exc}")
    if not args.targets:
        for name, filename in VALIDATORS:
            step = validator_step(name, filename)
            report["stages"].append(step)
            successful = successful and step["exit_code"] == 0
    elapsed = time.perf_counter() - started
    report.update(status=("passed_with_skips" if result.skipped else "passed") if successful else "failed",
                  finished_at=now(), seconds=round(elapsed, 3))
    if successful:
        if args.targets:
            print(f"\nPASS：所选离线测试通过，用时 {elapsed:.1f} 秒。")
        elif result.skipped:
            print(f"\nPASS：离线测试完成（跳过 {len(result.skipped)} 项），用时 {elapsed:.1f} 秒。")
        else:
            print(f"\nPASS：全部离线测试通过，用时 {elapsed:.1f} 秒。")
    else:
        print(f"\nFAIL：离线测试未全部通过，用时 {elapsed:.1f} 秒。")
    return (0 if successful else 1), report


def main(argv=None):
    parser = argparse.ArgumentParser(description="火影OL统一离线测试入口（不启动游戏任务）")
    parser.add_argument("targets", nargs="*", help="可选：测试模块、类或方法；支持 .py 路径")
    parser.add_argument("--list", action="store_true", help="只列出模块及用例数量")
    parser.add_argument("--failfast", action="store_true", help="首个单元测试失败后停止单元阶段")
    parser.add_argument("--quiet", action="store_true", help="精简 unittest 进度输出")
    parser.add_argument("--log-path", default="logs/offline_test_latest.log")
    parser.add_argument("--summary-path", default="logs/offline_test_latest.json")
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    if args.list:
        suite, duplicates, errors = collect_tests(args.targets)
        counts = Counter(case.id().rsplit(".", 2)[0] for case in flatten(suite))
        for name, count in sorted(counts.items()):
            print(f"{name}: {count}")
        print(f"TOTAL={suite.countTestCases()} MODULES={len(counts)} DUPLICATES_REMOVED={len(duplicates)}")
        if not args.targets:
            print("STATIC=" + ", ".join(name for _, name in VALIDATORS))
        for error in errors:
            print(error, file=sys.stderr)
        return 2 if errors else 0

    log_path, summary_path = absolute_path(args.log_path), absolute_path(args.summary_path)
    if log_path == summary_path:
        parser.error("日志和 JSON 结果不能使用同一路径。")
    run_id = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_directory = ROOT / "logs" / "offline_tests" / run_id
    run_directory.mkdir(parents=True)
    console = run_directory / "console.log"
    try:
        with console.open("w", encoding="utf-8") as output:
            tee = Tee(sys.stdout, output)
            with redirect_stdout(tee), redirect_stderr(tee):
                print(f"项目目录: {ROOT}\nPython: {sys.executable}\n本轮输出: {run_directory}")
                try:
                    code, report = execute(args, run_directory)
                except KeyboardInterrupt:
                    code, report = 130, {"status": "interrupted", "finished_at": now()}
                    print("INTERRUPTED：测试已中断，不记录通过。")
                except Exception as exc:
                    import traceback
                    traceback.print_exc()
                    code, report = 2, {"status": "error", "error": f"{type(exc).__name__}: {exc}", "finished_at": now()}
                report.update(run_id=run_id, scope="selected" if args.targets else "full", exit_code=code,
                              console_log=str(console), run_directory=str(run_directory))
                print(f"结果清单: {summary_path}")
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        (run_directory / "summary.json").write_text(payload, encoding="utf-8")
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(payload, encoding="utf-8")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path != console:
            shutil.copy2(console, log_path)
        return code
    except OSError as exc:
        print(f"无法写入测试结果：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
