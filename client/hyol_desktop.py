"""Windows WebView2 shell for the accepted UI and existing Python runtime."""
from __future__ import annotations

from hyol_environment import configure_numeric_runtime
configure_numeric_runtime()

import argparse
from dataclasses import asdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

from hyol_runtime import VERSION, RELEASE_DATE, EDITION
from hyol_opencv import backend_info
from hyol_desktop_bridge import DesktopBridge, parse_request
from hyol_scheduler import atomic_json
from hyol_app_paths import application_root

PLAN_CONTROLS_READY = ('!!window.HYOL_NATIVE?.ready && '
                       '!!document.querySelector("[data-action=start]:not([disabled])") && '
                       '!!document.querySelector("[data-section=independent]:not([disabled])")')


def _save_acceptance_report(path, report):
    """Readers and Windows scanners may briefly deny replacing a report."""
    if path is not None:
        atomic_json(path, report)


def _request_script(request):
    """Use the same visible controls as a person; preview before any start."""
    if request.mode == 'team':
        raise ValueError('This one-shot acceptance helper does not run team modes')
    if request.mode == 'team_then_multi':
        return 'const desired='+json.dumps(asdict(request),ensure_ascii=False)+''';
        const click=(selector)=>{const node=document.querySelector(selector);if(!node||node.disabled)throw new Error('Unavailable control: '+selector);node.click()};
        const change=(selector,value)=>{const node=document.querySelector(selector);if(!node||node.disabled)throw new Error('Unavailable field: '+selector);node.value=value;node.dispatchEvent(new Event('change',{bubbles:true}))};
        const toggle=(selector,value)=>{const node=document.querySelector(selector);if(!node)throw new Error('Missing toggle: '+selector);if(node.checked!==value)node.click()};
    click('[data-section="independent"]');click('[data-independent="arena"]');toggle('[data-config="arenaSecondSkill"]',desired.arena_second_skill);
        click('[data-section="team"]');click('[data-team-mode="pipeline"]');
        toggle('[data-config="preformedDuos"]',desired.preformed_duos);
        toggle('[data-config="pipelineAuto"]',!desired.leaders.length);
        if(desired.leaders.length)for(let index=0;index<3;index++)change('[data-pipeline-leader="'+index+'"]',desired.leaders[index]||'');
        for(const [leader,member] of desired.preformed_pairs)change('[data-preformed-member="'+leader+'"]',member);
        for(const task of window.HYOL_DEMO.PIPELINE_TASKS)toggle('input[data-task-code="'+task.code+'"][data-task-group="pipeline"]',desired.team_tasks.includes(task.code));
        for(const task of window.HYOL_DEMO.SOLO_TASKS)toggle('input[data-task-code="'+task.code+'"][data-task-group="solo"]',desired.tasks.includes(task.code));
        if(desired.team_tasks.includes('bond')) {
          change('[data-config="bondTargetMode"]','battles');
          change('[data-config="bondCount"]',desired.bond_count);
          change('[data-config="bondTargetMode"]','kills');
          change('[data-config="bondKillTarget"]',desired.bond_kill_target);
          change('[data-config="bondTargetMode"]','score');
          change('[data-config="bondScoreTarget"]',desired.bond_score_target);
          change('[data-config="bondTargetMode"]',desired.bond_target_mode);
          for(const [leader,mode,target] of desired.bond_team_goals) {
            change('[data-bond-team-mode="'+leader+'"]',mode);
            change('[data-bond-team-target="'+leader+'"]',target);
          }
        }
        change('[data-config="formation"]',desired.formation);
        toggle('[data-config="finalManaged"]',desired.final_managed);
        if(desired.tasks.includes('grocery')){click('[data-action="select-task"][data-task-code="grocery"]');for(const el of document.querySelectorAll('[data-grocery-item]'))toggle('[data-grocery-item="'+el.dataset.groceryItem+'"]',desired.grocery_items.includes(el.dataset.groceryItem));}
        click('[data-action="show-request"]');
        '''
    return 'const desired='+json.dumps(asdict(request),ensure_ascii=False)+''';
    const click=(selector)=>{const node=document.querySelector(selector);if(!node||node.disabled)throw new Error('Unavailable control: '+selector);node.click()};
    const change=(selector,value)=>{const node=document.querySelector(selector);if(!node||node.disabled)throw new Error('Unavailable field: '+selector);node.value=value;node.dispatchEvent(new Event('change',{bubbles:true}))};
    const toggle=(selector,value)=>{const node=document.querySelector(selector);if(!node)throw new Error('Missing toggle: '+selector);if(node.checked!==value)node.click()};
    click('[data-section="independent"]');click('[data-independent="arena"]');toggle('[data-config="arenaSecondSkill"]',desired.arena_second_skill);
    click('[data-section="independent"]');click('[data-independent="bond"]');
    change('[data-config="bondStopMode"]',desired.bond_stop_mode);
    toggle('[data-config="newBondRound"]',desired.new_bond_round);
    click('[data-section="daily"]');click('[data-daily-mode="training_record"]');
    change('[data-config="trainingArenaStopPolicy"]',desired.training_arena_stop_policy||'auto');
    click('[data-daily-mode="manual"]');click('input[data-config="scope"][value="multi"]');
    for(let index=0;index<6;index++) {
        const count=Number(document.querySelector('output[aria-label="账号数量"]').textContent.split('/')[0].trim());
        if(count===desired.account_count)break;
        click('[data-action="'+(count>desired.account_count?'account-minus':'account-plus')+'"]');
    }
    const arena=document.querySelector('input[data-task-code="arena"][data-task-group="solo"]');
    if(!arena.checked)arena.click();
    click('[data-action="select-task"][data-task-code="arena"]');
    click('input[data-config="arenaTargetMode"][value="wins"]');
    change('[data-config="arenaWins"]',desired.arena_wins);
    click('input[data-config="arenaTargetMode"][value="'+desired.arena_target_mode+'"]');
    change('[data-config="formation"]',desired.formation);
    toggle('[data-config="finalManaged"]',desired.final_managed);
    for(const task of window.HYOL_DEMO.SOLO_TASKS) {
        toggle('input[data-task-code="'+task.code+'"][data-task-group="solo"]',desired.tasks.includes(task.code));
    }
    for(let index=0;index<desired.tasks.length;index++) {
        const code=desired.tasks[index];
        for(let move=0;move<window.HYOL_DEMO.SOLO_TASKS.length;move++) {
            const rows=[...document.querySelectorAll('[data-action="task-up"][data-task-group="solo"]')].filter(node=>desired.tasks.includes(node.dataset.taskCode));
            const position=rows.findIndex(node=>node.dataset.taskCode===code);
            if(position<=index)break;
            click('[data-action="task-up"][data-task-group="solo"][data-task-code="'+code+'"]');
        }
    }
    if(desired.mode==='anniversary') {
        click('[data-section="anniversary"]');
    } else if(['solo','multi'].includes(desired.mode)) {
        click('input[data-config="scope"][value="'+(desired.mode==='multi'?'multi':'current')+'"]');
        if(desired.tasks.length===1 && desired.tasks[0]==='training_record')click('[data-daily-mode="training_record"]');
    } else {
        click('[data-section="independent"]');click('[data-independent="'+desired.mode+'"]');
    }
    if(desired.mode==='solo' && !(desired.tasks.length===1 && desired.tasks[0]==='training_record')) {
        for(const task of window.HYOL_DEMO.CURRENT_TASKS) {
            toggle('input[data-task-code="'+task.code+'"][data-task-group="current"]',desired.tasks.includes(task.code));
        }
        for(let index=0;index<desired.tasks.length;index++) {
            const code=desired.tasks[index];
            if(code==='arena_3v3')continue;
            for(let move=0;move<window.HYOL_DEMO.CURRENT_TASKS.length;move++) {
                const rows=[...document.querySelectorAll('[data-action="task-up"][data-task-group="current"]')].filter(node=>desired.tasks.includes(node.dataset.taskCode));
                const position=rows.findIndex(node=>node.dataset.taskCode===code);
                if(position<=index)break;
                click('[data-action="task-up"][data-task-group="current"][data-task-code="'+code+'"]');
            }
        }
        if(desired.tasks.includes('bond')) {
            click('[data-action="select-task"][data-task-code="bond"]');
            change('[data-config="bondTargetMode"]',desired.bond_target_mode);
            const key=desired.bond_target_mode==='kills'?'bondKillTarget':desired.bond_target_mode==='score'?'bondScoreTarget':'bondCount';
            change('[data-config="'+key+'"]',desired.bond_target_mode==='kills'?desired.bond_kill_target:desired.bond_target_mode==='score'?desired.bond_score_target:desired.bond_count);
        }
    }
        if(desired.tasks.includes('grocery')){click('[data-action="select-task"][data-task-code="grocery"]');for(const el of document.querySelectorAll('[data-grocery-item]'))toggle('[data-grocery-item="'+el.dataset.groceryItem+'"]',desired.grocery_items.includes(el.dataset.groceryItem));}
    click('[data-action="show-request"]');
    '''


