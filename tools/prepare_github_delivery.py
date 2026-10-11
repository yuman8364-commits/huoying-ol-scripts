"""Prepare current audited portable source; does not access GitHub."""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_generic_release import clean_config, personal_asset
from hyol_runtime import VERSION


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def regression_summary(text):
    groups = [dict(tests=int(count), seconds=float(seconds)) for count, seconds in
              re.findall(r'^Ran (\d+) tests? in ([\d.]+)s\s*$', text, re.MULTILINE)]
    complete = re.search(r'PASS：全部离线测试通过，用时 ([\d.]+) 秒', text)
    if (not groups or len(re.findall(r'^OK\s*$', text, re.MULTILINE)) != len(groups)
            or not complete or re.search(r'^(?:FAILED\s*\(|FAIL: |ERROR: )', text, re.MULTILINE)):
        raise ValueError('Complete successful regression transcript required')
    return dict(tests=sum(row['tests'] for row in groups), seconds=float(complete[1]), groups=groups)


def prepare(output, unified, team, verification, regression):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a new delivery directory; preserve earlier artifacts')
    verified = json.loads(Path(verification).read_text('utf-8-sig'))
    summary = regression_summary(Path(regression).read_text('utf-8-sig'))
    if verified.get('Result') != 'PASS' or verified.get('ExpectedVersion') != VERSION:
        raise ValueError('Current-version release verification required')
    if ({row['Client'] for row in verified['Packages']} != {'unified', 'team'}
            or not all(row['GuiLaunchVerified'] for row in verified['Packages'])):
        raise ValueError('Both current Windows packages need GUI verification')
    for path in (unified, team):
        with zipfile.ZipFile(path) as bundle:
            if bundle.testzip():
                raise ValueError('Client archive CRC failed')
    source = output / 'source'
    source.mkdir(parents=True)

    def copy(path, relative=None):
        target = source / (relative or path.relative_to(ROOT))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if sha(path) != sha(target):
            raise ValueError('Source copy differs')

    for path in ROOT.glob('hyol_*.py'):
        copy(path)
    for name in ('build_unified_client.ps1', 'build_team_client.ps1'):
        copy(ROOT / name)
    for name in ('preflight_live.py', 'prepare_generic_release.py', 'verify_packaged_source.py',
                 'release_runtime_snapshot.py', 'verify_desktop_release.ps1',
                 'prepare_github_delivery.py', 'replay_combat_watchdog.py'):
        copy(ROOT / 'tools' / name)
    for name in ('test_hyol_live_solo_fixes.py', 'test_hyol_history.py', 'test_hyol_network_retry.py',
                 'test_hyol_combat_watchdog.py', 'test_hyol_current_identity.py',
                 'test_hyol_managed_resume_fixes.py'):
        copy(ROOT / name)
    for name in ('survival_battle_hud_occluded_20261009.png', 'managed_panel_offset_20261009.png'):
        copy(ROOT / 'tests/fixtures' / name)
    # Retain the exact numerical tests without unrelated private replays.
    tree = ast.parse((ROOT / 'test_hyol_recognition_performance.py').read_text('utf-8-sig'))
    tests_class = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    tests_class.body = [node for node in tests_class.body if isinstance(node, ast.FunctionDef)
                       and node.name in ('test_template_spectrum_reused_across_frames_with_exact_scores',
                                         'test_mutable_templates_never_use_stale_spectra',
                                         'test_template_spectra_obey_memory_and_entry_bounds')]
    imports = 'import unittest\nfrom unittest.mock import patch\nimport numpy as np\nimport hyol_scheduler as hs\n\n'
    (source / 'test_template_spectra.py').write_text(imports + ast.unparse(tests_class) + '\n', 'utf-8')
    for directory in ('assets', 'models'):
        for path in (ROOT / directory).rglob('*'):
            if path.is_file() and not personal_asset(path.relative_to(ROOT / directory)):
                copy(path)
    for name in ('HYOL_Daily.ini', 'DAILY_Client.ini', 'TEAM_Client.ini', 'ARENA_Client.ini'):
        (source / name).write_text(clean_config((ROOT / name).read_text('utf-8-sig')), 'utf-8')
    guide = (ROOT / 'README_通用客户端.md').read_text('utf-8')
    for name in ('README_通用客户端.md', 'README_统一客户端.md', 'TEAM_TASKS_README.md'):
        (source / name).write_text(guide, 'utf-8')
    (source / 'YOLO_WORKFLOW.md').write_text('CPU视觉模型保持shadow；操作只使用已验证的模板门。\n', 'utf-8')
    requirements = '\n'.join(name + '==' + importlib.metadata.version(name)
                            for name in ('numpy', 'pillow', 'onnxruntime', 'pyinstaller',
                                         'pyinstaller-hooks-contrib')) + '\n'
    (source / 'requirements.txt').write_text(requirements, 'utf-8')
    packages = [dict(client=row['Client'], modules=row['SourceModulesChecked'], assets=row['AssetsChecked'],
                     sha256=row['Sha256'], gui_verified=row['GuiLaunchVerified']) for row in verified['Packages']]
    report = dict(version=VERSION, regression_tests=summary['tests'], regression_seconds=summary['seconds'],
                  regression_groups=summary['groups'], release_result=verified['Result'], packages=packages,
                  runtime_files_preserved=verified['RuntimePreservation']['runtime_files_checked'],
                  live_acceptance='pending', multiplayer_tasks='paused',
                  combat_stall_seconds=180, combat_max_seconds=600,
                  fixes=['battlefield no-change watchdog', 'independent per-battle hard deadline',
                         'bounded bond/team/sealed/treasure combat observation',
                         'stalled account excluded from automatic retries after positive tab verification',
                         'same joint identity context for current-account binding',
                         'managed panel offset and historical checkpoint preservation'])
    (source / 'ACCEPTANCE_REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    (source / 'README.md').write_text(guide + f'\n版本：{VERSION}。\n\n'
        '源码运行：Windows / Python 3.11+，创建 `.build-env` 后安装 requirements.txt，执行 hyol_client.py。\n\n'
        '构建：运行 build_unified_client.ps1 或 build_team_client.ps1。\n\n'
        '匿名专项测试：python -m unittest discover。随包不包含本机实机日志和历史账号资料。'
        f'完整本地回归为 {summary["tests"]} 项，结果见 ACCEPTANCE_REPORT.json。\n\n'
        '本版增加战斗防卡死：默认画面连续180秒无明显变化或单场等待600秒即保存证据并中止等待。'
        '切换账号、动画及重复识别战斗标记不重置单场上限。当前标签与战斗页确认后跳过卡住账号，'
        '继续其他账号；画面不明时停止。未知原场不重开、不计胜、不自动托管。\n\n'
        '此前单账号身份误拦截、托管面板偏移、保持阵容和历史断点修复均已合入。'
        '多人游戏任务暂缓，完整实机仍待验。\n', 'utf-8')
    labels_path = ROOT / 'tests/fixtures/legacy_identity_labels.json'
    labels = json.loads(labels_path.read_text('utf-8'))['labels'] if labels_path.exists() else []
    files = sorted(p for p in source.rglob('*') if p.is_file())
    forbidden = [str(p.relative_to(source)) for p in files if
                 any(part in ('state', 'logs', 'references', '__pycache__', '.git')
                     for part in p.relative_to(source).parts) or p.name == 'PROGRESS.md'
                 or 'assets' in p.relative_to(source).parts and personal_asset(p.relative_to(source / 'assets'))]
    private = [str(p.relative_to(source)) for p in files
               if p.suffix in ('.py', '.ini', '.ps1', '.md', '.json', '.txt')
               and any(label in p.read_text('utf-8-sig') for label in labels)]
    if forbidden or private:
        raise ValueError('Private source material detected: ' + repr(forbidden + private))
    archive = output / ('huoying-ol-scripts-source-' + VERSION + '.zip')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, Path('huoying-ol-scripts') / path.relative_to(source))
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip():
            raise ValueError('Source archive CRC failed')
    artifacts = [dict(kind=kind, name=Path(path).name, path=str(Path(path).resolve()), sha256=sha(path),
                      bytes=Path(path).stat().st_size) for kind, path in
                 (('source', archive), ('unified', unified), ('team', team))]
    manifest = dict(repository='yuman8364-commits/huoying-ol-scripts', private=True, tag=VERSION,
                    uploaded=False, source_files=len(files), source_privacy='PASS', artifacts=artifacts,
                    source_hashes={str(p.relative_to(source)): sha(p) for p in files},
                    report=report, content_clicks=0)
    (output / 'github_upload_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    (output / 'RELEASE_NOTES.md').write_text(f'# {VERSION}：战斗防卡死\n\n'
        '默认战斗画面连续3分钟无明显变化，或单场等待达到10分钟，立即保存截图与断点并停止该场等待。'
        '账号与场次独立计时，切换标签、背景动画和重复识别战斗标记不会重置单场上限。'
        '多账号模式确认当前标签与战斗页后跳过卡住账号，继续其他账号；页面不明时停止。'
        '原场保留待处理，不自动重开、不计胜、不自动托管。\n\n'
        '同时合入单账号身份误拦截、托管面板偏移、默认保持阵容和历史断点保全修复。\n\n'
        f'{summary["tests"]}项完整本地回归及两个Windows客户端GUI、嵌入源码、素材和原运行文件保全通过。'
        '故障日志时间轴回放与模拟时钟验证不代表实机超时闭环；完整实机验收仍待完成。'
        '多人游戏任务仍暂缓。\n\n'
        '下载源码包、完整客户端或组队客户端ZIP；发布包不包含本机账号、状态和运行日志。\n', 'utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'unified', 'team', 'verification', 'regression'):
        parser.add_argument('--' + name, type=Path, required=True)
    result = prepare(**vars(parser.parse_args()))
    print(json.dumps({k: result[k] for k in ('repository', 'tag', 'source_files', 'source_privacy', 'uploaded')},
                     ensure_ascii=False))
