"""Assemble current source and clean clients for the existing private repository."""
from __future__ import annotations

import argparse
from datetime import datetime
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
from tools.verify_packaged_source import verify_package

CONFIGS = ('HYOL_Daily.ini', 'DAILY_Client.ini', 'TEAM_Client.ini', 'ARENA_Client.ini')
EXCLUDED = {'runtime', 'state', 'logs', '__pycache__', '.git'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if digest(source) != digest(target):
        raise ValueError(f'Copy differs: {source}')


def source(checkout, report):
    checkout, report = Path(checkout).resolve(), Path(report).resolve()
    if not checkout.is_relative_to(ROOT/'releases') or not (checkout/'.git').is_dir():
        raise ValueError('Use a separate cloned release checkout')
    if report.exists():
        raise ValueError('Preserve prior preparation report')
    paths = set()
    for pattern in ('hyol_*.py', 'test*.py', 'validate*.py', 'build*.ps1', 'run*.ps1',
                    'README*.md', '*_README.md', '*requirements*.txt'):
        paths.update(ROOT.glob(pattern))
    paths.update(ROOT/name for name in ('daruma_standalone_gui.py', '启动达摩独立版.cmd',
                 'offline_full_flow.py', 'codex_quota_continuation.py', 'SCRIPT_INDEX.md'))
    for directory in ('tools', 'assets', 'models', 'fixtures', 'tests', 'daruma_standalone'):
        for path in (ROOT/directory).rglob('*'):
            relative = path.relative_to(ROOT)
            if not path.is_file() or any(part in EXCLUDED for part in relative.parts):
                continue
            if directory == 'assets' and personal_asset(path.relative_to(ROOT/'assets')):
                continue
            if path.suffix.lower() in ('.pyc', '.pyo', '.tmp', '.lnk'):
                continue
            paths.add(path)
    files = []
    for path in sorted(paths):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if path.name in ('PROGRESS.md', 'HANDOFF.md'):
            continue
        target = checkout/relative
        copy(path, target)
        if 'requirements' in path.name and path.suffix == '.txt':
            portable = '\n'.join(line for line in target.read_text(encoding='utf-8-sig').splitlines()
                                 if '@ file:' not in line and not re.search(r'(?i)file:///[a-z]:', line))+'\n'
            target.write_text(portable, encoding='utf-8')
        files.append(dict(path=relative.as_posix(), sha256=digest(target), bytes=target.stat().st_size))
    for name in CONFIGS:
        target = checkout/name
        target.write_text(clean_config((ROOT/name).read_text(encoding='utf-8-sig')), encoding='utf-8')
        files.append(dict(path=name, sha256=digest(target), bytes=target.stat().st_size))
    requirements = '\n'.join(name+'=='+importlib.metadata.version(name) for name in
        ('numpy', 'pillow', 'onnxruntime', 'pyinstaller', 'pyinstaller-hooks-contrib', 'pefile'))+'\n'
    (checkout/'requirements.txt').write_text(requirements, encoding='utf-8')
    files.append(dict(path='requirements.txt', sha256=digest(checkout/'requirements.txt'),
                      bytes=(checkout/'requirements.txt').stat().st_size))
    # No local credentials or operational records belong in a source snapshot.
    forbidden = []
    secret = re.compile(rb'gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{35,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
    for row in files:
        path = checkout/row['path']
        if any(part in EXCLUDED for part in Path(row['path']).parts) or secret.search(path.read_bytes()):
            forbidden.append(row['path'])
    if forbidden:
        raise ValueError('Unexpected publishing material: '+repr(forbidden))
    result = dict(status='prepared', checkout=str(checkout), files=files, file_count=len(files),
                  runtime_data_included=False, personal_account_config_included=False,
                  replay_test_fixtures_included=True, created_at=datetime.now().astimezone().isoformat())
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


def clients(output, staged, daruma):
    output, staged, daruma = map(lambda value: Path(value).resolve(), (output, staged, daruma))
    if not output.is_relative_to(ROOT/'releases') or output.exists():
        raise ValueError('Choose a new release folder inside the workspace')
    main = json.loads(staged.read_text(encoding='utf-8-sig'))
    independent = json.loads(daruma.read_text(encoding='utf-8-sig'))
    packages = [dict(key=row['Key'], version=main['Version'], exe=Path(row['Exe']),
                     name=row['Name']) for row in main['Packages']]
    packages.append(dict(key='daruma', version=independent['version'], exe=Path(independent['exe']),
                         name=Path(independent['package']).name))
    output.mkdir(parents=True)
    results = []
    for item in packages:
        probe = verify_package(ROOT, item['exe'])
        if not probe['source_code_matches']:
            raise ValueError('Stale source in '+item['key'])
        package = output/item['key']/item['name']
        for path in sorted(item['exe'].parent.rglob('*')):
            relative = path.relative_to(item['exe'].parent)
            if (not path.is_file() or any(part in EXCLUDED for part in relative.parts)
                    or path.suffix.lower() in ('.ini', '.md', '.lnk')
                    or 'assets' in relative.parts and personal_asset(relative)):
                continue
            copy(path, package/relative)
        if item['key'] == 'daruma':
            copy(item['exe'].parent/'README.md', package/'README.md')
        else:
            for directory in ('assets', 'models'):
                for path in sorted((ROOT/directory).rglob('*')):
                    if path.is_file() and not personal_asset(path.relative_to(ROOT/directory)):
                        copy(path, package/directory/path.relative_to(ROOT/directory))
            for name in CONFIGS:
                (package/name).write_text(clean_config((ROOT/name).read_text(encoding='utf-8-sig')), encoding='utf-8')
            guide = dict(daily='DAILY_TASKS_README.md', team='TEAM_TASKS_README.md',
                         arena='ARENA_CLIENT_README.md', bond='BOND_CLIENT_README.md',
                         unified='README_统一客户端.md')[item['key']]
            copy(ROOT/guide, package/'README.md')
        archive = output/f'huoying-ol-{item["key"]}-client-{item["version"]}.zip'
        with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(package.rglob('*')):
                if path.is_file():
                    bundle.write(path, Path(item['name'])/path.relative_to(package))
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip():
                raise ValueError('Archive CRC failed: '+item['key'])
            if any(part in EXCLUDED for name in bundle.namelist() for part in Path(name).parts):
                raise ValueError('Runtime data in client archive')
        results.append(dict(key=item['key'], version=item['version'], exe=str(package/item['exe'].name),
            archive=str(archive), bytes=archive.stat().st_size, sha256=digest(archive),
            source_modules=probe['modules_checked'], source_matches=True, crc='pass',
            runtime_data_included=False, game_task_started=False))
        print(json.dumps(dict(client=item['key'], status='prepared', bytes=archive.stat().st_size), ensure_ascii=False), flush=True)
    result = dict(status='prepared', clients=results, content_clicks=0)
    (output/'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    src = commands.add_parser('source')
    src.add_argument('--checkout', type=Path, required=True)
    src.add_argument('--report', type=Path, required=True)
    binaries = commands.add_parser('clients')
    binaries.add_argument('--output', type=Path, required=True)
    binaries.add_argument('--staged', type=Path, required=True)
    binaries.add_argument('--daruma', type=Path, required=True)
    arguments = vars(parser.parse_args())
    command = arguments.pop('command')
    result = (source if command == 'source' else clients)(**arguments)
    print(json.dumps(dict(status=result['status'], files=result.get('file_count'),
                          clients=len(result.get('clients', []))), ensure_ascii=False))
