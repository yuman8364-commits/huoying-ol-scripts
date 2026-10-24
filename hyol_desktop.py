"""Windows WebView2 shell for the accepted UI and existing Python runtime."""
from __future__ import annotations

from hyol_environment import configure_numeric_runtime
configure_numeric_runtime()

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

from hyol_runtime import VERSION
from hyol_desktop_bridge import DesktopBridge


def application_root():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent


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
    args=parser.parse_args(argv)
    import webview
    root=application_root()
    if not (root/'web/index.html').is_file():
        raise RuntimeError('界面资源缺失，请完整解压客户端目录。')
    api=None if args.demo else DesktopBridge(root)
    server=asset_server(root/'web')
    address=f'http://127.0.0.1:{server.server_port}/index.html'+('' if args.demo else '?native=1')
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=False
    window=webview.create_window('火影OL 完整客户端 '+VERSION+(' · 演示' if args.demo else ''),
                                 url=address,js_api=api,width=1180,height=820,min_size=(800,600),
                                 background_color='#F2F5F3',hidden=args.hidden)
    if api:
        window.events.closing += api._can_close
    if args.smoke_report:
        def inspect():
            result={'version':VERSION,'mode':'demo' if args.demo else 'native','game_task_started':False,
                    'game_input_count':0,'success':False}
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
            ctypes.windll.user32.MessageBoxW(None,'客户端启动失败：'+str(exc)+'\n请确认WebView2运行库已安装，并保留完整解压目录。','火影OL 客户端',0x10)
        raise
