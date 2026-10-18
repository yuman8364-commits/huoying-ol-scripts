"""Observe the explicitly started current run without game input."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.release_runtime_snapshot import default_desktop


def read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {}


def collect():
    start = read(ROOT/'logs/fresh_run_live_start_20261017.json')
    if not start:
        raise ValueError('No authorized current-run start receipt')
    runs = ROOT/'state/runs'
    candidates = [p for p in runs.iterdir() if p.is_dir() and p.name >= start['minimum_run_id']] if runs.is_dir() else []
    if not candidates:
        return dict(status='waiting_for_run', minimum_run_id=start['minimum_run_id'], version=start['version'])
    directory = max(candidates, key=lambda p: p.name)
    run_id = directory.name
    pipeline = read(directory/'team_pipeline_latest.json')
    solo = read(directory/'multi_solo_latest.json')
    logs = [ROOT/'logs', default_desktop()/'可用脚本/火影OL组队任务/logs',
            default_desktop()/'可用脚本/火影OL完整客户端/logs']
    events, paths, terminal = [], [], {}
    for folder in logs:
        path = folder/f'HYOL_Scheduler_{run_id}.jsonl'
        if path.exists():
            paths.append(str(path))
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass  # The worker may still be writing the final line.
        receipt = read(folder/f'unified_{run_id}.json') or read(folder/'unified_latest.json')
        if receipt.get('run_id') == run_id:
            terminal = receipt
    events.sort(key=lambda row: row.get('ts', ''))
    clicks = [row for row in events if row.get('event') == 'content_click']
    irreversible = Counter((r.get('account'), r.get('target')) for r in clicks if r.get('irreversible'))
    routine = {'template_match', 'content_click', 'chrome_click', 'window_bound', 'pipeline_team_tick',
               'template_frame_cache_hit'}
    useful = [row for row in events if row.get('event') not in routine]
    outcome = [row for row in useful if any(word in row.get('event', '') for word in
        ('complete', 'pending', 'dissolved', 'eligible', 'stalled', 'error', 'offline'))]
    accounts = {p.relative_to(directory).as_posix():read(p) for p in directory.glob('????-??-??/*_tasks.json')}
    result = dict(at=datetime.now().astimezone().isoformat(), run_id=run_id,
        directory=str(directory), terminal=terminal, pipeline=pipeline, solo=solo,
        account_tasks=accounts, sources=paths, latest_events=useful[-6:], outcomes=outcome[-12:],
        audit=dict(content_clicks=len(clicks),
            full_identity_scans=sum(r.get('event') == 'account_scan_started' for r in events),
            startup_tab_clicks=sum(r.get('event') == 'chrome_click' and
                str(r.get('target','')).startswith('observe_account:') for r in events),
            reused_solo_maps=sum(r.get('event') == 'dynamic_accounts_bound' and
                r.get('reused') is True for r in events),
            repeated_irreversible_targets=[dict(account=a, target=t, count=n)
                for (a, t), n in irreversible.items() if n > 1],
            managed_setting_clicks=[r for r in clicks if any(x in r.get('target', '')
                for x in ('managed_option', 'managed_setting', 'player_display', 'org_wheel', 'hide_other_players'))]),
        observer_game_inputs=0, observer_task_state_writes=0)
    path = ROOT/'logs'/('fresh_run_live_snapshot_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    teams = {k:dict(owner=v.get('owner'), status=v.get('status'), error=v.get('last_error'),
        tasks={task:{field:row.get(field) for field in ('phase','status','completed','daily_remaining',
               'friendship_remaining','reward_exhausted')} for task,row in v.get('tasks', {}).items()})
        for k,v in pipeline.get('teams', {}).items()}
    return dict(report=str(path),run_id=run_id,phase=pipeline.get('phase'),teams=teams,
        eligible=pipeline.get('solo_eligible'),exclusions=pipeline.get('solo_exclusions'),
        solo_stage=solo.get('stage'),terminal={k:terminal.get(k) for k in
            ('status','result','detail','version','state_directory','error_type') if k in terminal},latest_events=useful[-4:],
        outcomes=outcome[-5:],audit=result['audit'])


if __name__ == '__main__':
    print(json.dumps(collect(), ensure_ascii=False))
