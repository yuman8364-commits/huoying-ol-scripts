"""Existing cooperative activities with a hard current-account input boundary."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from types import MethodType

import hyol_scheduler as hs
from hyol_daily_tasks import DailyRunner, SOLO_OPTIONS, calendar_task_plan
from hyol_errors import TaskUnavailable

CURRENT_TEAM_TASKS = ('group_instance', 'wanted', 'strong_approach', 'sealed',
                      'rescue_jinchuriki', 'team_pvp', 'bond', 'arena_3v3')
CURRENT_TEAM_DEFAULTS = ('group_instance', 'strong_approach')
LEGACY_KEYS = {'group_instance': 'group', 'strong_approach': 'strong'}


@dataclass(frozen=True)
class CurrentControlTeam:
    """Visible party size and controlled accounts are deliberately separate."""
    index: int
    members: tuple[str, ...]
    observed_member_count: int

    @property
    def member_count(self):
        return self.observed_member_count

    @property
    def member_label(self):
        return f'{self.member_count}人队伍（仅控制本号）'


def install_current_input_boundary(scheduler, account):
    """Even an accidentally reused team helper cannot reach another tab."""
    scheduler.current_account_only = account.name
    scheduler.generic_current_tab_x = account.tab_x
    scheduler.config['CurrentWindowOnly'] = '1'
    if getattr(scheduler, '_current_input_owner', None) == account.name:
        return
    if getattr(scheduler, '_current_input_owner', None) is not None:
        raise hs.SafetyError('当前账号运行期间不能更换输入范围')
    scheduler._current_input_owner = account.name
    for name in ('switch_account', 'switch_account_for_combat', 'click_point', 'click_template'):
        original = getattr(scheduler, name)
        def guarded(self, target, *args, _original=original, **kwargs):
            if getattr(target, 'name', None) != account.name or getattr(target, 'tab_x', None) != account.tab_x:
                raise hs.SafetyError('仅当前账号模式禁止操作其他标签或队友')
            return _original(target, *args, **kwargs)
        setattr(scheduler, name, MethodType(guarded, scheduler))
    original_chrome = scheduler.chrome_click
    owner = account
    def chrome(self, x, y, label, account=None, *args, **kwargs):
        if account is not None and getattr(account, 'name', None) != owner.name:
            raise hs.SafetyError('仅当前账号模式禁止切换队友标签')
        if y < 35 and abs(x-owner.tab_x) > 12:
            raise hs.SafetyError('当前任务禁止切换到其他浏览器标签')
        return original_chrome(x, y, label, account, *args, **kwargs)
    scheduler.chrome_click = MethodType(chrome, scheduler)
    original_event = getattr(scheduler, 'event', None)
    if callable(original_event):
        def event(self, name, account=None, **fields):
            fields.setdefault('completion_scope', 'current_account')
            if name == 'team_all_members_auto_verified':
                name = 'current_team_auto_verified'
                fields.update(detail='本号自动战斗已核实，队友状态未检查', controlled_account_count=1)
            return original_event(name, account, **fields)
        scheduler.event = MethodType(event, scheduler)


def observe_current_party(runner, *, required=None):
    from hyol_accounts import is_captain
    from hyol_team_pipeline import party_name_masks
    s, account = runner.scheduler, runner.account
    samples, proof = [], []
    for n in range(2):
        frame = s.content_guard(account)
        if not s.main_ready(frame) or not is_captain(s, frame):
            raise TaskUnavailable('请先在当前账号组好队伍并由本号担任队长；脚本不会邀请或操作队友')
        names = party_name_masks(frame)
        if len(names) not in (1, 2) or required and len(names)+1 != required:
            raise TaskUnavailable(f'当前队伍人数未确认'+(f'为{required}人' if required else '')+'，保留队伍，未开始活动')
        samples.append(names)
        proof.append(str(s.save_raw_evidence(frame, account, 'current_party_'+str(n+1), 'observed')))
        if n == 0:
            s.wait_interruptible(.25)
    if len(samples[0]) != len(samples[1]) or any(hs.mask_f1(a,b) < .98 for a,b in zip(*samples)):
        raise hs.PendingError('当前队伍成员画面尚未稳定，未开始活动')
    signature = hashlib.sha256(b''.join(mask.tobytes() for mask in samples[-1])).hexdigest()
    result = dict(owner=account.name, member_count=len(samples[-1])+1,
                  signature=signature, evidence=proof[-1], samples=proof,
                  observation_scope='current_account')
    runner.record(runner.current)['party_observation'] = result
    runner.save()
    return CurrentControlTeam(0, (account.name,), result['member_count'])


class CurrentAccountRunner(DailyRunner):
    def __init__(self, task='all', *, selected_tasks, **kwargs):
        desired = tuple(selected_tasks)
        if not desired or len(desired) != len(set(desired)) or any(t not in (*SOLO_OPTIONS, *CURRENT_TEAM_TASKS) for t in desired):
            raise ValueError('当前账号任务选择无效')
        ordinary = tuple(t for t in desired if t in SOLO_OPTIONS)
        super().__init__(task, selected_tasks=ordinary or ('mail',), **kwargs)
        self.requested_tasks = tuple(t for t in desired if t != 'arena_3v3') + (('arena_3v3',) if 'arena_3v3' in desired else ())
        self.calendar_plan = calendar_task_plan(self.requested_tasks)
        self.selected_tasks = tuple(self.calendar_plan['effective_tasks'])
        self.scheduler.config.update(CurrentWindowOnly='1', PreserveActivityNotices='1')
        self.scheduler.completion_scope = 'current_account'
        self.group_count = 0
        self._verified_current_activities = set()

    def bind(self):
        self.scheduler.bind_window(wait=True, timeout_seconds=15)
        frame = self.scheduler.capture()
        from hyol_arena_3v3 import page_visible
        if ('bond' in self.selected_tasks and self.scheduler.template(
                'bond_ready_anchor.bmp', (1000,200,1500,450), frame, threshold=.98)[0]):
            from hyol_current_team_battles import bind_preserved_bond
            bind_preserved_bond(self, frame)
        elif 'arena_3v3' in self.selected_tasks and page_visible(frame, self.scheduler.templates):
            self.account = self.scheduler.bind_generic_current_window(require_game_context=False)
            from hyol_checkpoints import load_tasks, current_identity_mask
            self.path, self.records = load_tasks(self.scheduler.state, self.account.name)
            self.scheduler._arena3v3_masks = {self.account.index: current_identity_mask(frame,self.scheduler.templates)}
        else:
            super().bind()
        install_current_input_boundary(self.scheduler, self.account)
        self.scheduler.accounts = [self.account]
        self.scheduler.by_name = {self.account.name:self.account}
        for task in self.selected_tasks:
            self.record(task)

    def event(self, name, **fields):
        task = fields.get('task', getattr(self, 'current', ''))
        task = {'group': 'group_instance', 'strong': 'strong_approach'}.get(task, task)
        if task in CURRENT_TEAM_TASKS:
            fields['task'] = task
            fields['completion_scope'] = 'current_account'
            mapped = {'daily_task_started': 'current_team_task_started',
                      'daily_task_pending': 'current_team_task_pending',
                      'daily_task_complete': 'current_team_task_done'}
            if name in mapped:
                rec = self.record(task)
                fields.update(home_returned=rec.get('home_returned', False),
                              activity_ready=rec.get('activity_ready', False))
                name = mapped[name]
        return super().event(name, **fields)

    def record(self, task):
        key = LEGACY_KEYS.get(task, task)
        if task != key and task in self.records and key in self.records and self.records[task] != self.records[key]:
            raise hs.PendingError('当前任务的新旧记录不一致，保留原账本：'+task)
        if task != key and key not in self.records and task in self.records:
            self.records[key] = self.records[task]
        result = super().record(key)
        if task != key:
            self.records[task] = result
        if task in CURRENT_TEAM_TASKS:
            result['completion_scope'] = 'current_account'
        return result

    def preserve_activity_page(self, task):
        return task in ('bond', 'arena_3v3') and self.record(task).get('activity_ready') is True

    def current_task_complete(self, task, rec):
        from hyol_checkpoints import complete
        if task in ('bond', 'arena_3v3'):
            return (task in getattr(self, '_verified_current_activities', set())
                    and rec.get('status') == 'done' and rec.get('task_completed') is True
                    and rec.get('completion_scope') == 'current_account'
                    and rec.get('activity_ready') is True and bool(rec.get('evidence')))
        return complete(rec)

    def force_handler_recheck(self, task):
        return task in ('bond', 'arena_3v3') and task not in getattr(self, '_verified_current_activities', set())

    def group_instance(self):
        from hyol_team_pipeline import group_open_now
        if not group_open_now():
            raise TaskUnavailable('组队副本当前未开放，开放时段为08:00至24:00')
        # The single-account driver already handles the game's explicit solo
        # confirmation and reconciles each result against its reward counter.
        super().group()
        if not self.record('group_instance').get('reward_exhausted'):
            raise hs.PendingError('本号组队副本剩余奖励次数尚未确认为零')
        self.record('group_instance').update(reward_exhausted=True, zero_verified=True)

    def wanted(self):
        # Its existing solo confirmation belongs to this account's audited chain.
        return super().wanted()

    def strong_approach(self):
        team = hs.Team(0, (self.account.name,))
        self.ensure_activity_formation('strong')
        # Boss titles rotate. The stable normal-reward label is independent
        # of the artwork; the existing start button and counter stay required.
        shell = 'strong_reward_label_core_current.bmp'
        spec = replace(hs.TEAM_ACTIVITY_BY_KEY['strong_approach'], require_all_member_auto=False,
                       page_anchor=shell, return_anchor=shell, require_reward_confirmation=True)
        # The single-leader loop verifies this account's auto control. Its
        # multi-member gate would switch to the same full-screen battle and
        # demand the village name strip, which is hidden during combat.
        attempt = self.scheduler._next_team_attempt_index(team, self.account, spec)
        base = self.scheduler._team_attempt_base(team, spec, attempt)
        if self.scheduler.state.action_status(self.account.name, base + '_start') in ('pending', 'done'):
            frame = self.scheduler.capture()
            if (self.scheduler._team_battle_start_visible(frame)
                    or self.scheduler._battle_hud_visible(frame)
                    or self.scheduler._team_result_template(frame)
                    or self.scheduler._team_reward_result_visible(frame)):
                self.scheduler._poll_generic_team_activity(team, self.account, spec, attempt_index=attempt)
        self.scheduler.run_verified_strong_approach(team, self.account, spec, target_count=None)
        if not hs.Scheduler._team_activity_completion_verified(self.scheduler, team, self.account, spec):
            raise hs.PendingError('本号强者奖励次数尚未确认为零')
        self.record('strong_approach')['completed'] = self.scheduler._next_team_attempt_index(team, self.account, spec) - 1
        self.save()

    def sealed(self):
        observe_current_party(self)
        return super().sealed()

    def _generic_activity(self, task):
        if task == 'rescue_jinchuriki' and not hs.rescue_activity_open_now():
            raise TaskUnavailable('营救人柱力当前不在开放时段，保留待测试')
        spec = hs.TEAM_ACTIVITY_BY_KEY[task]
        team = observe_current_party(self, required=spec.required_member_count)
        self.ensure_formation_once()
        spec = replace(spec, require_all_member_auto=True)
        self.scheduler.run_generic_team_activity(team, self.account, spec)
        if not self.scheduler._team_activity_completion_verified(team, self.account, spec):
            raise hs.PendingError('本号活动结果或奖励尚未核实：'+spec.label)

    def rescue_jinchuriki(self):
        return self._generic_activity('rescue_jinchuriki')

    def team_pvp(self):
        return self._generic_activity('team_pvp')

    def bond(self):
        from hyol_current_team_battles import run_bond
        result = run_bond(self)
        self._verified_current_activities.add('bond')
        return result

    def arena_3v3(self):
        from hyol_current_team_battles import run_arena3v3
        result = run_arena3v3(self)
        self._verified_current_activities.add('arena_3v3')
        return result
