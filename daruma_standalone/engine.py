"""Finite Daruma workflow, isolated from DailyRunner and the weekend scheduler."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading
import time

import numpy as np
from PIL import Image

from .journal import Journal, atomic_json
from .runtime import Desktop, RUNTIME
from .trace import REWARDS, crop_roi, save_trace, measured_latency
from .vision import (BAR, BOTTOM, CLOSE, START, TRACE,
                     Pending, Stopped, bottom_bounds, dispatch_at, estimate_motion,
                     f1, flame_columns, judgment_from_frames, layer_count, perfect_zone, binding_matches,
                     moving_column)


class DarumaRunner:
    def __init__(self, hwnd, *, stop=None, root=RUNTIME, sink=None, desktop_factory=Desktop,
                 strike_limit=None, expected_binding=None, round_limit=None, quantity_request_id=None):
        self.hwnd, self.root = hwnd, Path(root)
        self.stop = stop if stop is not None else threading.Event()
        self.sink = sink
        self.desktop_factory = desktop_factory
        self.strike_limit = strike_limit
        self.expected_binding = expected_binding
        self.round_limit = round_limit
        self.quantity_request_id = quantity_request_id
        self.quantity_satisfied = False
        self.strikes_sent = 0
        self.journal = None
        self.desktop = None
        self.verified_frame = None
        self.motion_samples = []

    def emit(self, event, **fields):
        if self.journal:
            self.journal.event(event, **fields)
        elif self.sink:
            self.sink(dict(event=event, **fields))

    def progress(self, phase, **fields):
        self.journal.save(phase=phase, **fields)
        self.emit('progress', **{key: self.journal.data.get(key) for key in (
            'phase', 'layers', 'chance', 'remaining', 'round', 'retries', 'status',
            'remaining_estimate', 'reward_needs_check')})

    def run(self, *, readonly=False):
        desktop = self.desktop_factory(self.hwnd, self.stop, root=self.root, readonly=readonly)
        self.desktop = desktop
        try:
            with desktop:
                frame = desktop.capture()
                page = desktop.vision.page(frame)
                if readonly:
                    return self.inspect_frame(desktop, frame)
                if self.expected_binding is not None and not binding_matches(self.expected_binding, desktop.binding):
                    raise Pending('当前账号与本次指定的测试账号不符，未开始挑战')
                self.journal = Journal(self.root, desktop.binding, self.sink)
                desktop.journal = self.journal
                saved = self.journal.data['binding']
                if (saved['session'] != desktop.binding['session']
                        or f1(np.asarray(saved['identity'], bool),
                              np.asarray(desktop.binding['identity'], bool)) < .94):
                    raise Pending('独立断点与当前角色不一致；未导入旧主脚本记录')
                day = datetime.now().date().isoformat()
                if self.journal.data.get('day', day) != day:
                    raise Pending('独立断点已经跨日，先核实原局；不自动重开')
                if self.journal.data['status'] == 'new' and page not in ('ready', 'playing', 'over', 'weekend'):
                    raise Pending('请先打开指定账号的达摩挑战页或周末福利达摩页')
                self.journal.save(status='running', day=day, reason='')
                if self.round_limit is not None:
                    if type(self.round_limit) is not int or self.round_limit < 1 or not self.quantity_request_id:
                        raise Pending('达摩数量请求无效，未消耗新机会')
                    requests = dict(self.journal.data.get('quantity_requests', {}))
                    requests.setdefault(self.quantity_request_id, dict(
                        base_round=max(0,int(self.journal.data.get('round',0))
                                       -int(bool(self.journal.data.get('round_active')))), limit=self.round_limit))
                    self.journal.save(quantity_requests=requests)
                if page == 'ready' and not self.journal.data.get('intent'):
                    self.journal.save(reward_checked_for_start=False)
                self.journal.evidence(frame, 'bound')
                self.emit('bound', hwnd=self.hwnd, tab_x=desktop.binding['tab_x'],
                          binding_key=desktop.binding['key'], checkpoint=str(self.journal.path))
                self.drive()
                if self.quantity_satisfied:
                    return dict(status='request_satisfied', reason='本次修行录完整局数已满足，日常剩余奖励保持实读',
                                checkpoint=str(self.journal.path), content_clicks=desktop.content_clicks,
                                quantity_request_id=self.quantity_request_id)
                return dict(status='done', reason='剩余奖励已正向核实为0，停留活动界面',
                            checkpoint=str(self.journal.path), content_clicks=desktop.content_clicks)
        except (Pending, KeyboardInterrupt) as exc:
            status = 'stopped' if isinstance(exc, (Stopped, KeyboardInterrupt)) else 'pending'
            return self.finish_failure(desktop, status, exc)
        except Exception as exc:
            return self.finish_failure(desktop, 'pending', f'{type(exc).__name__}: {exc}')

    def finish_failure(self, desktop, status, reason):
        report = desktop.failure_report(reason)
        if self.journal:
            self.journal.save(status=status, reason=str(reason), last_evidence=report.get('evidence'),
                              content_clicks=desktop.content_clicks)
            report['checkpoint'] = str(self.journal.path)
        report.update(status=status, reason=str(reason))
        atomic_json(Path(report['report_path']), {**report, 'binding': desktop.binding})
        self.emit('finished', **report)
        return report

    def inspect_frame(self, desktop, frame):
        page = desktop.vision.page(frame)
        report = desktop.failure_report('只读识别，无游戏输入')
        report.update(status='observed', page=page, layers=None, chance=None, remaining=None)
        if page in ('ready', 'playing', 'over'):
            report.update(layers=layer_count(frame), chance=desktop.vision.digit(frame, 'chance'))
            if page == 'playing':
                report['perfect_zone'] = perfect_zone(np.asarray(frame.crop(BAR)))
        elif page == 'weekend':
            report['remaining'] = desktop.vision.digit(frame, 'reward')
        atomic_json(Path(report['report_path']), {**report, 'binding': desktop.binding})
        self.emit('observation', **report)
        return report

    def stable_game(self, timeout=4, *, wait_for_change_from=None):
        deadline = time.monotonic()+timeout
        previous = None
        last_stable = None
        last_reason = '达摩页面未稳定'
        while time.monotonic() < deadline:
            frame = self.desktop.capture()
            page = self.desktop.vision.page(frame)
            if page not in ('ready', 'playing', 'over'):
                raise Pending(f'当前页面不是达摩挑战：{page}')
            try:
                observation = (page, layer_count(frame), self.desktop.vision.digit(frame, 'chance'))
            except Pending as exc:
                last_reason = str(exc)
                previous = None
                last_stable = None
                self.desktop.wait(.15)
                continue
            if previous == observation:
                last_stable = observation, frame
                if (wait_for_change_from is None or observation[1] != wait_for_change_from
                        or page == 'over'):
                    return observation, frame
            else:
                last_stable = None
            previous = observation
            self.desktop.wait(.06 if wait_for_change_from is not None else .12)
        if last_stable is not None and wait_for_change_from is not None:
            return last_stable
        raise Pending(last_reason)

    def playing_frame(self, layers, *, deadline, frame=None):
        """Wait through occlusion, but never accept a known page/count change."""
        obscured = 0
        while True:
            frame = frame if frame is not None else self.desktop.capture()
            if self.desktop.vision.page(frame) != 'playing':
                raise Pending('敲击前页面变化；未发送新的敲击')
            try:
                count = layer_count(frame)
            except Pending:
                obscured += 1
                if time.perf_counter() >= deadline:
                    raise Pending('奖励栏持续遮挡，7秒内无法核实；未发送新的敲击')
                self.desktop.wait(.10)
                frame = None
                continue
            if count != layers:
                raise Pending('敲击前层数变化；未发送新的敲击')
            if obscured:
                self.emit('occlusion_cleared', frames=obscured)
            return frame, bool(obscured)

    def stable_reward(self):
        previous = None
        deadline = time.monotonic()+4
        while time.monotonic() < deadline:
            frame = self.desktop.capture()
            if self.desktop.vision.page(frame) != 'weekend':
                raise Pending('剩余奖励只能从已确认的周末福利页读取')
            try:
                count = self.desktop.vision.digit(frame, 'reward')
            except Pending:
                previous = None
                self.desktop.wait(.18)
                continue
            if count == 0 and not self.desktop.vision.reward_completed(frame):
                previous = None
                self.desktop.wait(.20)
                continue
            if previous == count:
                evidence = self.journal.evidence(frame, 'reward_'+str(count))
                self.progress('reward_verified', remaining=count, remaining_estimate=count,
                              reward_needs_check=False, reward_check_round=int(self.journal.data['round']),
                              reward_evidence=evidence)
                return count
            previous = count
            self.desktop.wait(.20)
        raise Pending('剩余奖励数字不明确，不能推断为0')

    def wait_page(self, expected, seconds=8):
        deadline = time.monotonic()+seconds
        last = None
        while time.monotonic() < deadline:
            frame = self.desktop.capture()
            page = self.desktop.vision.page(frame)
            if page in expected and page == last:
                return page, frame
            last = page
            self.desktop.wait(.16)
        raise Pending(f'动作后未确认预期页面：{", ".join(expected)}')

    def click_template(self, name, roi, offset, allowed, expected, kind):
        frame = self.desktop.capture()
        if self.desktop.vision.page(frame) not in allowed:
            raise Pending(f'{kind} 操作前页面不符')
        matched, _score, x, y = self.desktop.vision.match(frame, name, roi)
        if not matched:
            raise Pending(f'{kind} 按钮未可靠定位')
        self.journal.begin(kind, expected=list(expected), before_page=self.desktop.vision.page(frame),
                           before_evidence=self.journal.evidence(frame, kind+'_before'))
        self.desktop.pulse((x+offset[0], y+offset[1]))
        _page, after = self.wait_page(expected)
        self.journal.resolve('page_verified', evidence=self.journal.evidence(after, kind+'_after'))

    def open_activity(self):
        page = self.desktop.vision.page(self.desktop.capture())
        if page in ('ready', 'over'):
            self.click_template('daruma_game_close.bmp', CLOSE, (16, 16), (page,),
                                ('weekend', 'field', 'home'), 'close_game')
            page = self.desktop.vision.page(self.desktop.capture())
        if page in ('field', 'home'):
            self.click_template('daruma_hotspot.bmp', (450, 69, 550, 145), (27, 27),
                                (page,), ('menu',), 'open_hotspot')
            page = 'menu'
        if page == 'menu':
            self.click_template('daruma_weekend_menu.bmp', (475, 240, 705, 755), (65, 15),
                                ('menu',), ('weekend',), 'open_weekend')
            page = 'weekend'
        if page != 'weekend':
            raise Pending('无法从当前已核实页面进入周末福利；未强制回村')

    def reconcile(self):
        intent = self.journal.data.get('intent')
        if not intent:
            return
        kind = intent['kind']
        if kind == 'strike':
            if self.desktop.vision.page(self.desktop.capture()) == 'weekend':
                remaining = self.stable_reward()
                if remaining != 0:
                    raise Pending('上次敲击未决且当前已离开挑战页；奖励未归零，保留原动作')
                self.journal.resolve('superseded_by_verified_reward_zero',
                    state=dict(round_active=False, layer_details_incomplete=True),
                    evidence=self.journal.data['reward_evidence'], replayed=False)
                self.progress('done', status='done', zero_verified=True,
                              zero_evidence=self.journal.data['reward_evidence'],
                              completion_basis='activity_counter_after_external_progress')
                return
            self.verify_strike(intent, resuming=True)
        elif kind == 'start':
            self.verify_start(intent)
        elif self.desktop.vision.page(self.desktop.capture()) in intent.get('expected', []):
            frame = self.desktop.capture()
            self.journal.resolve('page_verified_after_resume', evidence=self.journal.evidence(frame, 'resume_page'))
        else:
            raise Pending('上一次页面操作结果仍未核实；保留原动作，不重复点击')

    def drive(self):
        self.reconcile()
        if self.journal.data['status'] == 'done' and self.journal.data.get('zero_verified'):
            return
        if (self.round_limit is not None and self._quantity_met()
                and not self.journal.data.get('intent') and not self.journal.data.get('round_active')):
            self.open_activity()
            self.stable_reward()
            self.quantity_satisfied = True
            self.progress('request_satisfied',status='pending',round_active=False)
            return
        self.refresh_calibration()
        while True:
            self.desktop.check_stop()
            rec = self.journal.data
            verified, self.verified_frame = self.verified_frame, None
            if verified is not None:
                (page, count, chance), frame = verified
            else:
                frame = self.desktop.capture()
                page = self.desktop.vision.page(frame)
            if page == 'playing':
                if verified is None:
                    (page, count, chance), frame = self.stable_game()
                if rec.get('round_active'):
                    if count != rec['layers']:
                        raise Pending('当前层数与独立断点不一致，不能猜测上次结果')
                else:
                    self.progress('adopted_round', layers=count, chance=chance, round_active=True,
                                  round=int(rec['round'])+1, adopted=True, retries=0)
                self.play_layer(frame)
                continue
            if page == 'over':
                if verified is None:
                    (page, count, chance), frame = self.stable_game()
                results = list(rec.get('round_results', []))
                if not any(r['round'] == rec['round'] for r in results):
                    results.append(dict(round=rec['round'], layers=count, six_icons_verified=count == 6))
                allowance = self.round_allowance(results)
                self.progress('round_finished', layers=count, chance=chance, round_active=False,
                              result_evidence=self.journal.evidence(frame, 'game_over'),
                              round_results=results, remaining_estimate=allowance, reward_needs_check=True,
                              six_icons_verified=count == 6, game_over_verified=True)
                if self.round_limit is not None and self._quantity_met():
                    self.open_activity()
                    self.stable_reward()
                    self.quantity_satisfied = True
                    self.progress('request_satisfied', status='pending', round_active=False)
                    return
                if chance > 0 and allowance is not None and allowance > 0:
                    self.progress('restart_round')
                    self.start_round()
                    continue
                self.open_activity()
            elif page == 'ready':
                # Read the activity reward counter before spending a new chance.
                if rec.get('reward_checked_for_start'):
                    self.start_round()
                    continue
                self.open_activity()
            elif page in ('field', 'home', 'menu'):
                self.open_activity()
            elif page != 'weekend':
                raise Pending(f'未知页面 {page}，已保留现场')
            remaining = self.stable_reward()
            if remaining == 0:
                self.progress('done', status='done', round_active=False, zero_verified=True,
                              zero_evidence=self.journal.data['reward_evidence'])
                return
            if rec.get('chance') == 0 and not rec.get('round_active'):
                raise Pending(f'当前局已结束，奖励还剩{remaining}，今日机会已耗尽')
            self.click_template('daruma_go.bmp', (1190, 635, 1330, 710), (47, 18),
                                ('weekend',), ('ready',), 'go_game')
            self.progress('ready', reward_checked_for_start=True, round_active=False)

    def _quantity_met(self):
        request = self.journal.data.get('quantity_requests', {}).get(self.quantity_request_id, {})
        base = request.get('base_round')
        limit = request.get('limit')
        if type(base) is not int or type(limit) is not int:return False
        return sum(result.get('round', 0) > base for result in self.journal.data.get('round_results', [])) >= limit

    def round_allowance(self, results=None):
        rec = self.journal.data
        if rec.get('remaining') is None:
            return None
        anchor = int(rec.get('reward_check_round', rec.get('round', 0)))
        completed = sum(r['round'] > anchor and r['six_icons_verified']
                        for r in (results if results is not None else rec.get('round_results', [])))
        return max(0, rec['remaining']-completed)

    def start_round(self):
        (page, count, chance), frame = self.stable_game()
        if page not in ('ready', 'over') or (page == 'ready' and count != 0):
            raise Pending('开始前页面或奖励图标不符，不消耗机会')
        if chance <= 0:
            raise Pending('没有新的挑战机会，未点击开始')
        if not self.round_allowance():
            raise Pending('缺少已核实的剩余奖励，不开始新局')
        ok, _score, x, y = self.desktop.vision.match(frame, 'daruma_start.bmp', START)
        if not ok:
            raise Pending('达摩开始按钮未确认')
        intent = self.journal.begin('start', chance_before=chance, expected=['playing', 'over'],
                                    before_evidence=self.journal.evidence(frame, 'start_before'))
        self.desktop.pulse((x+105, y+46))
        self.verify_start(intent)

    def verify_start(self, intent):
        self.wait_page(('playing', 'over'))
        (page, count, chance), frame = self.stable_game()
        if page != 'playing' or count != 0 or chance != intent['chance_before']-1:
            raise Pending('开始后的页面和机会变化未对账，不重复点击开始')
        state = dict(chance=chance, layers=count, round_active=True, adopted=False,
                     round=int(self.journal.data['round'])+1, retries=0, reward_checked_for_start=False)
        self.journal.resolve('started', state=state, evidence=self.journal.evidence(frame, 'start_verified'))
        self.verified_frame = ((page, count, chance), frame)
        self.motion_samples = []
        self.progress('playing', **state)

    def select_bottom(self):
        for probe in ((1045, 645), (995, 645), (1090, 645), (950, 645), (1140, 645)):
            self.desktop.move(probe)
            self.desktop.wait(.04)
            pixels, _, _ = self.desktop.roi(BOTTOM)
            bounds = bottom_bounds(pixels)
            if bounds is None:
                continue
            centre = round(sum(bounds)/2)
            side = self.journal.data.get('side', 'left')
            point = (centre + (20 if side == 'right' else -20), 650)
            self.desktop.move(point)
            self.desktop.wait(.04)
            pixels, _, _ = self.desktop.roi(BOTTOM)
            confirmed = bottom_bounds(pixels)
            if confirmed and abs(sum(confirmed)/2-centre) <= 6:
                return confirmed, point
        raise Pending('最下层圆柱和选中轮廓无法可靠定位；未敲击')

    def play_layer(self, before):
        rec = self.journal.data
        if rec['layers'] >= 6:
            raise Pending('六个图标已齐，等待结算核实，禁止继续敲击')
        if rec['retries'] > 2 or rec.get('correction_exhausted'):
            raise Pending('本层已自动纠正两次，保留现场')
        self.progress('select_bottom')
        bounds, point = self.select_bottom()
        deadline = time.perf_counter()+7
        before, _ = self.playing_frame(rec['layers'], deadline=deadline)
        evidence = self.journal.evidence(before, 'layer_before')
        zone = None
        samples, velocities = list(self.motion_samples), []
        metrics = dict(frames=0, unique_positions=0, fits=0, unstable_velocity=0, rejected_predictions=0)
        self.progress('timing')
        last_full = time.perf_counter()
        while time.perf_counter() < deadline:
            if time.perf_counter()-last_full >= 1.0:
                _frame, obscured = self.playing_frame(rec['layers'], deadline=deadline)
                if obscured:
                    samples.clear()
                    velocities.clear()
                    zone = None
                if time.perf_counter() >= deadline:
                    break
                last_full = time.perf_counter()
            pixels, at, elapsed = self.desktop.roi(BAR)
            metrics['frames'] += 1
            columns = flame_columns(pixels)
            column = moving_column(columns, rec.get('judgment_x'))
            if column is None:
                samples.clear()
                velocities.clear()
                self.desktop.wait(.004)
                continue
            if zone is None:
                # A flame over the center hides part of the yellow band. Only
                # measure its width when the moving pointer is away from it.
                if 985 <= column <= 1110:
                    continue
                try:
                    zone = perfect_zone(pixels)
                except Pending:
                    continue
            if samples and samples[-1][1] == column:
                continue
            metrics['unique_positions'] += 1
            samples.append((at, column, elapsed))
            samples = samples[-48:]
            motion = estimate_motion(samples)
            if motion is None:
                self.desktop.wait(.004)
                continue
            metrics['fits'] += 1
            metrics['last_fit'] = dict(x=motion.x, velocity=motion.velocity, residual=motion.residual,
                noise=motion.noise, capture_ms=motion.capture_seconds*1000, samples=motion.sample_count,
                span=at-samples[0][0], zone=zone)
            velocities.append(motion.velocity)
            velocities = velocities[-4:]
            if len(velocities) < 4 or max(velocities)-min(velocities) > 25:
                metrics['unstable_velocity'] += 1
                continue
            target_at = dispatch_at(motion, zone, rec['latency'], time.perf_counter())
            if target_at is None:
                metrics['rejected_predictions'] += 1
                continue
            intent = self.journal.begin('strike', before_layers=rec['layers'], before_bottom=list(bounds),
                before_chance=rec['chance'], before_evidence=evidence, point=list(point), zone=list(zone),
                velocity=motion.velocity, latency=rec['latency'], capture_ms=elapsed*1000,
                target_at=target_at, side=rec.get('side', 'left'))
            sent_at = self.desktop.pulse(point, at=target_at)
            if sent_at is None:
                continue
            self.strikes_sent += 1
            self.record_trace(intent)
            self.verify_strike(intent)
            if self.strike_limit and self.strikes_sent >= self.strike_limit:
                raise Stopped('本次受控敲击已结束，保存结果供时序核验')
            return
        self.journal.save(timing_diagnostics=metrics)
        raise Pending('连续7秒未获得可靠敲击时机；本层未发送新的敲击')

    def refresh_calibration(self):
        actions = self.journal.data['actions']
        strikes = [a for a in actions if a['kind'] == 'strike' and a['outcome'] == 'layer_hit']
        if not strikes:
            return
        action = strikes[-1]
        trace = action.get('trace')
        if not trace or not Path(trace).is_file() or self.journal.data.get('calibrated_action') == action['id']:
            return
        with np.load(trace, allow_pickle=False) as recording:
            judgment = judgment_from_frames(list(recording['frames']), tuple(action['zone']), tuple(recording['roi']))
        if judgment is not None:
            latency, history = measured_latency(self.journal.data, action, judgment)
            self.journal.save(latency=latency, latency_history=history, judgment_x=judgment,
                              calibrated_action=action['id'])
            self.emit('timing_measured', judgment_x=judgment, latency=latency, trace=trace)

    def record_trace(self, intent):
        bars, bottoms, rewards, times, durations = [], [], [], [], []
        path = self.journal.directory/('trace_'+intent['id']+'.npz')
        self.journal.data['intent']['trace'] = str(path)
        self.journal.save()
        deadline = time.perf_counter()+.75
        previous_bounds = None
        before_bottom = np.asarray(Image.open(intent['before_evidence']).crop(BOTTOM), dtype=np.int16)
        self.motion_samples = []
        try:
            while time.perf_counter() < deadline:
                pixels, at, _elapsed = self.desktop.roi(TRACE)
                bars.append(crop_roi(pixels, BAR))
                bottoms.append(crop_roi(pixels, BOTTOM))
                rewards.append(crop_roi(pixels, REWARDS))
                times.append(at)
                durations.append(_elapsed)
                if intent['before_layers'] < 5:
                    try:
                        count = layer_count(Image.fromarray(rewards[-1]), origin=REWARDS[:2])
                    except Pending:
                        count = None
                    bounds = bottom_bounds(bottoms[-1])
                    judgment = judgment_from_frames(bars, tuple(intent['zone']), roi=BAR)
                    changed = float(np.abs(before_bottom-bottoms[-1].astype(np.int16)).mean()) >= 4
                    stable = bounds is not None and bounds == previous_bounds
                    previous_bounds = bounds
                    if (count == intent['before_layers']+1 and stable and changed and judgment is not None):
                        self.emit('animation_ready', observation_ms=round((times[-1]-times[0])*1000, 1))
                        break
                self.desktop.wait(.012)
        finally:
            if bars:
                began = time.perf_counter()
                save_trace(path, bars, bottoms, rewards, times)
                self.emit('trace_saved', compression_ms=round((time.perf_counter()-began)*1000, 2),
                          frames=len(bars), bytes=path.stat().st_size, trace=str(path))
                self.journal.save(last_trace=str(path))
                judgment = judgment_from_frames(bars, tuple(intent['zone']), roi=BAR)
                for bar, at, elapsed in zip(bars, times, durations):
                    column = moving_column(flame_columns(bar), judgment)
                    if column is not None:
                        self.motion_samples.append((at, column, elapsed))

    def verify_strike(self, intent, *, resuming=False):
        frames = []
        trace_roi = TRACE
        trace = intent.get('trace')
        if trace and Path(trace).is_file():
            with np.load(trace, allow_pickle=False) as recording:
                trace_roi = tuple(int(x) for x in recording['roi'])
                if not (trace_roi[0] <= BAR[0] and trace_roi[1] <= BAR[1]
                        and trace_roi[2] >= BAR[2] and trace_roi[3] >= BAR[3]):
                    raise Pending('敲击轨迹区域不符')
                frames = list(recording['frames'])
        zone = tuple(intent['zone'])
        judgment = judgment_from_frames(frames, zone, roi=trace_roi) if frames else None
        (page, count, chance), after = self.stable_game(10 if intent['before_layers'] == 5 else 4,
                                                     wait_for_change_from=intent['before_layers'])
        if chance != intent['before_chance']:
            raise Pending('敲击后机会数异常变化，不重复输入')
        evidence = self.journal.evidence(after, 'strike_after')
        if judgment is not None:
            corrected, history = measured_latency(self.journal.data, intent, judgment)
            self.journal.save(latency=corrected, latency_history=history,
                              judgment_x=judgment, calibrated_action=intent['id'])
            self.emit('timing_measured', judgment_x=judgment, latency=corrected, trace=trace)
        before = Image.open(intent['before_evidence']).convert('RGB')
        body_change = float(np.abs(np.asarray(before.crop(BOTTOM), dtype=np.int16)
                                   - np.asarray(after.crop(BOTTOM), dtype=np.int16)).mean())
        if count == intent['before_layers']+1 and (body_change >= 4 or page == 'over'):
            if judgment is not None and not zone[0] <= judgment <= zone[1]:
                raise Pending('奖励图标增加，但判定轨迹与Perfect区域矛盾')
            state = dict(layers=count, chance=chance, retries=0, last_layer_evidence=evidence)
            self.journal.resolve('layer_hit', state=state, evidence=evidence, judgment_x=judgment,
                                 perfect_verified=judgment is not None, resumed=resuming)
            self.verified_frame = ((page, count, chance), after)
            self.progress('layer_verified', **state)
            return
        if count != intent['before_layers']:
            raise Pending('敲击后图标数量跳变，不能按预定敲击次数记完成')
        if page == 'over':
            self.journal.resolve('round_ended_without_new_layer', state=dict(chance=chance, round_active=False),
                                 evidence=evidence)
            self.progress('round_finished', chance=chance, round_active=False)
            return
        if page != 'playing' or judgment is None or zone[0] <= judgment <= zone[1]:
            raise Pending('敲击结果未核实；未把图标不增加直接判为空击')
        a, _, _ = self.desktop.roi(BAR)
        self.desktop.wait(.12)
        b, _, _ = self.desktop.roi(BAR)
        first = [x for x in flame_columns(a) if abs(x-judgment) > 12]
        second = [x for x in flame_columns(b) if abs(x-judgment) > 12]
        moving = len(first) == len(second) == 1 and abs(first[0]-second[0]) >= 4
        after_bounds = bottom_bounds(np.asarray(after.crop(BOTTOM)))
        displaced = (after_bounds is not None
                     and abs(sum(after_bounds)/2-sum(intent['before_bottom'])/2) >= 8)
        if not moving or not displaced:
            raise Pending('未命中判定缺少持续移动或圆柱偏移证据；不重试')
        if int(self.journal.data['retries']) >= 2:
            self.journal.resolve('weak_hit_verified', state=dict(correction_exhausted=True),
                                 evidence=evidence, judgment_x=judgment)
            raise Pending('本层已自动纠正两次，保留当前局')
        retries = int(self.journal.data['retries'])+1
        side = 'right' if intent['side'] == 'left' else 'left'
        self.journal.resolve('weak_hit_verified', state=dict(retries=retries, side=side),
                             evidence=evidence, judgment_x=judgment)
        self.progress('correcting', retries=retries, side=side)
        self.emit('correction', reason='过早' if judgment < zone[0] else '过晚', next_side=side, retries=retries)
        if retries > 2:
            raise Pending('本层已自动纠正两次，保留当前局')
