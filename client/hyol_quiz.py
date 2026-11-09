"""Solo ninja quiz: choose the first option, verify each submission visually."""
import hashlib
import time
import numpy as np
import hyol_scheduler as hs

PAGE = (820,380,1060,450)
QUESTION = (680,445,1155,486)
FIRST = (750,512,805,559)
CLAIM_BUTTON_COLOR = (900,675,978,697)


def claim_button_disabled(frame):
    pixels = np.asarray(frame.crop(CLAIM_BUTTON_COLOR).convert('RGB'), dtype=np.float32)
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    orange = (red > green * 1.25) & (red > blue * 1.5) & (red > 110)
    return float(orange.mean()) < .12


def result_visible(scheduler, frame):
    return (scheduler.template('quiz_zero.bmp',(1150,413,1250,445),frame,threshold=.95)[0]
            and scheduler.template('quiz_close.bmp',(1250,325,1350,410),frame,threshold=.90)[0]
            and (scheduler.template('quiz_finish.bmp',(550,300,1350,850),frame,threshold=.92)[0]
                 or scheduler.template('quiz_solo_layout.bmp',(0,160,300,300),frame,threshold=.98)[0]))


def claimed_result_visible(scheduler, frame):
    return (result_visible(scheduler, frame) and claim_button_disabled(frame)
            and scheduler.template('quiz_finish.bmp',(550,300,1350,850),frame,threshold=.92)[0])


def claim_button_template(scheduler, frame):
    if not result_visible(scheduler,frame) or claim_button_disabled(frame):return None
    if scheduler.template('quiz_finish.bmp',(550,300,1350,850),frame,threshold=.92)[0]:
        return 'quiz_finish.bmp',.92
    if scheduler.template('quiz_finish_current_20261027.bmp',(550,300,1350,850),frame,threshold=.98)[0]:
        return 'quiz_finish_current_20261027.bmp',.98
    return None


def start_dialog_visible(scheduler, frame):
    return (scheduler.template('quiz_start.bmp',(800,540,1000,650),frame,threshold=.92)[0]
            and scheduler.template('quiz_npc_anchor.bmp',hs.VIEW,frame,threshold=.90)[0]
            and scheduler.template('quiz_task_link.bmp',(0,200,300,500),frame,threshold=.84)[0]
            and not scheduler.template('quiz_team_leave.bmp',(280,165,455,240),frame,threshold=.90)[0])


def finish_claimed_result(r, frame):
    s, rec = r.scheduler, r.record('quiz')
    if not claimed_result_visible(s,frame):
        raise hs.PendingError('测验领奖状态未确认，保留原结果')
    evidence=str(s.save_raw_evidence(frame, r.account, 'quiz_claim_disabled', 'verified'))
    if s.state.action_status(r.account.name,'daily_quiz_finish')=='pending':
        s.commit_action(r.account,'daily_quiz_finish','测验结果页领取按钮已禁用：'+evidence)
    for key, status in rec.get('submissions', {}).items():
        if status == 'submitted' and s.state.action_status(r.account.name,'daily_quiz_submit_'+key)=='pending':
            s.commit_action(r.account,'daily_quiz_submit_'+key,'剩余题目为0且领取按钮已禁用')
    r.click('quiz_close.bmp',(1250,325,1350,410),17,17,'daily_quiz_close',
            ['quiz_finish.bmp','quiz_close.bmp'])
    if not s.wait_absent('quiz_finish.bmp',(550,300,1350,850),10,threshold=.92):
        raise hs.PendingError('测验奖励页未关闭')
    rec.update(task_completed=True, completed=max(1, rec.get('completed',0)), phase='ready')
    r.save()

def solo_verified(runner, frame):
    # The left-side "组队副本进行中" label is a task entry/status row and can
    # remain visible while the account is solo. Only a real team control or
    # leader badge blocks this entry.
    return (runner.visible('quiz_task_link.bmp',(0,200,300,500),frame,.84)
            and not runner.visible('quiz_team_leave.bmp',(280,165,455,240),frame,.90)
            and not runner.visible('sealed_leader_badge.bmp',(0,69,65,150),frame,.90))


def solo_pending_reason(runner, frame):
    from PIL import Image
    if not isinstance(frame,Image.Image):return '忍者测验单人状态尚未核实'
    if (runner.visible('quiz_team_leave.bmp',(280,165,455,240),frame,.90)
            or runner.visible('sealed_leader_badge.bmp',(0,69,65,150),frame,.90)):
        return '忍者测验仅限单人：当前已识别组队控件，未进入答题'
    if not runner.visible('quiz_task_link.bmp',(0,200,300,500),frame,.84):
        return '当前未确认忍者测验任务入口，保留待核实；未判定组队或完成'
    return '忍者测验单人状态尚未核实'

