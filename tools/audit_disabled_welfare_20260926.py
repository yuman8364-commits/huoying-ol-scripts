"""Reconcile the six disabled welfare buttons clicked by the 10:24 run.

Each reset requires the exact run's before-click image to show the grey
disabled fill. The irreversible-click ledger is retained in the JSONL.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs

RUN = '20260926_102429_538879_e781837b'
LOG = hs.LOGS / f'HYOL_Scheduler_{RUN}.jsonl'
DAY = '2026-09-26'


def main():
    rows = [json.loads(line) for line in LOG.read_text(encoding='utf-8').splitlines()]
    clicks = [row for row in rows if row.get('event') == 'content_click'
              and row.get('target') == 'welfare_claim_2']
    if len(clicks) != 6 or len({row['account'] for row in clicks}) != 6:
        raise RuntimeError('expected six distinct welfare claim 2 clicks')
    audit = []
    for row in clicks:
        path = Path(row['evidence'])
        if not path.is_file() or path.name.find('before_welfare_claim_2') < 0:
            raise RuntimeError(f'missing exact before-click evidence: {path}')
        rgb = np.asarray(Image.open(path).convert('RGB'), dtype=np.float64)
        center = rgb[711:729, 967:1031].mean(axis=(0, 1))
        if not (abs(center[0]-center[1]) < 10 and abs(center[1]-center[2]) < 10):
            raise RuntimeError(f'button was not proven grey: {path}')
        state_path = hs.STATE_DIR / DAY / f'{hs.slug(row["account"])}.json'
        state = json.loads(state_path.read_text(encoding='utf-8'))
        action = state['actions']['welfare_claim_2']
        if action.get('status') != 'pending' or action.get('detail') != 'written before click':
            raise RuntimeError(f'action ledger changed: {state_path}')
        audit.append(dict(account=row['account'], evidence=str(path),
                          mean_rgb=[round(float(v), 2) for v in center],
                          state=str(state_path)))
    # Validate every account before mutating any ledger.
    for item in audit:
        state_path = Path(item['state'])
        state = json.loads(state_path.read_text(encoding='utf-8'))
        state['actions']['welfare_claim_2'] = dict(
            status='new', detail=f'audited disabled grey button in {RUN}; no claim made',
            updated_at=hs.now_iso())
        hs.atomic_json(state_path, state)
    output = hs.LOGS / 'welfare_disabled_audit_20260926.json'
    hs.atomic_json(output, dict(run_id=RUN, result='six disabled clicks reset to new', rows=audit))
    print(output)


if __name__ == '__main__':
    main()
