"""Run native-window UI checks against an injected controller; no game access."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image
import webview
from hyol_desktop import asset_server
from hyol_desktop_bridge import DesktopBridge,empty_record,task_codes


class FixtureController:
    def __init__(self,sink,root):
        self.sink,self.root=sink,Path(root)
        self.identifier=''
        self.busy=False
        self.runner=None
        self.request=None
        self.calls=[]
    def emit(self,event,**fields):
        self.sink(dict(event=event,run_id=self.identifier,**fields))
    def start(self,request):
        if self.busy:
            return False
        self.identifier='fixture-'+uuid.uuid4().hex
        self.request=request
        self.calls.append(request)
        self.busy=True
        threading.Thread(target=lambda:(time.sleep(.05),self.emit('unified_status',detail='离线验收替身运行中')),daemon=True).start()
        return True
    def stop(self):
        if not self.busy:
            return False
        self.emit('unified_stop_requested',detail='替身收到停止')
        def finish():
            time.sleep(.15)
            self.emit('unified_finished',status='stopped',detail='替身已停止')
            self.emit('unified_idle')
            self.busy=False
        threading.Thread(target=finish,daemon=True).start()
        return True
    def check_window(self):
        if self.busy:
            return False
        self.identifier='check-'+uuid.uuid4().hex
        self.busy=True
        def finish():
            time.sleep(.1)
            self.emit('unified_window_check',check={'status':'ready','healthy':True,'main_ready':True,'content_clicks':0})
            self.emit('unified_idle')
            self.busy=False
        threading.Thread(target=finish,daemon=True).start()
        return True
    detect_team_leaders=check_window


class FixtureReader:
    def __init__(self,control,proof):
        self.control,self.proof=control,str(proof)
    def load(self,day,run_id):
        request=self.control.request
        rows=[]
        for index in range(1,7 if request.mode=='multi' else 2):
            tasks={code:empty_record() for code in task_codes(request)}
            tasks[task_codes(request)[0]].update(status='running' if index==1 else 'offline' if index==3 else 'new',evidence_paths=[self.proof])
            rows.append(dict(index=index,account=None if index==6 else f'fixture-{index}',
                             health='身份待核实' if index==6 else '已掉线：提示确认' if index==3 else '已绑定',tasks=tasks))
        return dict(rows=rows,warnings=[],day=day,run_id=run_id)
    def close(self):
        pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=dict(scope='offline injected controller and image fixture only',checks=[],game_input_count=0,live_game_started=False,success=False)
    with tempfile.TemporaryDirectory(prefix='hyol-native-ui-') as folder:
        root=Path(folder)
        proof=root/'logs/proof.png'
        proof.parent.mkdir()
        Image.new('RGB',(80,40),(24,98,76)).save(proof)
        holder={}
        def factory(sink,root):
            holder['control']=FixtureController(sink,root)
            return holder['control']
        api=DesktopBridge(root,controller_factory=factory,
                          reader_factory=lambda _:FixtureReader(holder['control'],proof))
        control=holder['control']
        server=asset_server(ROOT/'web')
        window=webview.create_window('火影OL · 离线界面验收替身',f'http://127.0.0.1:{server.server_port}/index.html?native=1',
                                     js_api=api,hidden=True,width=1180,height=820)
        def js(code):
            return window.evaluate_js(code)
        def check(condition,label):
            if not condition:
                raise AssertionError(label)
            result['checks'].append(label)
        def wait(code,label):
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                if js(code):
                    result['checks'].append(label)
                    return
                time.sleep(.1)
            raise AssertionError('Timed out: '+label)
        def run():
            try:
                wait('!!window.HYOL_NATIVE?.ready','native bridge initialized')
                check(not js('document.body.innerText.includes("界面演示数据")'),'production page has no demo banner')
                check(not js('document.querySelector(".demo-actions").getBoundingClientRect().height'),'scenario switch hidden in production')
                check(not control.calls,'opening does not start a task')
                js('document.querySelector("input[data-config=scope][value=multi]").click()')
                js('document.querySelector("[data-action=start]").click()')
                wait('document.querySelectorAll(".progress-table tbody tr").length===6','real bridge displays six fixture rows')
                check(control.calls[-1].mode=='multi' and control.calls[-1].account_count==6,'submitted controls reach RunRequest')
                check(js('!!document.querySelector("[data-action=stop]") && !document.querySelector("[data-action=start]")'),'offline account preserves overall stop control')
                check('身份待核实' in js('document.querySelector(".progress-table tbody tr:last-child td:nth-child(2)").innerText'),'unknown account is not marked offline')
                check(not api._can_close(),'busy native window refuses close')
                js('document.querySelector("[data-action=stop]").click()')
                wait('document.querySelector(".dock-copy strong").textContent.includes("已停止") && !!document.querySelector("[data-action=start]")','stop -> stopped with new-run control')
                check(api._can_close(),'idle native window permits close')
                js('document.querySelector(\'.task-cell[data-account="1"]\').click()')
                check(js('document.querySelector("#app-dialog").open'),'task detail opens')
                js('document.querySelector("[data-action=open-evidence]").click()')
                wait('document.querySelector("#dialog-title")?.textContent==="本轮图片证据"','evidence opens through native validated image path')
                check(js('document.querySelector(".evidence-thumb").src.startsWith("data:image/png;base64,")'),'evidence is an actual decoded PNG fixture')
                js('document.querySelector("[data-action=back-to-detail]").click()')
                check(js('document.querySelector("#dialog-title").textContent.includes("邮件")'),'evidence returns to original task detail')
                js('document.querySelector(".dialog-close").click()')
                check(not js('document.querySelector("#dialog-content img")'),'closing releases displayed image data')
                js('document.querySelector("[data-page=logs]").click()')
                js('document.querySelector("#log-search").value="替身";document.querySelector("#log-search").dispatchEvent(new Event("input",{bubbles:true}));document.querySelector("#log-search").focus()')
                control.emit('unified_status',detail='替身追加日志')
                wait('document.querySelector(".log-stream").textContent.includes("替身追加日志")','new native log reaches log list')
                check(js('document.activeElement.id==="log-search" && document.activeElement.value==="替身"'),'polling preserves log search input and focus')
                js('document.querySelector("[data-action=export-logs]").click()')
                deadline=time.monotonic()+10
                while not list((root/'logs/exports').glob('*.txt')) and time.monotonic()<deadline:
                    time.sleep(.1)
                check(bool(list((root/'logs/exports').glob('*.txt'))),'native export writes fixed local folder')
                result['request']=vars(control.calls[-1])
                result['success']=True
            except Exception as exc:
                result['error']=str(exc)
                result['traceback']=traceback.format_exc()
            finally:
                control.busy=False
                window.destroy()
        window.events.loaded += run
        try:
            webview.start(gui='edgechromium',debug=False)
        finally:
            server.shutdown()
            server.server_close()
            api._close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['success'] else 1


if __name__=='__main__':
    raise SystemExit(main())
