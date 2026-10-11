"""Replay journal timings through the hard battle deadline, without game input."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hyol_combat_watchdog import BattleWatchdog


def replay(journal):
    rows = [json.loads(line) for line in Path(journal).read_text('utf-8-sig').splitlines() if line.strip()]
    active, timeouts, completed = {}, [], 0
    for row in rows:
        if not isinstance(row.get('ts'), str):
            continue
        now = datetime.fromisoformat(row['ts']).timestamp()
        key = (row.get('account'), row.get('mode'))
        if row.get('event') == 'combat_battle_started':
            active[key] = dict(index=row['index'], started_at=row['ts'],
                               watchdog=BattleWatchdog(started_at=now), reported=False)
        elif row.get('event') == 'combat_battle_finished':
            current = active.get(key)
            if current and current['index'] == row.get('index'):
                completed += 1
                active.pop(key)
        elif row.get('event') in ('combat_account_bound', 'combat_account_bound_by_page') and row.get('running'):
            current = active.get(key)
            if current and not current['reported']:
                observation = current['watchdog'].observe(None, now)
                if observation:
                    timeouts.append(dict(account=key[0], mode=key[1], index=current['index'],
                                         started_at=current['started_at'], would_stop_at=row['ts'], **observation))
                    current['reported'] = True
    return dict(journal_sha256=hashlib.sha256(Path(journal).read_bytes()).hexdigest(),
                completed_battles=completed, predicted_timeouts=timeouts,
                note='Timing replay only; no battlefield frames or actual timeout recovery were executed.',
                game_inputs=0, state_changes=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.journal)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write('\n')
    print(json.dumps(result, ensure_ascii=False))
