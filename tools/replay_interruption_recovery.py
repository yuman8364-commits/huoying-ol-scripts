"""Replay authentic failure frames without a window, input, or state writes."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
import hyol_scheduler as hs
from hyol_navigation import MARKET_CLOSE_ROI, MARKET_TITLE_ROI


def inspect(path, kind):
    frame = Image.open(path).convert('RGB')
    store = hs.TemplateStore(.98)
    scheduler = object.__new__(hs.Scheduler)
    scheduler.template = lambda name, roi, frame=None, **kwargs: store.match(
        frame, ROOT/'assets'/name, roi, **kwargs)
    result = dict(path=str(Path(path).resolve()), kind=kind)
    if kind == 'battle':
        result.update(arena_active=scheduler._combat_active_visible('arena', frame),
                      survival_active=scheduler._combat_active_visible('survival', frame),
                      automatic_confirmed=scheduler._battle_auto_on_visible(frame),
                      victory=scheduler._arena_victory_visible(frame),
                      main_ready=scheduler.main_ready(frame))
        result['pass'] = (result['arena_active'] and result['survival_active']
                          and not result['main_ready'] and not result['victory'])
    elif kind == 'market':
        result.update(title=scheduler.template('market_anchor.bmp', MARKET_TITLE_ROI, frame, threshold=.98),
                      close=scheduler.template('market_close.bmp', MARKET_CLOSE_ROI, frame, threshold=.98))
        result['pass'] = result['title'][0] and result['close'][0]
    else:
        result.update(arena_active=scheduler._combat_active_visible('arena', frame),
                      survival_active=scheduler._combat_active_visible('survival', frame),
                      main_ready=scheduler.main_ready(frame))
        result['pass'] = not result['arena_active'] and not result['survival_active'] and result['main_ready']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--battle', required=True, type=Path)
    parser.add_argument('--market', required=True, type=Path)
    parser.add_argument('--main', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    rows = [inspect(args.battle, 'battle'), inspect(args.market, 'market'), inspect(args.main, 'main')]
    report = dict(result='PASS' if all(row['pass'] for row in rows) else 'FAIL', frames=rows,
                  game_inputs=0, task_state_writes=0, threshold_lowered=False,
                  battle_results_credited=0, task_completions_credited=0)
    with args.output.open('x', encoding='utf-8') as target:
        json.dump(report, target, ensure_ascii=False, indent=2)
        target.write('\n')
    print(json.dumps(dict(report=str(args.output.resolve()), **report), ensure_ascii=False))
    return 0 if report['result'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
