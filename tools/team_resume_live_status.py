"""Read-only evidence snapshots for the currently authorized team continuation."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hyol_task_progress import ProgressReader, local_day


def collect():
    start = json.loads((ROOT/'logs/team_resume_live_start_20261014.json').read_text(encoding='utf-8-sig'))
    reader = ProgressReader(ROOT)
    pipeline = json.loads((reader.state/'team_pipeline_latest.json').read_text(encoding='utf-8-sig'))
    events = {}
    paths = []
    for folder in reader.log_roots:
        for path in folder.glob('HYOL_Scheduler_'+local_day().replace('-', '')+'_*.jsonl'):
            included = False
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('ts', '') < start['at']:
                    continue
                key = (row.get('run_id'), row.get('ts'), row.get('event'), row.get('account'), row.get('target'))
                events[key] = row
                included = True
            if included:
                paths.append(str(path))
    ordered = sorted(events.values(), key=lambda row: row['ts'])
    clicks = [r for r in ordered if r.get('event') == 'content_click']
    counts = Counter((r.get('account'), r.get('target')) for r in clicks)
    original_group_starts = [r for r in clicks if r.get('target', '').startswith('pipeline_'+pipeline['id']+'_group_')
                             and '_select' in r['target']]
    completions = [r for r in ordered if r.get('event') in ('pipeline_team_dissolved', 'pipeline_solo_eligible',
        'multi_solo_task_complete', 'multi_solo_managed_complete', 'multi_solo_survival_second_run_complete')]
    pending = [r for r in ordered if r.get('event', '').endswith(('_pending', '_stalled', '_error', '_offline'))]
    last = [r for r in ordered if r.get('event') not in ('template_match', 'content_click', 'chrome_click', 'window_bound')]
    progress = reader.load(local_day())
    return dict(at=datetime.now().astimezone().isoformat(), version=start['version'], original_batch=pipeline['id'],
        pipeline=pipeline, progress=progress, source_logs=paths, completions=completions,
        latest_events=last[-8:], pending=pending[-12:],
        audit=dict(game_content_clicks=len(clicks), original_group_starts=original_group_starts,
            repeated_irreversible_targets=[dict(account=a, target=t, clicks=n) for (a,t),n in counts.items()
                if n>1 and any(r.get('irreversible') and r.get('account')==a and r.get('target')==t for r in clicks)],
            managed_setting_clicks=[r for r in clicks if any(x in r.get('target', '') for x in
                ('managed_option', 'managed_setting', 'player_display', 'org_wheel', 'hide_other_players'))]),
        observer_game_inputs=0, observer_task_state_writes=0)


if __name__ == '__main__':
    result = collect()
    path = ROOT/'logs'/('team_resume_snapshot_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    p = result['pipeline']
    print(json.dumps(dict(report=str(path), phase=p.get('phase'),
        teams={k: dict(status=v.get('status'), error=v.get('last_error'),
            group_completed=v.get('tasks', {}).get('group', {}).get('completed')) for k,v in p['teams'].items()},
        eligible=p.get('solo_eligible'), completions=result['completions'][-6:],
        latest=result['latest_events'][-3:], pending=result['pending'][-3:], audit=result['audit']), ensure_ascii=False))
