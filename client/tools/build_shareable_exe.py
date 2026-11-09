"""Build and audit the clean Windows single-file desktop edition."""
from __future__ import annotations

import argparse
import hashlib
import json
import marshal
from pathlib import Path
import re
import shutil
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_generic_release import clean_config, personal_asset
from tools.verify_packaged_source import verify_package
from hyol_runtime import VERSION, RELEASE_DATE

CONFIGS = ('HYOL_Daily.ini', 'DAILY_Client.ini', 'TEAM_Client.ini', 'ARENA_Client.ini')
DIGITS = ('bond_kill_digits_20261030.json', 'bond_score_digits_20261030.json',
          'survival_counter_glyphs.json', 'arena3v3_counter_glyphs.json', 'grocery_number_glyphs.json')
WEB_FILES = ('index.html', 'styles.css', 'app.js', 'demo-data.js', 'native-adapter.js',
             'assets/favicon.svg', 'assets/demo-evidence.svg')
PUZZLE_NAME = '拼图 忍术训练V2.1.exe'
PUZZLE_SHA256 = 'ffa2ca6cd1eb8c3cc4185bd8cd52b5d85e3d9ad6864d779e1295a548db3425be'


def sha(content):
    return hashlib.sha256(content).hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def shareable_asset(relative):
    relative = Path(relative)
    if (personal_asset(relative) or any(part.startswith('_') for part in relative.parts)
            or relative.name.startswith('acceptance_')
            or relative.name.startswith('friend_') and relative.name != 'friend_invite_team.bmp'
            or 'friend_name' in relative.name):
        return False
    if len(relative.parts) == 1:
        return relative.suffix.lower() in ('.bmp', '.png') or relative.name == 'windows_ocr.ps1'
    return relative.as_posix() == 'puzzle_backend/'+PUZZLE_NAME


def clean_digit_material(value):
    variants = []
    for row in value['variants']:
        if not re.fullmatch(r'[0-9/]', str(row['digit'])) or not all(
                re.fullmatch(r'[01]+', line) for line in row['rows']):
            raise ValueError('Invalid digit material')
        variants.append(dict(digit=row['digit'], rows=row['rows']))
    return dict(variants=variants)


def known_labels(root):
    path = root/'tests/fixtures/legacy_identity_labels.json'
    return json.loads(path.read_text(encoding='utf-8'))['labels'] if path.is_file() else []


def text_issues(content, labels):
    issues = []
    if any(label and label in content for label in labels):
        issues.append('known_personal_identifier')
    if re.search(r'(?i)[a-z]:[\\/]+Users[\\/]+', content):
        issues.append('literal_user_profile_path')
    if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', content):
        issues.append('private_key')
    if re.search(r'(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{25,})', content):
        issues.append('service_token')
    if re.search(r'(?i)(?:password|passwd|api_key|access_token|refresh_token|secret)\s*[:=]\s*[\"\x27][^\"\x27\s]{8,}', content):
        issues.append('credential_literal')
    return issues


