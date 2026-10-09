"""Current-account weekend activity registry."""
from __future__ import annotations

from datetime import datetime

import hyol_scheduler as hs


class WeekendUnavailable(hs.PendingError):
    """A weekend activity is visibly absent or closed; continue other tasks."""


def is_weekend(at: datetime | None = None) -> bool:
    from hyol_puzzle import activity_day
    current = at or datetime.now()
    # Daruma keeps its existing calendar gate. Puzzle's supplied notice ends
    # at 05:00, so the common entry must also survive Sunday -> Monday midnight.
    return current.weekday() >= 5 or datetime.fromisoformat(activity_day(current)).weekday() >= 5


def run_all(r) -> None:
    if not is_weekend():
        r.event('weekend_calendar_skip', detail='达摩日历与拼图05:00活动日均不在周末；零游戏识别')
        raise WeekendUnavailable('周末活动日期门已关闭，跳过不计为实机完成')
    from hyol_daruma import run as run_daruma
    from hyol_puzzle import run as run_puzzle, progress_complete, activity_day
    # A real in-flight Daruma still owns its page; new runs do puzzles first.
    record = r.record('weekend')
    actions = r.scheduler.state.accounts.get(r.account.name, {}).get('actions', {})
    daruma_pending = any(key.startswith('daruma_') and row.get('status') == 'pending'
                         for key, row in actions.items())
    if r.visible('daruma_game_page.bmp', (920,220,1300,325)):
        daruma_pending = True
    activities = (('daruma', run_daruma), ('puzzle', run_puzzle)) if daruma_pending else (
        ('puzzle', run_puzzle), ('daruma', run_daruma))
    unavailable = []
    for name, handler in activities:
        if (name == 'daruma' and record.get('legacy_daruma_finished')
                and record.get('daruma_game_day') == activity_day()
                and record.get('zero_verified') and record.get('zero_evidence') and not daruma_pending):
            r.event('weekend_activity_verified', activity=name, preserved=True)
            continue
        r.event('weekend_activity_started', activity=name)
        try:
            if name == 'daruma':
                from hyol_daruma import is_weekend as daruma_open_day
                if not daruma_open_day():
                    if daruma_pending:
                        raise hs.PendingError('达摩原操作仍未决，日历门关闭；保留原页面，不转开拼图')
                    raise WeekendUnavailable('达摩未通过既有周六/周日日历门；未核实游戏开放状态')
            handler(r)
        except WeekendUnavailable as exc:
            unavailable.append(str(exc))
            r.event('weekend_activity_unavailable', activity=name, detail=str(exc))
            continue
        if name == 'puzzle' and not progress_complete(record.get('puzzle')):
            raise hs.PendingError('三个拼图难度的通关和领奖证据尚未齐全')
        if name == 'daruma':
            if not (record.get('zero_verified') and record.get('zero_evidence')):
                raise hs.PendingError('达摩剩余次数为0的证据缺失，不把跳过记为完成')
            record['legacy_daruma_finished'] = bool(record.get('zero_verified') and record.get('zero_evidence'))
            record['daruma_game_day'] = activity_day()
            r.save()
        r.event('weekend_activity_verified', activity=name)
    if unavailable:
        raise WeekendUnavailable('；'.join(unavailable))