def question_key(frame):
    # The stable text area includes the question number, never the countdown.
    return hashlib.sha256(frame.crop(QUESTION).convert('L').tobytes()).hexdigest()[:20]

def run(r):
    iterator=run_steps(r)
    while True:
        try:next(iterator)
        except StopIteration as result:return result.value


def prepare_entry(r, initial):
    from hyol_errors import TaskUnavailable
    s,rec=r.scheduler,r.record('quiz')
    if solo_verified(r,initial):return 'ready'
    if not isinstance(initial,hs.Image.Image):
        raise TaskUnavailable('忍者测验单人状态尚未核实')
    if claimed_result_visible(s,initial) or result_visible(s,initial):return 'ready'
    actions=s.state.accounts.get(r.account.name,{}).get('actions',{})
    if not isinstance(actions,dict):
        raise hs.PendingError('忍者测验单人入口与原动作账本未确认，暂不进入')
    if any(str(key).startswith('daily_quiz_') and isinstance(row,dict) and row.get('status')=='pending'
           for key,row in actions.items()):
        raise hs.PendingError('原测验操作结果未核实，日常完成标记不能代替原操作对账')
    from hyol_daily_observation import scan
    observed=rec.get('daily_observation',{})
    if (observed.get('run_id')!=getattr(getattr(s,'diagnostics',None),'identifier',None)
            or observed.get('account')!=r.account.name or observed.get('daily_complete') is None):
        observed=scan(r,('quiz',),force=True).get('quiz',{})
    initial=s.capture()
    if observed.get('daily_complete') is True and s.main_ready(initial):
        rec.update(task_completed=True,preexisting_complete=True,completed=rec.get('completed',0),
                   outcome_label='日常已完成',last_error='',phase='ready')
        r.save()
        r.event('quiz_preexisting_complete',task='quiz',detail='日常面板已确认忍者测验完成，本次未重新答题或计领奖',evidence=observed.get('evidence'))
        return 'observed_done'
    if not solo_verified(r, initial):
        from hyol_errors import TaskUnavailable
        reason=solo_pending_reason(r,initial)
        rec.update(status='pending',last_error=reason,outcome_label='已组队，测验需单人' if '组队控件' in reason else '测验入口未确认')
        r.save()
        raise TaskUnavailable(reason)
    return 'ready'


