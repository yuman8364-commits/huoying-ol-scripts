"""Read-only receipts for a resumed solo batch; offline checks never credit live tasks."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import hyol_scheduler as hs
from hyol_checkpoints import read_checkpoint
from hyol_runtime import VERSION


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--journals',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Preserve previous acceptance receipts')
    baseline=read_checkpoint(args.baseline)
    original=next(row for row in baseline['state'] if Path(row['path']).name=='multi_solo_latest.json')
    prior=read_checkpoint(original['backup'])
    day=prior['updated_at'][:10]
    rows=[]
    journals=[]
    for path in args.journals:
        events=[json.loads(line) for line in path.read_text('utf-8').splitlines() if line.strip()]
        rows.extend(dict(event,source_journal=str(path)) for event in events)
        journals.append(dict(path=str(path),sha256=digest(path),events=len(events)))
    started=read_checkpoint(ROOT/'logs/managed_resume_desktop_started_20261009.json')['started_at']
    current_events=[row for row in rows if row.get('ts','')>=started]
    accounts=[]
    for account in sorted(prior['accounts']):
        path=ROOT/'state'/day/(hs.slug(account)+'_tasks.json')
        records=read_checkpoint(path)
        actions=read_checkpoint(ROOT/'state'/day/(hs.slug(account)+'.json')).get('actions',{})
        tasks={task:deepcopy(records.get(task,{})) for task in ('ranking','survival','quiz','managed')}
        task_evidence={task:[] for task in tasks}
        for row in rows:
            if row.get('account')!=account or not row.get('evidence'):continue
            target=str(row.get('target',''))
            for task in tasks:
                if (row.get('task')==task or row.get('mode')==task or target.startswith((task+'_','daily_'+task+'_'))
                        or task=='managed' and row.get('event')=='managed_spending_disabled'):
                    evidence=Path(row['evidence'])
                    task_evidence[task].append(dict(at=row.get('ts'),event=row.get('event'),target=target,
                        path=str(evidence),exists=evidence.is_file(),
                        sha256=digest(evidence) if evidence.is_file() else None,
                        journal=row['source_journal']))
        complete=all(row.get('status')=='done' and row.get('task_completed')
            and (task=='managed' and bool(row.get('evidence')) or task!='managed' and row.get('home_returned'))
            and not row.get('followup_check_due') for task,row in tasks.items())
        accounts.append(dict(account=account,selected_tasks_complete=complete,tasks=tasks,
            evidence=task_evidence,pending_actions={key:value for key,value in actions.items()
                if isinstance(value,dict) and value.get('status')=='pending'}))
    bad_backups=[row['backup'] for row in baseline['state']
        if not Path(row['backup']).is_file() or digest(row['backup'])!=row['sha256']]
    historical=[row for row in baseline['state'] if Path(row['path']).parent.name[:4].isdigit()
        and Path(row['path']).parent.name!=day]
    changed_historical=[row['path'] for row in historical
        if not Path(row['path']).is_file() or digest(row['path'])!=row['sha256']]
    active_unknown=[]
    for row in baseline['state']:
        path=Path(row['path'])
        if path.parent.name!=day or path.name.endswith('_tasks.json'):continue
        original=read_checkpoint(row['backup']).get('actions',{})
        for key,action in original.items():
            if (isinstance(action,dict) and action.get('status')=='pending'
                    and key.startswith('survival_battle_step_') and action.get('operation_id','').startswith(day)):
                current=read_checkpoint(path).get('actions',{}).get(key)
                active_unknown.append(dict(path=str(path),action=key,unchanged=current==action,
                    original=deepcopy(action),current=deepcopy(current)))
    inputs=[row for row in current_events if row.get('event')=='content_click']
    ninja_targets=('daily_open_ninja','daily_close_ninja','daily_select_main_formation','daily_select_fast_formation')
    ninja=[row for row in inputs if row.get('target') in ninja_targets
        or str(row.get('target','')).startswith(('daily_formation_','formation_','daily_ninja_','ninja_open'))]
    result=dict(at=hs.now_iso(),version=VERSION,result='PASS' if all(row['selected_tasks_complete'] for row in accounts) else 'pending',
        all_six_accepted=all(row['selected_tasks_complete'] for row in accounts) and len(accounts)==6,
        selected_accounts_completed=sum(row['selected_tasks_complete'] for row in accounts),
        accounts=accounts,journals=journals,formation='keep',final_managed=True,
        current_ninja_inputs=ninja,current_survival_start_inputs=[row for row in inputs
            if str(row.get('target','')).startswith('survival_battle_step_') and str(row['target']).endswith('_start')],
        state_preservation=dict(backed_up=len(baseline['state']),invalid_backups=bad_backups,
            historical_checked=len(historical),changed_historical=changed_historical,
            original_current_survival_unknown=active_unknown),content_clicks_by_this_tool=0)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('result','all_six_accepted','selected_accounts_completed')},ensure_ascii=False))
    if bad_backups or changed_historical or ninja:raise SystemExit('Evidence guard failed; inspect receipt')


if __name__=='__main__':main()
