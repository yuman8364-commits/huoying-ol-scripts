"""Read-only source and portable ZIP audit; never reads or changes game state."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.prepare_generic_release import personal_asset


def audit(root, archives):
    labels = json.loads((root/'tests/fixtures/legacy_identity_labels.json').read_text(encoding='utf-8'))['labels']
    paths = sorted(set(root.glob('*.py')) | set(root.glob('*.ini'))
                   | set(root.glob('*.ps1')) | set((root/'tools').glob('*.py')))
    violations = [str(path.relative_to(root)) for path in paths
                  if any(label in path.read_text(encoding='utf-8-sig') for label in labels)]
    packages = []
    for path in archives:
        with zipfile.ZipFile(path) as bundle:
            names = bundle.namelist()
            forbidden = [name for name in names if
                any(part in ('state','logs','references','tests','__pycache__') for part in Path(name).parts)
                or 'assets' in Path(name).parts and personal_asset(name)]
            configs = [name for name in names if name.lower().endswith('.ini')]
            invalid = []
            for name in configs:
                content = bundle.read(name).decode('utf-8-sig')
                values = dict(line.split('=',1) for line in content.splitlines()
                              if '=' in line and not line.startswith(('#',';')))
                if (any(label in content for label in labels)
                        or re.search(r'(?i)[a-z]:[\\/]',content)
                        or values.get('SharedStateDirectory') != r'%LOCALAPPDATA%\HYOLClient\state'
                        or values.get('AccountDiscovery') != 'automatic'
                        or values.get('AllowMarketPurchase') != '0'
                        or values.get('MarketDirectBuyVerified') != '0'
                        or values.get('TabAccountNames','').strip()):
                    invalid.append(name)
            packages.append(dict(archive=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 files=len(names),config_count=len(configs),forbidden=forbidden,
                                 invalid_configs=invalid,crc_error=bundle.testzip()))
    valid = (not violations and packages and all(not row['forbidden'] and not row['invalid_configs']
             and row['config_count']==4 and not row['crc_error'] for row in packages))
    return dict(result='PASS' if valid else 'FAIL',source_files_checked=len(paths),
                personal_identifier_literals=violations,packages=packages,content_clicks=0)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives',nargs='+',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('Preserve prior evidence; choose a new output file')
    result = audit(Path(__file__).resolve().parents[1],args.archives)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(0 if result['result']=='PASS' else 1)
