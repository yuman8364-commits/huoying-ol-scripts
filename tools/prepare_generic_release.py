"""Create a portable clean release without local account material or runtime data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile


def personal_asset(path):
    path = Path(path)
    return ('accounts' in path.parts or path.name.endswith('.source.json')
            or bool(re.match(r'friend_.+_row\.',path.name))
            or bool(re.match(r'team\d+_(two|three)_member_anchor\.',path.name))
            or path.name.startswith('team_invite_from_'))


def clean_config(text):
    lines = []
    for line in text.splitlines():
        key = line.split('=',1)[0].strip()
        if (key in ('TabAccountNames','SharedStateDirectory','PanelCurrentAccount','PanelCurrentTabX')
                or re.match(r'Team\d+(Verified|Friend|Invite|Member)',key)
                or re.fullmatch(r'Team\d+',key)):
            continue
        if key == 'AllowMarketPurchase': line = 'AllowMarketPurchase=0'
        if key == 'MarketDirectBuyVerified': line = 'MarketDirectBuyVerified=0'
        lines.append(line)
    updates = dict(AccountDiscovery='automatic',HistoricalPendingPolicy='isolate_inactive',
                   SharedStateDirectory=r'%LOCALAPPDATA%\HYOLClient\state',
                   AllowMarketPurchase='0',MarketDirectBuyVerified='0')
    lines = [line for line in lines if line.split('=',1)[0].strip() not in updates]
    lines.extend(key+'='+value for key,value in updates.items())
    return '\n'.join(lines)+'\n'


def sync_assets(source,destination,backup):
    source,destination,backup = Path(source).resolve(),Path(destination).resolve(),Path(backup).resolve()
    destination.mkdir(parents=True,exist_ok=True)
    for path in list(destination.rglob('*')):
        if path.is_file() and personal_asset(path.relative_to(destination)):
            target = backup/path.relative_to(destination)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target)
            if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                raise ValueError('Personal asset archive failed; original preserved')
            path.unlink()
    count = 0
    for path in source.rglob('*'):
        relative = path.relative_to(source)
        if path.is_file() and not personal_asset(relative):
            target = destination/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target)
            count += 1
    return dict(generic_assets=count,personal_assets_archived=True)


def prepare(root, package, output):
    root,package,output = Path(root).resolve(),Path(package).resolve(),Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new release output directory; existing files are preserved')
    if output == package or package in output.parents or output in package.parents:
        raise ValueError('Clean release and installed package must be separate')
    destination = output/package.name
    destination.mkdir(parents=True)
    for path in package.rglob('*'):
        relative = path.relative_to(package)
        if (not path.is_file() or any(part in ('state','logs','references','tests','__pycache__') for part in relative.parts)
                or re.search(r'_(state|result|failure)\.json$',path.name)
                or path.suffix.lower() == '.ini' or path.suffix.lower() == '.md'
                or 'assets' in relative.parts and personal_asset(relative)):
            continue
        target = destination/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,target)
    for name in ('HYOL_Daily.ini','DAILY_Client.ini','TEAM_Client.ini','ARENA_Client.ini'):
        content = clean_config((root/name).read_text(encoding='utf-8-sig'))
        (destination/name).write_text(content,encoding='utf-8')
    shutil.copy2(root/'README_通用客户端.md',destination/'README.md')
    files = [dict(path=str(path.relative_to(destination)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
             for path in sorted(destination.rglob('*')) if path.is_file()]
    (output/'manifest.json').write_text(json.dumps(dict(files=files,personal_account_material=False,
        runtime_data_included=False,shared_state='per_user',live_acceptance='pending'),
        ensure_ascii=False,indent=2),encoding='utf-8')
    archive = output/(package.name+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in destination.rglob('*'):
            if path.is_file(): bundle.write(path,path.relative_to(output))
    return dict(folder=str(destination),archive=str(archive),files=len(files),
                personal_account_material=False,runtime_data_included=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--assets-only',action='store_true')
    parser.add_argument('--backup',type=Path)
    args = parser.parse_args()
    if args.assets_only:
        if args.backup is None: parser.error('--assets-only requires --backup')
        result = sync_assets(args.package,args.output,args.backup)
    else:
        result = prepare(args.root,args.package,args.output)
    print(json.dumps(result,ensure_ascii=False))
