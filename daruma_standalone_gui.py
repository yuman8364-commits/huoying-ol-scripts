"""Independent entry. Default launches the GUI without starting any game action."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description='达摩独立测试；默认只打开操作窗口')
    from daruma_standalone import VERSION
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('--self-check', type=Path, help='离线检查素材与打包路径，报告写入指定文件')
    parser.add_argument('--list-windows', action='store_true', help='只枚举游戏窗口')
    parser.add_argument('--observe', action='store_true', help='只读识别指定窗口，零游戏输入')
    parser.add_argument('--run', action='store_true', help='开始/继续指定窗口的达摩挑战')
    parser.add_argument('--hwnd', type=lambda value: int(value, 0), help='明确的测试窗口句柄')
    parser.add_argument('--strike-limit', type=int, choices=(1,), help='核实一次敲击后停止，供首击实测')
    parser.add_argument('--smoke-ui', action='store_true', help='只打开并关闭独立GUI，不启动游戏任务')
    args = parser.parse_args(argv)
    if args.observe and args.run:
        parser.error('--observe 与 --run 不能同时使用')
    if args.strike_limit and not args.run:
        parser.error('--strike-limit 仅用于 --run')
    if args.self_check:
        if args.run or args.observe or args.list_windows or args.smoke_ui:
            parser.error('--self-check 不能与窗口或任务操作同时使用')
        from daruma_standalone.bundle_check import check
        result = check(args.self_check)
        if __import__('sys').stdout is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'pass' else 2
    if args.list_windows or args.observe or args.run:
        from daruma_standalone.runtime import windows
        targets = windows()
        if args.list_windows:
            print(json.dumps(targets, ensure_ascii=False))
            return 0
        hwnd = args.hwnd
        if hwnd is None:
            if len(targets) != 1:
                print(json.dumps(dict(status='pending', reason='请用 --hwnd 明确指定测试窗口', windows=targets),
                                 ensure_ascii=False))
                return 2
            hwnd = targets[0]['hwnd']
        from daruma_standalone.engine import DarumaRunner
        result = DarumaRunner(hwnd, strike_limit=args.strike_limit).run(readonly=args.observe)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] in ('done', 'observed') else 2
    from daruma_standalone.gui import DarumaApp
    app = DarumaApp()
    if args.smoke_ui:
        from daruma_standalone.ui_smoke import schedule
        schedule(app)
    app.mainloop()
    return 0 if not args.smoke_ui or app.smoke_status == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
