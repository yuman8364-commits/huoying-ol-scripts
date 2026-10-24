"""Check the accepted JavaScript controls against RunRequest without game access."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hyol_desktop_bridge import parse_request
from hyol_runtime import MODES


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    script=r'''
const fs=require('fs'),vm=require('vm');
const box={window:{}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),box);
const D=box.window.HYOL_DEMO;
const cases=[];
for(const mode of ['solo','multi','team','team_then_multi','arena','bond','weekend','treasure']) {
  const c=D.freshConfig();
  if(['solo','multi'].includes(mode)) { c.section='daily';c.scope=mode==='multi'?'multi':'current'; }
  else if(['team','team_then_multi'].includes(mode)) { c.section='team';c.teamMode=mode==='team'?'auto':'pipeline'; }
  else { c.section='independent';c.independent=mode; }
  cases.push({name:mode,error:D.validate(c),request:D.requestFor(c)});
}
const friend=D.freshConfig();
Object.assign(friend,{section:'team',friendShortcut:true,leader:6,members:[1,2]});
cases.push({name:'friend-invite',error:D.validate(friend),request:D.requestFor(friend)});
const manual=D.freshConfig();
Object.assign(manual,{section:'team',teamMode:'pipeline',pipelineAuto:false,pipelineLeaders:[2,5],
  tasks:['quiz','mail','arena'],pipelineTasks:['strong_approach','wanted'],finalManaged:false,
  arenaTargetMode:'wins',arenaWins:8,formation:'fast'});
cases.push({name:'manual-pipeline',error:D.validate(manual),request:D.requestFor(manual)});
console.log(JSON.stringify(cases));
'''
    process=subprocess.run(['node','-e',script,str(ROOT/'web/demo-data.js')],
                           check=True,capture_output=True,encoding='utf-8')
    cases=json.loads(process.stdout)
    for case in cases:
        if case['error']:
            raise AssertionError(case)
        request=parse_request(case['request'])
        if json.loads(json.dumps(asdict(request)))!=case['request']:
            raise AssertionError('Field mapping changed: '+case['name'])
    if set(MODES)!={case['request']['mode'] for case in cases}:
        raise AssertionError('Missing request mode')
    manual=cases[-1]['request']
    if manual['tasks']!=['mail','quiz','arena'] or manual['team_tasks']!=['wanted','strong_approach']:
        raise AssertionError('Existing task order changed')
    report=dict(success=True,cases=cases,modes=list(MODES),game_task_started=False,
                scope='JavaScript request generation to existing Python RunRequest; no controller started')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(dict(success=True,cases=len(cases),modes=list(MODES)),ensure_ascii=False))


if __name__=='__main__':
    main()
