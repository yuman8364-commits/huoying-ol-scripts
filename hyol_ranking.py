"""Ranking daily: first opponent, end battle once, verify the game counter."""
import time
import hashlib
from pathlib import Path
import numpy as np
import hyol_scheduler as hs

PAGE = (790, 95, 1080, 195)
TOOLBAR = (980, 69, 1130, 150)
COUNTER = (1470, 153, 1586, 184)
DIGIT = (1563, 156, 1585, 183)
CURRENT_COUNTER = (1560, 156, 1586, 184)
# Both padded 09 and unpadded 9 occur in real captures. Search the number
# field rather than a fixed right-hand glyph; current whole-field templates
# take precedence so two-digit 10 cannot be mistaken for one glyph.
# The '+' purchase button starts to the right of 1586. It is never a digit.
HIGH_DIGIT = (1563, 156, 1586, 183)
FIRST = (535, 845, 681, 932)
END = (1410, 850, 1550, 980)
RESULT = (860, 640, 1060, 710)


def transition_loading_visible(frame):
    """Detect the cyan progress bar that can cover an otherwise valid village."""
    pixels = np.asarray(frame.crop((740, 530, 1180, 552)).convert('RGB'), dtype=np.float32)
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    cyan = (blue > red * 1.3) & (green > red * 1.2) & (blue > 100)
    return float(cyan.mean()) >= .08


def remaining(r, frame=None, *, save_unknown=True):
    frame = frame if frame is not None else r.scheduler.capture()
    if not r.visible('ranking_page.bmp', PAGE, frame, .95) or not r.visible('ranking_counter_label.bmp', COUNTER, frame, .95):
        raise hs.PendingError('排位赛页面或剩余挑战次数标签未确认')
    if not (hs.ASSETS/'ranking_count_10_current.bmp').exists():
        raise hs.PendingError('排位两位数10的实拍素材缺失，禁止退回单字形猜次数')
    matches = []
    for value in range(11):
        name = f'ranking_count_{value}_current.bmp'
        if (hs.ASSETS/name).exists() and r.visible(name, CURRENT_COUNTER, frame, .985):
            matches.append(value)
    if not matches:
        for value in range(6):
            name = f'ranking_digit_{value}.bmp'
            if (hs.ASSETS/name).exists() and r.visible(name, DIGIT, frame, .985):
                matches.append(value)
    if not matches:
        scores = []
        for value in range(6, 10):
            name = f'market_balance_digit_{value}.bmp'
            if (hs.ASSETS/name).exists():
                _, score, _, _ = r.scheduler.template(name, HIGH_DIGIT, frame, threshold=0)
                scores.append((score, value))
        scores.sort(reverse=True)
        if (scores and scores[0][0] >= .74
                and (len(scores) == 1 or scores[0][0] - scores[1][0] >= .08)):
            matches.append(scores[0][1])
    if len(matches) != 1:
        if save_unknown:
            path = r.scheduler.save_raw_evidence(frame, r.account, 'ranking_counter_unknown', 'pending')
            raise hs.PendingError(f'排位赛剩余次数无法唯一识别，不猜测、不补点：{path}')
        raise hs.PendingError('排位赛剩余次数无法唯一识别')
    return matches[0]


def stable_remaining(r, seconds=8):
    deadline = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            before = remaining(r, save_unknown=False)
            r.scheduler.wait_interruptible(.35)
            if remaining(r, save_unknown=False) == before:
                return before
            last_error = '排位赛剩余次数尚未稳定'
        except hs.PendingError as exc:
            last_error = str(exc)
        r.scheduler.wait_interruptible(.25)
    path = r.scheduler.save_raw_evidence(r.scheduler.capture(), r.account,
                                         'ranking_counter_unknown', 'pending')
    raise hs.PendingError(f'{last_error or "排位赛剩余次数尚未稳定"}；不猜测、不补点：{path}')


def manual_result_witness_valid(rec):
    path = rec.get('manual_defeat_result_evidence')
    digest = rec.get('manual_defeat_result_sha256')
    if not path or not digest:
        return False
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    except OSError:
        return False


