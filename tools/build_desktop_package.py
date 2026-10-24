"""Build the accepted desktop interface with the existing standard runtime."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hyol_runtime import VERSION
from tools.build_unified_package import copy_resources,validate_profile
from tools.verify_packaged_source import verify_package


def build(output,with_vision=False):
    output=Path(output).resolve()
    if output.exists():
        raise ValueError('请使用新的构建目录，保留已有成品。')
    if with_vision and importlib.util.find_spec('onnxruntime') is None:
        raise ValueError('视觉诊断构建需要先安装requirements-vision.txt')
    output.mkdir(parents=True)
    profile='vision' if with_vision else 'standard'
    name='火影OL完整客户端'
    package=output/'dist'/name
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
             '--name',name,'--distpath',str(output/'dist'),'--workpath',str(output/'build'),
             '--specpath',str(output/'build'),'--hidden-import','webview.platforms.edgechromium',
             '--hidden-import','webview.platforms.winforms']
    # pythonnet loads prebuilt DLLs through CFFI's ABI mode. Its C-extension
    # compiler and setuptools are build-time tools, not desktop dependencies.
    for module in ('PyQt5','PyQt6','PySide2','PySide6','cefpython3','gi',
                   'setuptools','cffi.setuptools_ext','cffi.recompiler','cffi.verifier'):
        command += ['--exclude-module',module]
    if not with_vision:
        command += ['--exclude-module','onnxruntime']
    command.append(str(ROOT/'hyol_desktop.py'))
    subprocess.run(command,cwd=ROOT,check=True)
    copy_resources(ROOT,package,with_vision)
    shutil.copytree(ROOT/'web',package/'web',ignore=shutil.ignore_patterns('__pycache__','*.tmp'))
    for filename in ('README_DESKTOP.md','INTERFACE_MAP.md','DESIGN.md'):
        shutil.copy2(ROOT/filename,package/filename)
    (package/'查看界面演示.cmd').write_bytes(('@echo off\r\ncd /d "%~dp0"\r\n"'+name+'.exe" --demo\r\n').encode('utf-8'))
    validate_profile(package,with_vision)
    embedded=verify_package(ROOT,package/(name+'.exe'))
    if not embedded['source_code_matches']:
        raise ValueError('成品内嵌源码与当前源码不同')
    info=dict(version=VERSION,profile=profile,ui='Kimi accepted design / native WebView2',
              runtime='RuntimeController',base_commit='e1ce1616a38be80d332de111a99e5272e7f63a49',
              live_game_acceptance='pending user testing',game_task_started=False,
              requires='Windows x64, Microsoft Edge WebView2 Runtime, .NET Framework 4.8',
              demo_default=False,optional_vision=with_vision,personal_material=False)
    (package/'PACKAGE_INFO.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),'utf-8')
    files=sorted(p for p in package.rglob('*') if p.is_file())
    manifest=[dict(path=p.relative_to(package).as_posix(),bytes=p.stat().st_size,
                   sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files]
    prefix=Path('火影OL完整客户端_'+VERSION)/name
    archive=output/f'huoying-ol-desktop-{profile}-{VERSION}.zip'
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path,prefix/path.relative_to(package))
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip():
            raise ValueError('客户端ZIP校验失败')
    report=dict(**info,archive=str(archive),archive_bytes=archive.stat().st_size,
                unpacked_bytes=sum(row['bytes'] for row in manifest),file_count=len(manifest),
                sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),zip_crc='PASS',
                modules_checked=embedded['modules_checked'],embedded_source_matches=True,
                files=manifest)
    (output/'desktop-package-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    (output/'desktop-embedded-source-report.json').write_text(json.dumps(embedded,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='files'},ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--with-vision',action='store_true')
    args=parser.parse_args()
    build(args.output,args.with_vision)
