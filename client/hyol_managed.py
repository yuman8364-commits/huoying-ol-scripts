"""Start managed mode with the user's existing in-game settings unchanged."""
TITLE = 'managed_settings_title_current.bmp'
TITLE_ROI = (750,190,1170,290)
TITLE_ORIGIN = (875,224)
CHECKED = 'managed_checkbox_checked_current.bmp'
UNCHECKED = 'managed_checkbox_unchecked_current.bmp'
OPTIONS = (
    ('org_wheel', (1007,357,1033,382), (1031,357,1114,382)),
    ('org_donation', (1007,391,1033,417), (1031,390,1140,417)),
    ('copper_gift', (1007,507,1033,533), (1030,506,1117,533)),
    ('merchant', (550,708,576,735), (576,710,640,735)),
    ('space_exchange', (662,708,690,735), (688,710,779,735)),
    ('market', (779,708,807,735), (807,710,866,735)),
)

CARD_PROMPT = 'managed_first_card_prompt_current.bmp'
CARD_CONFIRM = 'managed_first_card_confirm_current.bmp'
CARD_PROMPT_ROI = (800,490,1120,570)
CARD_CONFIRM_ROI = (800,565,950,625)
CARD_ACTION = 'start_managed_card_confirm'


def task_settled_for_managed(runner, task):
    """Allow a proven quota stop while keeping its unearned rewards pending."""
    from pathlib import Path
    rec = runner.record(task)
    if not rec.get('home_returned') or rec.get('followup_check_due'):
        return False
    if rec.get('status') == 'done' and rec.get('task_completed'):
        if task == 'daruma':
            from hyol_daruma_task import verified
            return verified(rec)
        if task == 'escort':
            from hyol_escort import verified
            return verified(rec)
        return task != 'training_record' or rec.get('all_goals_complete') is True
    if (task != 'arena' or rec.get('status') != 'pending'
            or rec.get('outcome') != 'quota_exhausted' or rec.get('task_completed') is not False
            or type(rec.get('tickets_remaining')) is not int or rec['tickets_remaining'] != 0):
        return False
    from hyol_arena_outcome import exhausted_record
    observed = exhausted_record(runner.scheduler, runner.account)
    if not observed:
        return False
    # Recheck the original scheduler receipts, not just the UI task record.
    # Missing files, a different day or an unknown battle/claim remain blockers.
    return all(rec.get(key) == observed.get(key) and bool(rec.get(key))
               and Path(rec[key]).is_file() for key in ('quota_evidence', 'home_evidence'))


def blocking_tasks(runner, tasks):
    return [task for task in tasks if not task_settled_for_managed(runner, task)]


def active_observation(s, frame):
    """Use the toolbar or the complete status panel as positive active proof."""
    score=s.managed_active_score(frame)
    def matched(name,roi):
        result=s.template(name,roi,frame,threshold=.98)
        return isinstance(result,(tuple,list)) and bool(result[0])
    panel=(score<.70 and matched('managed_status_title_current.bmp',(40,600,180,665))
           and matched('managed_status_exit_current.bmp',(100,675,215,740)))
    return dict(active=score>=.70 or bool(panel),badge_score=score,status_panel=bool(panel))


def first_card_prompt_visible(s, frame):
    """The complete first-day card text and its own button must agree."""
    return all(s.template(name, roi, frame, threshold=.98)[0] for name,roi in (
        (TITLE, TITLE_ROI), (CARD_PROMPT, CARD_PROMPT_ROI),
        (CARD_CONFIRM, CARD_CONFIRM_ROI)))


def validate_card_intent(s, account):
    """Tie the card confirmation to this day's original start receipt."""
    from pathlib import Path
    import re
    import hyol_scheduler as hs
    actions = s.state.accounts[account.name].get('actions', {})
    budget = s.state.accounts[account.name].get('network_retries', {}).get('start_managed', {})
    attempts = budget.get('attempts')
    day = s.state.day
    if (isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 3
            or budget.get('source_day') != day):
        raise hs.PendingError('托管卡确认缺少本日原启动预算，未点击')
    receipts = [(key,row) for key,row in actions.items()
                if re.fullmatch(r'start_managed_network_\d+', key)
                and isinstance(row, dict) and row.get('status') == 'pending']
    if not receipts:
        raise hs.PendingError('托管卡确认没有对应未决启动，未点击')
    for key,row in receipts:
        number = int(key.rsplit('_', 1)[1])
        if (not 1 <= number <= attempts or row.get('origin') or row.get('checkpoint_conflict')
                or not str(row.get('updated_at', '')).startswith(day+'T')
                or not Path(row.get('before_evidence', '')).is_file()):
            raise hs.PendingError('托管原启动凭据有冲突或缺失，未确认托管卡')
    previous = actions.get(CARD_ACTION, {})
    if previous and (previous.get('origin') or previous.get('checkpoint_conflict')
                     or not str(previous.get('updated_at', '')).startswith(day+'T')):
        raise hs.PendingError('托管卡原确认凭据存在冲突，保留待核实')


