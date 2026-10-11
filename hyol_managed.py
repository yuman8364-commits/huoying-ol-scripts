"""Positively disable spending in the game's managed settings before starting."""
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


def prepare_non_spending_options(s, account):
    """Only click positively checked boxes; never toggle an unchecked option."""
    import hyol_scheduler as hs
    s.ensure_assets([TITLE, CHECKED, UNCHECKED] + ['managed_spend_'+key+'_label.bmp' for key,_,_ in OPTIONS])
    record = s.state.accounts[account.name].setdefault('managed_settings', {})
    record.update(status='pending', started_at=record.get('started_at', hs.now_iso()))
    frame = s.capture()
    try:
        for key, checkbox_roi, label_roi in OPTIONS:
            s.check_stop()
            frame = s.capture()
            state = option_state(s, key, checkbox_roi, label_roi, frame)
            if state == 'checked':
                record.update(phase='disable_'+key, requested_at=hs.now_iso())
                s.state.save_account(account.name)
                current_box, _ = option_rois(s, checkbox_roi, label_roi, frame)
                if not s.click_template(account, CHECKED, current_box, 7,7,
                                        'managed_disable_'+key,
                                        ['managed_spend_'+key+'_label.bmp'],
                                        threshold=.98, anchor_threshold=.98):
                    raise hs.PendingError('消费选项点击前状态变化：'+key)
                s.wait_interruptible(.35)
            # Independent captures prove off after both a new click and restart.
            for sample in range(2):
                frame = s.capture()
                if option_state(s, key, checkbox_roi, label_roi, frame) != 'unchecked':
                    raise hs.PendingError('托管消费选项尚未关闭：'+key)
                if sample == 0:
                    s.wait_interruptible(.35)
            record.setdefault('options', {})[key] = {'status':'disabled', 'verified_at':hs.now_iso()}
            s.state.save_account(account.name)
        # Check the complete panel again so an intervening repaint cannot turn
        # an earlier option back on while a later one was being processed.
        for _ in range(2):
            frame = s.capture()
            for key, checkbox_roi, label_roi in OPTIONS:
                if option_state(s, key, checkbox_roi, label_roi, frame) != 'unchecked':
                    raise hs.PendingError('托管最终禁用复核失败：'+key)
            s.wait_interruptible(.35)
        evidence = s.save_raw_evidence(frame, account, 'managed_spending_disabled', 'verified')
        record.update(status='done', phase='verified', evidence=str(evidence), verified_at=hs.now_iso())
        s.state.save_account(account.name)
        s.event('managed_spending_disabled', account, evidence=str(evidence), options=[key for key,_,_ in OPTIONS])
        return record
    except (Exception, KeyboardInterrupt) as exc:
        evidence = s.save_raw_evidence(frame, account, 'managed_settings_pending', 'pending')
        record.update(status='pending', last_error=str(exc), pending_evidence=str(evidence))
        s.state.save_account(account.name)
        raise
