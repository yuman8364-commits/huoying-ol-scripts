"""Audit immutable solo evidence and timing changes without game input."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import hyol_scheduler as hs
from hyol_survival_counts import read_counter, _counter_glyphs, grade_candidates


def main():
    source=Path.home()/'Desktop/可用脚本/火影OL完整客户端/logs'
    journal=source/'HYOL_Scheduler_20261008_210508_788505_79267ed7.jsonl'
    events=[json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
    s=object.__new__(hs.Scheduler)
    s.templates,s.detector=hs.TemplateStore(.92),None
    observations=[]
    for event in events:
        if event.get('event')=='content_click' and str(event.get('target','')).startswith('arena_casual_battle_'):
            path=Path(event['evidence'])
            with Image.open(path) as frame:
                count=s._arena_token_count(frame)
            observations.append({'target':event['target'],'tokens':count,'evidence':str(path),
                                 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    # Compare same immutable frames with identical template decisions, while
    # counting repeated screenshot requests separately from local matching time.
    with Image.open(ROOT/'fixtures/survival_highest/counters.png') as image:
        frame=image.convert('RGB')
    counters=grade_candidates(frame,s.templates,hs.ASSETS)
    samples={'uncached':[],'cached':[]}
    outputs={key:[] for key in samples}
    for _ in range(15):
        for mode in samples:
            started=time.perf_counter()
            values=[]
            for candidate in counters:
                if mode=='uncached':_counter_glyphs.cache_clear()
                grade=next(row for row in hs.SURVIVAL_GRADES if row[0]==candidate['grade'])
                values.append(read_counter(frame,candidate['x']+grade[2],candidate['y']+grade[3],hs.ASSETS))
            samples[mode].append((time.perf_counter()-started)*1000)
            outputs[mode].append(values)
    report={'scope':'archived screenshots and offline recognition only; no live task completion',
            'input_operations':0,'observed_starts':observations,
            'archived_zero_token_starts':sum(row['tokens']==0 for row in observations),
            'all_archived_token_counts_recognized':all(row['tokens'] is not None for row in observations),
            'counter_glyph_cache':{'uncached_median_ms':statistics.median(samples['uncached']),
                                   'cached_median_ms':statistics.median(samples['cached']),
                                   'same_decisions':outputs['uncached']==outputs['cached']},
            'elapsed_tasks':[row for row in events if row.get('event')=='multi_solo_task_elapsed'],
            'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
                'hyol_scheduler.py','hyol_multi_solo.py','hyol_daily_tasks.py','hyol_performance.py','hyol_survival_counts.py')},
            'live_acceptance':'pending','whole_run_speedup':'not_measured'}
    hs.atomic_json(ROOT/'logs/solo_improvements_evidence_20261009.json',report)
    print(json.dumps({key:report[key] for key in ('all_archived_token_counts_recognized','archived_zero_token_starts','counter_glyph_cache','live_acceptance')},ensure_ascii=False))
    if not report['all_archived_token_counts_recognized'] or not report['counter_glyph_cache']['same_decisions']:
        raise SystemExit(1)

if __name__=='__main__':
    main()
