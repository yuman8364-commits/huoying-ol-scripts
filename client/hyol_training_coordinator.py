"""One foreground owner, independently progressing training plans per account."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import time
import uuid

import hyol_scheduler as hs
import hyol_training_record as training
from hyol_checkpoints import complete
from hyol_training_policy import arena_policy, local_day, selected_arena_policy


@contextmanager
def task_context(runner, request):
    scheduler = runner.scheduler
    names = ('current', 'selected_tasks', 'training_request', 'training_yield_each_battle', 'interleave_after_end')
    missing = object()
    old = {name: getattr(runner, name, missing) for name in names}
    navigator = getattr(scheduler, 'navigator', None)
    scheduler_request = getattr(scheduler, 'training_request', missing)
    try:
        runner.current = request['task']
        runner.selected_tasks = (request['task'],)
        runner.training_request = request
        runner.training_yield_each_battle = True
        runner.interleave_after_end = True
        scheduler.navigator = runner
        scheduler.training_request = request
        yield
    finally:
        scheduler.navigator = navigator
        if scheduler_request is missing:scheduler.__dict__.pop('training_request', None)
        else:scheduler.training_request = scheduler_request
        for name, value in old.items():
            if value is missing:
                runner.__dict__.pop(name, None)
            else:
                setattr(runner, name, value)
        runner.save()


def job_key(task, goals, day, policy, account=''):
    identity = (account, day, task, policy, tuple(sorted((g['key'], g['current'], g['target']) for g in goals)))
    return hashlib.sha256(repr(identity).encode()).hexdigest()[:24]


class Driver:
    exclusive = False
    waiting = False
    interval = .25

    def __init__(self, runner, request):
        self.r, self.s, self.request = runner, runner.scheduler, request

    def finish(self, reason='目标缺口已补足', *, exhausted=False):
        status = ('exhausted' if exhausted else 'operation_complete'
                  if self.request.get('quantity_state') == 'requires_goal_rescan' else 'satisfied')
        self.request.update(status=status, stop_reason=reason,
                            finished_at=hs.now_iso(), home_returned=True)
        self.r.save()
        return True


class RankingDriver(Driver):
    def step(self):
        if self.request.get('unit','count') != 'count':
            raise training.TrainingPending('排位目标单位不是挑战次数，未将次数结算当成胜场或积分')
        rec = self.r.record('ranking')
        phase = rec.get('phase', 'ready')
        self.s.switch_account_for_combat(self.r.account, 'ranking', running=phase != 'ready')
        result = self.r.ranking()
        self.waiting = rec.get('phase', 'ready') != 'ready'
        self.request['settled'] = rec.get('completed', 0)-self.request['base_completed']
        if type(rec.get('remaining')) is int:
            self.request['available'] = rec['remaining']
        if result == 'training_target_reached':
            return self.finish()
        if result == 'training_day_changed':
            self.request['home_returned'] = True
            return True
        if result in ('yield_after_end', 'yield_waiting_result', 'yield_after_battle'):
            return False
        if rec.get('zero_verified') is True:
            rec.update(status='done', task_completed=True, home_returned=True,
                       finished_at=hs.now_iso(), last_error='')
            return self.finish('当天排位次数已用完', exhausted=self.request['settled'] < self.request['required'])
        raise training.TrainingPending('排位未取得目标满足或次数归零证据')


class CombatDriver(Driver):
    def __init__(self, runner, request):
        super().__init__(runner, request)
        self.mode = request['task']
        self.slot = hs.CombatSlot(runner.account, self.mode,
                                  next_index=self.s._next_combat_index(runner.account, self.mode))
        self.prepared = False
        self.interval = .4

    def _close_partial(self, *, satisfied=True):
        s, r = self.s, self.r
        frame = s.capture()
        anchor = 'arena_ready_anchor.bmp' if self.mode == 'arena' else 'survival_anchor.bmp'
        roi = (851,199,1451,449) if self.mode == 'arena' else (201,69,601,419)
        if not s.template(anchor, roi, frame)[0]:
            raise training.TrainingPending('本次修行录目标已满足，原任务页面仍待核实')
        evidence = str(s.save_raw_evidence(frame, r.account, 'training_'+self.mode+'_target', 'verified'))
        if not s.click_template(r.account, 'survival_close.bmp', (1550,60,1700,200), 20,20,
                                'training_'+self.mode+'_target_close', [anchor,'survival_close.bmp'],
                                threshold=.84,anchor_threshold=.84):
            raise training.TrainingPending('修行录目标收尾关闭按钮未确认')
        if not s.wait_until_main(12):
            raise training.TrainingPending('修行录子任务收尾尚未核实回村')
        self.request['evidence'] = evidence
        if self.mode == 'arena' and satisfied:
            saved = s.state.global_state.get('arena_requests', {}).get(r.account.name)
            if isinstance(saved, dict):
                saved.update(status='done', completion_scope='training_goal', completed_at=hs.now_iso(),
                             home_returned=True, evidence=evidence)
                s.state.save_global()
        r.record(self.mode).update(home_returned=True, status='pending', task_completed=False,
                                   training_request_satisfied=self.request['id'])
        return self.finish()

    def _target_met(self, frame):
        req = self.request
        if self.mode == 'arena':
            req['settled'] = (self.s._arena_victory_count(self.r.account)-req['base_wins']
                              if req['unit'] == 'wins' else req.get('battles_settled',0))
            tokens = self.s._arena_token_count(frame)
            if tokens is not None:
                req.update(available=tokens, quota_unit='reward_tickets', quantity_state='verified')
                if req.get('scheduled') is None:
                    req.update(scheduled=tokens if req['target_mode']=='tickets' else req['required'],
                               scheduled_unit='reward_tickets' if req['target_mode']=='tickets' else req['unit'])
            return req['target_mode'] == 'wins' and req['settled'] >= req['required']
        if req['unit'] == 'count':
            req['settled'] = req.get('battles_settled',0)
            return req['settled'] >= req['required']
        if req['unit'] != 'points':
            raise training.TrainingPending('生存目标单位未有可靠结果读数，未将挑战次数或积分当成胜场')
        score = self.s._survival_observed_score(frame)
        if score is None:
            raise training.TrainingPending('生存当前积分未唯一识别，暂停本号新挑战')
        if req.get('base_score') is None:
            req['base_score'] = score
        req['settled'] = max(0, score-req['base_score'])
        req.update(current_score=score, quantity_state='verified')
        return req['unit'] == 'points' and req['settled'] >= req['required']

    def step(self):
        s, r, req, slot = self.s, self.r, self.request, self.slot
        if not self.prepared:
            if self.mode == 'arena':
                saved = s.state.global_state.get('arena_requests', {}).get(r.account.name, {})
                actions = s.state.accounts[r.account.name].get('actions', {})
                pending = any(k.startswith('arena_casual_battle_') and v.get('status') == 'pending'
                              for k,v in actions.items() if isinstance(v, dict))
                if not pending:
                    s.prepare_additional_arena_request(r.account, req['required'], target_mode=req['target_mode'])
                elif not saved:
                    raise training.TrainingPending('原竞技场战斗缺少目标凭据，未重建请求')
                s._prepare_arena_slot(slot)
            else:
                pending = s.state.action_status(r.account.name, s._combat_action_name('survival',slot.next_index)) == 'pending'
                if not pending:r.ensure_formation_once()
                s._prepare_survival_slot(slot)
            self.prepared = True
        elif slot.running:
            frame = s.switch_account_for_combat(r.account, self.mode, running=True)
            before = slot.next_index
            if self.mode == 'arena':
                slot.observation_frame = frame
                try:s._poll_arena_slot(slot)
                finally:slot.observation_frame = None
            else:
                s._poll_survival_slot(slot, frame)
            if slot.next_index != before:
                req['battles_settled'] = req.get('battles_settled', 0)+1
                if self.mode == 'survival':s.reconcile_survival_result_confirmations(r.account)
            self.waiting = slot.running
            self.interval = hs.arena_poll_interval(slot) if self.mode == 'arena' else .4
            # Let every account advance before considering another battle.
            return False
        else:
            s.switch_account_for_combat(r.account, self.mode, running=False)
        if slot.complete:
            if s.state.module_status(r.account.name, self.mode) == 'done':
                r.record(self.mode).update(status='done', task_completed=True, home_returned=True,
                                          finished_at=hs.now_iso(), last_error='')
            return self.finish('本次竞技场结束条件已核实' if self.mode == 'arena' else '本轮生存可用挑战已结束')
        if slot.running:
            self.waiting = True
            return False
        frame = s.capture()
        if req.get('completion_scope') == 'original_continuation' and req.get('battles_settled',0) >= 1:
            req['quantity_state'] = 'requires_goal_rescan'
            self._close_partial(satisfied=False)
            req['stop_reason'] = '原未决场已结算并回村，随后重新读取修行录目标'
            return True
        if self._target_met(frame):
            return self._close_partial()
        if req['execution_day'] != local_day():
            return self._close_for_day_change()
        old_index = slot.next_index
        if self.mode == 'arena':
            s._start_arena_slot(slot)
        else:
            s._start_survival_slot(slot)
        if slot.running:
            req['started'] = req.get('started', 0)+1
            req['last_attempt'] = old_index
            if self.mode == 'survival':
                action = s._combat_action_name('survival',old_index)
                observations, key = s._survival_attempt_evidence(r.account,action)
                observed = observations.get(key,{})
                req.update(available=observed.get('remaining_before'), quota_unit='current_grade_challenges',
                           grade=observed.get('grade'))
        self.waiting = slot.running
        if slot.complete:
            return self.finish('当天任务可用次数已用完', exhausted=True)
        return False

    def _close_for_day_change(self):
        self._close_partial(satisfied=False)
        self.request.update(status='deferred', stop_reason='执行日期已改变，原场已结算；下次重新扫描当天额度')
        return True


class QuizDriver(Driver):
    def __init__(self, runner, request):
        super().__init__(runner, request)
        from hyol_quiz import run_steps
        self.originally_complete = complete(runner.record('quiz'))
        self.iterator = run_steps(runner)

    def step(self):
        self.s.switch_account(self.r.account)
        if self.originally_complete:
            self.request.update(available=0,scheduled=0,settled=0,quantity_state='verified')
            self.r.return_to_main()
            return self.finish('当天测验已完成，无剩余可执行次数',exhausted=True)
        try:
            next(self.iterator)
            self.waiting = True
            return False
        except StopIteration:
            count_goal=self.request['unit']=='count'
            self.request.update(available=0, scheduled=1 if count_goal else None,
                                settled=1 if count_goal else None,operations_completed=1,
                                quantity_state='verified' if count_goal else 'requires_goal_rescan')
            self.r.return_to_main()
            return self.finish('本次完整测验已核实',exhausted=count_goal and self.request['required']>1)


class EscortDriver(Driver):
    def __init__(self, runner, request):
        super().__init__(runner, request)
        from hyol_escort import RemainingPriorityDriver
        self.driver = RemainingPriorityDriver(runner)

    def step(self):
        from hyol_escort import poll_seconds, tick_through_departure
        rec = self.r.record('escort')
        settled = len(rec.get('legs', []))-self.request['base_completed']
        self.request['settled'] = settled
        remaining = rec.get('last_panel', {}).get('remaining')
        if type(remaining) is int:
            self.request.update(available=remaining, quantity_state='verified')
        date_changed = self.request['execution_day'] != local_day()
        if (settled >= self.request['required'] or date_changed) and not rec.get('active'):
            from hyol_navigation import return_to_leaf
            if not return_to_leaf(self.s, self.r.account):
                raise training.TrainingPending('本次押镖目标已满足，回村尚未核实')
            if date_changed:
                self.request.update(status='deferred', home_returned=True, stop_reason='原押送已恢复，跨日后重新读取当天额度')
                return True
            from hyol_escort import inspect_daily_remaining
            daily = inspect_daily_remaining(self.r)
            self.request.update(available=daily['remaining'], quota_evidence=daily['samples'], quantity_state='verified')
            return self.finish()
        done = tick_through_departure(self.driver, self.s)
        self.waiting = bool(rec.get('active'))
        self.interval = poll_seconds(rec)
        return self.finish('当天押镖次数已用完', exhausted=True) if done else False


class TreasureDriver(Driver):
    interval = 1.

    def __init__(self, runner, request):
        super().__init__(runner, request)
        from hyol_treasure import TreasureRun
        rec = runner.record('treasure')
        if complete(rec):
            archives = rec.setdefault('training_cycles', [])
            archives.append({key:deepcopy(value) for key,value in rec.items() if key != 'training_cycles'})
            history = archives
            rec.clear()
            rec.update(status='new', phase='ready', training_cycles=history,
                       training_action_suffix=request['id'])
        # Rewards, traps and transformations do not prove one completed map.
        # Keep the original finite timer unless the activity supplies a real
        # map-completion counter; then stop at the locked quantity boundary.
        rec['training_single_cycle'] = False
        if type(rec.get('verified_maps_completed')) is int:
            request.setdefault('base_maps_completed', rec['verified_maps_completed'])
        self.driver = TreasureRun(runner)

    def step(self):
        from hyol_treasure import switch_to
        rec = self.r.record('treasure')
        if rec.get('deadline_at') is None:
            self.s.switch_account(self.r.account)
        else:
            switch_to(self.r)
        count = rec.get('verified_maps_completed')
        if (type(count) is int and type(self.request.get('base_maps_completed')) is int
                and rec.get('map_completion_evidence')):
            self.request.update(settled=max(0,count-self.request['base_maps_completed']),
                                quantity_state='verified',quota_unit='maps')
            if self.request['settled'] >= self.request['required']:
                rec['verified_map_goal_reached'] = True
        done = self.driver.step()
        self.waiting = not done
        if done:
            if not rec.get('map_completion_evidence'):
                self.request.update(available=None, quantity_state='requires_goal_rescan', settled=None)
            return self.finish('本次挖宝有界周期已停止并回村，仅定向核实藏宝图目标；不将弹窗数当地图数')
        return False


class AtomicDriver(Driver):
    exclusive = True

    def step(self):
        self.s.switch_account(self.r.account)
        if self.request['execution_day'] != local_day():
            self.request.update(status='deferred', stop_reason='日期已改变，下次按当天目标与额度重新扫描')
            return True
        # These activities expose a minimum complete game/tool operation.
        task = self.request['task']
        if task == 'welfare':
            self.s.module_welfare(self.r.account)
        elif task in ('puzzle','daruma'):
            getattr(self.r, task)()
            self.r.return_to_main()
        else:
            training.execute_supported(self.r, task)
        if self.request.get('quantity_state') != 'verified':
            self.request.update(quantity_state='requires_goal_rescan',settled=None)
        exhausted = (type(self.request.get('available')) is int and type(self.request.get('settled')) is int
                     and self.request['settled'] < self.request['required'])
        return self.finish('最小完整操作已结束，按修行录复扫核实数量', exhausted=exhausted)


DRIVERS = {'arena': CombatDriver, 'survival': CombatDriver, 'ranking': RankingDriver,
           'quiz': QuizDriver, 'escort': EscortDriver, 'treasure': TreasureDriver}


class AccountPlan:
    def __init__(self, runner, *, driver_factory=None):
        self.r, self.s = runner, runner.scheduler
        self.rec = runner.record('training_record')
        self.driver_factory = driver_factory or (lambda r, req: DRIVERS.get(req['task'], AtomicDriver)(r, req))
        self.day = self.s.state.day
        self.generator = None
        self.driver = None
        self.done = False
        self.next_at = 0.
        self.last_served = 0
        self.errors = self.rec.setdefault('errors', {})
        self.rec.setdefault('jobs', [])
        self.rec.update(status='running', task_completed=False, all_goals_complete=False,
                         scheduler_version=3, home_returned=False)
        self.exhausted = self.rec.setdefault('exhausted_tasks', {})
        if self.rec.get('exhausted_day') != self.day:
            self.exhausted.clear()
            self.rec['exhausted_day'] = self.day
        active = self.rec.get('active_job')
        pending = training.unresolved_supported_tasks(runner)
        if isinstance(active, dict) and active.get('status') in ('running','waiting','pending'):
            if len(pending) > 1:
                raise training.TrainingPending('多个原子任务未决，保留原凭据等待对账')
            self.driver = self.driver_factory(runner, active)
            self.rec.update(phase='resuming_supported', current_task=active['task'])
        elif pending:
            if len(pending) > 1:
                raise training.TrainingPending('多个原子任务未决，未打开修行录')
            task = pending[0]
            if task not in self.rec.get('planned_tasks', ()):
                raise training.TrainingPending('原'+task+'尚未结算，先沿原任务恢复')
            req = self._resume_request(task)
            self.rec['active_job'] = req
            self.driver = self.driver_factory(runner, req)
        else:
            self._scan('before')
        runner.save()

    @property
    def waiting(self):
        return self.driver is not None and self.driver.waiting

    @property
    def exclusive(self):
        return self.driver is not None and self.driver.exclusive

    def _resume_request(self, task):
        child = self.r.record(task)
        req = child.get('training_goal_request', {})
        wins = req.get('wins', 1)
        saved = self.s.state.global_state.get('arena_requests', {}).get(self.r.account.name, {})
        return dict(id=uuid.uuid4().hex, task=task, execution_day=self.day, status='running',
                    required=saved.get('requested_wins', wins) if task == 'arena' else 1,
                    unit='wins' if task == 'arena' else 'count', completion_scope='original_continuation',
                    target_mode=saved.get('target_mode','wins'), base_wins=saved.get('base_wins',0),
                    base_completed=child.get('completed',0), started=0, settled=0,
                    scheduled=None, available=None, stop_reason='', quantity_state='awaiting_observation')

    def _scan(self, label):
        self.generator = training.refresh_goal_steps(self.r, label)
        self.rec.update(phase='collecting_rewards', current_task='', rewards_complete=False)

    def _scan_task(self, task):
        snapshot = self.rec.get('latest', {})
        if snapshot.get('scan_version') == 2 and snapshot.get('scan_complete') is True:
            self.generator = training.scan_task_steps(self.r, task, 'after_'+task, snapshot)
            self.rec.update(phase='checking_series_boundary',current_task=task,rewards_complete=False)
        else:
            # Old/incomplete snapshots must first obtain a complete baseline.
            self._scan('after_'+task)

    def _plan(self, snapshot):
        from hyol_task_order import load
        from hyol_training_resume import pending_prior_sources
        previous=pending_prior_sources(self.r)
        self.rec['prior_pending_sources']=previous
        for source in previous:
            self.errors[source['task']]='同账号原运行'+source['run_id']+'的'+source['action']+'未决；原凭据只读，不重新发送'
        order = load(hs.ROOT)['order']['solo']
        plan, calendar = training.daily_plan(self.r, snapshot, order, self.day, set(), self.errors)
        self.errors.pop('scan',None)
        if not self.errors:self.rec['last_error']=''
        self.rec.update(latest=snapshot, daily_plan=plan,
                        remaining=[row for row in snapshot['rows'] if not training.goal_complete(row)],
                        series=training.series_summary(snapshot['rows']))
        self.rec.setdefault('before', snapshot)
        self.rec['after'] = snapshot
        if self.day != local_day():
            self.errors['date'] = '原任务已恢复；执行日期已改变，下次重新扫描当天目标和额度'
            self._finish()
            return
        for item in plan['tasks']:
            if item['status'] != 'ready':continue
            if item['task'] in self.exhausted:
                item.update(status='deferred',stop_reason=self.exhausted[item['task']])
                continue
            policy = arena_policy(self.day,selected_arena_policy(self.r))['mode'] if item['task'] == 'arena' else 'goal'
            key = job_key(item['task'], item['goals'], self.day, policy, self.r.account.name)
            previous = next((job for job in self.rec['jobs'] if job['id'] == key), None)
            if previous:
                item.update(status='pending', stop_reason=previous.get('stop_reason','本次目标已处理，等待数量复查'))
                continue
            if item.get('unit') == 'mixed' or not item.get('required'):
                item.update(status='pending', stop_reason='目标单位不一致或缺口未核实')
                continue
            req = deepcopy(item)
            child = self.r.record(item['task'])
            req.update(id=key, account=self.r.account.name, execution_day=self.day, status='running', base_completed=child.get('completed',0),
                       base_wins=self.s._arena_victory_count(self.r.account) if item['task'] == 'arena' else 0,
                       target_mode=policy, started_at=hs.now_iso())
            if item['task'] == 'escort':req['base_completed'] = len(child.get('legs', []))
            self.rec['active_job'] = req
            self.rec['planned_tasks'] = list(dict.fromkeys([*self.rec.get('planned_tasks', []), item['task']]))
            self.rec.update(phase='executing', current_task=item['task'])
            self.driver = self.driver_factory(self.r, req)
            self.r.event('training_plan_detected', tasks=[item['task']], plan=plan,
                         detail='本号缺口与额度独立计算，等待期间轮转其他账号')
            self.r.save()
            return
        self._finish()

    def _finish(self):
        if self.rec.get('latest',{}).get('scope') == 'task' and 'date' not in self.errors:
            # Scoped updates cannot certify that another tier/series was not
            # unlocked elsewhere. Exactly one full scan closes this batch.
            self._scan('final')
            return
        remaining = self.rec.get('remaining', [])
        verified = not remaining and not self.errors
        supported_remaining = [row for row in remaining if row.get('task')]
        self.rec.update(status='done' if verified else 'pending', phase='verified' if verified else 'pending',
                        task_completed=verified, all_goals_complete=verified,
                        supported_tasks_complete=not self.errors and not supported_remaining,
                        current_task='', home_returned=True, finished_at=hs.now_iso())
        self.r.save()
        self.done = True
        self.r.event('training_goals_rechecked', remaining=len(remaining), all_goals_complete=verified)

    def step(self):
        self.s.check_stop()
        if self.generator is not None:
            self.s.switch_account(self.r.account)
            try:next(self.generator)
            except StopIteration as result:
                self.generator = None
                self._plan(result.value)
        elif self.driver is not None:
            request = self.rec['active_job']
            resuming = self.rec.get('phase') == 'resuming_supported'
            with task_context(self.r, request):
                finished = self.driver.step()
            if resuming:
                # Clear stale stop text only after the original driver has
                # successfully observed its page. Keep the old diagnostic and
                # every action/budget; this is not a completion receipt.
                previous = dict(at=hs.now_iso(), task=request['task'],
                                stop_reason=request.get('stop_reason',''),
                                error=self.errors.get(request['task'],self.rec.get('last_error','')))
                self.rec.setdefault('resume_observations',[]).append(previous)
                self.rec.update(phase='executing',current_task=request['task'])
                self.errors.pop(request['task'],None)
                if not self.errors:self.rec['last_error']=''
                if not finished:request['stop_reason']=''
                self.r.event('training_task_resumed',task=request['task'],
                             detail='原任务已恢复观察，继续核实结果；原场与剩余额度保留')
            request['status'] = request.get('status') if finished else ('waiting' if self.driver.waiting else 'running')
            self.r.save()
            if finished:
                self.rec['jobs'].append(deepcopy(request))
                self.rec.pop('active_job', None)
                task = request['task']
                self.errors.pop(task,None)
                quota_zero=(request.get('available') == 0 and (
                    task in ('ranking','quiz','escort')
                    or task == 'arena' and request.get('target_mode') == 'tickets'))
                if request.get('status') in ('exhausted','deferred') or quota_zero:
                    self.exhausted[task]=request.get('stop_reason') or '本号可用次数已用完，最终目标保留待续'
                self.driver = None
                self._scan_task(task)
        self.next_at = time.monotonic()+(self.driver.interval if self.driver else .05)

    def pending(self, error):
        self.errors[self.rec.get('current_task') or 'scan'] = str(error)
        request = self.rec.get('active_job')
        if request:
            request.update(status='pending', stop_reason=str(error))
        self.rec.update(status='pending', task_completed=False, last_error=str(error), phase='pending',
                        home_returned=False)
        self.r.save()
        self.done = True
        self.r.event('training_account_pending', task=self.rec.get('current_task'), detail=str(error))


def run_plans(runners, *, owner=None, driver_factory=None):
    if not runners:return
    scheduler = runners[0].scheduler
    plans = []
    for runner in runners:
        try:plans.append(AccountPlan(runner, driver_factory=driver_factory))
        except hs.PendingError as exc:
            rec = runner.record('training_record')
            rec.update(status='pending', task_completed=False, last_error=str(exc))
            runner.save()
            runner.event('training_account_pending', detail=str(exc))
    sequence = 0
    waiting_turns = 0
    while any(not plan.done for plan in plans):
        scheduler.check_stop()
        if scheduler.check_pause_boundary():
            raise training.TrainingPending('已暂停修行录轮转，原任务凭据已保存')
        now = time.monotonic()
        active = [plan for plan in plans if not plan.done]
        waiting = [plan for plan in active if plan.waiting]
        ready = [plan for plan in active if plan.next_at <= now
                 and not (plan.exclusive and waiting)]
        if not ready:
            scheduler.wait_interruptible(min(.4,max(.01,min(plan.next_at for plan in active)-now)))
            continue
        # Bound the priority burst so a continuously due battle cannot starve
        # the scans/starts that make the other accounts concurrent.
        due_waiting = [plan for plan in ready if plan.waiting]
        other = [plan for plan in ready if not plan.waiting]
        if due_waiting and (waiting_turns < 2 or not other):
            plan = min(due_waiting,key=lambda p:(p.next_at,p.last_served))
            waiting_turns += 1
        else:
            plan = min(other or ready,key=lambda p:(p.last_served,p.next_at))
            waiting_turns = 0
        sequence += 1
        plan.last_served = sequence
        scheduler.navigator = plan.r
        try:
            plan.step()
        except hs.AccountOffline as exc:
            plan.pending(exc)
            if owner:owner.skip_disconnected(plan.r.account,'training_record')
        except hs.BattleStalled as exc:
            plan.pending(exc)
            if owner:owner.skip_stalled(plan.r.account,'training_record',exc)
        except hs.SafetyError:
            # Global focus/window/F12 safety failures stop the shared foreground owner.
            raise
        except hs.PendingError as exc:
            plan.pending(exc)
        finally:
            scheduler.navigator = None
            if owner:owner.save_progress('training_record')
    return plans


def run_single(runner):
    run_plans([runner])
    rec = runner.record('training_record')
    if rec.get('all_goals_complete') is not True:
        raise training.TrainingPending(rec.get('last_error') or
            f"修行录本号可执行计划已处理，{len(rec.get('remaining', []))}项目标保留待续")


def run_multi(owner):
    runners = []
    for configured in tuple(owner.scheduler.accounts):
        account = owner.scheduler.by_name.get(configured.name, configured)
        if (account.name not in owner.startup_returned or account.name in owner.manual_deferred
                or account.name in owner.excluded or account.name in owner.disconnected):
            owner.note_skipped(account,'training_record')
            continue
        runner=owner.runner_for(account)
        parent=runner.record('training_record')
        if parent.get('active_job') or training.unresolved_supported_tasks(runner):
            # The child's own mode-aware guard can observe a hidden identity
            # during its original battle; the generic idle check cannot.
            runners.append(runner)
        elif owner.check_account(account):runners.append(runner)
        else:owner.note_skipped(account,'training_record')
    run_plans(runners,owner=owner)
