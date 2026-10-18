"""Build all five clients in a separate directory without publishing or input."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.release_runtime_snapshot import PACKAGES
from tools.verify_packaged_source import verify_package
from hyol_runtime import VERSION

SOURCES = dict(daily='hyol_daily_tasks.py', team='hyol_team_tasks.py',
               arena='hyol_arena.py', bond='hyol_bond.py', unified='hyol_client.py')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', required=True)
    parser.add_argument('--build', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--log', required=True)
    args = parser.parse_args()
    dist, build, report, log = [(ROOT/value).resolve() for value in
        (args.dist,args.build,args.report,args.log)]
    if any(not path.is_relative_to(ROOT) for path in (dist,build,report,log)):
        raise ValueError('All build outputs must stay in the workspace')
    if report.exists() or log.exists():
        raise ValueError('Preserve existing build evidence')
    packages = []
    with log.open('x',encoding='utf-8') as stream:
        for key,_,name,folder in PACKAGES:
            command = [str(ROOT/'.build-env/Scripts/python.exe'),'-m','PyInstaller',
                '--noconfirm','--clean','--onedir','--windowed','--name',name,
                '--distpath',str(dist),'--workpath',str(build/key),
                '--specpath',str(build/key),str(ROOT/SOURCES[key])]
            result = subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
            stream.flush()
            if result.returncode:
                raise RuntimeError(f'Build failed: {key}; see {log}')
            exe = dist/name/(name+'.exe')
            check = verify_package(ROOT,exe)
            if not check['source_code_matches']:
                raise ValueError(f'Embedded source differs: {key}')
            packages.append(dict(Key=key,Name=name,Folder=folder,Source=SOURCES[key],Exe=str(exe),
                Sha256=hashlib.sha256(exe.read_bytes()).hexdigest().upper(),
                SourceCodeMatches=True,Modules=check['modules_checked']))
            print(f'Built and source verified: {key}',flush=True)
    report.write_text(json.dumps(dict(Version=VERSION,Status='staged',Packages=packages,
        GameTaskStarted=False),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(version=VERSION,packages=len(packages),report=str(report)),ensure_ascii=False))


if __name__ == '__main__':
    main()
