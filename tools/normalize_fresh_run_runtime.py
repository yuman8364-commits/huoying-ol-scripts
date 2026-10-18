"""Keep the installed runtime when a staged build differs only in signatures.

Only the staged build is changed. Executable sections must be byte-identical;
all original staged files are backed up before any replacement.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.release_runtime_snapshot import PACKAGES, default_desktop
import pefile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pe_payload(data):
    pe = pefile.PE(data=data, fast_load=True)
    certificate = pe.OPTIONAL_HEADER.DATA_DIRECTORY[4]
    if certificate.VirtualAddress <= 0 or certificate.Size < 8:
        raise ValueError('A terminal signature is required')
    if certificate.VirtualAddress + certificate.Size != len(data):
        raise ValueError('Unexpected data after the signature')
    checksum = pe.OPTIONAL_HEADER.get_field_absolute_offset('CheckSum')
    payload = bytearray(data)
    payload[checksum:checksum + 4] = bytes(4)
    payload[certificate.VirtualAddress:] = bytes(certificate.Size)
    return digest(payload)


def metadata_compatible(first, second):
    a = list(csv.reader(io.StringIO(first.decode('utf-8'))))
    b = list(csv.reader(io.StringIO(second.decode('utf-8'))))
    permitted = {'../../Scripts/f2py.exe', '../../Scripts/numpy-config.exe'}
    if len(a) != len(b):
        return False
    for left, right in zip(a, b):
        if left == right:
            continue
        if (len(left) != 3 or len(right) != 3 or left[0] != right[0]
                or left[0] not in permitted or left[2] != right[2]
                or not all(row[1].startswith('sha256=') and len(row[1]) == 50
                           for row in (left, right))):
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--staging-directory', default='dist_fresh_run_final_20261017')
    parser.add_argument('--backup-directory', default='logs/release_preserved/fresh_run_20261017_signatures')
    parser.add_argument('--report', default='logs/fresh_run_runtime_compatibility_20261017.json')
    args = parser.parse_args()
    stage, backup, report_path = [(ROOT/value).resolve() for value in
        (args.staging_directory, args.backup_directory, args.report)]
    if any(not path.is_relative_to(ROOT) for path in (stage, backup, report_path)):
        raise ValueError('Staging, backup and report paths must stay in the workspace')
    desktop = default_desktop() / '可用脚本'
    changes = []
    for key, _, name, folder in PACKAGES:
        source = stage / name / '_internal'
        installed = desktop / folder / '_internal'
        staged_files = {p.relative_to(source): p for p in source.rglob('*') if p.is_file()}
        installed_files = {p.relative_to(installed): p for p in installed.rglob('*') if p.is_file()}
        if staged_files.keys() != installed_files.keys():
            raise ValueError(f'Dependency inventory differs: {key}')
        for relative, path in staged_files.items():
            old = installed_files[relative]
            a, b = path.read_bytes(), old.read_bytes()
            if a == b or relative.as_posix() == 'base_library.zip':
                continue
            payload = ''
            if path.suffix.lower() in ('.dll', '.pyd'):
                payload = pe_payload(a)
                if payload != pe_payload(b):
                    raise ValueError(f'Executable payload differs: {key}/{relative}')
                reason = 'identical executable payload; signature and checksum differ'
            elif relative.as_posix() == 'numpy-2.3.5.dist-info/RECORD' and metadata_compatible(a, b):
                reason = 'only two unused command-line launcher hashes differ'
            else:
                raise ValueError(f'Unexpected dependency difference: {key}/{relative}')
            changes.append(dict(key=key, staged=str(path), installed=str(old),
                                backup=str(backup/key/relative), staged_sha256=digest(a),
                                installed_sha256=digest(b), payload_sha256=payload, reason=reason))
    if args.apply:
        if report_path.exists():
            raise ValueError('Preserve the existing compatibility report')
        for row in changes:
            if digest(Path(row['staged']).read_bytes()) != row['staged_sha256']:
                raise ValueError('Staged file changed during review')
            if digest(Path(row['installed']).read_bytes()) != row['installed_sha256']:
                raise ValueError('Installed runtime changed during review')
            target = Path(row['backup'])
            if target.exists():
                raise ValueError('Preserve the existing staged backup')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(row['staged'], target)
            if digest(target.read_bytes()) != row['staged_sha256']:
                raise ValueError('Staged backup hash mismatch')
        for row in changes:
            shutil.copy2(row['installed'], row['staged'])
            if digest(Path(row['staged']).read_bytes()) != row['installed_sha256']:
                raise ValueError('Staged runtime normalization failed')
        report_path.write_text(json.dumps(dict(status='normalized', changes=changes,
            installed_files_changed=0, game_inputs=0), ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(status='normalized' if args.apply else 'reviewed', files=len(changes),
        packages=len(PACKAGES), report=str(report_path), installed_files_changed=0, game_inputs=0), ensure_ascii=False))


if __name__ == '__main__':
    main()
