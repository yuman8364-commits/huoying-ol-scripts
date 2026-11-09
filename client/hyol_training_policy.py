"""Units and dated arena policy shared by training plans and task adapters."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


POLICY_VERSION = 3
UNIT_LABELS = {'count': '次', 'wins': '胜', 'points': '积分', 'layers': '层', 'claims': '项'}


def local_day():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def arena_policy(day, selection='auto'):
    parsed = date.fromisoformat(day)
    if parsed.isoformat() != day:
        raise ValueError('竞技场执行日期必须为YYYY-MM-DD')
    if selection not in ('auto', 'final_goal', 'tickets'):
        raise ValueError('修行录竞技场结束条件须为自动、完成最终目标或战令用完')
    sunday = parsed.weekday() == 6
    resolved = ('final_goal' if sunday else 'tickets') if selection == 'auto' else selection
    return dict(version=POLICY_VERSION, execution_day=day, weekday=parsed.weekday(),
                selection=selection, stop_policy=resolved,
                mode='wins' if resolved == 'final_goal' else 'tickets',
                label=('周日补足修行录最终目标' if selection == 'auto' and sunday else
                       '平日打到实际战令归零' if selection == 'auto' else
                       '完成修行录最终目标' if resolved == 'final_goal' else '打到实际战令归零'),
                fixed_battle_cap=None)


def selected_arena_policy(runner):
    value = getattr(runner, 'training_arena_stop_policy', None)
    if value is None:
        value = getattr(getattr(runner, 'scheduler', None), 'training_arena_stop_policy', 'auto')
    if value not in ('auto', 'final_goal', 'tickets'):
        raise ValueError('修行录竞技场结束条件无效')
    return value


def goal_unit(task, description):
    if '积分' in description:
        return 'points'
    if '获胜' in description or '胜利' in description:
        return 'wins'
    if '层' in description:
        return 'layers'
    return 'count'


def quantity_plan(task, goals, day, arena_stop_policy='auto'):
    """Overlapping goals share work; incompatible units never share a number."""
    deficits = {}
    for goal in goals:
        current, target = goal.get('current'), goal.get('target')
        if type(current) is not int or type(target) is not int or not 0 <= current <= target:
            continue
        unit = goal_unit(task, goal.get('description', ''))
        deficits[unit] = max(deficits.get(unit, 0), target-current)
    unit = next(iter(deficits)) if len(deficits) == 1 else 'mixed'
    result = dict(unit=unit, unit_label=UNIT_LABELS.get(unit, '多种目标'), deficits=deficits,
                  required=deficits.get(unit), available=None, scheduled=None,
                  started=0, settled=0, completion_scope='training_goal',
                  quantity_state='awaiting_observation', stop_reason='')
    if task == 'arena':
        result['date_policy'] = arena_policy(day, arena_stop_policy)
        result['stop_policy'] = result['date_policy']['stop_policy']
        result['stop_condition'] = 'goal' if result['date_policy']['mode'] == 'wins' else 'tickets_zero'
    else:
        result['stop_condition'] = 'goal_or_quota'
    return result


def bounded_count(required, available):
    if type(required) is not int or required < 0 or type(available) is not int or available < 0:
        raise ValueError('目标缺口和可用次数必须已可靠识别')
    return min(required, available)