def run_steps(r):
    s, rec = r.scheduler, r.record('quiz')
    initial = s.capture()
    if claimed_result_visible(s, initial):
        finish_claimed_result(r, initial)
        return
    if prepare_entry(r,initial)=='observed_done':return
    initial=s.capture()
    initial_result=result_visible(s,initial)
    initial_question=r.visible('quiz_page.bmp',PAGE,initial)
    actions=s.state.accounts.get(r.account.name,{}).get('actions',{})
    started=any(str(key).startswith('daily_quiz_start_') and isinstance(value,dict)
                and value.get('status') in ('pending','done') for key,value in actions.items())
    if not initial_result and not initial_question and (started or rec.get('submissions')):
        raise hs.PendingError('原测验已启动但题目或结果页未确认，禁止重新进入')
    if (not initial_result and not initial_question
            and not r.visible('quiz_start.bmp',(800,540,1000,650))):
        r.click('quiz_task_link.bmp',(0,200,300,500),60,7,'daily_quiz_entry',
                ['quiz_task_link.bmp', 'quiz_npc_anchor.bmp'], threshold=.84,
                anchor_threshold=.84)
        deadline, total = time.monotonic()+60, time.monotonic()+300
        position = None
        while not r.visible('quiz_start.bmp',(800,540,1000,650)):
            yield
            frame = s.capture()
            new_position = frame.crop((1700,117,1905,137)).tobytes()
            if new_position != position:
                position = new_position
                deadline = time.monotonic()+60
            if time.monotonic() >= min(deadline,total):
                raise hs.PendingError('测验寻路未到达：60秒位置无进展或总寻路超过5分钟')
            s.wait_interruptible(.5)
    if not initial_result and start_dialog_visible(s,s.capture()):
        r.once('quiz_start.bmp',(800,540,1000,650),40,10,'daily_quiz_start_'+s.diagnostics.identifier)
        r.wait('quiz_page.bmp',PAGE,12)
    deadline = time.monotonic()+60
    last_seen = None
    while time.monotonic() < deadline:
        yield
        s.check_stop()
        frame = s.capture()
        if not solo_verified(r, frame):
            raise hs.PendingError('测验中单人状态无法确认，停止答题')
        button=claim_button_template(s,frame)
        if button:
            if not r.visible('quiz_zero.bmp',(1150,413,1250,445),frame,.95):
                raise hs.PendingError('领取按钮出现但未确认剩余题目为0')
            s.save_raw_evidence(frame, r.account, 'quiz_zero', 'verified')
            for key, status in rec.get('submissions', {}).items():
                if status == 'submitted' and s.state.action_status(r.account.name,'daily_quiz_submit_'+key)=='pending':
                    s.commit_action(r.account,'daily_quiz_submit_'+key,'剩余题目为0，答题流程已结束')
            if s.state.action_status(r.account.name,'daily_quiz_finish') in ('pending','done'):
                raise hs.PendingError('原测验领奖凭据尚未核实或按钮状态冲突，禁止重复领取')
            name,threshold=button
            for sample in range(3):
                frame=s.content_guard(r.account,[name],anchor_threshold=threshold)
                if claim_button_template(s,frame)!=(name,threshold):
                    raise hs.PendingError('测验领奖按钮未连续确认，未发送点击')
                if sample<2:s.wait_interruptible(.2)
            r.click(name,(550,300,1350,850),30,10,'daily_quiz_finish',
                    [name],True,threshold=threshold,anchor_threshold=threshold,
                    frame_predicate=lambda fresh:claim_button_template(s,fresh)==(name,threshold))
            claimed = False;stable=0
            claim_deadline = time.monotonic() + 12
            while time.monotonic() < claim_deadline:
                after = s.content_guard(r.account,['quiz_zero.bmp'],anchor_threshold=.95)
                stable=stable+1 if claimed_result_visible(s,after) else 0
                if stable>=3:claimed=True;break
                s.wait_interruptible(.3)
            if not claimed:
                raise hs.PendingError('领取后未连续核实禁用状态，保留原领奖凭据')
            finish_claimed_result(r, after)
            return
        if not r.visible('quiz_page.bmp',PAGE,frame):
            s.wait_interruptible(.3)
            continue
        key = question_key(frame)
        if not r.visible('quiz_answer_a.bmp',FIRST,frame,.90):
            s.wait_interruptible(.3)
            continue
        if key != last_seen:
            last_seen = key
            deadline = time.monotonic()+60
        submissions = rec.setdefault('submissions', {})
        if key in submissions:
            s.wait_interruptible(.3)
            continue
        # Confirm the question in an independent frame before selecting.
        s.wait_interruptible(.3)
        if question_key(s.capture()) != key:
            continue
        submissions[key] = 'selection_pending'
        r.save()
        r.click('quiz_answer_a.bmp',FIRST,12,15,'daily_quiz_select_'+key,['quiz_page.bmp'])
        # click_point has already captured a guarded post-click frame.  Reuse
        # that fresh observation; submit still performs its own content guard.
        selected_frame = getattr(s, '_last_frame', None)
        if question_key(selected_frame if selected_frame is not None else s.capture()) != key:
            # Selection is reversible. Keep its receipt, observe the new
            # question in this same quiz, and never submit the stale key.
            r.event('daily_quiz_question_changed', question=key,
                    detail='选项后题目刷新，未提交原题；继续观察本场测验')
            s.wait_interruptible(.3)
            continue
        action='daily_quiz_submit_'+key
        try:
            r.click('quiz_submit.bmp',(850,710,1000,785),45,20,action,['quiz_page.bmp'],True,
                    frame_predicate=lambda fresh: question_key(fresh) == key
                        and r.visible('quiz_page.bmp',PAGE,fresh))
        except (hs.AccountOffline,hs.BattleStalled):
            raise
        except hs.PendingError:
            if s.state.action_status(r.account.name,action)!='new':raise
            fresh=s.capture()
            changed=(r.visible('quiz_page.bmp',PAGE,fresh) and question_key(fresh)!=key)
            if not changed and not result_visible(s,fresh):raise
            submissions[key]='advanced_without_submit'
            r.save()
            r.event('daily_quiz_question_changed',question=key,submission_inputs=0,
                    detail='提交前题目已切换或结束，未重复提交上一题；继续核实本场测验')
            continue
        submissions[key] = 'submitted'
        r.save()
        r.event('daily_quiz_submitted', detail='已选第一项并提交，等待下一题', question=key)
        # Irreversible submissions already retain their guarded before frame.
        # Encoding a second full desktop PNG for every question is diagnostic
        # work, not part of result verification.
        if s.config.get('QuizDebugFrames', '0') == '1':
            s.capture().save(hs.LOGS/'quiz_latest.png')
        s.wait_interruptible(.4)
    raise hs.PendingError('忍者测验60秒未确认新题或完成结果')
