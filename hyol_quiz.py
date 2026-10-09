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
    return (scheduler.template('quiz_finish.bmp',(550,300,1350,850),frame,threshold=.92)[0]
            and scheduler.template('quiz_zero.bmp',(1150,413,1250,445),frame,threshold=.95)[0]
            and scheduler.template('quiz_close.bmp',(1250,325,1350,410),frame,threshold=.90)[0])


def claimed_result_visible(scheduler, frame):
    return result_visible(scheduler, frame) and claim_button_disabled(frame)


def start_dialog_visible(scheduler, frame):
    return (scheduler.template('quiz_start.bmp',(800,540,1000,650),frame,threshold=.92)[0]
            and scheduler.template('quiz_npc_anchor.bmp',hs.VIEW,frame,threshold=.90)[0]
            and scheduler.template('quiz_task_link.bmp',(0,200,300,500),frame,threshold=.84)[0]
            and not scheduler.template('quiz_team_leave.bmp',(280,165,455,240),frame,threshold=.90)[0])


def finish_claimed_result(r, frame):
    s, rec = r.scheduler, r.record('quiz')
    s.save_raw_evidence(frame, r.account, 'quiz_claim_disabled', 'verified')
    for key, status in rec.get('submissions', {}).items():
        if status == 'submitted':
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

def question_key(frame):
    # The stable text area includes the question number, never the countdown.
    return hashlib.sha256(frame.crop(QUESTION).convert('L').tobytes()).hexdigest()[:20]

def run(r):
    s, rec = r.scheduler, r.record('quiz')
    initial = s.capture()
    if claimed_result_visible(s, initial):
        finish_claimed_result(r, initial)
        return
    if not solo_verified(r, initial):
        rec.update(status='pending', last_error='忍者测验仅限单人，当前未确认单人状态')
        r.save()
        raise hs.PendingError(rec['last_error'])
    if not r.visible('quiz_page.bmp',PAGE) and not r.visible('quiz_start.bmp',(800,540,1000,650)):
        r.click('quiz_task_link.bmp',(0,200,300,500),60,7,'daily_quiz_entry',
                ['quiz_task_link.bmp', 'quiz_npc_anchor.bmp'], threshold=.84,
                anchor_threshold=.84)
        deadline, total = time.monotonic()+60, time.monotonic()+300
        position = None
        while not r.visible('quiz_start.bmp',(800,540,1000,650)):
            frame = s.capture()
            new_position = frame.crop((1700,117,1905,137)).tobytes()
            if new_position != position:
                position = new_position
                deadline = time.monotonic()+60
            if time.monotonic() >= min(deadline,total):
                raise hs.PendingError('测验寻路未到达：60秒位置无进展或总寻路超过5分钟')
            s.wait_interruptible(.5)
    if r.visible('quiz_start.bmp',(800,540,1000,650)):
        r.once('quiz_start.bmp',(800,540,1000,650),40,10,'daily_quiz_start_'+s.diagnostics.identifier)
        r.wait('quiz_page.bmp',PAGE,12)
    deadline = time.monotonic()+60
    last_seen = None
    while time.monotonic() < deadline:
        s.check_stop()
        frame = s.capture()
        if not solo_verified(r, frame):
            raise hs.PendingError('测验中单人状态无法确认，停止答题')
        if r.visible('quiz_finish.bmp',(550,300,1350,850),frame,.92):
            if not r.visible('quiz_zero.bmp',(1150,413,1250,445),frame,.95):
                raise hs.PendingError('领取按钮出现但未确认剩余题目为0')
            s.save_raw_evidence(frame, r.account, 'quiz_zero', 'verified')
            for key, status in rec.get('submissions', {}).items():
                if status == 'submitted':
                    s.commit_action(r.account,'daily_quiz_submit_'+key,'剩余题目为0，答题流程已结束')
            r.click('quiz_finish.bmp',(550,300,1350,850),30,10,'daily_quiz_finish',['quiz_finish.bmp'])
            claimed = False
            claim_deadline = time.monotonic() + 12
            while time.monotonic() < claim_deadline:
                after = s.capture()
                if not r.visible('quiz_finish.bmp',(550,300,1350,850),after,.92):
                    claimed = True
                    break
                if claim_button_disabled(after):
                    claimed = True
                    s.save_raw_evidence(after, r.account, 'quiz_claim_disabled', 'verified')
                    break
                s.wait_interruptible(.3)
            if not claimed:
                raise hs.PendingError('领取后按钮仍可点击，未确认领取结果')
            finish_claimed_result(r, s.capture())
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
            raise hs.PendingError('选项点击时题目已变化，未提交')
        r.click('quiz_submit.bmp',(850,710,1000,785),45,20,'daily_quiz_submit_'+key,['quiz_page.bmp'],True)
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
