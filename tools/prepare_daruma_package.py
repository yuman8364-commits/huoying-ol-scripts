"""Prepare and inspect the independent Windows package without game input."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from daruma_standalone import VERSION
from tools.prepare_generic_release import personal_asset
from tools.verify_packaged_source import verify_package

NAME = '火影OL达摩独立版'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(package, report):
    package, report = Path(package).resolve(), Path(report).resolve()
    if not package.is_relative_to(ROOT) or not report.is_relative_to(ROOT):
        raise ValueError('Build outputs must stay in the project')
    if report.exists():
        raise ValueError('Preserve previous build evidence')
    exe = package/(NAME+'.exe')
    if not exe.is_file():
        raise ValueError('Build the executable before preparing its resources')
    assets = []
    for path in sorted((ROOT/'assets').rglob('*')):
        if not path.is_file() or personal_asset(path.relative_to(ROOT/'assets')):
            continue
        destination = package/'assets'/path.relative_to(ROOT/'assets')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        if digest(path) != digest(destination):
            raise ValueError('Asset copy mismatch')
        assets.append(str(destination.relative_to(package)))
    (package/'README.md').write_text(
        f'# 火影OL达摩独立版 {VERSION}\n\n'
        '双击“火影OL达摩独立版.exe”打开窗口，无需安装Python。\n\n'
        '游戏登录目标账号，打开达摩挑战或周末福利达摩页。选择游戏窗口，先点“只读识别”，'
        '确认后点“开始 / 继续”。启动客户端本身不会开始游戏任务。\n\n'
        '有已核实的剩余奖励和挑战机会时连续完成各局；最终以连续两帧奖励0及“已完成”收尾。'
        '停止按钮或F12保留当前结果；未核实的输入不会重复。\n\n'
        '独立运行记录保存在同目录runtime内。更新程序时保留runtime。'
        '角色、窗口或页面不符时保留现场和待处理原因。\n\n'
        '本版打包、离线回归与窗口启动已验证；完整游戏挑战以实机验收记录为准。\n',
        encoding='utf-8')
    (package/'PACKAGE.json').write_text(json.dumps(dict(version=VERSION, product='daruma',
        executable=exe.name, default_action='open_gui', live_acceptance='pending_user_testing'),
        ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    source = verify_package(ROOT, exe)
    if not source['source_code_matches']:
        raise ValueError('Frozen executable contains stale source')
    report.parent.mkdir(parents=True, exist_ok=True)
    resource_report = report.with_name(report.stem+'_resources.json')
    result = subprocess.run([str(exe), '--self-check', str(resource_report)], cwd=package,
                            timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not resource_report.is_file():
        raise ValueError('Frozen resource self-check failed')
    resources = json.loads(resource_report.read_text(encoding='utf-8'))
    if resources['status'] != 'pass' or resources['version'] != VERSION or not resources['frozen']:
        raise ValueError('Frozen resource paths or version are incorrect')
    files = [dict(path=str(path.relative_to(package)), bytes=path.stat().st_size, sha256=digest(path))
             for path in sorted(package.rglob('*')) if path.is_file()
             and 'runtime' not in path.relative_to(package).parts]
    receipt = dict(version=VERSION, status='packaging_pass', package=str(package), exe=str(exe),
        sha256=digest(exe), assets=len(assets), files=files, source=source,
        resources=resources, resource_report=str(resource_report),
        content_clicks=0, game_task_started=False, live_acceptance='pending_user_testing')
    report.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    receipt = prepare(args.package, args.report)
    print(json.dumps({key: receipt[key] for key in ('version', 'status', 'exe', 'assets')}, ensure_ascii=False))
