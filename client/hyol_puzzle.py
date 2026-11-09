"""Weekend Ninja Puzzle: three daily difficulties using the supplied solver.

The client owns account selection, page entry, reward evidence and navigation.
The legacy solver is permitted to run only on the yellow Start Game page.
An uncertain in-flight puzzle is reconciled, never started a second time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import time
import uuid

import numpy as np
from PIL import Image

import hyol_scheduler as hs
from hyol_checkpoints import read_checkpoint
from hyol_puzzle_backend import PuzzleScriptSession, verify_executable, backend_path, require_account_tab


@dataclass(frozen=True)
class Difficulty:
    key: str
    label: str
    index: int
    grid: int


DIFFICULTIES = (Difficulty('easy', '简单', 0, 4), Difficulty('normal', '普通', 1, 5),
                Difficulty('hard', '困难', 2, 6))
MAX_SCRIPT_SECONDS = 240
# The supplied activity notice starts/ends at 05:00. Use that same operational
# day boundary so a midnight restart cannot consume the previous day's request.
ACTIVITY_BOUNDARY_HOUR = 5


def activity_day(at=None):
    return ((at or datetime.now()) - timedelta(hours=ACTIVITY_BOUNDARY_HOUR)).date().isoformat()


def progress_complete(record, day=None):
    if not isinstance(record, dict) or record.get('game_day') != (day or activity_day()):
        return False
    rows = record.get('difficulties', {})
    return (record.get('status') == 'done' and all(
        isinstance(rows.get(diff.key), dict) and rows[diff.key].get('claimed') is True
        and rows[diff.key].get('solved') is True and rows[diff.key].get('claim_evidence')
        for diff in DIFFICULTIES))


def normalize_weekend_record(record, day=None):
    """Invalidate only the old aggregate gate, preserving all Daruma fields."""
    if record.get('status') == 'done' and not progress_complete(record.get('puzzle'), day):
        if record.get('task_completed') and record.get('home_returned'):
            record.setdefault('legacy_daruma_finished', True)
            if not record.get('daruma_game_day') and record.get('finished_at'):
                try:
                    record['daruma_game_day'] = activity_day(datetime.fromisoformat(record['finished_at']))
                except (ValueError, TypeError):
                    pass
        record.update(status='new', task_completed=False, home_returned=False)
    return record


def weekend_verified(record, day=None):
    return (isinstance(record, dict) and record.get('status') == 'done'
            and record.get('task_completed') is True and record.get('home_returned') is True
            and progress_complete(record.get('puzzle'), day))


def _match(r, name, frame=None, roi=hs.VIEW, threshold=.95):
    if not (hs.ASSETS / name).is_file():
        return False, 0., 0, 0
    return r.scheduler.template(name, roi, frame, threshold=threshold)


def select_layout(r, frame):
    found, _, x, y = _match(r, 'puzzle_select_title.bmp', frame)
    if not found:
        return None
    # The title plus all three distinct rows identify the page, not a generic X.
    for diff in DIFFICULTIES:
        roi = (x-170, y+70+80*diff.index, x-35, y+125+80*diff.index)
        if not _match(r, 'puzzle_'+diff.key+'_label.bmp', frame, roi)[0]:
            return None
    return x, y


def row_rois(layout, diff):
    x, y = layout
    return ((x+192, y+108+80*diff.index, x+300, y+173+80*diff.index),
            (x+113, y+117+80*diff.index, x+200, y+163+80*diff.index))


def claim_state(r, frame, layout, diff):
    start, claim = row_rois(layout, diff)
    label = _match(r, 'puzzle_claim_text.bmp', frame, claim)
    start_match = _match(r, 'puzzle_row_start.bmp', frame, start, .93)
    if not start_match[0]:
        return 'unknown'
    _, _, sx, sy = start_match
    blue = np.asarray(frame.crop((sx+12,sy+6,sx+72,sy+28)).convert('RGB'), dtype=np.int16)
    if np.mean((blue[:,:,2] > 120) & (blue[:,:,2]-blue[:,:,0] > 35)
               & (blue[:,:,2]-blue[:,:,1] > 15)) < .12:
        return 'unknown'
    if _match(r,'puzzle_claimed_text.bmp',frame,claim,.97)[0]:
        return 'claimed'
    if not label[0]:
        return 'unknown'
    # Exclude the golden border and white lettering; dimming the whole modal
    # also fails the still-blue Start control above. Grey alone proves nothing.
    _, _, x, y = label
    pixels = np.asarray(frame.crop((x-8, y-3, x+44, y+21)).convert('RGB'), dtype=np.int16)
    spread = pixels.max(axis=2)-pixels.min(axis=2)
    colorful = float(np.mean((spread > 65) & (pixels.max(axis=2) > 110)))
    gray = _match(r, 'puzzle_claim_gray.bmp', frame, claim, .97)[0]
    if gray and colorful < .18:
        return 'gray'
    if colorful >= .22:
        return 'active'
    return 'unknown'


def game_layout(r, frame):
    found, _, x, y = _match(r, 'puzzle_game_anchor.bmp', frame)
    if not found:
        shell, _, sx, sy = _match(r, 'puzzle_game_shell_core.bmp', frame,threshold=.98)
        if not shell:
            return None
        # A delegated tile drag can cover the central scroll. The independent
        # outer artwork and the close control still locate the same board.
        x,y=sx+534,sy+123
    left = (x-448, y-32, x-21, y+394)
    close = (x+390, y-122, x+450, y-55)
    if not (1 <= left[0] < left[2] < 1919 and 69 <= left[1] < left[3] < 1031):
        return None
    if not _match(r, 'puzzle_game_close.bmp', frame, close)[0]:
        return None
    return left, close


def board_error(reference, current, grid):
    """Compare tile interiors, excluding the yellow Start overlay and seams."""
    if reference.size != current.size or not 4 <= grid <= 6:
        return float('inf')
    a, b = np.asarray(reference.convert('RGB'), dtype=np.int16), np.asarray(current.convert('RGB'), dtype=np.int16)
    h, w = a.shape[:2]
    errors = []
    for row in range(grid):
        for column in range(grid):
            x, y = round((column+.5)*w/grid), round((row+.5)*h/grid)
            radius = min(12, round(min(w,h)/grid*.14))
            # User's Start overlay occupies the right edge of the target image.
            if x+radius >= w-100 and h*.38 <= y+radius and y-radius <= h*.57:
                continue
            patch = a[y-radius:y+radius, x-radius:x+radius]
            other = b[y-radius:y+radius, x-radius:x+radius]
            if patch.size:
                errors.append(float(np.mean(np.abs(patch-other))))
    if not errors or float(a.std()) < 20:
        return float('inf')
    return max(errors)


def activity_ledger_path(scheduler, account, day):
    """Reuse only positively matched activity identities across fresh runs."""
    materials = getattr(scheduler, '_dynamic_identity_materials', {})
    material = materials.get(account.name) if isinstance(materials, dict) else None
    if not isinstance(material, dict) or not material.get('mask') or not material.get('shape'):
        return scheduler.state.directory / 'weekend_puzzle' / day / (hs.slug(account.name)+'.json'), False
    from hyol_checkpoints import decode_mask, identity_mask_scores
    context = getattr(scheduler, 'run_context', None)
    root = Path(context.identity_directory if context else scheduler.shared_state_directory)/'activities'/'puzzle'
    registry_path = root/'identities.json'
    registry = read_checkpoint(registry_path)
    mask = decode_mask(material['mask'],tuple(material['shape']))
    scores = identity_mask_scores(mask,registry)
    ranked = sorted(scores,key=scores.get,reverse=True)
    if ranked and scores[ranked[0]] >= .92:
        if len(ranked)>1 and scores[ranked[0]]-scores[ranked[1]] < .08:
            raise hs.PendingError('拼图活动身份记录存在歧义，不重开或领取')
        owner=ranked[0]
    else:
        for candidate,value in scores.items():
            if value<.80:continue
            for path in root.glob('????-??-??/'+candidate+'.json'):
                prior=read_checkpoint(path)
                if any(row.get('phase') in ('solving','await_claim','claim_pending')
                       for row in prior.get('difficulties',{}).values()):
                    raise hs.PendingError('拼图活动身份与未决记录相似，保留原断点')
        owner=uuid.uuid4().hex
        registry[owner]=dict(mask=material['mask'],shape=material['shape'])
        hs.atomic_json(registry_path,registry)
    for old_path in root.glob('????-??-??/'+owner+'.json'):
        if old_path.parent.name == day:
            continue
        old=read_checkpoint(old_path)
        if any(row.get('phase') in ('solving','await_claim','claim_pending')
               for row in old.get('difficulties',{}).values()):
            raise hs.PendingError('拼图有上个活动日的未决开始或领奖；保留原票据')
    return root/day/(owner+'.json'), True


class PuzzleRun:
    def __init__(self, runner, *, session_factory=PuzzleScriptSession, at=None):
        self.r, self.s, self.account = runner, runner.scheduler, runner.account
        self.session_factory = session_factory
        self.day = activity_day(at)
        self.parent = runner.record('weekend')
        previous = self.parent.get('puzzle', {})
        if (previous and previous.get('game_day') != self.day
                and any(row.get('phase') in ('solving', 'claim_pending')
                        for row in previous.get('difficulties', {}).values())):
            raise hs.PendingError('拼图有上个活动日的未决开始或领奖；保留原票据，先核实原结果')
        self.path, matched_identity = activity_ledger_path(self.s,self.account,self.day)
        current = read_checkpoint(self.path)
        if current and current.get('account') != self.account.name and not matched_identity:
            raise hs.PendingError('拼图断点账号与实名绑定不一致，未操作游戏')
        if current and previous.get('game_day') == self.day and previous != current:
            # The canonical activity ledger is saved before its daily view.
            # Only a view explicitly linked to that ledger may lag behind it.
            if previous.get('ledger_path') != str(self.path):
                raise hs.PendingError('拼图存在不同来源的断点，先核实原结果')
        self.record = current or (previous if previous.get('game_day') == self.day else {}) or dict(
            version=1, account=self.account.name, game_day=self.day, status='new', difficulties={})
        self.record['ledger_path'] = str(self.path)
        self.record['current_run_account'] = self.account.name
        self.record.setdefault('difficulties', {})
        for diff in DIFFICULTIES:
            self.record['difficulties'].setdefault(diff.key, dict(phase='ready', solved=False, claimed=False))
        self.parent['puzzle'] = self.record

    def save(self):
        self.record['updated_at'] = hs.now_iso()
        hs.atomic_json(self.path, self.record)
        self.parent['puzzle'] = self.record
        self.r.save()

    def evidence(self, frame, label, phase='pending'):
        return str(self.s.save_raw_evidence(frame, self.account, label, phase))

    def capture(self):
        self.s.check_input_pause()
        frame = self.s.capture()
        require_account_tab(self.s, self.account, frame)
        return frame

    def click(self, *args, **kwargs):
        self.capture()
        return self.r.click(*args, **kwargs)

    def action(self, diff, suffix):
        label = f'puzzle_{hs.slug(self.account.name)}_{self.day}_{diff.key}_{suffix}'
        attempt = self.record['difficulties'][diff.key].get('attempt',1)
        return label+f'_attempt_{attempt}' if suffix=='start' and attempt>1 else label

    def close_board(self, frame, label):
        layout = game_layout(self.r, frame)
        if not layout:
            raise hs.PendingError('拼图关闭前未确认当前游戏页')
        self.click('puzzle_game_close.bmp', layout[1], 18, 18, label,
                     ['puzzle_game_anchor.bmp'], threshold=.95, anchor_threshold=.95)
        self.r.wait('puzzle_select_title.bmp', hs.VIEW, 10)

    def open_selection(self):
        frame = self.capture()
        layout = select_layout(self.r, frame)
        if layout:
            return frame, layout
        if game_layout(self.r, frame):
            if any(row.get('phase') == 'solving' for row in self.record['difficulties'].values()):
                raise hs.PendingError('原拼图仍在进行，不能切换难度')
            if not _match(self.r, 'puzzle_game_start.bmp', frame)[0]:
                raise hs.PendingError('拼图游戏已经开始但缺少当前难度断点，保留现场')
            self.close_board(frame, 'puzzle_close_unstarted')
            frame = self.capture()
            layout = select_layout(self.r, frame)
            if layout:
                return frame, layout
        from hyol_daruma import _open_weekend
        _open_weekend(self.r)
        frame = self.capture()
        matched, _, x, y = _match(self.r, 'puzzle_entry_title.bmp', frame)
        if not matched:
            from hyol_weekend import WeekendUnavailable, puzzle_replaced_by_known_activities
            actions = self.s.state.accounts.get(self.account.name, {}).get('actions', {})
            pending = (any(row.get('phase') not in ('ready','done')
                           for row in self.record['difficulties'].values())
                       or any(key.startswith('puzzle_') and isinstance(row,dict)
                              and row.get('status') == 'pending' for key,row in actions.items()))
            if not pending and puzzle_replaced_by_known_activities(self.r, frame):
                self.s.wait_interruptible(.25)
                frame = self.capture()
                if puzzle_replaced_by_known_activities(self.r, frame):
                    proof = self.evidence(frame, 'puzzle_not_in_current_rotation', 'verified')
                    reason = '本期展示忍者任务、骰子手气王和达摩，未提供忍者拼图；本轮跳过拼图'
                    self.record['availability'] = dict(status='not_offered', evidence=proof, reason=reason)
                    self.save()
                    raise WeekendUnavailable(reason)
            raise hs.PendingError('周末福利中未确认忍者拼图入口；可能尚未开放')
        self.click('daruma_go.bmp', (x+425,y+45,x+555,y+99), 47,18,
                     'puzzle_open_selection', ['puzzle_entry_title.bmp'], threshold=.94)
        self.r.wait('puzzle_select_title.bmp', hs.VIEW, 10)
        frame = self.capture()
        layout = select_layout(self.r, frame)
        if not layout:
            raise hs.PendingError('忍者拼图三个难度的选择页未确认')
        return frame, layout

    def enter_board(self, diff, layout, row):
        start, _ = row_rois(layout, diff)
        row.update(phase='prepared', entered_at=hs.now_iso())
        self.save()
        self.click('puzzle_row_start.bmp', start, 40,19, self.action(diff,'enter'),
                     ['puzzle_select_title.bmp'], threshold=.94, anchor_threshold=.95)
        self.r.wait('puzzle_game_start.bmp', hs.VIEW, 30)
        frame = self.capture()
        layout = game_layout(self.r, frame)
        if not layout or not _match(self.r, 'puzzle_game_start.bmp', frame)[0]:
            raise hs.PendingError('未进入该难度的开始拼图页面，未启动解题')
        row['reference_evidence'] = self.evidence(frame, 'puzzle_'+diff.key+'_prepared')
        self.save()
        return frame, layout

    def prepared_board(self, diff, row):
        if row.get('phase')!='prepared' or not row.get('entered_at'):
            return None
        if row.get('loading_recovery',{}).get('status')=='pending':
            raise hs.PendingError('原加载页关闭请求未核实，不能启动拼图')
        frame=self.capture();board=game_layout(self.r,frame)
        if not board or not _match(self.r,'puzzle_game_start.bmp',frame)[0]:
            return None
        # A slow resource load may finish after the bounded entry wait. The
        # saved difficulty entry still owns this unstarted preview; refresh its
        # reference before delegating, without another entry or Start click.
        row['reference_evidence']=self.evidence(frame,'puzzle_'+diff.key+'_prepared_resumed')
        self.save()
        return frame,board

    def observe_result_after_stop(self, session, diff, row):
        """Stop all delegated input first, then inspect a bounded finish repaint."""
        session.close()
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            try:
                current=self.capture()
            except (hs.SafetyError,hs.PendingError):
                return False
            if _match(self.r,'puzzle_success_text.bmp',current,hs.VIEW,.97)[0]:
                row['success_evidence']=self.evidence(current,'puzzle_'+diff.key+'_success_after_stop','verified')
                return True
            selection=select_layout(self.r,current)
            if selection and claim_state(self.r,current,selection,diff)=='active':
                row['solved_evidence']=self.evidence(current,'puzzle_'+diff.key+'_claim_after_stop','verified')
                return True
            self.s.wait_interruptible(.15)
        return False

    def solve(self, diff, row, frame, layout):
        reference = frame.crop(layout[0])
        with self.session_factory(self.s, self.account) as session:
            # The intent is persisted after native settings are verified and
            # immediately before the first delegated game input.
            self.s.state.set_action(self.account.name, self.action(diff,'start'), 'pending',
                                    '已核实开始页面；即将委托原程序自动识别解题')
            row.update(phase='solving',solver='legacy',operation_id=uuid.uuid4().hex,
                       script_started_at=hs.now_iso(),external_input_count=None,
                       start_action=self.action(diff,'start'))
            self.save()
            session.start()
            began=time.monotonic()
            deadline, dispersed, stable = began+MAX_SCRIPT_SECONDS, False, 0
            while time.monotonic() < deadline:
                try:
                    current = self.capture()
                except hs.SafetyError:
                    # The supplied program may show its own UI on completion.
                    # Stop all delegated input before looking for a positive
                    # game result; foreign foreground never supplies evidence.
                    if self.observe_result_after_stop(session,diff,row):
                        break
                    raise
                if _match(self.r,'puzzle_success_text.bmp',current,hs.VIEW,.97)[0]:
                    row['success_evidence']=self.evidence(current,'puzzle_'+diff.key+'_success','verified')
                    break
                if _match(self.r,'puzzle_timeout_banner.bmp',current,hs.VIEW,.97)[0]:
                    row['timeout_evidence']=self.evidence(current,'puzzle_'+diff.key+'_timeout_observed','verified')
                    self.save()
                    # Keep the started phase. Reconcile owns the positively
                    # failed round and its bounded retry, after the job stops.
                    return
                selection = select_layout(self.r, current)
                if selection and claim_state(self.r, current, selection, diff) == 'active':
                    row['solved_evidence'] = self.evidence(current, 'puzzle_'+diff.key+'_claim_ready', 'verified')
                    break
                try:
                    session.check_running()
                    if hs.Win32.foreground() != self.s.hwnd:
                        raise hs.SafetyError('拼图解题期间游戏前台窗口发生变化，已停止原解题程序')
                except hs.SafetyError:
                    if self.observe_result_after_stop(session,diff,row):
                        break
                    raise
                board = game_layout(self.r, current)
                if not board:
                    if time.monotonic()-began<8:
                        self.s.wait_interruptible(.15)
                        continue
                    raise hs.PendingError('原解题程序运行后页面未知；未计入完成')
                if board[0] != layout[0]:
                    raise hs.SafetyError('拼图游戏区域发生变化，停止原解题程序')
                prepared = _match(self.r, 'puzzle_game_start.bmp', current)[0]
                error = board_error(reference, current.crop(board[0]), diff.grid)
                if not prepared and error > 18:
                    dispersed = True
                stable = stable+1 if dispersed and not prepared and error <= 7 else 0
                if stable >= 3:
                    row['assembled_evidence'] = self.evidence(current, 'puzzle_'+diff.key+'_assembled', 'verified')
                    break
                if dispersed and prepared:
                    raise hs.PendingError('本局结束后重新出现开始按钮，结果未核实；不自动重开')
                self.s.wait_interruptible(.15)
            else:
                raise hs.PendingError('拼图解题超时，保留原局；不重复开始')
        row['phase'] = 'await_claim'
        self.save()

    def reconcile(self, diff, row):
        frame = self.capture()
        if _match(self.r,'puzzle_success_text.bmp',frame,hs.VIEW,.97)[0]:
            if row.get('phase') not in ('solving','await_claim'):
                raise hs.PendingError('拼图成功弹窗缺少当前难度开始断点，保留现场')
            row['success_evidence']=self.evidence(frame,'puzzle_'+diff.key+'_success','verified')
            row['phase']='await_claim';self.save()
            action=self.action(diff,'success_confirm')
            self.click('puzzle_success_confirm.bmp',(880,560,1040,650),40,14,action,
                       ['puzzle_success_text.bmp'],irreversible=True,threshold=.95,anchor_threshold=.97)
            if not self.s.wait_absent('puzzle_success_text.bmp',hs.VIEW,8,threshold=.97):
                raise hs.PendingError('拼图成功确认后弹窗仍在，保留原确认票据')
            self.s.commit_action(self.account,action,'本难度成功弹窗已确认并消失')
            frame=self.capture()
        if _match(self.r,'puzzle_timeout_banner.bmp',frame,hs.VIEW,.97)[0]:
            if not row.get('operation_id') or row.get('phase')!='solving':
                raise hs.PendingError('时间到页面缺少本账号本难度开始票据，保留现场')
            failure=self.evidence(frame,'puzzle_'+diff.key+'_timeout','verified')
            action=row.get('start_action') or self.action(diff,'start')
            row.setdefault('attempt_history',[]).append(dict(operation_id=row.get('operation_id'),
                action=action,result='timeout',evidence=failure,at=hs.now_iso()))
            self.s.commit_action(self.account,action,'本局时间到已核实；未通关、未领奖')
            if row.get('attempt',1)>=3:
                self.save()
                raise hs.PendingError('该难度三次有凭据失败，停止重试并保留记录')
            found,_,x,y=_match(self.r,'puzzle_game_close.bmp',frame,hs.VIEW,.95)
            if not found:
                self.save()
                raise hs.PendingError('时间到已核实，关闭按钮未知，未重开')
            self.click('puzzle_game_close.bmp',(x-2,y-2,x+45,y+45),18,18,
                       self.action(diff,'close_failed'),['puzzle_timeout_banner.bmp'],threshold=.95)
            self.r.wait('puzzle_select_title.bmp',hs.VIEW,10)
            current=self.capture(); selection=select_layout(self.r,current)
            if not selection or claim_state(self.r,current,selection,diff)!='gray':
                raise hs.PendingError('失败后原难度与未领奖状态尚未核实，不重开')
            row.update(attempt=row.get('attempt',1)+1,phase='ready',solved=False,claimed=False)
            self.save()
            prepared,board=self.enter_board(diff,selection,row)
            self.solve(diff,row,prepared,board)
            return self.reconcile(diff,row)
        selection = select_layout(self.r, frame)
        if selection and claim_state(self.r, frame, selection, diff) == 'active':
            return frame, selection
        board = game_layout(self.r, frame)
        reference_path = Path(row.get('reference_evidence', ''))
        if (board and row.get('solver') == 'native' and row.get('phase') == 'solving'
                and reference_path.is_file()):
            raise hs.PendingError('原局仍在进行；已改用用户现有拼图脚本，先保留原局或核实时间到，不重发开始')
        if board and reference_path.is_file() and (row.get('assembled_evidence') or row.get('success_evidence')):
            with Image.open(reference_path) as reference:
                assembled = board_error(reference.crop(board[0]), frame.crop(board[0]), diff.grid) <= 7
                if assembled or row.get('success_evidence'):
                    self.close_board(frame, self.action(diff, 'close_solved'))
                    current = self.capture()
                    selection = select_layout(self.r, current)
                    if selection:
                        return current, selection
        if row.get('phase') == 'claim_pending' and selection:
            return frame, selection
        raise hs.PendingError('原拼图的结果尚未核实；保留原局和开始票据，不再次调用解题')

    def claim(self, diff, row, frame, layout):
        label = self.action(diff, 'claim')
        state = self.s.state.action_status(self.account.name, label)
        before_path = Path(row.get('claim_before_evidence', ''))
        prior_active = False
        if before_path.is_file():
            with Image.open(before_path) as before:
                prior_layout = select_layout(self.r, before)
                prior_active = bool(prior_layout and claim_state(self.r, before, prior_layout, diff) == 'active')
        current = claim_state(self.r, frame, layout, diff)
        if state == 'pending' or row.get('phase') == 'claim_pending':
            if not prior_active or current != 'claimed':
                raise hs.PendingError('拼图原领奖结果未知，禁止重复点击领取')
        else:
            if current != 'active':
                raise hs.PendingError('拼图未出现可领取奖励；灰色按钮不能证明完成或已领取')
            row.update(solved=True, phase='claim_pending',
                       solved_evidence=self.evidence(frame, 'puzzle_'+diff.key+'_solved', 'verified'),
                       claim_before_evidence=self.evidence(frame, 'puzzle_'+diff.key+'_before_claim'))
            self.s.commit_action(self.account, self.action(diff,'start'), '该难度可领取奖励，通关已核实')
            self.save()
            _, roi = row_rois(layout, diff)
            self.click('puzzle_claim_text.bmp', roi,17,8,label,
                         ['puzzle_select_title.bmp'], irreversible=True, threshold=.94,
                         anchor_threshold=.95)
        deadline, stable = time.monotonic()+8, 0
        last = frame
        while time.monotonic() < deadline:
            last = self.capture()
            found = select_layout(self.r, last)
            valid = bool(found and claim_state(self.r, last, found, diff) == 'claimed')
            stable = stable+1 if valid else 0
            if stable >= 3:
                break
            self.s.wait_interruptible(.2)
        else:
            raise hs.PendingError('拼图领取后未连续确认已领取文字，保留原领奖票据')
        self.s.commit_action(self.account, label, '原可领取按钮已变为已领取，三帧难度页和开始按钮仍确认')
        row.update(phase='done', solved=True, claimed=True, finished_at=hs.now_iso(),
                   claim_evidence=self.evidence(last, 'puzzle_'+diff.key+'_claimed', 'verified'))
        self.save()
        self.r.event('puzzle_difficulty_complete', difficulty=diff.label, game_day=self.day,
                     evidence=row['claim_evidence'])

    def run(self):
        if datetime.fromisoformat(self.day).weekday() < 5:
            from hyol_weekend import WeekendUnavailable
            raise WeekendUnavailable('本期周末拼图未通过活动公告05:00日期门；保留待处理')
        if progress_complete(self.record, self.day):
            if self.record.get('last_error'):
                self.record['last_error']='';self.save()
            self.r.event('puzzle_daily_already_complete', game_day=self.day)
            return
        if self.session_factory is not None:
            verify_executable(backend_path())
        self.record['status'] = 'running'
        self.save()
        request = getattr(self.r, 'training_request', None)
        request = request if isinstance(request, dict) and request.get('task') == 'puzzle' else None
        claimed_before = {diff.key for diff in DIFFICULTIES
                          if self.record['difficulties'][diff.key].get('claimed')
                          and self.record['difficulties'][diff.key].get('claim_evidence')}
        if request is not None:
            claimed_before = set(request.setdefault('base_difficulties', sorted(claimed_before)))
            request.update(available=len(DIFFICULTIES)-len(claimed_before), quantity_state='verified')
            self.r.save()
        try:
            for diff in DIFFICULTIES:
                settled = sum(diff.key not in claimed_before and self.record['difficulties'][diff.key].get('claimed')
                              and self.record['difficulties'][diff.key].get('origin') != 'observed_claimed'
                              and bool(self.record['difficulties'][diff.key].get('claim_evidence')) for diff in DIFFICULTIES)
                if request is not None and settled >= request['required']:break
                row = self.record['difficulties'][diff.key]
                if row.get('claimed') and row.get('claim_evidence'):
                    continue
                self.s.check_input_pause()
                prepared=self.prepared_board(diff,row)
                if row.get('phase') in ('solving', 'await_claim', 'claim_pending'):
                    frame, layout = self.reconcile(diff, row)
                elif prepared:
                    self.solve(diff,row,*prepared)
                    frame,layout=self.reconcile(diff,row)
                else:
                    frame, layout = self.open_selection()
                    state = claim_state(self.r, frame, layout, diff)
                    if state == 'unknown':
                        raise hs.PendingError('拼图领奖按钮状态无法核实；未开始挑战')
                    if state == 'claimed':
                        for _ in range(2):
                            self.s.wait_interruptible(.2)
                            frame=self.capture();checked=select_layout(self.r,frame)
                            if not checked or claim_state(self.r,frame,checked,diff)!='claimed':
                                raise hs.PendingError('已领取文字未连续确认，未重开该难度')
                        row.update(phase='done',solved=True,claimed=True,origin='observed_claimed',
                                   claim_evidence=self.evidence(frame,'puzzle_'+diff.key+'_already_claimed','verified'))
                        self.save()
                        continue
                    if state != 'active':
                        prepared, game = self.enter_board(diff, layout, row)
                        self.solve(diff, row, prepared, game)
                        frame, layout = self.reconcile(diff, row)
                self.claim(diff, row, frame, layout)
            finished = progress_complete(dict(self.record, status='done'), self.day)
            self.record.update(status='done' if finished else 'pending', finished_at=hs.now_iso(),last_error='')
            if request is not None:
                request['settled'] = sum(diff.key not in claimed_before and self.record['difficulties'][diff.key].get('claimed')
                                         and self.record['difficulties'][diff.key].get('origin') != 'observed_claimed'
                                         and bool(self.record['difficulties'][diff.key].get('claim_evidence')) for diff in DIFFICULTIES)
                request['scheduled'] = min(request['required'],request['available'])
            self.save()
            frame = self.capture()
            layout = select_layout(self.r, frame)
            if not layout:
                raise hs.PendingError('三个难度奖励已核实，难度页退出仍待处理')
            x, y = layout
            self.click('puzzle_select_close.bmp', (x+275,y-5,x+330,y+65),18,18,
                         'puzzle_close_selection_complete', ['puzzle_select_title.bmp'], threshold=.95)
            self.r.event('puzzle_daily_complete', game_day=self.day, completed_difficulties=3,
                         external_input_count=getattr(self.s, 'external_input_count', 0))
        except BaseException as exc:
            self.record.update(status='pending', last_error=str(exc))
            self.save()
            raise


def run(r):
    PuzzleRun(r).run()
