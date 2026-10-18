"""Measure exact recognition reuse and inspect the stopped run without game input."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace

from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import hyol_scheduler as hs
from hyol_checkpoints import read_checkpoint
from hyol_history import inactive_retry_budgets,session_started_at


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Preserve previous evidence')
    events=[json.loads(line) for line in args.journal.read_text('utf-8').splitlines()]
    starts=[event for event in events if event.get('event')=='content_click'
            and str(event.get('target','')).startswith('survival_battle_step_')]
    result_events=[event for event in events if event.get('event')=='content_click'
                   and str(event.get('target','')).startswith('survival_result_confirm_')]
    frames=[]
    for event in starts+result_events:
        with Image.open(event['evidence']) as image:frames.append(image.convert('RGB'))
    proofs=[event['proof'] for event in events if isinstance(event.get('proof'),dict)
            and event['proof'].get('session_token')]
    if not frames or not proofs:raise ValueError('Archived run lacks inputs or session proof')
    probes=(('anchor_top_menu.bmp',(500,69,1500,189),.92),
            ('anchor_right_panel.bmp',(1569,289,1919,489),.92),
            ('survival_anchor.bmp',(201,69,601,419),.92),
            ('survival_fight_start_core.bmp',(751,169,1151,419),.98),
            ('survival_result_confirm.bmp',(701,519,1151,819),.98),
            ('battle_auto_on_current_core.bmp',hs.BATTLE_AUTO_ROI,.98),
            ('survival_battle_hud_left_current.bmp',(410,200,520,255),.98),
            ('survival_battle_hud_right_current.bmp',(1390,200,1520,255),.98))
    stores={'reference':hs.TemplateStore(.92),'cached':hs.TemplateStore(.92)}
    stores['reference']._template_spectra=None
    outputs={mode:[] for mode in stores};samples={mode:[] for mode in stores}
    for trial in range(19):
        for mode in (tuple(stores) if trial%2==0 else tuple(reversed(stores))):
            store=stores[mode];values=[]
            began=time.perf_counter()
            for frame in frames:
                # Discard per-frame caches in both modes, as a new capture does.
                store.begin_frame(frame)
                for asset,roi,cutoff in probes:values.append(store.match(frame,hs.ASSETS/asset,roi,cutoff))
            elapsed=(time.perf_counter()-began)*1000
            if trial: samples[mode].append(elapsed);outputs[mode].append(values)
    reference_ms=statistics.median(samples['reference']);cached_ms=statistics.median(samples['cached'])
    same_decisions=outputs['reference']==outputs['cached']
    store=stores['cached'];eligibility=[]
    started=session_started_at(proofs[0]['session_token'])
    for path in sorted((ROOT/'state/2026-10-09').glob('account_*.json')):
        if path.stem.endswith(('_tasks','_multi_solo','_daily_client','_team_client')):continue
        record=read_checkpoint(path)
        account=hs.Account(1,'anonymous-role',0)
        state=SimpleNamespace(directory=ROOT/'state',day='2026-10-09',accounts={account.name:record},
                              account_paths={account.name:path})
        eligible=inactive_retry_budgets(state,account,record.get('actions',{}),{},
                                        read_checkpoint(ROOT/'state/historical_isolations.json'),started)
        if eligible:eligibility.append({'account_file':path.name,'targets':list(eligible),
            'source_days':sorted({p.parent.name for _,sources in eligible.values() for p,_ in sources})})
    baseline=read_checkpoint(ROOT/'logs/solo_live_fix_runtime_before_20261009.json')
    root_baseline=json.loads((ROOT/'logs/solo_live_fix_root_state_before_20261009.json').read_text('utf-8'))
    changed=[row['path'] for row in root_baseline if not Path(row['path']).is_file()
             or hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()!=row['sha256']]
    report=dict(version='2026.10.09.2',content_clicks=0,scope='offline archived recognition and read-only state audit',
        original_journal_sha256=hashlib.sha256(args.journal.read_bytes()).hexdigest(),
        original_survival_starts=len(starts),original_survival_confirm_inputs=len(result_events),
        old_budget_migration_eligible=eligibility,
        template_spectra=dict(reference_median_ms=reference_ms,cached_median_ms=cached_ms,
            reduction_percent=100*(reference_ms-cached_ms)/reference_ms,
            same_scores_decisions_coordinates=same_decisions,iterations=18,frames=len(frames),
            probes_per_frame=len(probes),resident_bytes=store._template_spectra.bytes,
            max_bytes=store._template_spectra.max_bytes),
        root_state_files_checked=len(root_baseline),root_state_changed=changed,
        runtime_files_in_baseline=len(baseline['files']),live_acceptance='pending',whole_run_speedup='not_measured')
    hs.atomic_json(args.output,report)
    print(json.dumps(report,ensure_ascii=False))
    if not same_decisions or changed or not eligibility:raise SystemExit(1)


if __name__=='__main__':main()