def sent_result_can_reopen(r, rec):
    """Only a uniquely audited result from today may reopen its counter page."""
    if rec.get('phase') != 'result_sent':
        return False
    day = hs.date.today().isoformat()
    if r.scheduler.state.day != day or rec.get('continuation_day', day) != day:
        return False
    completed, before = rec.get('completed', 0), rec.get('before')
    if (not isinstance(completed, int) or isinstance(completed, bool) or completed < 0
            or not isinstance(before, int) or isinstance(before, bool) or not 1 <= before <= 10):
        return False
    base = f'daily_ranking_{completed+1}'
    result = r.scheduler.state.action_record(r.account.name, base+'_result')
    if result.get('status') not in ('pending', 'done'):
        return False
    for suffix in ('start', 'confirm', 'end', 'result', 'result_recover'):
        action = r.scheduler.state.action_record(r.account.name, base+'_'+suffix)
        if not action:
            continue
        if (action.get('checkpoint_conflict') or action.get('origin')
                or not str(action.get('updated_at', '')).startswith(day+'T')):
            return False
    try:
        path = Path(rec['counter_before_evidence'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != rec['counter_before_sha256']:
            return False
        with hs.Image.open(path) as image:
            return remaining(r, image.convert('RGB'), save_unknown=False) == before
    except (KeyError, OSError, ValueError, hs.PendingError):
        return False


def run(r):
    s, rec = r.scheduler, r.record('ranking')
    manual_result = rec.get('manual_defeat_result_evidence')
    recover_manual_result = (rec.get('phase') == 'confirmed'
        and manual_result_witness_valid(rec))
    recover_sent_result = sent_result_can_reopen(r, rec)
    if (rec.get('phase', 'ready') == 'ready' or recover_manual_result or recover_sent_result) and not r.visible('ranking_page.bmp', PAGE):
        if (recover_manual_result or recover_sent_result) and not s.main_ready():
            raise hs.PendingError('排位原结果虽已留证，但当前未回到主界面；保留未决动作')
        s.ensure_activity_toolbar(r.account, 'ranking_toolbar.bmp', TOOLBAR)
        r.click('ranking_toolbar.bmp', TOOLBAR,23,25,
                'ranking_open', ['anchor_top_menu.bmp'])
        r.wait('ranking_page.bmp', PAGE)
        if recover_sent_result:
            r.event('daily_ranking_result_counter_reopened', before=rec['before'],
                    detail='只重进原排位页核对次数；未重复开战或发送结算')
    deadline = time.monotonic() + 60
    main_seen_at = None
    while time.monotonic() < deadline:
        s.check_stop()
        frame = s.capture()
        phase = rec.get('phase', 'ready')
        index = rec.get('completed', 0) + 1
        base = f'daily_ranking_{index}'
        if (phase != 'ready' and s.main_ready(frame)
                and not r.visible('ranking_page.bmp', PAGE, frame, .95)):
            if transition_loading_visible(frame):
                main_seen_at = None
                s.wait_interruptible(.4)
                continue
            if main_seen_at is None:
                main_seen_at = time.monotonic()
            if time.monotonic() - main_seen_at < 3:
                s.wait_interruptible(.4)
                continue
            evidence = s.save_raw_evidence(frame, r.account, 'ranking_unresolved_on_main', 'pending')
            raise hs.PendingError(f'排位上次挑战结果未核实，当前已在主界面；保留未决动作：{evidence}')
        main_seen_at = None
        if r.visible('ranking_page.bmp', PAGE, frame, .95) and not r.visible('ranking_power_warning.bmp',(790,505,1130,560),frame,.95):
            try:
                count = stable_remaining(r)
            except hs.PendingError:
                if phase == 'ready':
                    raise
                # The challenge starts between two captures. Observe the
                # existing attempt; never interpret a repaint as zero/new work.
                s.wait_interruptible(.4)
                continue
            # The title and counter can paint before the opponent list.
            if count > 0 and not r.visible('ranking_challenge.bmp', FIRST, frame, .95):
                s.wait_interruptible(.4)
                continue
            if phase != 'ready':
                if count == rec.get('before'):
                    s.wait_interruptible(.4)
                    continue
                manual_decrement = (phase == 'confirmed'
                    and manual_result_witness_valid(rec))
                if (phase not in ('end_sent', 'result_sent') and not manual_decrement) or count != rec.get('before', -1)-1:
                    raise hs.PendingError('排位赛上次动作与次数变化不一致，禁止重复挑战')
                for suffix in ('start', 'confirm', 'end', 'result', 'result_recover'):
                    action = base+'_'+suffix
                    if s.state.action_status(r.account.name, action) == 'pending':
                        s.commit_action(r.account, action, f'结束战斗后返回排位页，次数{rec["before"]}→{count}')
                rec.update(completed=index, phase='ready', remaining=count)
                if manual_decrement:
                    rec['manual_result_reconciled'] = str(manual_result)
                    r.event('daily_ranking_manual_result_reconciled', completed=index,
                            before=rec['before'], remaining=count, evidence=str(manual_result))
                rec.pop('manual_defeat_result_evidence', None)
                rec.pop('manual_defeat_result_sha256', None)
                r.save()
                r.event('daily_ranking_battle_complete', completed=index, remaining=count,
                        detail=f'排位第{index}场结算已核实，剩余{count}次')
                deadline = time.monotonic()+60
                index += 1
                base = f'daily_ranking_{index}'
            if count == 0:
                evidence = s.save_raw_evidence(s.capture(),r.account,'ranking_zero','verified')
                rec.update(remaining=0, zero_verified=True, zero_evidence=str(evidence))
                r.save()
                r.click('ranking_close.bmp',(1600,95,1670,160),17,17,'ranking_close',['ranking_page.bmp'])
                if not s.wait_until_main(12):
                    raise hs.PendingError('排位次数归零，关闭后尚未确认主界面')
                return
            counter_evidence = s.save_raw_evidence(frame, r.account, 'ranking_before', 'observed')
            rec.update(before=count, phase='start_pending', zero_verified=False,
                       counter_before_evidence=str(counter_evidence),
                       counter_before_sha256=hashlib.sha256(Path(counter_evidence).read_bytes()).hexdigest())
            r.save()
            r.click('ranking_challenge.bmp',FIRST,30,10,base+'_start',['ranking_page.bmp'],True)
            deadline = time.monotonic()+60
        elif r.visible('ranking_power_warning.bmp',(790,505,1130,560),frame,.95):
            if phase != 'start_pending' or s.state.action_status(r.account.name,base+'_confirm') != 'new':
                raise hs.PendingError('排位确认已发送或无对应挑战，禁止重复确认')
            r.click('ranking_power_confirm.bmp',(810,560,940,610),30,10,
                    base+'_confirm',['ranking_power_warning.bmp'],True)
            rec['phase']='confirmed'
            r.save()
            deadline=time.monotonic()+60
        elif r.visible('ranking_result_confirm.bmp',RESULT,frame,.95) and r.visible('ranking_result_stats.bmp',(890,605,1035,653),frame,.95):
            if phase == 'result_sent' or (phase == 'result_pending' and s.state.action_status(r.account.name,base+'_result') == 'pending'):
                s.wait_interruptible(.4)
                continue
            if phase == 'result_pending' and s.state.action_status(r.account.name,base+'_result') == 'new':
                # A state-write failure before the click must not strand a safe result.
                phase = 'end_sent'
            defeat_without_end = (phase in ('start_pending', 'confirmed')
                and r.visible('arena_defeat_banner.bmp', (650, 320, 1250, 800), frame, .90))
            if phase != 'end_sent' and not defeat_without_end:
                raise hs.PendingError('未确认已点击结束战斗，且无明确失败结果；保留排位结果待核对')
            if defeat_without_end:
                r.event('daily_ranking_defeat_verified', detail='明确失败结果出现；无需补点结束战斗',
                        completed=index)
            # The result button can appear while the final skill dialogue still
            # intercepts mouse input. Let that animation settle before one click.
            s.wait_interruptible(3)
            if not r.visible('ranking_result_confirm.bmp',RESULT,threshold=.95):
                continue
            rec['phase']='result_pending'
            r.save()
            r.click('ranking_result_confirm.bmp',RESULT,55,15,base+'_result',['ranking_result_stats.bmp'],True)
            rec['phase']='result_sent'
            r.save()
            deadline=time.monotonic()+60
        elif phase in ('start_pending','confirmed','end_pending') and r.visible('ranking_battle_anchor.bmp',(1400,205,1510,251),frame,.95) and r.visible('ranking_end_battle.bmp',END,frame,.93):
            if phase == 'end_pending' and s.state.action_status(r.account.name,base+'_end') != 'new':
                s.wait_interruptible(.4)
                continue
            rec['phase']='end_pending'
            r.save()
            r.click('ranking_end_battle.bmp',END,40,32,base+'_end',
                    ['ranking_battle_anchor.bmp','ranking_end_battle.bmp'],True)
            rec['phase']='end_sent'
            r.save()
            deadline=time.monotonic()+60
        s.wait_interruptible(.4)
    raise hs.PendingError('排位赛60秒未确认下一阶段，保留场数及未决动作')