def stage_release(root, output, name):
    root, output = Path(root).resolve(), Path(output).resolve()
    stage, bundle = output/'source', output/'hyol_bundle'
    stage.mkdir(parents=True)
    bundle.mkdir()
    inputs = {}

    def original(path):
        content = path.read_bytes()
        inputs[path.relative_to(root).as_posix()] = sha(content)
        return content

    sources = sorted(root.glob('hyol_*.py')) + [root/'daruma_standalone_gui.py']
    sources += sorted((root/'daruma_standalone').glob('*.py'))
    sources += [root/'tools/preflight_live.py']
    if (root/'tools/__init__.py').is_file():
        sources.append(root/'tools/__init__.py')
    for path in sources:
        target = stage/path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original(path))
    excluded = []
    for path in sorted((root/'assets').rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(root/'assets')
        if not shareable_asset(relative):
            excluded.append(relative.as_posix())
            continue
        target = bundle/'assets'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # The older disconnect capture includes a role name at its bottom edge.
        # Use the already enrolled clean variant; the runtime also uses this
        # same variant and the text-only fallback for disconnect recognition.
        safe_path = root/'assets/offline_login_prompt_current.bmp' if relative.as_posix() == 'offline_login_prompt.bmp' else path
        target.write_bytes(original(safe_path))
    puzzle = bundle/'assets/puzzle_backend'/PUZZLE_NAME
    if sha(puzzle.read_bytes()) != PUZZLE_SHA256:
        raise ValueError('Original puzzle engine hash mismatch')
    # Only machine-independent glyph pixels are needed at runtime.
    for name_part in DIGITS:
        value = json.loads(original(root/'assets'/name_part).decode('utf-8-sig'))
        if name_part.startswith('bond_'):
            value = clean_digit_material(value)
        save_json(bundle/'assets'/name_part, value)
    (bundle/'assets/puzzle_backend/uservar.ini').write_text(
        '[44f3a72e-6be2-445e-92bc-4c380f16b66d]\nForm1.MSXZ.ListIndex=-1\n'
        'Form1.JSXZ.ListIndex=1\nForm1.ndxz.ListIndex=1\nForm1.OptionBox1.Value=1\n'
        'Form1.OptionBox2.Value=0\nForm1.zdsb.Value=1\n', encoding='utf-8')
    for filename in CONFIGS:
        content = clean_config(original(root/filename).decode('utf-8-sig'))
        content = re.sub(r'^SharedStateDirectory=.*$',
                         lambda _: r'SharedStateDirectory=%LOCALAPPDATA%\HYOLShareClient\state',
                         content, flags=re.M)
        content = re.sub(r'^EnableShadowDetection=.*\n?', '', content, flags=re.M)
        (bundle/filename).write_text(content+'EnableShadowDetection=0\n', encoding='utf-8')
    for filename in WEB_FILES:
        target = bundle/'web'/filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original(root/'web'/filename))
    samples = output/'daruma_samples'
    samples.mkdir()
    for path in sorted((root/'daruma_standalone/samples').glob('*.bmp')):
        (samples/path.name).write_bytes(original(path))
    (bundle/'使用说明.txt').write_text(
        '火影OL 单文件分享版 '+VERSION+'\n\n'
        '只需转发这一个EXE，不需要安装Python。Windows 10/11 64位，需系统WebView2和.NET Framework 4.8。\n'
        '先登录自己的游戏并打开所需账号标签，再双击EXE；选择任务、账号范围后点击开始。F12或停止按钮停止本轮。\n'
        '打开程序保持空闲，不自动启动游戏任务。首次使用按当前标签自动识别账号。\n'
        '标签按游戏窗口从左到右编号；选择四个账号只操作前四个标签，不用修改角色名为数字。\n'
        '单人页的组队任务只控制当前账号，使用现有队伍；新增组队项目默认只选组队副本和强者。\n'
        '修行录按最终灰色已完成状态核实；竞技场可手动选择最终目标或战令用完。\n'
        '分享包不携带制作者账号素材、日志、运行断点、浏览器登录数据或个人配置。\n'
        '个人设置和断点保存在当前Windows用户的%LOCALAPPDATA%\\HYOLShareClient；完整中文日志在桌面“火影OL日志”，原始记录同时保留。\n'
        '再次分享时只转发原始EXE；不要附带自己的运行数据目录。\n', encoding='utf-8')
    manifest = dict(format=1, edition='shareable-onefile', build=VERSION,
                    files=[dict(path=p.relative_to(bundle).as_posix(), sha256=sha(p.read_bytes()))
                           for p in sorted(bundle.rglob('*')) if p.is_file()])
    save_json(bundle/'manifest.json', manifest)
    save_json(output/'source-baseline.json', inputs)
    labels, issues = known_labels(root), []
    for folder in (stage, bundle):
        for path in folder.rglob('*'):
            if path.is_file() and path.suffix in ('.py', '.ini', '.json', '.js', '.html', '.css', '.svg', '.ps1', '.txt'):
                for issue in text_issues(path.read_text(encoding='utf-8-sig'), labels):
                    issues.append(dict(path=path.relative_to(output).as_posix(), issue=issue))
    report = dict(result='PASS' if not issues else 'FAIL', files=len(manifest['files']),
                  excluded_assets=excluded, findings=issues, source_files=len(sources),
                  replaced_assets={'offline_login_prompt.bmp': 'offline_login_prompt_current.bmp'},
                  personal_state=False, browser_data=False, recipient_state='HYOLShareClient')
    save_json(output/'privacy-before-build.json', report)
    if issues:
        raise ValueError('Staged privacy audit failed; see privacy-before-build.json')
    return stage, bundle, samples, inputs


def code_strings(code):
    yield code.co_filename
    for value in code.co_consts:
        if isinstance(value, str):
            yield value
        elif isinstance(value, types.CodeType):
            yield from code_strings(value)
        elif isinstance(value, (tuple, frozenset)):
            for item in value:
                if isinstance(item, str):
                    yield item


