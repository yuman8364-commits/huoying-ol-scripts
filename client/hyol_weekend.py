"""Current-account weekend activity registry."""
from __future__ import annotations

from datetime import datetime

import hyol_scheduler as hs
from hyol_errors import TaskUnavailable


class WeekendUnavailable(TaskUnavailable):
    """A weekend activity is visibly absent or closed; continue other tasks."""


def is_weekend(at: datetime | None = None) -> bool:
    from hyol_puzzle import activity_day
    current = at or datetime.now()
    # The live weekend notice ends on Monday at 05:00 for the whole panel.
    return datetime.fromisoformat(activity_day(current)).weekday() >= 5


def puzzle_replaced_by_known_activities(r, frame):
    """Three positive row titles distinguish this rotation from a missing load."""
    return (r.visible('daruma_weekend_page.bmp', (880,245,1190,360), frame, .98)
            and all(r.visible(name, roi, frame, .98) for name, roi in (
                ('weekend_ninja_mission_title_20261109.bmp', (750,375,890,425)),
                ('weekend_dice_title_20261109.bmp', (750,480,900,535)),
                ('weekend_daruma_title_20261109.bmp', (750,595,890,645)))))


def run_all(r) -> None:
    if not is_weekend():
        r.event('weekend_calendar_skip', detail='周末活动按公告在周一五点结束；当前不在活动日')
        raise WeekendUnavailable('周末活动日期门已关闭，跳过不计为实机完成')
    from hyol_daruma import run as run_legacy_daruma
    from hyol_daruma_task import run as run_current_daruma, verified as daruma_verified
    from hyol_puzzle import run as run_puzzle, progress_complete, activity_day
    # A real in-flight Daruma still owns its page; new runs do puzzles first.
    record = r.record('weekend')
    actions = r.scheduler.state.accounts.get(r.account.name, {}).get('actions', {})
    legacy_pending = any(key.startswith('daruma_') and isinstance(row, dict) and row.get('status') == 'pending'
                         for key, row in actions.items())
    daruma_pending = legacy_pending or r.visible('daruma_game_page.bmp', (920,220,1300,325))
    run_daruma = run_legacy_daruma if legacy_pending else run_current_daruma
    activities = (('daruma', run_daruma), ('puzzle', run_puzzle)) if daruma_pending else (
        ('puzzle', run_puzzle), ('daruma', run_daruma))
    unavailable = []
    for name, handler in activities:
        r.event('weekend_activity_started', activity=name)
        try:
            if name == 'daruma':
                from hyol_daruma import is_weekend as daruma_open_day
                if not daruma_open_day():
                    if daruma_pending:
                        raise hs.PendingError('达摩原操作仍未决，日历门关闭；保留原页面，不转开拼图')
                    raise WeekendUnavailable('达摩未通过周末五点活动日门；未核实游戏开放状态')
            handler(r)
        except WeekendUnavailable as exc:
            unavailable.append(str(exc))
            r.event('weekend_activity_unavailable', activity=name, detail=str(exc))
            continue
        if name == 'puzzle' and not progress_complete(record.get('puzzle')):
            raise hs.PendingError('三个拼图难度的通关和领奖证据尚未齐全')
        if name == 'daruma':
            if not legacy_pending:
                current = r.record('daruma')
                if not daruma_verified(current):
                    raise hs.PendingError('达摩独立执行器未提供奖励归零及断点证据')
                current.update(status='done',task_completed=True,home_returned=True,finished_at=hs.now_iso())
                record.update({key:current[key] for key in ('remaining','zero_verified','zero_evidence','checkpoint')})
                record['daruma_engine'] = 'standalone'
            if not (record.get('zero_verified') and record.get('zero_evidence')):
                raise hs.PendingError('达摩剩余次数为0的证据缺失，不把跳过记为完成')
            record['legacy_daruma_finished'] = bool(record.get('zero_verified') and record.get('zero_evidence'))
            record['daruma_game_day'] = activity_day()
            r.save()
        r.event('weekend_activity_verified', activity=name)
    if unavailable:
        raise WeekendUnavailable('；'.join(unavailable))
