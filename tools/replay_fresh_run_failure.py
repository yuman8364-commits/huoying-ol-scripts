"""Replay the October 16 repeated-discovery failure using saved frames only."""
from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from PIL import Image
import hyol_scheduler as hs
import hyol_accounts as accounts
from hyol_checkpoints import current_identity_mask
from hyol_multi_solo import active_tab_centers, tab_close_positions
from hyol_run_context import RunContext
from hyol_daily_tasks import DailyScheduler
from hyol_team_pipeline import TeamPipelineRunner
from tools.release_runtime_snapshot import default_desktop


def main():
    logs=default_desktop()/'可用脚本/火影OL完整客户端/logs'
    sources=[logs/('HYOL_Scheduler_'+identifier+'.jsonl') for identifier in (
        '20261016_203407_479898_e59e9a22','20261016_203616_644626_03dee6a4')]
    rows=[[json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines()] for path in sources]
    captured=next(row for row in rows[0] if row.get('event')=='pipeline_captains_reassociated')
    refs=json.loads((ROOT/'state/account_identities.json').read_text(encoding='utf-8-sig'))
    frames={int(slot):Image.open(path).convert('RGB') for slot,path in captured['evidence'].items()}
    templates=hs.TemplateStore(.98)
    masks={slot:current_identity_mask(frame,templates) for slot,frame in frames.items()}
    observations={slot:[mask]*3 for slot,mask in masks.items()}
    mapping,refs=accounts.resolve_observations(observations,refs)
    expected=next(row['mapping'] for row in rows[1] if row.get('event')=='dynamic_accounts_bound')
    assert {str(k):v for k,v in mapping.items()}==expected,(mapping,expected)
    centers=active_tab_centers(tab_close_positions(frames[1]))
    assert all(active_tab_centers(tab_close_positions(frame))==centers for frame in frames.values())
    with tempfile.TemporaryDirectory() as folder:
        ctx=RunContext(Path(folder),Path(folder)/'state')
        ctx.identity_directory.mkdir()
        (ctx.identity_directory/'account_identities.json').write_text(json.dumps(refs),encoding='utf-8')
        (ctx.identity_directory/'team_pipeline_latest.json').write_text('{broken old task',encoding='utf-8')
        ctx.monitor_started=True
        events=[];handoff_scan_counts=[]
        config=dict(AccountDiscovery='automatic',SharedStateDirectory=str(ctx.identity_directory))
        def recorded_observation(scheduler):
            scheduler._observed_captain_tabs={centers[i-1]:accounts.is_captain(scheduler,frame)
                                               for i,frame in frames.items()}
            return centers,observations,frames,{}
        with patch.object(hs,'read_config',return_value=config),patch.object(hs,'LOGS',Path(folder)/'logs'):
            owner=TeamPipelineRunner(root=Path(folder),run_context=ctx,event_sink=events.append)
            owner.scheduler.bind_window=Mock()
            owner.prepare_teams=Mock();owner.run_teams=Mock()
            def handoff_without_game_tasks(solo):
                handoff_scan_counts.append(observe.call_count)
                assert {str(a.index):a.name for a in solo.scheduler.accounts}==expected
                solo.scheduler.accounts=[]
            owner.handoff=handoff_without_game_tasks
            with patch.object(accounts,'observe_accounts',side_effect=recorded_observation) as observe, \
                 patch.object(accounts,'game_session_token',return_value='recorded-frame-session'), \
                 patch.object(hs,'ForegroundRuntimeLease'),patch.object(DailyScheduler,'bind_window'), \
                 patch('hyol_diagnostics.clear_manual_pause'), \
                 patch.object(hs.Scheduler,'chrome_click',side_effect=AssertionError('live input')), \
                 patch.object(hs.Scheduler,'click_point',side_effect=AssertionError('live input')):
                outcome=owner.run()
            assert outcome==2 and observe.call_count==1 and handoff_scan_counts==[1]
            assert any(e.get('event')=='dynamic_accounts_bound' and e.get('reused') for e in events)
            assert not any(e.get('event')=='multi_solo_stage_started' for e in events)
            scan_count=observe.call_count
            additional_scans=scan_count-handoff_scan_counts[0]
    boundary=rows[0].index(captured)
    original_scans=[sum(r.get('event')=='chrome_click' for r in rows[0][:boundary]),
                    sum(str(r.get('target','')).startswith('observe_account:') for r in rows[0][boundary:]),
                    sum(str(r.get('target','')).startswith('observe_account:') for r in rows[1])]
    report=dict(result='PASS',sources=list(map(str,sources)),real_frame_identity_mapping=mapping,
        original_discovery_visits=original_scans,original_full_scans=3,
        new_start_full_scans=scan_count,additional_handoff_full_scans=additional_scans,
        current_captain_slots=list(owner.leaders),recorded_frame_backend=True,empty_handoff_result=outcome,
        original_pending=[r['detail'] for r in rows[0] if r.get('event')=='pipeline_team_pending'],
        old_task_state_loaded=False,game_input_operations=0,real_state_writes=0,
        temporal_validation='One saved frame per tab; repeated input checks matching only. Live three-frame stability remains separate.')
    path=ROOT/'logs/fresh_run_failure_replay_20261017.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