def sanitize_native_binaries(binaries, output):
    """Redact compiler diagnostic paths in copies, retaining executable sections."""
    import pefile
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    prefix = str(Path.home()).encode('utf-8')
    replacement = b'C:\\build\\portable'
    if len(replacement) > len(prefix):
        raise ValueError('Build profile path is too short for fixed-size redaction')
    replacement = replacement.ljust(len(prefix), b'_')
    result, changes = [], []
    for destination, source, kind in binaries:
        path = Path(source)
        content = path.read_bytes() if path.suffix.lower() in ('.pyd', '.dll') else b''
        matches = list(re.finditer(re.escape(prefix), content, re.I)) if content else []
        if not matches:
            result.append((destination, source, kind))
            continue
        pe = pefile.PE(data=content, fast_load=True)
        text_before = [(s.Name, sha(s.get_data())) for s in pe.sections
                       if s.Characteristics & 0x20000000]
        for match in matches:
            section = next((s for s in pe.sections if s.PointerToRawData <= match.start()
                            < s.PointerToRawData+s.SizeOfRawData), None)
            end = content.find(b'\0', match.start())
            diagnostic = content[match.start():end] if end != -1 else b''
            if (section is None or section.Characteristics & 0x20000000
                    or len(diagnostic) > 1024 or not diagnostic.lower().endswith((b'.c', b'.h', b'.pdb', b'.inc'))):
                raise ValueError('Refuse to alter a non-diagnostic native path: '+destination)
            pe.set_bytes_at_offset(match.start(), replacement)
        # An altered binary must not retain a misleading original signature.
        security = pe.OPTIONAL_HEADER.DATA_DIRECTORY[4]
        certificate_offset, certificate_size = security.VirtualAddress, security.Size
        security.VirtualAddress, security.Size = 0, 0
        modified = pe.write()
        if certificate_size and certificate_offset+certificate_size == len(modified):
            modified = modified[:certificate_offset]
        updated = pefile.PE(data=modified, fast_load=True)
        updated.OPTIONAL_HEADER.CheckSum = updated.generate_checksum()
        modified = updated.write()
        text_after = [(s.Name, sha(s.get_data())) for s in pefile.PE(data=modified).sections
                      if s.Characteristics & 0x20000000]
        if text_before != text_after or re.search(re.escape(prefix), modified, re.I):
            raise ValueError('Native diagnostic redaction validation failed: '+destination)
        target = output/sha(str(source).encode())[:16]/path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(modified)
        changes.append(dict(file=destination, diagnostic_paths=len(matches),
                            source_sha256=sha(content), cleaned_sha256=sha(modified),
                            executable_sections_unchanged=True,
                            original_signature_removed=bool(certificate_size)))
        result.append((destination, str(target), kind))
    save_json(output/'native-cleaning-report.json', dict(files=changes, count=len(changes)))
    return result


def audit_executable(root, executable, bundle, samples):
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    pyz = archive.open_embedded_archive('PYZ.pyz')
    archive_names = {name.replace('\\', '/'): name for name in archive.toc}
    labels, findings, checked = known_labels(root), [], 0
    local_paths = (str(Path.home()), str(Path(root).resolve()))
    all_modules_checked = 0
    for module in pyz.toc:
        code = pyz.extract(module)
        if isinstance(code, types.CodeType):
            all_modules_checked += 1
            if any(any(prefix.casefold() in value.casefold() for prefix in local_paths)
                   for value in code_strings(code)):
                findings.append(dict(module=module, issue='literal_local_path'))
        if module.startswith(('hyol_', 'daruma_standalone')) or module == 'tools.preflight_live':
            checked += 1
            for value in code_strings(code):
                for issue in text_issues(value, labels):
                    findings.append(dict(module=module, issue=issue))
    for name, row in archive.toc.items():
        raw = archive.extract(name)
        if isinstance(raw, bytes) and any(prefix.encode(encoding).lower() in raw.lower()
                                         for prefix in local_paths for encoding in ('utf-8', 'utf-16-le')):
            findings.append(dict(path=name, issue='literal_local_path'))
        if row[-1] == 's' and name.startswith('hyol_'):
            checked += 1
            for value in code_strings(marshal.loads(archive.extract(name))):
                for issue in text_issues(value, labels):
                    findings.append(dict(module=name, issue=issue))
        parts = Path(name.replace('\\', '/')).parts
        if any(part.lower() in ('logs', 'state', 'accounts', 'fixtures', 'tests', 'references', '__pycache__')
               for part in parts) or re.search(r'_(?:state|result|failure)\.json$', name):
            findings.append(dict(path=name, issue='forbidden_runtime_data'))
    actual_resources = {name.replace('\\', '/') for name in archive.toc
                        if name.replace('\\', '/').startswith('hyol_bundle/')}
    expected_resources = {'hyol_bundle/'+p.relative_to(bundle).as_posix()
                          for p in bundle.rglob('*') if p.is_file()}
    if actual_resources != expected_resources:
        findings.append(dict(issue='resource_manifest_mismatch'))
    resources = [(p, 'hyol_bundle/'+p.relative_to(bundle).as_posix())
                 for p in bundle.rglob('*') if p.is_file()]
    resources += [(p, 'daruma_standalone/samples/'+p.name) for p in samples.glob('*.bmp')]
    for path, name in resources:
        content = archive.extract(archive_names[name])
        if sha(content) != sha(path.read_bytes()):
            findings.append(dict(path=name, issue='embedded_resource_changed'))
        if path.suffix in ('.json', '.ini', '.js', '.html', '.css', '.svg', '.txt', '.ps1'):
            for issue in text_issues(content.decode('utf-8-sig'), labels):
                findings.append(dict(path=name, issue=issue))
    puzzle = archive.extract(archive_names['hyol_bundle/assets/puzzle_backend/'+PUZZLE_NAME])
    for label in labels:
        for encoding in ('utf-8', 'utf-16-le', 'gb18030'):
            if label and label.encode(encoding) in puzzle:
                findings.append(dict(issue='identifier_in_original_puzzle_binary'))
    source = verify_package(root, executable)
    if not source['source_code_matches']:
        findings.append(dict(issue='embedded_source_mismatch'))
    return dict(result='PASS' if not findings else 'FAIL', findings=findings,
                python_modules_checked=checked, embedded_resources_checked=len(resources),
                all_python_modules_checked=all_modules_checked, archive_entries_checked=len(archive.toc),
                source=source, original_puzzle_sha256=sha(puzzle),
                original_puzzle_audit='Exact hash and encoded identifier scan; encrypted script not recovered',
                sha256=sha(executable.read_bytes()), bytes=executable.stat().st_size)