def confirm_first_card(s, account, frame):
    """Send one audited confirmation; disappearance alone never proves success."""
    import hyol_scheduler as hs
    if not first_card_prompt_visible(s, frame):
        return False
    validate_card_intent(s, account)
    status = s.state.action_status(account.name, CARD_ACTION)
    if status != 'new':
        return False  # An unconfirmed or already confirmed card is never spent again.
    def still_current(fresh):
        validate_card_intent(s, account)
        return (s.state.action_status(account.name, CARD_ACTION) == 'new'
                and first_card_prompt_visible(s, fresh))
    if not s.click_template(account, CARD_CONFIRM, CARD_CONFIRM_ROI, 50,16,
            CARD_ACTION, [TITLE, CARD_PROMPT, CARD_CONFIRM], irreversible=True,
            threshold=.98, anchor_threshold=.98,
            frame_predicate=still_current):
        raise hs.PendingError('托管卡确认提示在点击前变化，未重复启动')
    s.event('managed_first_card_confirmation_sent', account,
            detail='本日原启动对应的1张已有托管卡确认已发送，等待托管中十帧核实')
    return True


def reconcile_card_confirmation(s, account):
    """Called only after the primary ten-frame active gate has passed."""
    if s.state.action_status(account.name, CARD_ACTION) == 'pending':
        # The start network receipts may already be committed, so validate the
        # card receipt itself here rather than inventing another start intent.
        import hyol_scheduler as hs
        row = s.state.accounts[account.name]['actions'][CARD_ACTION]
        if (row.get('origin') or row.get('checkpoint_conflict')
                or not str(row.get('updated_at', '')).startswith(s.state.day+'T')):
            raise hs.PendingError('托管卡确认来源未核实，保留原记录')
        s.commit_action(account, CARD_ACTION, '原托管卡确认后托管中连续十帧核实')


def option_rois(s, checkbox_roi, label_roi, frame):
    """Keep each label and checkbox attached to the positively identified panel."""
    import hyol_scheduler as hs
    ok, _score, x, y = s.template(TITLE, TITLE_ROI, frame, threshold=.98)
    if not ok:
        raise hs.PendingError('托管设置正文或消费选项未确认')
    dx, dy = x-TITLE_ORIGIN[0], y-TITLE_ORIGIN[1]
    def shifted(roi):
        return roi[0]+dx, roi[1]+dy, roi[2]+dx, roi[3]+dy
    return shifted(checkbox_roi), shifted(label_roi)


def stopped_panel_visible(s, frame):
    """Require the known panel and orange start control, never badge absence alone."""
    import numpy as np
    if not s.template(TITLE, TITLE_ROI, frame, threshold=.98)[0]:
        return False
    ok, _score, x, y = s.template('managed_start_button.bmp', (651,619,1051,919), frame, threshold=.98)
    if not ok:
        return False
    asset = s.templates.load(__import__('hyol_scheduler').ASSETS/'managed_start_button.bmp')
    height, width = asset.shape
    crop = np.asarray(frame.crop((x,y,x+width,y+height)).convert('RGB'), dtype=np.int16)
    red, green, blue = crop[:,:,0], crop[:,:,1], crop[:,:,2]
    return float(((red >= 105) & (red >= green+18) & (red >= blue+30)).mean()) >= .12


