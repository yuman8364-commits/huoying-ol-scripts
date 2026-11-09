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

# 20261106 ZCode: full per-file sha256 verification still runs whenever any
# bundled or installed file changes; the cache only skips re-reading files
# whose (mtime_ns, size) are unchanged since the last fully verified launch.
# The cache lives beside (not inside) the client directory so the audited
# install tree keeps its exact published file list.  Write failures are
# ignored — the cache is a pure optimization and its absence forces the
# original full verification.
_VERIFY_CACHE_NAME = '.hyol-resource-cache.json'


def _verify_cache_path(destination):
    return destination.parent / _VERIFY_CACHE_NAME


def _load_verify_cache(destination, manifest_sha):
    try:
        cache = json.loads(_verify_cache_path(destination).read_text('utf-8'))
    except (OSError, ValueError):
        return None
    if (not isinstance(cache, dict) or cache.get('format') != 1
            or cache.get('manifest_sha256') != manifest_sha):
        return None
    return cache


def _stat_fingerprint(path):
    stat = path.stat()
    return {'mtime_ns': stat.st_mtime_ns, 'size': stat.st_size}


def _stat_matches(path, entry):
    try:
        fingerprint = _stat_fingerprint(path)
    except OSError:
        return False
    return (isinstance(entry, dict)
            and fingerprint['mtime_ns'] == entry.get('mtime_ns')
            and fingerprint['size'] == entry.get('size'))


def _revalidated(cache, checked):
    """True when every stat matches the last fully verified launch."""
    sources, targets = cache.get('sources'), cache.get('targets')
    if not isinstance(sources, dict) or not isinstance(targets, dict):
        return False
    for relative, source, target in checked:
        if not _stat_matches(source, sources.get(relative)):
            return False
        if relative in CONFIG_FILES:
            if not target.is_file():
                return False
            continue
        if not target.is_file() or not _stat_matches(target, targets.get(relative)):
            return False
    return True


def _write_verify_cache(destination, payload):
    path = _verify_cache_path(destination)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.hyol-cache-', delete=False,
                                         mode='w', encoding='utf-8') as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream)
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def materialize_bundle(bundle, destination):
    """Keep user settings and journals across launches; verify bundled resources."""
    bundle, destination = Path(bundle).resolve(), Path(destination).resolve()
    manifest_bytes = (bundle/'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes.decode('utf-8'))
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
        checked.append((relative, source, target))
    digests = {row['path']: row['sha256'] for row in manifest['files']}
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    cache = _load_verify_cache(destination, manifest_sha)
    if cache is not None and _revalidated(cache, checked):
        return destination
    source_stats, target_stats, verified_contents = {}, {}, {}
    for relative, source, target in checked:
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != digests[relative]:
            raise ValueError('单文件客户端资源校验失败：'+relative)
        source_stats[relative] = _stat_fingerprint(source)
        verified_contents[relative] = content
    # Validate the entire bundle before initializing even one missing config.
    for relative, source, target in checked:
        content = verified_contents.pop(relative)
        if relative in CONFIG_FILES and target.is_file():
            continue
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digests[relative]:
            target_stats[relative] = _stat_fingerprint(target)
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
        if relative not in CONFIG_FILES:
            target_stats[relative] = _stat_fingerprint(target)
    _write_verify_cache(destination, {
        'format': 1,
        'manifest_sha256': manifest_sha,
        'sources': source_stats,
        'targets': target_stats,
    })
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


_EDITION_CACHE = None


def read_client_edition():
    """Display-only edition override from the packaged client INI (cached).

    The client INIs are section-less ``key=value`` files, so this is a plain
    line scan.  Returns '' when unset or unreadable; callers must treat
    recognition behavior as independent of this value.
    """
    global _EDITION_CACHE
    if _EDITION_CACHE is not None:
        return _EDITION_CACHE
    value = ''
    try:
        for line in (application_root()/'DAILY_Client.ini').read_text(encoding='utf-8-sig').splitlines():
            stripped = line.strip()
            if stripped.startswith('ClientEdition='):
                value = stripped.split('=', 1)[1].strip()
                break
    except OSError:
        value = ''
    _EDITION_CACHE = value
    return value
