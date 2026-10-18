"""Review and publish the five entry READMEs and the enrolled toolbar core."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.release_runtime_snapshot import PACKAGES,default_desktop
from hyol_runtime import VERSION
from hyol_scheduler import ForegroundRuntimeLease
from PIL import Image

DOCS = dict(daily='DAILY_TASKS_README.md',team='TEAM_TASKS_README.md',
            arena='ARENA_CLIENT_README.md',bond='BOND_CLIENT_README.md',
            unified='README_统一客户端.md')
CORE = 'assets/team_toolbar_core_current.bmp'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def allowed():
    desktop = (default_desktop()/'可用脚本').resolve()
    rows = []
    for key,dist,name,folder in PACKAGES:
        for scope,parent,boundary in (
                ('desktop',desktop/folder,desktop),('dist',ROOT/dist/name,ROOT)):
            parent = parent.resolve()
            if not parent.is_relative_to(boundary) or not parent.is_dir():
                raise ValueError('Unexpected package resource directory')
            for relative,kind in ((DOCS[key],'readme'),(CORE,'asset')):
                source,target = (ROOT/relative).resolve(),(parent/relative).resolve()
                if not source.is_relative_to(ROOT) or not target.is_relative_to(parent):
                    raise ValueError('Resource path escaped its package')
                if not source.is_file() or not target.parent.is_dir():
                    raise ValueError('Source or installed resource directory is missing')
                rows.append(dict(key=key,scope=scope,relative=relative,kind=kind,
                                 source=str(source),target=str(target)))
    return rows


def validate_sources():
    for name in DOCS.values():
        if '版本：'+VERSION not in (ROOT/name).read_text(encoding='utf-8-sig'):
            raise ValueError('README version differs: '+name)
    with Image.open(ROOT/'assets/team_toolbar.bmp') as original, Image.open(ROOT/CORE) as core:
        if core.size != (14,14) or original.crop((3,5,17,19)).tobytes() != core.tobytes():
            raise ValueError('Toolbar core differs from its reviewed source crop')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',default='logs/fresh_run_scope_fix_resources_plan_20261017.json')
    parser.add_argument('--backup',default='logs/release_preserved/fresh_run_scope_fix_20261017/resources')
    parser.add_argument('--receipt',default='logs/fresh_run_scope_fix_resources_published_20261017.json')
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    plan,backup,receipt = [(ROOT/value).resolve() for value in (args.plan,args.backup,args.receipt)]
    if any(not path.is_relative_to(ROOT) for path in (plan,backup,receipt)):
        raise ValueError('Evidence and backups must remain in the workspace')
    validate_sources()
    expected = allowed()
    if not args.apply:
        if plan.exists():
            raise ValueError('Preserve existing resource plan')
        for row in expected:
            row.update(sha256=digest(row['source']),previous_sha256=(
                digest(row['target']) if Path(row['target']).exists() else None))
        plan.write_text(json.dumps(dict(version=VERSION,files=expected),
            ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(status='reviewed',version=VERSION,files=len(expected),plan=str(plan)),ensure_ascii=False))
        return
    data = json.loads(plan.read_text(encoding='utf-8-sig'))
    if data.get('version') != VERSION or receipt.exists():
        raise ValueError('Plan version differs or receipt already exists')
    rows = data['files']
    if len(rows) != len(expected) or any(
            any(row.get(k) != allowed_row[k] for k in allowed_row)
            for row,allowed_row in zip(rows,expected)):
        raise ValueError('Plan differs from the exact resource allowlist')
    lease = ForegroundRuntimeLease()
    if not lease.acquire():
        raise ValueError('A game worker is active; preserve desktop resources')
    try:
        for row in rows:
            target = Path(row['target'])
            current = digest(target) if target.exists() else None
            if current != row['previous_sha256'] or digest(row['source']) != row['sha256']:
                raise ValueError('Reviewed resource changed')
            saved = backup/row['scope']/row['key']/row['relative']
            if saved.exists():
                raise ValueError('Preserve the prior resource backup')
            if target.exists():
                saved.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(target,saved)
                if digest(saved) != current:
                    raise ValueError('Resource backup hash mismatch')
                row['backup'] = str(saved)
        for row in rows:
            target = Path(row['target'])
            temporary = target.with_name(target.name+'.fresh_run_resource')
            if temporary.exists():
                raise ValueError('Preserve an earlier resource temporary file')
            shutil.copy2(row['source'],temporary)
            if digest(temporary) != row['sha256']:
                raise ValueError('Copied resource hash mismatch')
            temporary.replace(target)
        if any(digest(row['target']) != row['sha256'] for row in rows):
            raise ValueError('Published resource hash mismatch')
        receipt.write_text(json.dumps(dict(status='published',version=VERSION,files=rows,
            at=datetime.now().astimezone().isoformat(),game_inputs=0),ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(status='published',version=VERSION,files=len(rows),receipt=str(receipt)),ensure_ascii=False))
    finally:
        lease.release()


if __name__=='__main__':
    main()
