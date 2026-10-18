"""One-account solo acceptance with current preflight and measured safe stages.

Unrelated pending actions stay untouched. Combat-page observations never start
battles. Explicit quiz/ranking probes use the existing independent runners;
no probe resets counters, logs in, buys items, or starts managed mode.
"""
import argparse
from copy import deepcopy
import functools
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_daily_tasks import DailyRunner
from hyol_multi_solo import MultiSoloRunner
from hyol_diagnostics import manual_pause_status
from hyol_navigation import LEAF_NAME_ROI, move_cursor_clear
from tools.preflight_live import run as preflight


TASK_CHOICES = ('mail', 'welfare', 'formation', 'quiz', 'ranking', 'treasure', 'combat_pages', 'treasure_inventory')


def wait_arena_page(s, account, seconds=60):
    """A title can paint before controls. Observe that page without any input."""
    deadline = time.monotonic()+seconds
    while True:
        s.check_stop()
        frame = s.capture()
        if s._arena_victory_visible(frame) or s._arena_defeat_visible(frame) or s._arena_waiting_battle_visible(frame):
            raise hs.PendingError('竞技场出现原结果或匹配，保留原战，不关闭或开始新场')
        if s._combat_ready_visible('arena', frame):
            return frame
        if time.monotonic() >= deadline:
            raise hs.PendingError('竞技场页面60秒内未完整加载，只保存观察，不关闭或开战')
        s.wait_interruptible(.35)


def observe_pages(r, task, observations=None):
    """Read current anchored pages and close them; never settle old actions."""
    s, account = r.scheduler, r.account
    if observations is None:
        observations = []
    pages = ('arena', 'survival') if task == 'combat_pages' else ('treasure',)
    for page in pages:
        if page == 'arena':
            s.ensure_activity_toolbar(account, 'arena_toolbar_core.bmp', (1201,69,1501,169))
            r.click('arena_toolbar_core.bmp', (1201,69,1501,169), 22,22,
                    'solo_observe_arena_entry', ['anchor_top_menu.bmp'])
            r.wait('arena_ready_anchor.bmp', (851,199,1451,449))
            wait_arena_page(s, account)
        elif page == 'survival':
            hs.Scheduler._open_verified_survival(s, account)
        else:
            __import__('hyol_treasure').open_page(r)
        samples = []
        for index in range(3):
            frame = s.capture()
            if page == 'arena':
                if (s._arena_victory_visible(frame) or s._arena_defeat_visible(frame)
                        or s._arena_waiting_battle_visible(frame)
                        or s.template('arena_loading_anchor.bmp', hs.ARENA_LOADING_ROI, frame)[0]):
                    raise hs.PendingError('竞技场出现原结果或匹配/加载，只观察，不关闭或开始新场')
                if not s._combat_ready_visible('arena', frame):
                    raise hs.PendingError('竞技场具体准备页未确认，不点击其关闭控件')
                row = {'ready_page': True,
                    'ranked_button': s.template('arena_ranked_button_current.bmp', (521,719,851,919), frame)[0],
                    'casual_button': s.template('arena_casual_button_current.bmp', (760,719,1050,919), frame)[0],
                    'explicit_new_victories': 0, 'numeric_remaining': None}
            elif page == 'survival':
                if not s.template('survival_anchor.bmp', (201,69,601,419), frame)[0]:
                    raise hs.PendingError('生存页面标题未确认，未猜测次数')
                candidates = hs.grade_candidates(frame, s.templates, hs.ASSETS)
                try:
                    if {item['grade'] for item in candidates} != {item[0] for item in hs.SURVIVAL_GRADES}:
                        raise ValueError('等级识别不完整')
                    highest = hs.choose_grade(candidates)
                    error = None
                except ValueError as exc:
                    highest, error = None, str(exc)
                row = {'full_score_220': s._survival_full_score_visible(frame),
                       'score_twenty': s._survival_score_twenty_visible(frame),
                       'grade_counters': candidates, 'highest_available': highest,
                       'counter_error': error}
            else:
                t = __import__('hyol_treasure')
                if not r.visible('treasure_page_anchor.bmp', t.PAGE_ROI, frame, .96):
                    raise hs.PendingError('藏宝页未确认，不猜测地图库存')
                row = {'existing_map_visible': r.visible('treasure_map_item.bmp', t.INVENTORY_ROI, frame, .96),
                       'auto_dig_visible': r.visible('treasure_auto_dig.bmp', t.AUTO_ROI, frame, .96),
                       'maps_consumed': 0, 'timer_started': False}
            row['evidence'] = str(s.save_raw_evidence(frame, account, 'solo_observe_'+page+'_'+str(index+1), 'observed'))
            samples.append(row)
            if index < 2:
                s.wait_interruptible(.35)
        observations.append({'page': page, 'samples': samples, 'fresh_task_completed': False})
        if page == 'arena':
            r.click('survival_close.bmp', (1550,60,1700,200),20,20,
                    'solo_observe_arena_close', ['arena_ready_anchor.bmp', 'survival_close.bmp'],
                    threshold=.84, anchor_threshold=.84)
            if not s.wait_until_main(12):
                raise hs.PendingError('竞技场观察后关页未确认主界面')
        elif page == 'survival':
            s.close_survival(account)
        else:
            __import__('hyol_treasure').close_page(r)
            if not s.wait_until_main(12):
                raise hs.PendingError('藏宝页观察后关页未确认主界面')
    return observations


