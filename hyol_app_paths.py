"""Locate normal installations and materialize the clean single-file edition."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile


CONFIG_FILES = frozenset(('HYOL_Daily.ini', 'DAILY_Client.ini', 'TEAM_Client.ini',
                          'ARENA_Client.ini', 'assets/puzzle_backend/uservar.ini'))


def materialize_bundle(bundle, destination):
    """Keep user settings and journals across launches; verify bundled resources."""
    bundle, destination = Path(bundle).resolve(), Path(destination).resolve()
    manifest = json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 1 or not isinstance(manifest.get('files'), list):
        raise ValueError('单文件客户端资源清单无效')
    checked, seen = [], set()
    for row in manifest['files']:
        relative = row['path']
        path = PurePosixPath(relative)
        if (not relative or '\\' in relative or ':' in relative or path.is_absolute()
                or '..' in path.parts or path.as_posix() != relative or relative in seen):
            raise ValueError('单文件客户端资源路径无效')
        seen.add(relative)
        source, target = (bundle/relative).resolve(), (destination/relative).resolve()
        if not source.is_relative_to(bundle) or not target.is_relative_to(destination):
            raise ValueError('单文件客户端资源超出目录')
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != row['sha256']:
            raise ValueError('单文件客户端资源校验失败：'+relative)
        checked.append((relative, target, content, row['sha256']))
    for relative, target, content, digest in checked:
        if target.is_file():
            if relative in CONFIG_FILES or hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.hyol-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return destination


def application_root():
    if not getattr(sys, 'frozen', False):
        return Path(__file__).resolve().parent
    extracted = getattr(sys, '_MEIPASS', None)
    bundle = Path(extracted)/'hyol_bundle' if extracted else None
    if bundle is not None and (bundle/'manifest.json').is_file():
        local = Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')
        return materialize_bundle(bundle, local/'HYOLShareClient'/'client')
    return Path(sys.executable).resolve().parent
