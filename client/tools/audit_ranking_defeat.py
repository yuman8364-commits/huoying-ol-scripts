"""Record an inspected ranking defeat result for later counter reconciliation.

This tool never controls the game window.  The operator must inspect the
account name in the supplied screenshot before specifying --account.
"""
import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_ranking as ranking
import hyol_scheduler as hs


def audit(account: str, evidence: Path) -> dict:
    evidence = evidence.resolve(strict=True)
    if not evidence.is_relative_to(hs.LOGS.resolve()):
        raise ValueError('结果截图必须来自本项目日志目录')
    if date.today().strftime('%Y%m%d') not in evidence.name:
        raise ValueError('只接受当日结果截图')
    state_path = hs.STATE_DIR / date.today().isoformat() / f'{hs.slug(account)}_multi_solo.json'
    action_path = hs.STATE_DIR / date.today().isoformat() / f'{hs.slug(account)}.json'
    recs = json.loads(state_path.read_text(encoding='utf-8'))
    ledger = json.loads(action_path.read_text(encoding='utf-8'))
    if ledger.get('account') != account:
        raise ValueError('动作账本与指定账号不一致')
    actions = ledger.get('actions', {})
    rec = recs.get('ranking', {})
    if (rec.get('status') != 'pending' or rec.get('phase') != 'confirmed'
            or not isinstance(rec.get('before'), int) or rec['before'] <= 0):
        raise ValueError('排位断点不是已确认且次数大于零的待核实挑战')
    index = rec.get('completed', 0) + 1
    if any(actions.get(f'daily_ranking_{index}_{part}', {}).get('status') != 'pending'
           for part in ('start', 'confirm')):
        raise ValueError('排位开始及确认动作未同时处于 pending')
    with Image.open(evidence) as source:
        frame = source.convert('RGB')
    if frame.size != (1920, 1032):
        raise ValueError('结果截图尺寸与当前共享窗口不一致')
    store = hs.TemplateStore(.90)
    anchors = (
        ('ranking_result_confirm.bmp', ranking.RESULT, .95),
        ('ranking_result_stats.bmp', (890, 605, 1035, 653), .95),
        ('arena_defeat_banner.bmp', (650, 320, 1250, 800), .90),
    )
    scores = {}
    for name, roi, threshold in anchors:
        found, score, _, _ = store.match(frame, hs.ASSETS/name, roi, threshold)
        if not found:
            raise ValueError(f'结果截图缺少明确失败锚点 {name}: {score:.3f}')
        scores[name] = round(score, 4)
    rec['manual_defeat_result_evidence'] = str(evidence)
    rec['manual_defeat_result_sha256'] = hashlib.sha256(evidence.read_bytes()).hexdigest()
    rec['manual_defeat_result_audited_at'] = hs.now_iso()
    recs['updated_at'] = hs.now_iso()
    hs.atomic_json(state_path, recs)
    return {'account': account, 'before': rec['before'], 'phase': rec['phase'],
            'status': rec['status'], 'evidence': str(evidence), 'scores': scores,
            'content_clicks': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.account, args.evidence), ensure_ascii=False))
