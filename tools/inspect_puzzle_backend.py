"""Inspect the supplied native solver; optional owned-process lifecycle check.

No Start command, game hotkey or game-content click is issued by this tool.
The lifecycle check launches only the bundled copy inside a kill-on-close job.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_puzzle_backend as backend
import hyol_scheduler as hs


def inspect(*, lifecycle=False):
    import pefile
    path = backend.verify_executable(backend.backend_path())
    with pefile.PE(str(path)) as pe:
        machine = pe.FILE_HEADER.Machine
        clr = pe.OPTIONAL_HEADER.DATA_DIRECTORY[14].VirtualAddress
    api = backend.NativeApi()
    existing = api.windows()
    report = dict(schema_version=1, checked_at=datetime.now().isoformat(),
                  executable=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  pe_machine=hex(machine), managed_clr=bool(clr), source_recovered=False,
                  runtime='QuickMacro / 按键小精灵 2014',
                  native_windows_before=existing, game_start_commands=0, game_content_clicks=0,
                  lifecycle_requested=lifecycle)
    if lifecycle:
        job, owned_pid = api.create_job(), None
        try:
            owned_pid = api.spawn(path, job)
            deadline = time.monotonic()+12
            while time.monotonic() < deadline:
                owned = [row for row in api.windows() if row['pid'] == owned_pid]
                if owned:
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('Owned solver controls did not appear; no Start command sent')
            tree = api.process_tree(owned_pid)
            members = api.job_members(job)
            if not tree.issubset(set(members)):
                raise RuntimeError('Owned Runner was not inherited into the stop job')
            controls = owned[0]['controls']
            if any(not api.contained(job, row['pid']) for row in controls):
                raise RuntimeError('Owned native controls are not all contained')
            # Configure only our new instance, using the same verified surface.
            def button(label):
                rows = [row for row in api.children(owned[0]['hwnd']) if row['text'] == label]
                if len(rows) != 1:
                    raise RuntimeError('Ambiguous owned control: '+label)
                return rows[0]
            api.click(button('中止'))
            for label in ('拼图', '自动识别'):
                row = button(label)
                if row['checked'] != 1:
                    api.click(row)
            controls = api.children(owned[0]['hwnd'])
            if button('忍术修炼')['checked'] or not button('拼图')['checked'] or not button('自动识别')['checked']:
                raise RuntimeError('Owned automatic puzzle settings were not confirmed')
            api.click(button('保存设置'))
            api.hide(owned[0]['hwnd'])
            report['owned_process'] = dict(root_pid=owned_pid, process_tree=sorted(tree),
                                          job_members=members, verified_controls=controls,
                                          setting_commands=['中止','拼图/自动识别复核','保存设置'])
        finally:
            report['cleanup'] = api.kill(job)
        prior_pids = {row['pid'] for row in existing}
        remaining_pids = {row['pid'] for row in api.windows()}
        report['existing_windows_preserved'] = prior_pids.issubset(remaining_pids)
        report['owned_root_exited'] = not api.process_path(owned_pid)
        if not report['existing_windows_preserved'] or not report['owned_root_exited']:
            raise RuntimeError('Lifecycle check did not preserve existing instances or stop owned root')
    report['result'] = 'PASS'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lifecycle', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Preserve prior evidence; choose a new output path')
    result = inspect(lifecycle=args.lifecycle)
    hs.atomic_json(args.output, result)
    print(json.dumps({key:value for key,value in result.items()
                      if key in ('result','sha256','source_recovered','game_start_commands',
                                 'game_content_clicks','cleanup','existing_windows_preserved')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
