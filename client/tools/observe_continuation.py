"""Snapshot a started desktop continuation without input or checkpoint writes."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hyol_task_progress import ProgressReader


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def collect(start_path):
    start = read(start_path)
    day = start['at'][:10]
    reader = ProgressReader(ROOT)
    folders = [Path(start['entry']).parent/'logs'] if start.get('entry') else reader.log_roots
    events = {}
    paths = []
    for folder in folders:
        for path in folder.glob('HYOL_Scheduler_'+day.replace('-', '')+'_*.jsonl'):
            included = False
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('ts', '') < start['at']:
                    continue
                if row.get('hwnd') == 0:
                    continue
                key = tuple(str(row.get(k)) for k in ('run_id','ts','event','account','target','task','index'))
                events[key] = row
                included = True
            if included:
                paths.append(str(path))
    rows = sorted(events.values(), key=lambda row: row.get('ts', ''))
    latest = read(ROOT/'state/multi_solo_latest.json')
    receipt = ROOT/'state/team_pipeline_latest.json'
    pipeline = read(receipt) if receipt.is_file() else {}
    progress = reader.load(day)
    by_task = Counter()
    completed = []
    battles = {}
    for row in rows:
        if row.get('event') == 'combat_battle_finished':
            key = (row.get('tab_index'),row.get('mode'))
            cell = battles.setdefault(key,dict(tab=row.get('tab_index'),mode=row.get('mode'),
                                              finished=0,wins=0,defeats=0))
            cell['finished'] += 1
            if row.get('mode') == 'arena':
                cell['wins'] += row.get('victory') is True
                cell['defeats'] += row.get('victory') is False
        if ((row.get('event') == 'multi_solo_task_complete' and row.get('task_completed') is True
             and row.get('home_returned') is True and not row.get('followup_check_due'))
                or row.get('event') == 'multi_solo_managed_complete'):
            by_task[row.get('task', 'managed')] += 1
            completed.append(row)
    clicks = [row for row in rows if row.get('event') == 'content_click']
    irreversible = Counter((row.get('account'),row.get('target')) for row in clicks if row.get('irreversible'))
    duplicate = [dict(account=key[0],target=key[1],count=count)
                 for key,count in irreversible.items() if count > 1]
    ignored = {'template_match','chrome_click','combat_account_bound','combat_account_bound_by_page',
               'capture_focus_reacquire','window_bound','multi_solo_performance','page_anchor_retry',
               'combat_transition_wait','lease_release_wait'}
    important = [row for row in rows if row.get('event') not in ignored]
    pending = [row for row in important if (row.get('event','').endswith(('_pending','_error','_stalled','_skipped')))]
    endings = [row for row in rows if row.get('event') in ('multi_solo_run_end','scheduler_stopped')]
    started = [row for row in rows if row.get('event') == 'multi_solo_stage_started']
    terminal = []
    for folder in folders:
        receipt_path = folder/'unified_latest.json'
        if receipt_path.is_file():
            receipt = read(receipt_path)
            if receipt.get('at','') >= start['at']:
                terminal.append(receipt)
    return dict(at=datetime.now().astimezone().isoformat(), version=start['version'], start=str(start_path),
                source_logs=paths, original_batch=start.get('original_batch'),
                pipeline_phase=pipeline.get('phase'), solo_result=pipeline.get('solo_result'),
                eligible=len(pipeline.get('solo_eligible',[])), exclusions=pipeline.get('solo_exclusions',{}),
                latest_stage=started[-1].get('task') if started and not endings else
                    latest.get('stage') if latest.get('updated_at','') >= start['at'] else None,
                completions=dict(by_task), verified_completions=completed,battles=list(battles.values()),
                today=[dict(label=row['label'],account=row['account'],
                            tasks={task:cell.get('status') for task,cell in row['tasks'].items()})
                       for row in progress['rows']],
                latest_events=important[-8:], pending=pending[-12:], run_end=endings[-1] if endings else None,
                terminal_receipt=max(terminal,key=lambda row:row['at']) if terminal else None,
                audit=dict(content_clicks=len(clicks),duplicate_irreversible_inputs=duplicate,
                           group_starts=[row for row in clicks if row.get('target','').startswith('pipeline_group_')
                                         and row.get('irreversible')],
                           managed_setting_inputs=[row for row in clicks if row.get('target','').startswith('managed_setting')]),
                observer_game_inputs=0,observer_task_state_writes=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.start)
    path = ROOT/'logs'/('continuation_snapshot_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
    with path.open('x',encoding='utf-8') as target:
        json.dump(result,target,ensure_ascii=False,indent=2)
        target.write('\n')
    print(json.dumps(dict(report=str(path),stage=result['latest_stage'],eligible=result['eligible'],
        completions=result['completions'],battles=result['battles'],last=result['latest_events'][-3:],
        pending=result['pending'][-2:],run_end=result['run_end'],terminal=result['terminal_receipt'],audit=result['audit']),ensure_ascii=False))