def build(output, name):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a new build directory; preserve existing releases')
    output.mkdir(parents=True)
    stage, bundle, samples, inputs = stage_release(ROOT, output, name)
    excluded_modules = ['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3', 'gi', 'onnxruntime',
                        'setuptools', 'sitecustomize', 'cffi.setuptools_ext', 'cffi.recompiler', 'cffi.verifier']
    (output/'build').mkdir()
    spec = output/'build'/(name+'.spec')
    spec.write_text(
        '# -*- mode: python ; coding: utf-8 -*-\n'
        f'a = Analysis([{str(stage/"hyol_desktop.py")!r}], pathex=[], binaries=[], '
        f'datas={[(str(bundle), "hyol_bundle"), (str(samples), "daruma_standalone/samples")]!r}, '
        "hiddenimports=['webview.platforms.edgechromium', 'webview.platforms.winforms'], "
        f'hookspath=[], hooksconfig={{}}, runtime_hooks=[], excludes={excluded_modules!r}, '
        'noarchive=False, optimize=0)\n'
        'import sys\n'
        f'sys.path.insert(0, {str(ROOT)!r})\n'
        'from tools.build_shareable_exe import sanitize_native_binaries\n'
        f'a.binaries = sanitize_native_binaries(a.binaries, {str(output/"clean-native")!r})\n'
        'pyz = PYZ(a.pure)\n'
        f'exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name={name!r}, debug=False, '
        'bootloader_ignore_signals=False, strip=False, upx=False, upx_exclude=[], '
        'runtime_tmpdir=None, console=False, disable_windowed_traceback=False, '
        'argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None)\n',
        encoding='utf-8')
    command = [sys.executable, '-B', '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--distpath', str(output/'dist'), '--workpath', str(output/'build'), str(spec)]
    save_json(output/'build-command.json', command)
    with (output/'build.log').open('w', encoding='utf-8') as stream:
        subprocess.run(command, cwd=stage, stdout=stream, stderr=subprocess.STDOUT, check=True)
    changed = [name for name, digest in inputs.items() if sha((ROOT/name).read_bytes()) != digest]
    if changed:
        save_json(output/'baseline-drift.json', changed)
        raise ValueError('Build inputs changed; rebuild the final baseline')
    executable = output/'dist'/(name+'.exe')
    report = audit_executable(ROOT, executable, bundle, samples)
    save_json(output/'singlefile-audit.json', report)
    save_json(output/'embedded-source-report.json', report['source'])
    if report['result'] != 'PASS':
        raise ValueError('Final executable audit failed; see singlefile-audit.json')
    print(json.dumps(dict(executable=str(executable), result=report['result'],
                         bytes=report['bytes'], sha256=report['sha256'],
                         modules=report['python_modules_checked'],
                         resources=report['embedded_resources_checked']), ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--name', default='火影OL_分享版_'+RELEASE_DATE)
    arguments = parser.parse_args()
    build(arguments.output, arguments.name)
