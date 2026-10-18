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
