"""Build an audited, directly loadable folder for Windows distribution."""
from __future__ import annotations

import argparse
import json
import marshal
from pathlib import Path
import re
import shutil
import subprocess
import sys
import types
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.build_shareable_exe import (stage_release,save_json,sha,known_labels,
    text_issues,code_strings,CONFIGS,PUZZLE_NAME,PUZZLE_SHA256)
from tools.verify_packaged_source import verify_package
from hyol_runtime import VERSION, RELEASE_DATE


def copy_folder_resources(bundle, package):
    """Resources stay beside the executable; application_root never extracts them."""
    for source in bundle.rglob('*'):
        relative=source.relative_to(bundle)
        if not source.is_file() or relative.as_posix() in ('manifest.json','使用说明.txt'):continue
        target=package/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    for filename in CONFIGS:
        path=package/filename
        content=path.read_text('utf-8-sig')
        content=re.sub(r'^SharedStateDirectory=.*$',
            lambda _:r'SharedStateDirectory=%LOCALAPPDATA%\HYOLFolderClient\state',content,flags=re.M)
        path.write_text(content,encoding='utf-8')
    (package/'使用说明.txt').write_text(
        '火影OL 标准版 '+VERSION+'\n\n'
        '双击“火影OL.exe”运行，打开后保持空闲，选择任务再开始。F12或停止按钮停止。\n'
        '点击“检查并回木叶村”或“开始本轮”后，逐号先回木叶村，再检查其他忍者显示。\n'
        '同一次开始复用回村凭据并重新观察核实，不重复进行整轮回村。\n'
        '左侧勾选、拖拽任务，中间设置参数，右侧常驻日志；运行中固定本轮顺序。\n'
        '当前账号页可自选组队活动；组队副本、强者默认不选，只控制本号。\n'
        '当前账号羁绊和3v3完成后保留队伍及准备页，只核实本号结果。\n'
        '修行录连续完成已核实目标，最终灰态及领奖才算系列完成，必要解锁边界定向复核。\n'
        '修行录竞技场可选完成最终目标或战令用完；自动设置下周日默认完成最终目标。\n'
        '日志及报错以中文说明原因和处理建议，重复观察合并显示，原始诊断单独保存。\n'
        '生存按实际免费重置继续，原来已完成与本轮战斗、领奖分别统计。\n'
        '已有三人队可选择限时竞技场3v3；先完成其他组队任务，3v3固定最后。\n'
        '三人的六项任务奖励和三个宝箱分别核实，全部领取后退出、核实解散并回村。\n'
        '已在3v3时保留活动页面；黑屏、未知战斗或领取结果保留原动作，恢复后继续。\n'
        '文件夹内的配套文件必须保留。可以改名、移动或压缩整个原始文件夹发给其他人。\n'
        '无需安装Python；Windows 10/11 64位，需系统WebView2 Runtime与.NET Framework 4.8。\n\n'
        '在任务计划选择独立的“周年庆活动”板块，设置处理前1～6个账号，当前支持薪火签到。\n'
        '周年庆内容不混入普通领奖，未检测到限时活动时静默略过。\n'
        '按实际解锁日继续核对全部10天，已领取不重复；结果未知保留凭据，下次先核对。\n'
        '背包满或入口暂不可用时保留该项，核实安全后继续其他所选任务和账号。\n'
        '购买保持禁用。游戏窗口与显示条件沿用原版要求，不要同时运行多个控制游戏的脚本。\n\n'
        '分享包不带制作者账号、个人配置、运行日志、断点或登录缓存。\n'
        '本机用户的共享账本保存在%LOCALAPPDATA%\\HYOLFolderClient\\state；更换新版文件夹仍沿用。\n'
        '使用后生成的账号素材、日志、设置属于使用者；再次分享请使用未运行过的原始压缩包。\n',encoding='utf-8')


def audit_folder(root, package, bundle):
    from PyInstaller.archive.readers import CArchiveReader
    executable=package/'火影OL.exe'
    archive=CArchiveReader(str(executable))
    pyz=archive.open_embedded_archive('PYZ.pyz')
    labels=known_labels(root)
    local_paths=(str(Path.home()),str(Path(root).resolve()))
    findings=[]
    def inspect_code(code,label,project=False):
        if not isinstance(code,types.CodeType):return
        for value in code_strings(code):
            if any(prefix.casefold() in value.casefold() for prefix in local_paths):
                findings.append(dict(path=label,issue='literal_local_path'))
            if project:
                findings.extend(dict(path=label,issue=issue) for issue in text_issues(value,labels))
    for module in pyz.toc:
        inspect_code(pyz.extract(module),module,module.startswith(('hyol_','daruma_standalone')))
    for key,row in archive.toc.items():
        if row[-1]=='s':inspect_code(marshal.loads(archive.extract(key)),key,key.startswith('hyol_'))
    manifest=[]
    for path in sorted(package.rglob('*')):
        if not path.is_file():continue
        relative=path.relative_to(package).as_posix()
        content=path.read_bytes()
        manifest.append(dict(path=relative,bytes=len(content),sha256=sha(content)))
        if (any(part.lower() in ('logs','state','accounts','fixtures','tests','references','__pycache__')
                for part in path.relative_to(package).parts)
                or re.search(r'_(?:state|result|failure)\.json$',path.name)):
            findings.append(dict(path=relative,issue='runtime_data'))
        if any(prefix.encode(encoding).lower() in content.lower()
               for prefix in local_paths for encoding in ('utf-8','utf-16-le')):
            findings.append(dict(path=relative,issue='literal_local_path'))
        if path.suffix.lower() in ('.ini','.json','.js','.html','.css','.svg','.txt','.ps1'):
            findings.extend(dict(path=relative,issue=issue)
                            for issue in text_issues(content.decode('utf-8-sig'),labels))
        if path.name=='base_library.zip':
            with zipfile.ZipFile(path) as library:
                for name in library.namelist():
                    if name.endswith('.pyc'):inspect_code(marshal.loads(library.read(name)[16:]),relative+'/'+name)
    resources=[]
    for source in bundle.rglob('*'):
        relative=source.relative_to(bundle)
        if not source.is_file() or relative.as_posix() in ('manifest.json','使用说明.txt') or relative.name in CONFIGS:continue
        resources.append(relative.as_posix())
        if sha(source.read_bytes())!=sha((package/relative).read_bytes()):
            findings.append(dict(path=relative.as_posix(),issue='resource_changed'))
    if (package/'_internal/hyol_bundle').exists():findings.append(dict(issue='unnecessary_bundle_extraction'))
    puzzle=package/'assets/puzzle_backend'/PUZZLE_NAME
    if sha(puzzle.read_bytes())!=PUZZLE_SHA256:findings.append(dict(issue='puzzle_original_hash'))
    for label in labels:
        if label and any(label.encode(encoding) in puzzle.read_bytes() for encoding in ('utf-8','utf-16-le','gb18030')):
            findings.append(dict(issue='identifier_in_puzzle_binary'))
    source=verify_package(root,executable)
    if not source['source_code_matches']:findings.append(dict(issue='embedded_source_mismatch'))
    return dict(result='PASS' if not findings else 'FAIL',findings=findings,version=VERSION,
                packaging='onedir',startup_resource_extraction=False,personal_state_included=False,
                file_count=len(manifest),unpacked_bytes=sum(row['bytes'] for row in manifest),
                resources_checked=len(resources),python_modules_checked=len(pyz.toc),
                modules_checked=source['modules_checked'],source=source,files=manifest)


