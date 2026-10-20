"""Read-only snapshot of the authorized multiplayer expansion test."""
import json
from pathlib import Path
from collections import Counter
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
def read(p):
    try:return json.loads(p.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:return {}
start=read(ROOT/'logs/multiplayer_expansion_start_20261018.json')
folders=[p for p in (ROOT/'state/runs').iterdir() if p.is_dir() and p.name>=start['minimum_run_id']]
if not folders:
    print(json.dumps({'status':'waiting_for_run'},ensure_ascii=False));raise SystemExit()
r=max(folders,key=lambda p:p.name)
pipeline=read(r/'team_pipeline_latest.json')
solo=read(r/'multi_solo_latest.json')
events=[]
p=ROOT/'logs'/('HYOL_Scheduler_'+r.name+'.jsonl')
if p.exists():
    for line in p.read_text(encoding='utf-8-sig').splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
routine={'template_match','content_click','chrome_click','window_bound','pipeline_team_tick','template_frame_cache_hit','account_bound'}
important=[e for e in events if e.get('event') not in routine]
clicks=[e for e in events if e.get('event')=='content_click']
counts=Counter((e.get('account'),e.get('target')) for e in clicks if e.get('irreversible'))
teams={k:{'owner':v.get('owner'),'members':v.get('members'),'status':v.get('status'),'error':v.get('last_error'),
              'tasks':{t:{f:rec.get(f) for f in ('status','phase','completed','daily_remaining','friendship_remaining','captain_remaining','captain_observations','member_remaining','stop_reason','reward_exhausted') if f in rec} for t,rec in v.get('tasks',{}).items()}}
       for k,v in pipeline.get('teams',{}).items()}
accounts={p.name:{t:{f:rec.get(f) for f in ('status','completed','remaining','zero_verified','home_returned','last_error') if f in rec} for t,rec in read(p).items() if isinstance(rec,dict)} for p in r.glob('????-??-??/*_tasks.json')}
terminal=read(ROOT/'logs'/('unified_'+r.name+'.json'))
if not terminal:
    q=read(ROOT/'logs/unified_latest.json')
    if q.get('run_id')==r.name:terminal=q
result=dict(at=datetime.now().astimezone().isoformat(),run_id=r.name,phase=pipeline.get('phase'),teams=teams,
            eligible=pipeline.get('solo_eligible'),exclusions=pipeline.get('solo_exclusions'),solo_stage=solo.get('stage'),
            accounts=accounts,terminal={k:terminal.get(k) for k in ('status','result','detail','error_type') if k in terminal},
            last_events=important[-5:],last_log=events[-1] if events else None,
            strong_attempts=[{k:e.get(k) for k in ('ts','event','account','team','attempt','remaining','target','evidence') if k in e}
                for e in events if (e.get('activity')=='strong_approach' and e.get('event') in (
                    'team_activity_attempt_completed','team_reward_counter_verified','team_reward_counter_pending')
                    or e.get('event')=='irreversible_committed' and '_strong_approach_attempt_' in e.get('target',''))],
            audit=dict(full_identity_scans=sum(e.get('event')=='account_scan_started' for e in events),
                content_clicks=len(clicks),repeated_irreversible=[{'account':a,'target':t,'count':n} for (a,t),n in counts.items() if n>1],
                managed_setting_clicks=[e for e in clicks if any(x in e.get('target','') for x in ('managed_option','managed_setting','player_display','org_wheel','hide_other_players'))]))
report=ROOT/'logs'/('multiplayer_expansion_snapshot_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')
report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
