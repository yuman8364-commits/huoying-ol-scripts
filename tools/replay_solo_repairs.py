"""Replay saved daily runs without opening a window or writing task checkpoints."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
import hyol_scheduler as hs
from hyol_combat_watchdog import BattleWatchdog
from hyol_task_progress import ProgressReader
from hyol_survival_counts import grade_candidates, choose_grade


class MemoryLedger:
    def __init__(self, day, account, original):
        self.day, self.accounts = day, {account: copy.deepcopy(original)}
    def action_record(self, name, key):
        return self.accounts[name].get('actions', {}).get(key, {})
    def action_status(self, name, key):
        return self.action_record(name, key).get('status', 'new')
    def set_action(self, name, key, status, detail=''):
        row = self.accounts[name].setdefault('actions', {}).setdefault(key, {})
        row.update(status=status, detail=detail)
    def save_account(self, _name):
        pass


def run(root=ROOT, day='2026-10-12'):
    reader=ProgressReader(root)
    snapshot=reader.load(day)
    runs=[]
    for identifier, stamp in snapshot['runs']:
        history=reader.load(day, identifier)
        runs.append(dict(run_id=identifier, started_at=stamp,
                         labels={row['label']:row['account'] for row in history['rows']}))
    ledger_path=reader.state/day/'account_c9ba2069ff.json'
    original=json.loads(ledger_path.read_text(encoding='utf-8-sig'))
    account=hs.Account(3,original['account'],450)
    before_actions=copy.deepcopy(original.get('actions',{}))
    folder=next(logs/'scheduler_evidence'/day/'account_c9ba2069ff'
                for logs in reader.log_roots if (logs/'scheduler_evidence'/day/'account_c9ba2069ff'/
                    '170146_437605_pending_survival_followup_score_unknown.png').is_file())
    after_path=folder/'170146_437605_pending_survival_followup_score_unknown.png'
    s=object.__new__(hs.Scheduler);s.templates=hs.TemplateStore(.92);s.detector=None
    s.state=MemoryLedger(day,account.name,original);s.event=Mock()
    s.save_raw_evidence=Mock(return_value=after_path)
    frame=Image.open(after_path).convert('RGB')
    reset_verified=s.reconcile_survival_partial_reset(account,frame)
    battles_unchanged=all(s.state.action_record(account.name,key)==value for key,value in before_actions.items()
                          if key.startswith('survival_battle_'))
    watchdog=BattleWatchdog(mode='survival');watchdog.observe(frame,0)
    at29=watchdog.observe(frame,29);at30=watchdog.observe(frame,30)
    records={row['account']:row['tasks'] for row in snapshot['rows']}
    checks=dict(four_actual_runs=len(runs)==4,
                old_run_uses_old_labels=any(row['labels'].get('标签6')=='1区小黑' for row in runs),
                latest_run_uses_new_labels=runs[0]['labels'].get('标签3')=='1区小黑',
                reset_reconciled_in_memory=reset_verified,
                no_battle_synthesized=battles_unchanged,
                original_survival_still_pending=records['1区小黑']['survival']['status']=='pending',
                original_rank_still_pending=records['1区女室友']['ranking']['status']=='pending',
                interrupted_managed_not_complete=records['51区鸣路']['managed']['status']=='pending',
                completed_arena_kept=records['51区鸣路']['arena']['status']=='done',
                frozen_snapshot_29s_ok=at29 is None,
                frozen_snapshot_30s_offline=bool(at30 and at30['reason']=='unchanged_frame'))
    evidence=[after_path,folder/'135341_940873_before_survival_reset.jpg']
    return dict(status='offline_pass' if all(checks.values()) else 'failed', checks=checks,
                runs=runs, today=snapshot['rows'], game_inputs=0, checkpoint_writes=0,
                evidence=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in evidence],
                live_pass=[],pending=['自然运行中30秒掉线与切号','第二技能/自动实机各一次',
                                     '保留原托管设置并确认开始','原排位与生存未知结果'],
                note='静态截图和虚拟时钟回放，不作为连续实机画面或实际任务完成证明')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,
        default=ROOT/'logs/solo_repair_replay_20261012.json');args=parser.parse_args()
    result=run();args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(status=result['status'],checks=result['checks'],output=str(args.output)),ensure_ascii=False))
    raise SystemExit(0 if result['status']=='offline_pass' else 1)