def build(output, *, make_zip=True):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new output directory; existing versions are preserved')
    output.mkdir(parents=True)
    stage,bundle,samples,inputs=stage_release(ROOT,output,'火影OL')
    excludes=['PyQt5','PyQt6','PySide2','PySide6','cefpython3','gi','onnxruntime',
        'setuptools','sitecustomize','cffi.setuptools_ext','cffi.recompiler','cffi.verifier']
    spec=output/'build/folder.spec'
    spec.parent.mkdir()
    spec.write_text(
        '# -*- mode: python ; coding: utf-8 -*-\n'
        f'a=Analysis([{str(stage/"hyol_desktop.py")!r}],pathex=[],binaries=[], '
        f'datas={[(str(samples),"daruma_standalone/samples")]!r}, '
        "hiddenimports=['webview.platforms.edgechromium','webview.platforms.winforms'], "
        f'hookspath=[],hooksconfig={{}},runtime_hooks=[],excludes={excludes!r},noarchive=False,optimize=0)\n'
        'import sys\n'
        f'sys.path.insert(0,{str(ROOT)!r})\n'
        'from tools.build_shareable_exe import sanitize_native_binaries\n'
        f'a.binaries=sanitize_native_binaries(a.binaries,{str(output/"clean-native")!r})\n'
        'pyz=PYZ(a.pure)\n'
        "exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='火影OL',debug=False,"
        'bootloader_ignore_signals=False,strip=False,upx=False,console=False,disable_windowed_traceback=False)\n'
        "coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='火影OL最终版')\n",encoding='utf-8')
    command=[sys.executable,'-B','-m','PyInstaller','--clean','--noconfirm',
        '--distpath',str(output/'dist'),'--workpath',str(output/'build'),str(spec)]
    save_json(output/'build-command.json',command)
    with (output/'build.log').open('w',encoding='utf-8') as stream:
        subprocess.run(command,cwd=stage,stdout=stream,stderr=subprocess.STDOUT,check=True)
    package=output/'dist/火影OL最终版'
    copy_folder_resources(bundle,package)
    save_json(package/'PACKAGE_INFO.json',dict(version=VERSION,edition='OpenCV',packaging='onedir',
        release_date=RELEASE_DATE,startup_resource_extraction=False,
        personal_material=False,runtime_data_included=False,
        limited_rewards='Dedicated anniversary section; Xinhuo 10-day sign-in only when detected',
        team_activity='Limited arena_3v3; preformed trio; six task rewards and three milestone chests per account',
        shared_state='per Windows user; HYOLFolderClient/state',
        requires='Windows x64, Microsoft Edge WebView2 Runtime, .NET Framework 4.8'))
    changed=[name for name,digest in inputs.items() if sha((ROOT/name).read_bytes())!=digest]
    if changed:raise ValueError('Build baseline changed: '+repr(changed))
    report=audit_folder(ROOT,package,bundle)
    save_json(output/'folder-audit.json',report)
    save_json(output/'embedded-source-report.json',report['source'])
    if report['result']!='PASS':raise ValueError('Folder audit failed; see folder-audit.json')
    delivery=dict(version=VERSION,files=report['files'],archive_created=False)
    if make_zip:
        archive=output/'火影OL标准版.zip'
        with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as target:
            for row in report['files']:target.write(package/row['path'],Path(package.name)/row['path'])
        with zipfile.ZipFile(archive) as target:
            if target.testzip():raise ValueError('Folder ZIP CRC failed')
        delivery.update(archive_created=True,zip_crc='PASS',archive_sha256=sha(archive.read_bytes()))
    save_json(output/'delivery-manifest.json',delivery)
    print(json.dumps({key:report[key] for key in ('result','version','packaging','file_count',
        'unpacked_bytes','resources_checked','modules_checked')},ensure_ascii=False))
    return package


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--no-zip',action='store_true',help='仅构建并审计最终目录，不创建ZIP')
    args=parser.parse_args()
    build(args.output,make_zip=not args.no_zip)
