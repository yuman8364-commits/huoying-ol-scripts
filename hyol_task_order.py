"""Persist task order only; task selection and live ledgers remain separate."""
from __future__ import annotations

import json
from pathlib import Path


def defaults():
    from hyol_daily_tasks import DEFAULT_SOLO_OPTIONS, SOLO_OPTIONS
    from hyol_team_six import TASKS
    from hyol_team_pipeline import SUPPORTED_TEAM_TASKS
    solo = [t for t in DEFAULT_SOLO_OPTIONS if t != 'welfare']
    solo += [t for t in SOLO_OPTIONS if t not in solo and t not in ('welfare','anniversary')]
    return dict(solo=solo + ['welfare'], team=list(TASKS), pipeline=list(SUPPORTED_TEAM_TASKS))


def validate(value):
    allowed = defaults()
    if not isinstance(value, dict) or set(value) != set(allowed):
        raise ValueError('任务顺序须包含单人、组队和已有队伍三个列表')
    for group, tasks in value.items():
        if (not isinstance(tasks, list) or any(type(t) is not str for t in tasks)
                or len(tasks) != len(set(tasks)) or set(tasks) != set(allowed[group])):
            raise ValueError('任务顺序包含重复、缺失或不支持的任务：' + group)
    return {group: list(value[group]) for group in allowed}


def load(root):
    path = Path(root) / 'desktop_task_order.json'
    if not path.exists():
        return dict(ok=True, order=defaults(), warning='')
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        # New versions can add tasks without discarding a user's prior order.
        expected = defaults()
        if not isinstance(value, dict) or set(value) != set(expected):
            raise ValueError('任务顺序文件格式无效')
        for key, tasks in value.items():
            if (not isinstance(tasks, list) or any(type(t) is not str for t in tasks)
                    or len(tasks) != len(set(tasks)) or set(tasks) - set(expected[key])):
                raise ValueError('任务顺序文件包含无效任务')
            value[key] = tasks + [t for t in expected[key] if t not in tasks]
        return dict(ok=True, order=validate(value), warning='')
    except (OSError, ValueError, TypeError) as exc:
        return dict(ok=True, order=defaults(), warning='任务顺序读取失败，已使用默认顺序：' + str(exc))


def save(root, value):
    from hyol_scheduler import atomic_json
    order = validate(value)
    atomic_json(Path(root) / 'desktop_task_order.json', order)
    return dict(ok=True, order=order)