def asset_server(folder):
    """Read-only UI assets only; no task-control HTTP endpoint or folder listing."""
    folder = Path(folder).resolve()
    allowed = {'index.html','styles.css','app.js','demo-data.js','native-adapter.js',
               'assets/favicon.svg','assets/demo-evidence.svg'}
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,directory=str(folder),**kwargs)
        def log_message(self,*args):
            pass
        def do_GET(self):
            from urllib.parse import unquote,urlsplit
            name = unquote(urlsplit(self.path).path).lstrip('/') or 'index.html'
            if name not in allowed:
                self.send_error(404)
                return
            super().do_GET()
        def do_HEAD(self):
            from urllib.parse import unquote,urlsplit
            name = unquote(urlsplit(self.path).path).lstrip('/') or 'index.html'
            if name not in allowed:
                self.send_error(404)
                return
            super().do_HEAD()
        def end_headers(self):
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'")
            super().end_headers()
        def list_directory(self,path):
            self.send_error(404)
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True,name='HYOL-UI-assets').start()
    return server


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo',action='store_true',help='Open the isolated interface demo; no runtime API')
    parser.add_argument('--smoke-report',type=Path,help='Developer idle startup check; exits without game input')
    parser.add_argument('--hidden',action='store_true',help='Hide the window during an idle startup check')
    parser.add_argument('--task',choices=('puzzle','training_record'),help='Preselect an existing multi-account activity')
    parser.add_argument('--account-count',type=int,choices=range(1,7),default=6)
    parser.add_argument('--start',action='store_true',help='Explicitly start the selected activity once through the UI')
    parser.add_argument('--acceptance-report',type=Path,help='Save the explicit one-shot UI run receipt and exit after its worker ends')
    parser.add_argument('--acceptance-request',type=Path,help='Preselect a validated non-team RunRequest through the existing UI controls')
    parser.add_argument('--acceptance-control',type=Path,help='Optional local one-shot stop request, consumed through the UI stop button')
    args=parser.parse_args(argv)
    if args.task and args.acceptance_request:
        parser.error('Choose --task or --acceptance-request')
    if args.start and not (args.task or args.acceptance_request):
        parser.error('--start requires an explicitly selected activity or request')
    if (args.demo or args.smoke_report) and (args.task or args.start or args.acceptance_report or args.acceptance_request):
        parser.error('Demo and idle smoke checks cannot start game activities')
    if args.acceptance_report and not args.start:
        parser.error('--acceptance-report requires an explicit --task and --start')
    if args.acceptance_control and not args.acceptance_report:
        parser.error('--acceptance-control requires --acceptance-report')
    selected = None
    if args.task or args.acceptance_request:
        payload = json.loads(args.acceptance_request.read_text('utf-8-sig')) if args.acceptance_request else dict(mode=args.task,account_count=args.account_count,final_managed=False)
        selected = parse_request(payload)
        if selected.mode == 'team':
            parser.error('Team acceptance is outside this helper')
    import webview
    root=application_root()
    if not (root/'web/index.html').is_file():
        raise RuntimeError('界面资源缺失，请重新获取完整客户端。')
    api=None if args.demo else DesktopBridge(root)
    server=asset_server(root/'web')
    address=f'http://127.0.0.1:{server.server_port}/index.html'+('' if args.demo else '?native=1')
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=False
    window=webview.create_window('火影OL 完整客户端 '+VERSION+' · '+RELEASE_DATE+' '+EDITION+(' · 演示' if args.demo else ''),
                                 url=address,js_api=api,width=1180,height=820,min_size=(800,600),
                                 background_color='#F2F5F3',hidden=args.hidden)
    if api:
        window.events.closing += api._can_close
    if selected:
        started=threading.Event()
        def selected_activity():
            if started.is_set():
                return
            started.set()
            report={'version':VERSION,'mode':'native','task':selected.mode,'scope':'real RuntimeController through native UI',
                    'explicit_start':args.start,'ui_started':False,'success':False}
            def save():
                _save_acceptance_report(args.acceptance_report,report)
            try:
                deadline=time.monotonic()+25
                while time.monotonic()<deadline:
                    if window.evaluate_js(PLAN_CONTROLS_READY):
                        break
                    time.sleep(.1)
                else:
                    raise RuntimeError('Native UI did not become ready; no activity started')
                window.evaluate_js(_request_script(selected))
                configured=json.loads(window.evaluate_js('document.querySelector("#dialog-content pre").textContent'))
                if configured!=json.loads(json.dumps(asdict(selected))):
                    raise RuntimeError('Selected UI parameters do not match the explicit activity request')
                window.evaluate_js('document.querySelector(".dialog-close").click()')
                if not args.start:
                    return
                window.evaluate_js('document.querySelector("[data-action=start]").click()')
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    observed=api.get_state()
                    if observed.get('request') is not None:
                        break
                    time.sleep(.1)
                else:
                    raise RuntimeError('UI start was not accepted; no run receipt available')
                report['ui_started']=True
                report['request']=observed['request']
                if not args.acceptance_report:
                    return
                while True:
                    if args.acceptance_control and args.acceptance_control.is_file():
                        command=json.loads(args.acceptance_control.read_text('utf-8-sig'))
                        if command=={'action':'stop'}:
                            clicked=window.evaluate_js('(()=>{const b=document.querySelector("[data-action=stop]");if(!b||b.disabled)return false;b.click();return true})()')
                            if clicked:
                                report['stop_via_ui']=True
                                consumed=args.acceptance_control.with_suffix('.consumed.json')
                                consumed.unlink(missing_ok=True)
                                args.acceptance_control.rename(consumed)
                    observed=api.get_state()
                    report.update(state=observed,receipt=api._receipt)
                    save()
                    if not observed['busy']:
                        report['success']=observed['status']=='complete' and bool(api._receipt) and api._receipt.get('status')=='complete'
                        break
                    time.sleep(2)
            except Exception as exc:
                report['error']=str(exc)
                report['state']=api.get_state()
            finally:
                save()
                if args.acceptance_report and not api._controller.busy:
                    window.destroy()
        window.events.loaded += selected_activity
    if args.smoke_report:
        def inspect():
            result={'version':VERSION,'mode':'demo' if args.demo else 'native','game_task_started':False,
                    'game_input_count':0,'success':False,'release_date':RELEASE_DATE,
                    'edition':EDITION,'recognition':backend_info()}
            try:
                deadline=time.monotonic()+25
                while time.monotonic()<deadline:
                    result['ui']=window.evaluate_js("JSON.stringify({title:document.title,native:!!window.HYOL_NATIVE?.enabled,ready:!!window.HYOL_NATIVE?.ready,banner:document.querySelector('.demo-strip')?.innerText,body:document.body.innerText.slice(-1200)})")
                    ui=json.loads(result['ui'])
                    if args.demo or ui['ready']:
                        break
                    time.sleep(.2)
                result['ui']=json.loads(result['ui'])
                result['backend']=api.get_state() if api else None
                result['onboarding']=api.get_onboarding() if api else None
                result['success']=args.demo or (result['ui']['native'] and result['ui']['ready']
                                               and result['backend']['status']=='idle' and not result['backend']['busy'])
            except Exception as exc:
                result['error']=str(exc)
            finally:
                args.smoke_report.parent.mkdir(parents=True,exist_ok=True)
                args.smoke_report.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
                window.destroy()
        window.events.loaded += inspect
    try:
        webview.start(gui='edgechromium',debug=False,private_mode=True)
    finally:
        server.shutdown()
        server.server_close()
        if api:
            api._close()
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        if getattr(sys,'frozen',False):
            import ctypes
            import tempfile
            import traceback
            import uuid
            from hyol_log_messages import error_message
            message, suggestion = error_message(exc, '客户端未能正常启动。')
            diagnostic = Path(tempfile.gettempdir()) / ('HYOL_startup_'+uuid.uuid4().hex+'.log')
            try:
                diagnostic.write_text(traceback.format_exc(),encoding='utf-8')
                suggestion += '\n启动诊断文件：'+str(diagnostic)
            except OSError:
                suggestion += '\n启动诊断未能保存，请检查临时目录权限和磁盘空间。'
            ctypes.windll.user32.MessageBoxW(None,
                '客户端启动失败：'+message+'\n'+suggestion+
                '\n若界面无法打开，请确认网页显示运行库已安装，并使用完整客户端。',
                '火影OL 客户端',0x10)
            raise SystemExit(1) from None
        raise
