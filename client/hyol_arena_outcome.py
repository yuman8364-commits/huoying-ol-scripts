"""An exhausted, settled arena request is distinct from an unknown battle."""
import re


def exhausted_record(scheduler, account):
    state=scheduler.state
    request=state.global_state.get('arena_requests',{}).get(account.name,{})
    data=getattr(state,'accounts',{}).get(account.name,{})
    return exhausted_evidence(getattr(state,'day',''),request,data)


def exhausted_evidence(day, request, data):
    """Read old or current receipts without changing their original evidence."""
    if not day:return None
    actions=data.get('actions',{})
    proof=data.get('arena_quota_pending_rewards',{})
    if (request.get('status')!='quota_exhausted' or request.get('tickets_exhausted') is not True
            or request.get('ticket_count_at_completion')!=0 or request.get('home_returned') is not True
            or proof.get('unfinished_tasks_observed') is not True or not proof.get('evidence')
            or proof.get('day')!=day
            or any(str(k).startswith(('arena_','daily_arena_')) and isinstance(v,dict) and v.get('status')=='pending'
                   for k,v in actions.items())):
        return None
    settled={int(m[1]) for k,v in actions.items() if (m:=re.fullmatch(r'arena_casual_battle_(\d+)',k)) and v.get('status')=='done'}
    wins=sum(actions.get('arena_attempt_victory_'+str(i),{}).get('status')=='done' for i in settled)
    losses=sum(actions.get('arena_attempt_defeat_'+str(i),{}).get('status')=='done' for i in settled)
    return dict(status='pending',task_completed=False,home_returned=True,completed=len(settled),
                verified_battles=len(settled),verified_wins=wins,verified_defeats=losses,
                tickets_remaining=0,rewards_pending=True,followup_check_due=False,
                outcome='quota_exhausted',outcome_label='战令已用尽',
                last_error='战令已用尽并回村；每日奖励条件未达成，继续后续任务',
                quota_evidence=proof['evidence'],home_evidence=request.get('home_evidence',''))
