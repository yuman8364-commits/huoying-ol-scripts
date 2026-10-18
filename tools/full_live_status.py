"""Read-only evidence snapshots for the user-authorized full desktop live test.

Reads progress and JSONL; never constructs a scheduler or touches task ledgers.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hyol_task_progress import ProgressReader, TASK_LABELS, local_day


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def collect(day, identifier):
    reader = ProgressReader(ROOT)
    latest = read(reader.state/'multi_solo_latest.json')
    identifier = identifier or latest['run_id']
    snapshot = reader.load(day, identifier)
    today = {row['account']: row for row in reader.load(day)['rows'] if row['account']}
    start = read(ROOT/'logs/full_live_start_20261013.json')
    events, paths = {}, []
    for folder in reader.log_roots:
        for path in folder.glob('HYOL_Scheduler_'+day.replace('-', '')+'_*.jsonl'):
            included = False
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if str(row.get('ts', '')) < start['at']:
                    continue
                key = (row.get('run_id'), row.get('ts'), row.get('event'),
                       row.get('account'), row.get('target'), row.get('task'))
                events[key] = row
                included = True
            if included:
                paths.append(str(path))
    all_events = sorted(events.values(), key=lambda row: row.get('ts', ''))
    current = [row for row in all_events if row.get('run_id') == identifier]
    run_version = start['version']
    resumed_path = ROOT/'logs/full_live_resume_start_20261013.json'
    if resumed_path.is_file() and current:
        resumed = read(resumed_path)
        if datetime.fromisoformat(current[0]['ts']) >= datetime.fromisoformat(resumed['at']):
            run_version = resumed['version']
    clicks = [row for row in all_events if row.get('event') == 'content_click']
    opening = Counter((row.get('account'), row.get('target')) for row in clicks
                      if re.fullmatch(r'arena_opening_.+_(skill2|auto)', row.get('target', '')))
    setting_clicks = [row for row in clicks if re.search(
        r'(managed_setting|managed_option|player_display|org_wheel|hide_other_players)', row.get('target', ''))]
    duplicates = [dict(account=account, target=target, clicks=count)
                  for (account, target), count in opening.items() if count > 1]
    rows = []
    for row in snapshot['rows']:
        tasks = {}
        for task, cell in row['tasks'].items():
            native = today.get(row['account'], {}).get('tasks', {}).get(task, {})
            completed = [event for event in current if event.get('account') == row['account']
                         and event.get('task') == task and event.get('event') == 'multi_solo_task_complete'
                         and event.get('task_completed') is True and event.get('home_returned') is True
                         and not event.get('followup_check_due')]
            if task == 'managed' and native.get('status') == 'done' and native.get('evidence'):
                completed = [event for event in current if event.get('account') == row['account']
                             and event.get('event') == 'multi_solo_managed_complete']
            tasks[task] = dict(status=cell['status'], completed=native.get('completed', cell.get('completed')),
                               verified_wins=cell.get('verified_wins'),
                               task_completed=cell.get('task_completed'),
                               home_returned=cell.get('home_returned'),
                               phase=native.get('phase'), before=native.get('before'),
                               evidence=native.get('evidence'),
                               current_run_completion=completed[-1] if completed else None,
                               last_error=cell.get('last_error'))
        rows.append(dict(label=row['label'], account=row['account'], health=row['health'], tasks=tasks))
    counts = {task: sum(row['tasks'][task]['current_run_completion'] is not None for row in rows)
              for task in TASK_LABELS}
    today_counts = {task: sum(row['tasks'][task].get('status') == 'done' for row in today.values())
                    for task in TASK_LABELS}
    errors = [row for row in current if row.get('event') in ('multi_solo_run_error', 'stage_pending')
              or row.get('event', '').endswith(('_pending', '_offline', '_stalled'))]
    endings = [row for row in current if row.get('event') == 'multi_solo_run_end']
    started = [row for row in current if row.get('event') in ('multi_solo_task_started', 'multi_solo_stage_started')]
    counters = {}
    for row in current:
        if row.get('event') in ('daily_ranking_battle_complete', 'daily_survival_battle_complete'):
            counters.setdefault(row['account'], {})[row['event']] = row
    return dict(at=datetime.now().astimezone().isoformat(), run_id=identifier,
                version_at_initial_start=start['version'], current_run_version=run_version, source_logs=paths,
                latest_stage=latest.get('stage') if latest.get('run_id') == identifier else None,
                executing=started[-1] if started and not endings else None, actual_battle_counters=counters,
                startup_returned=latest.get('startup_returned') if latest.get('run_id') == identifier else None,
                run_end=endings[-1] if endings else None, last_event=current[-1] if current else None,
                current_run_verified_completions=counts, today_completions=today_counts,
                rows=rows, pending_events=errors[-20:],
                audit=dict(content_clicks=len(clicks), opening_inputs=[dict(account=account, target=target, clicks=n)
                    for (account,target), n in sorted(opening.items())],
                    duplicate_opening_inputs=duplicates, managed_setting_clicks=setting_clicks),
                observer_game_inputs=0, observer_state_writes=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', default=local_day())
    parser.add_argument('--run-id')
    args = parser.parse_args()
    result = collect(args.day, args.run_id)
    path = ROOT/'logs'/('full_live_snapshot_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
    with path.open('x', encoding='utf-8') as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write('\n')
    print(json.dumps(dict(report=str(path), run_id=result['run_id'], version=result['current_run_version'], stage=result['latest_stage'],
        executing=result['executing'], actual_battle_counters=result['actual_battle_counters'],
        completions=result['current_run_verified_completions'], today=result['today_completions'], run_end=result['run_end'],
        last_event=result['last_event'], recent_pending=result['pending_events'][-3:],
        duplicate_opening_inputs=result['audit']['duplicate_opening_inputs'],
        managed_setting_clicks=len(result['audit']['managed_setting_clicks']), observer_game_inputs=0), ensure_ascii=False))


if __name__ == '__main__':
    main()