def measure_scheduler(s):
    metrics = {}
    original_calls = []
    calls = [(s, name, name) for name in ('capture', 'template', 'content_guard', 'click_template',
                 'wait_interruptible', 'save_raw_evidence', 'main_ready')]
    calls.extend(((s.templates, 'match', 'template_store_match'), (hs, 'ncc_map', 'ncc_correlations')))
    for owner, attribute, name in calls:
        original = getattr(owner, attribute)
        original_calls.append((owner, attribute, original))
        @functools.wraps(original)
        def measured(*args, _name=name, _call=original, **kwargs):
            started = time.perf_counter()
            try:
                return _call(*args, **kwargs)
            finally:
                row = metrics.setdefault(_name, {'calls': 0, 'seconds': 0.0})
                row['calls'] += 1
                row['seconds'] += time.perf_counter() - started
        setattr(owner, attribute, measured)
    def restore():
        for owner, attribute, original in reversed(original_calls):
            setattr(owner, attribute, original)
    return metrics, restore


def run(task, expected_account):
    check = preflight()
    result = {'preflight': check, 'task': task, 'expected_account': expected_account,
              'status': 'blocked', 'content_clicks': 0, 'phases': [],
              'source_version': __import__('hyol_daily_tasks').VERSION, 'run_id': check.get('run_id'),
              'source_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in hs.ROOT.glob('hyol*.py')}}
    if not (check.get('status') == 'ready' and check.get('healthy') is True
            and check.get('main_ready') is True and check.get('content_clicks') == 0):
        result['reason'] = check.get('reason') or '预检未同时满足ready/healthy/main_ready/零输入'
        path = hs.LOGS/f'solo_{task}_acceptance_{check["run_id"]}.json'
        result['report_path'] = str(path)
        hs.atomic_json(path, result)
        return result
    multi = MultiSoloRunner(('mail', 'welfare'), managed_enabled=False)
    s = multi.scheduler
    result.update(run_id=s.diagnostics.identifier, source_version=__import__('hyol_daily_tasks').VERSION)
    runner = DailyRunner.for_bound_account(s, s.accounts[0], (task,), managed_enabled=False)
    runner.account = None
    s.config['AllowMarketPurchase'] = '0'
    s.config['AutoForeground'] = '0'
    s.config['AutoRefreshWhiteTab'] = '0'
    s.auto_foreground = False
    lease = hs.ForegroundRuntimeLease()
    owned = False
    before = {}
    allowed_prefixes = ('mail_', 'welfare_') if task in ('mail', 'welfare') else ('daily_'+task+'_',)
    metrics, restore_metrics = measure_scheduler(s)
    started = time.perf_counter()
    def phase(name, action):
        stamp = time.perf_counter()
        try:
            return action()
        finally:
            result['phases'].append({'name': name, 'seconds': time.perf_counter() - stamp})
    try:
        if manual_pause_status(hs.ROOT)['paused']:
            raise hs.PendingError('人工暂停仍在')
        owned = lease.acquire()
        if not owned:
            raise hs.PendingError('其他线程持有前台租约')
        if manual_pause_status(hs.ROOT)['paused']:
            raise hs.PendingError('取得租约后检测到人工暂停')
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        s.no_content_clicks = True
        phase('discover_tabs', multi.discover_tabs)
        phase('joint_identity_binding', multi.bind_accounts)
        account = s.by_name.get(expected_account)
        if account is None:
            raise hs.PendingError('指定账号当前未实名识别，未发送内容输入')
        phase('select_account', lambda: s.switch_account(account))
        for sample in range(2):
            frame = s.capture()
            if not s.verify_identity(account, frame)[0]:
                raise hs.PendingError('指定账号独立身份复核尚未确认')
            if sample == 0:
                s.wait_interruptible(3)
        runner = DailyRunner.for_bound_account(s, account, (task,), managed_enabled=False)
        s.navigator = runner
        s.no_content_clicks = False
        account = runner.account
        result.update(run_id=s.diagnostics.identifier, account=account.name,
                      source_version=__import__('hyol_daily_tasks').VERSION,
                      managed_enabled=False, purchase_enabled=False)
        before = deepcopy(s.state.accounts[account.name].get('actions', {}))
        result['original_pending_actions'] = [key for key, row in before.items()
            if isinstance(row, dict) and row.get('status') == 'pending']
        frame = s.capture()
        result['entrance_evidence'] = str(s.save_raw_evidence(frame, account, 'solo_'+task+'_entrance', 'observed'))
        nav = __import__('hyol_navigation')
        if nav.hot_spring_visible(s, frame) or s.template('home_leave.bmp', nav.HOME_LEAVE_ROI, frame)[0]:
            phase('recover_known_home', lambda: nav.return_to_leaf(s, account, allow_pending_navigation=True))
            frame = s.capture()
        known_welfare_panel = (s.template('welfare_title_current.bmp', (1120,230,1320,380), frame, threshold=.98)[0]
            and s.template('welfare_close_current.bmp', (1320,250,1450,350), frame, threshold=.98)[0])
        if known_welfare_panel:
            phase('close_known_welfare_overlay', lambda: s.prepare_main(account, clear_notifications=False))
            frame = s.capture()
        known_managed_panel = (s.template('managed_settings_title_current.bmp', (750,190,1170,290), frame, threshold=.98)[0]
            and s.template('managed_settings_close_current.bmp', (1320,230,1450,330), frame, threshold=.98)[0])
        if not s.main_ready(frame) and not known_managed_panel:
            raise hs.PendingError('当前主界面尚未确认，保留旧结果')
        phase('exit_current_managed', lambda: s.exit_managed_for_market(account))
        frame = s.capture()
        if s.managed_active_score(frame) >= .70:
            raise hs.PendingError('托管仍在运行，不开始独立任务')
        if not s.main_ready(frame):
            raise hs.PendingError('退出托管后主界面未确认')
        if task in ('ranking', 'quiz'):
            pending = [key for key, row in before.items() if key.startswith('daily_'+task+'_')
                       and isinstance(row, dict) and row.get('status') == 'pending']
            audited_ranking_result = (task == 'ranking'
                and __import__('hyol_ranking').sent_result_can_reopen(runner, runner.record(task)))
            if (pending or runner.record(task).get('phase', 'ready') != 'ready') and not audited_ranking_result:
                raise hs.PendingError('此独立任务存在原未决操作，不新开：' + ', '.join(pending))
        if task == 'treasure' and runner.record(task).get('deadline_at') is None:
            pending = [key for key, row in before.items() if key.startswith(('daily_treasure_', 'treasure_'))
                       and isinstance(row, dict) and row.get('status') == 'pending']
            if pending:
                raise hs.PendingError('原挖宝未决但缺少截止时间，禁止消耗新地图：' + ', '.join(pending))
        if task in ('mail', 'welfare'):
            phase(task, lambda: getattr(s, 'module_'+task)(account))
        elif task in ('quiz', 'ranking', 'treasure'):
            runner.current = task
            initial_completed = runner.record(task).get('completed', 0)
            runner.record(task).update(status='running', last_error='')
            if task != 'treasure' or runner.record(task).get('deadline_at') is None:
                runner.record(task)['started_at'] = hs.now_iso()
            runner.save()
            phase(task, lambda: getattr(runner, task)())
            result['new_completed_units'] = max(0, runner.record(task).get('completed',0)-initial_completed)
            if task == 'ranking':
                result['remaining'] = runner.record(task).get('remaining')
                result['zero_verified'] = runner.record(task).get('zero_verified')
            if task == 'treasure':
                result['treasure_run'] = deepcopy(runner.record(task))
        elif task in ('combat_pages', 'treasure_inventory'):
            result['observations'] = []
            phase(task, lambda: observe_pages(runner, task, result['observations']))
        else:
            phase('formation', runner.verify_main_formation)
        move_cursor_clear(s)
        samples = []
        for i in range(3):
            frame = s.capture()
            identity = s.verify_identity(account, frame)[0]
            main = s.main_ready(frame)
            leaf = s.template('leaf_village_name_current.bmp', LEAF_NAME_ROI, frame, threshold=.92)[0]
            evidence = s.save_raw_evidence(frame, account, 'solo_'+task+'_home_'+str(i+1), 'verified' if identity and main and leaf else 'pending')
            samples.append({'identity': identity, 'main_ready': main, 'leaf_visible': leaf, 'evidence': str(evidence)})
            if i < 2:
                s.wait_interruptible(.35)
        result['home_samples'] = samples
        if not all(row['identity'] and row['main_ready'] and row['leaf_visible'] for row in samples):
            raise hs.PendingError('独立任务已返回主界面，但木叶或身份尚未三帧确认')
        observation_only = task in ('combat_pages', 'treasure_inventory')
        exhausted_only = task == 'ranking' and result.get('new_completed_units') == 0 and result.get('zero_verified') is True
        result.update(status='observed' if observation_only else 'exhausted_observed' if exhausted_only else 'live_pass', home_returned=True,
                      task_completed=not observation_only, final_managed='deferred_until_all_selected_tasks_complete')
        result['fresh_task_execution'] = not (observation_only or exhausted_only)
        if task in ('mail', 'welfare', 'quiz', 'ranking', 'treasure', 'formation'):
            runner.record(task).update(status='done', task_completed=True, home_returned=True,
                finished_at=hs.now_iso(), last_error='', acceptance_run=s.diagnostics.identifier)
            runner.save()
    except KeyboardInterrupt:
        result.update(status='manual_pause', reason='F12')
    except hs.AccountOffline as exc:
        result.update(status='offline_skipped', reason=str(exc), evidence=exc.evidence)
    except Exception as exc:
        result.update(status='pending', reason=f'{type(exc).__name__}: {exc}')
        if runner.account and runner.current == task:
            runner.record(task).update(status='pending', last_error=result['reason'])
            runner.save()
        frame = getattr(s, '_last_frame', None)
        if frame is not None:
            evidence_account = runner.account or hs.Account(0, 'identity_unverified', 0)
            result['pending_evidence'] = str(s.save_raw_evidence(frame, evidence_account, 'solo_'+task+'_pending', 'pending'))
    finally:
        s.stop.set()
        restore_metrics()
        if owned:
            lease.release()
        after = s.state.accounts.get(runner.account.name, {}).get('actions', {}) if runner.account else {}
        unrelated = {key: row for key, row in before.items()
                     if not key.startswith(allowed_prefixes)}
        result.update(content_clicks=s.content_click_count, elapsed_seconds=time.perf_counter()-started,
            metrics=metrics, unrelated_original_actions_preserved=all(after.get(key) == row for key, row in unrelated.items()))
        if runner.account:
            result['task_records'] = deepcopy(runner.records)
        if result['unrelated_original_actions_preserved'] is not True:
            result.update(status='pending', reason='其他任务原动作发生变化，保持待审计')
        path = hs.LOGS/f'solo_{task}_acceptance_{s.diagnostics.identifier}.json'
        hs.atomic_json(path, result)
        result['report_path'] = str(path)
    return result


def select_main_readonly():
    """Move only browser chrome to an independently verified main-page account."""
    multi = MultiSoloRunner(('mail',), managed_enabled=False)
    s = multi.scheduler
    s.no_content_clicks = True
    s.auto_foreground = False
    s.config['AutoRefreshWhiteTab'] = '0'
    lease = hs.ForegroundRuntimeLease()
    owned = False
    result = {'status': 'pending', 'run_id': s.diagnostics.identifier}
    try:
        if manual_pause_status(hs.ROOT)['paused']:
            raise hs.PendingError('人工暂停仍在')
        owned = lease.acquire()
        if not owned:
            raise hs.PendingError('前台租约已占用')
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        multi.discover_tabs()
        multi.bind_accounts()
        for account in tuple(s.accounts):
            frame = s.switch_account(account)
            if s.main_ready(frame) and s.verify_identity(account, frame)[0]:
                result.update(status='main_observed', account=account.name,
                    evidence=str(s.save_raw_evidence(frame, account, 'solo_preflight_main_selection', 'readonly')))
                break
    except KeyboardInterrupt:
        result.update(status='manual_pause', reason='F12')
    except Exception as exc:
        result.update(reason=f'{type(exc).__name__}: {exc}')
    finally:
        s.stop.set()
        if owned:
            lease.release()
        result['content_clicks'] = s.content_click_count
        hs.atomic_json(hs.LOGS/f'solo_main_selection_{s.diagnostics.identifier}.json', result)
    return result


def run_batch(accounts, tasks):
    """Sequential independent probes; completed tickets are observed, never rerun."""
    report = {'status': 'partial', 'started_at': hs.now_iso(), 'accounts': [],
              'final_managed': 'pending_original_combat_results', 'multiplayer_started': False}
    report_path = hs.LOGS/('solo_independent_batch_'+'_'.join(tasks)+'_'+__import__('hyol_diagnostics').run_id()+'.json')
    report['report_path'] = str(report_path)
    for name in accounts:
        for task in tasks:
            if manual_pause_status(hs.ROOT)['paused']:
                report.update(status='manual_pause', reason='人工暂停仍在')
                break
            # Today's verified task ledger is authoritative. This avoids
            # claiming a second fresh loop merely for version verification.
            ledger_path = hs.STATE_DIR/hs.date.today().isoformat()/(hs.slug(name)+'_tasks.json')
            ledger = __import__('hyol_checkpoints').read_checkpoint(ledger_path)
            if isinstance(ledger.get('accounts'), list) and ledger.get('report_path'):
                raise hs.PendingError('任务账本含批次报告，需要保全并恢复：' + str(ledger_path))
            if __import__('hyol_checkpoints').complete(ledger.get(task, {})):
                report['accounts'].append({'account': name, 'task': task, 'status': 'already_done', 'fresh_live_acceptance': False, 'evidence_run': ledger[task].get('acceptance_run')})
                continue
            row = run(task, name)
            if row.get('status') == 'blocked' and row.get('preflight', {}).get('status') == 'ready' and row.get('preflight', {}).get('healthy') is True and row.get('preflight', {}).get('main_ready') is False:
                selection = select_main_readonly()
                if selection.get('status') == 'main_observed':
                    row = run(task, name)
                row['readonly_main_selection'] = selection
            report['accounts'].append(row)
            hs.atomic_json(report_path, report)
            print(json.dumps({'account': name, 'task': task, 'status': row.get('status'),
                'reason': row.get('reason'), 'report': row.get('report_path')}, ensure_ascii=False), flush=True)
            if row.get('status') in ('manual_pause', 'offline_skipped'):
                break
        if report['status'] == 'manual_pause' or manual_pause_status(hs.ROOT)['paused']:
            report['status'] = 'manual_pause'
            break
    report['finished_at'] = hs.now_iso()
    hs.atomic_json(report_path, report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', choices=TASK_CHOICES)
    parser.add_argument('--account')
    parser.add_argument('--select-main-readonly', action='store_true')
    parser.add_argument('--batch-accounts')
    parser.add_argument('--batch-tasks', default='mail,welfare')
    args = parser.parse_args()
    if not args.select_main_readonly and not args.batch_accounts and (not args.task or not args.account):
        parser.error('An independent task requires --task and --account')
    if args.batch_accounts:
        accounts, tasks = hs.parse_csv(args.batch_accounts), hs.parse_csv(args.batch_tasks)
        if not accounts or not tasks or any(task not in TASK_CHOICES for task in tasks):
            parser.error('Unknown independent task or page observation')
        result = run_batch(accounts, tasks)
    else:
        result = select_main_readonly() if args.select_main_readonly else run(args.task, args.account)
    print(json.dumps({key:result[key] for key in ('status','report_path','run_id','reason') if key in result}, ensure_ascii=False))
    raise SystemExit(0 if result['status'] in ('live_pass', 'main_observed', 'observed', 'exhausted_observed') else 2)