def option_state(s, key, checkbox_roi, label_roi, frame):
    import hyol_scheduler as hs
    checkbox_roi, label_roi = option_rois(s, checkbox_roi, label_roi, frame)
    label = 'managed_spend_'+key+'_label.bmp'
    if (not s.template(TITLE, TITLE_ROI, frame, threshold=.98)[0]
            or not s.template(label, label_roi, frame, threshold=.98)[0]):
        raise hs.PendingError('托管设置正文或消费选项未确认：'+key)
    checked = s.template(CHECKED, checkbox_roi, frame, threshold=.98)[0]
    unchecked = s.template(UNCHECKED, checkbox_roi, frame, threshold=.98)[0]
    if checked == unchecked:
        raise hs.PendingError('托管消费选项状态未知或冲突：'+key)
    return 'checked' if checked else 'unchecked'


def preserve_existing_options(s, account):
    """Observe the start panel; never inspect/toggle settings as an execution gate."""
    import hyol_scheduler as hs
    frame = s.capture()
    if not stopped_panel_visible(s, frame):
        raise hs.PendingError('托管面板标题或开始按钮未确认；未更改任何设置')
    evidence = s.save_raw_evidence(frame, account, 'managed_settings_preserved', 'observed')
    s.event('managed_settings_preserved', account, evidence=str(evidence),
            detail='沿用游戏内现有设置，仅开始托管；设置修改点击0')
    return {'policy': 'preserve', 'evidence': str(evidence), 'settings_clicks': 0}


def prepare_non_spending_options(s, account):
    """Compatibility entrypoint: the current user policy always preserves settings."""
    return preserve_existing_options(s, account)


def exit_visible_status(s, account, frame):
    """Stop the small in-game status panel before managed movement changes it."""
    import time
    import hyol_scheduler as hs
    from hyol_navigation import hot_spring_visible,home_visible
    title='managed_status_title_current.bmp'
    button='managed_status_exit_current.bmp'
    title_roi=(40,600,180,665)
    button_roi=(100,675,215,740)
    def visible(current):
        return (s.template(title,title_roi,current,threshold=.98)[0]
                and s.template(button,button_roi,current,threshold=.98)[0])
    records=s.state.accounts[account.name]
    row=records.get('managed_status_exit',{})
    pending=row.get('status')=='pending'
    if not visible(frame) and not pending:return False
    action='managed_status_exit'
    if not pending:
        for sample in range(3):
            checks=dict(panel=visible(frame),active=active_observation(s,frame)['active'],
                        tab=s._generic_active_tab_x(frame)==account.tab_x,
                        identity=s.verify_identity(account,frame)[0],
                        world=s.main_ready(frame) or hot_spring_visible(s,frame) or home_visible(s,frame))
            if not all(checks.values()):
                evidence=s.save_raw_evidence(frame,account,'managed_status_guard','pending')
                raise hs.PendingError(f'托管状态面板、身份和退出按钮未连续确认，未停止托管：{checks}；证据={evidence}')
            if sample<2:
                s.wait_interruptible(.2);frame=s.capture()
        before=str(s.save_raw_evidence(frame,account,'managed_status_exit','before'))
        row=dict(status='pending',phase='exit_requested',before_evidence=before,at=hs.now_iso())
        records['managed_status_exit']=row;s.state.save_account(account.name)
        if not s.click_template(account,button,button_roi,36,12,action,[title,button],
                irreversible=True,threshold=.98,anchor_threshold=.98):
            raise hs.PendingError('退出托管按钮在点击前变化，保留退出请求')
    deadline=time.monotonic()+10;stable=0
    while time.monotonic()<deadline:
        s.check_stop();frame=s.capture()
        known=(s.main_ready(frame) or hot_spring_visible(s,frame) or home_visible(s,frame) or stopped_panel_visible(s,frame))
        valid=(known and not visible(frame) and s.managed_active_score(frame)<.70
               and s._generic_active_tab_x(frame)==account.tab_x and s.verify_identity(account,frame)[0])
        stable=stable+1 if valid else 0
        if stable==3:
            evidence=str(s.save_raw_evidence(frame,account,'managed_status_stopped','verified'))
            row.update(status='done',phase='stopped_verified',evidence=evidence,finished_at=hs.now_iso())
            s.state.save_account(account.name)
            s.commit_action(account,action,'退出按钮和托管标志消失，实名非战斗页面三帧确认')
            s.event('managed_status_stopped',account,evidence=evidence)
            return True
        s.wait_interruptible(.2)
    raise hs.PendingError('原退出托管请求尚未核实，不重复点击')
