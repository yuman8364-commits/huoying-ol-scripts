"""Build a versioned portable client without modifying desktop installations."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_generic_release import clean_config, personal_asset

BASE_COMMIT = 'fe51a66014dd76f65c582638df302aee22df9533'


def copy_resources(root, package, with_vision=False):
    root, package = Path(root), Path(package)
    for path in (root/'assets').rglob('*'):
        relative = path.relative_to(root/'assets')
        if path.is_file() and not personal_asset(relative):
            target = package/'assets'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    if with_vision:
        manifest_path = root/'models/detector.json'
        manifest = json.loads(manifest_path.read_text('utf-8-sig'))
        model = (manifest_path.parent/manifest['file']).resolve()
        if not model.is_relative_to(manifest_path.parent.resolve()):
            raise ValueError('Model escaped manifest directory')
        if hashlib.sha256(model.read_bytes()).hexdigest() != manifest['sha256']:
            raise ValueError('Model SHA256 does not match manifest')
        model_target = package/'models'/manifest['file']
        model_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(model, model_target)
        shutil.copy2(manifest_path, package/'models/detector.json')
    for name in ('HYOL_Daily.ini', 'DAILY_Client.ini', 'TEAM_Client.ini', 'ARENA_Client.ini'):
        text = clean_config((root/name).read_text('utf-8-sig'))
        # Diagnostic inference always requires an explicit user opt-in.
        text = '\n'.join(line for line in text.splitlines() if not line.startswith('EnableShadowDetection='))
        (package/name).write_text(text+'\nEnableShadowDetection=0\n', 'utf-8')
    shutil.copy2(root/'README_统一客户端.md', package/'README.md')


def validate_profile(package, with_vision=False):
    package = Path(package)
    onnx = list((package/'_internal').rglob('*onnxruntime*'))
    if with_vision:
        if not onnx or not (package/'models/detector.json').is_file():
            raise ValueError('Diagnostic profile is missing model/runtime')
        if (package/'models/candidates').exists():
            raise ValueError('Development candidates must not be distributed')
    elif onnx or (package/'models').exists():
        raise ValueError('Standard profile contains optional vision components')


def build(output, *, with_vision=False, skip_build=False, install_desktop=False):
    from hyol_runtime import VERSION
    from tools.verify_packaged_source import verify_package
    output = Path(output).resolve()
    profile = 'vision' if with_vision else 'standard'
    name = '火影OL完整客户端'
    if with_vision and importlib.util.find_spec('onnxruntime') is None:
        raise ValueError('Install requirements-vision.txt into the build environment first')
    package = output/'dist'/name
    archive = output/f'huoying-ol-unified-{profile}-{VERSION}.zip'
    if archive.exists() or (package.exists() and not skip_build):
        raise ValueError('Choose a new output directory; existing packages are preserved')
    output.mkdir(parents=True, exist_ok=True)
    if not skip_build:
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
                   '--name', name, '--distpath', str(output/'dist'), '--workpath', str(output/'build'),
                   '--specpath', str(output/'build')]
        if not with_vision:
            command += ['--exclude-module', 'onnxruntime']
        command.append(str(ROOT/'hyol_client.py'))
        subprocess.run(command, cwd=ROOT, check=True)
    if not (package/(name+'.exe')).is_file():
        raise ValueError('Built executable is missing')
    copy_resources(ROOT, package, with_vision)
    validate_profile(package, with_vision)
    source = verify_package(ROOT, package/(name+'.exe'))
    if not source['source_code_matches']:
        raise ValueError('Embedded source differs from current source')
    info = dict(version=VERSION, base_commit=BASE_COMMIT, profile=profile,
                default_openblas_threads=2, explicit_environment_respected=True,
                optional_vision=with_vision, shadow_inference_default=False,
                personal_material=False, game_task_started=False)
    (package/'PACKAGE_INFO.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), 'utf-8')
    files = sorted(path for path in package.rglob('*') if path.is_file())
    manifest = [dict(path=path.relative_to(package).as_posix(), bytes=path.stat().st_size,
                     sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in files]
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, Path(name)/path.relative_to(package))
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip():
            raise ValueError('Archive CRC verification failed')
    report = dict(**info, archive=str(archive), archive_bytes=archive.stat().st_size,
                  unpacked_bytes=sum(row['bytes'] for row in manifest), files=len(files),
                  sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                  source_modules_checked=source['modules_checked'], source_code_matches=True,
                  zip_crc='PASS', file_manifest=manifest)
    (output/'package-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    (output/'embedded-source-report.json').write_text(json.dumps(source, ensure_ascii=False, indent=2), 'utf-8')
    if install_desktop:
        # A versioned destination preserves every existing desktop installation.
        destination = Path.home()/'Desktop'/'可用脚本'/(name+'_'+VERSION+'_'+profile)
        if destination.exists():
            raise ValueError('Versioned desktop destination already exists')
        shutil.copytree(package, destination)
        report['desktop_copy'] = str(destination)
    return {key:value for key,value in report.items() if key != 'file_manifest'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--with-vision', action='store_true')
    parser.add_argument('--skip-build', action='store_true')
    parser.add_argument('--install-desktop', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build(args.output, with_vision=args.with_vision,
                           skip_build=args.skip_build, install_desktop=args.install_desktop), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
