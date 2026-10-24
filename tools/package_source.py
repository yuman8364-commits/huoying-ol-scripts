"""Export portable source separately from large replay/development data."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_generic_release import clean_config, personal_asset

DATA_PREFIXES = ('fixtures/', 'tests/fixtures/', 'daruma_standalone/tests/', 'models/candidates/')
EXCLUDED = {'logs', 'state', 'runtime', '__pycache__', '.git'}


def source_paths(root):
    root = Path(root)
    selected = set()
    for pattern in ('*.py', '*.ps1', '*.ini', '*.md', '*requirements*.txt', '*.cmd', '.gitignore'):
        selected.update(path for path in root.glob(pattern) if path.is_file())
    for folder in ('assets', 'models', 'daruma_standalone', 'tools', 'tests', 'fixtures', 'docs', 'web'):
        selected.update(path for path in (root/folder).rglob('*') if path.is_file())
    return sorted(path for path in selected
                  if path.name not in ('PROGRESS.md', 'HANDOFF.md')
                  and not any(part in EXCLUDED for part in path.relative_to(root).parts)
                  and path.suffix not in ('.pyc', '.pyo', '.tmp', '.lnk')
                  and not (path.relative_to(root).parts[0] == 'assets'
                           and personal_asset(path.relative_to(root/'assets'))))


def package_sources(root, output, version):
    root, output = Path(root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = source_paths(root)
    reports = []
    for data in (False, True):
        kind = 'replay-data' if data else 'source'
        archive = output/f'huoying-ol-{kind}-{version}.zip'
        manifest = []
        with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
            for path in paths:
                relative = path.relative_to(root).as_posix()
                if relative.startswith(DATA_PREFIXES) != data:
                    continue
                content = path.read_bytes()
                if path.suffix.lower() == '.ini':
                    content = clean_config(content.decode('utf-8-sig')).encode('utf-8')
                target = relative if data else f'huoying-ol-scripts-{version}/{relative}'
                bundle.writestr(target, content)
                manifest.append(dict(path=relative, bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))
            record = dict(version=version, kind=kind, base_commit='e1ce1616a38be80d332de111a99e5272e7f63a49',
                          personal_material=False, runtime_data=False, files=manifest)
            target = 'REPLAY_MANIFEST.json' if data else f'huoying-ol-scripts-{version}/SOURCE_MANIFEST.json'
            bundle.writestr(target, json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'))
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip():
                raise ValueError('Source/data ZIP CRC failed')
        reports.append(dict(kind=kind, file=archive.name, bytes=archive.stat().st_size,
                            unpacked_bytes=sum(row['bytes'] for row in manifest), files=len(manifest),
                            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), zip_crc='PASS'))
    return reports


def main():
    from hyol_runtime import VERSION
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = package_sources(ROOT, args.output, VERSION)
    (args.output/'source-package-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
