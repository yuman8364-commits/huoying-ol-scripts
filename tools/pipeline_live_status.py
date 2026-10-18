"""Read the current acceptance ledger and recent desktop events without input."""
from pathlib import Path
import json
import os
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_checkpoints import read_checkpoint


def main():
    record=read_checkpoint(hs.STATE_DIR/'team_pipeline_latest.json')
    teams={i:dict(owner=t.get('owner'),status=t.get('status'),members=t.get('members'),
                  error=t.get('last_error'),tasks={key:{field:row.get(field) for field in (
                      'status','phase','completed','daily_remaining','friendship_remaining')}
                      for key,row in t.get('tasks',{}).items()}) for i,t in record.get('teams',{}).items()}
    folder=Path(os.environ['USERPROFILE'])/'Desktop/可用脚本/火影OL完整客户端/logs'
    files=sorted(folder.glob('HYOL_Scheduler_*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)
    events=[]
    if files:
        for line in files[0].read_text(encoding='utf-8').splitlines()[-50:]:
            row=json.loads(line)
            if row.get('event') in ('template_match','chrome_click','window_bound','content_click'):continue
            events.append({key:row.get(key) for key in ('ts','event','account','task','detail','evidence') if row.get(key) is not None})
    print(json.dumps(dict(at=hs.now_iso(),id=record.get('id'),updated=record.get('updated_at'),
                          phase=record.get('phase'),teams=teams,eligible=record.get('solo_eligible'),
                          solo_result=record.get('solo_result'),events=events[-7:]),ensure_ascii=False))


if __name__=='__main__':main()
