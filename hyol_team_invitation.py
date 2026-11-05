"""Recognize the wrapped invitation without relying on a player's name."""
import hashlib


JOIN = 'team_invite_join_text_20261031.bmp'
QUESTION = 'team_invite_question_suffix_20261031.bmp'
YES = 'team_invite_accept_yes.bmp'
NO = 'team_invite_decline_no_current.bmp'
ROI = (780, 480, 1140, 630)


def inspect(frame, templates):
    import hyol_scheduler as hs
    def match(asset):
        path = hs.ASSETS / asset
        return templates.match(frame, path, ROI, threshold=.98) if path.exists() else (False,0,0,0)
    join, question, yes, no = (match(name) for name in (JOIN, QUESTION, YES, NO))
    if not all(row[0] for row in (join, question, yes, no)):
        return None
    jx,jy = join[2:]; qx,qy = question[2:]; yx,yy = yes[2:]; nx,ny = no[2:]
    if (abs(qx-jx-1)>3 or abs(qy-jy-19)>3 or abs(yx-jx+106)>3
            or abs(yy-jy-50)>3 or abs(nx-yx-169)>3 or abs(ny-yy)>3):
        return None
    body = frame.crop((jx-135,jy-4,jx+200,jy+39)).convert('RGB')
    return dict(fingerprint=hashlib.sha256(body.tobytes()).hexdigest(),
                no_roi=(nx-3,ny-3,nx+106,ny+39), position=(jx,jy))


def decline(s, account, frame):
    """One write-ahead decline, outside any expected member invitation."""
    import hyol_scheduler as hs
    expected = getattr(s,'expected_invitation_members',None)
    if not isinstance(expected,set) or account.name in expected or s.no_content_clicks:
        return False
    modal = inspect(frame,s.templates)
    if not modal:
        return False
    # A resumed send may precede rebuilding the in-memory expected set.
    # Preserve every original invitation intent until membership reconciles it.
    if any('invite' in key and 'decline' not in key and value.get('status') == 'pending'
           for row in s.state.accounts.values() for key,value in row.get('actions',{}).items()):
        return False
    if (s.active != account or not s.tab_is_visually_active(account,frame)
            or not s.verify_identity(account,frame)[0]):
        raise hs.PendingError('外部邀请接收账号未核实，未拒绝')
    action = 'unsolicited_team_invite_decline_' + modal['fingerprint'][:20]
    if s.state.action_status(account.name,action) != 'new':
        raise hs.PendingError('原邀请拒绝结果仍待核实，不重复点击')
    def same(fresh):
        return (inspect(fresh,s.templates) == modal
                and account.name not in s.expected_invitation_members
                and s.verify_identity(account,fresh)[0]
                and s.tab_is_visually_active(account,fresh))
    s._approved_invitation_decline = (account.name,modal)
    try:
        if not s.click_template(account,NO,modal['no_roi'],50,17,action,[QUESTION],
                                irreversible=True,threshold=.98,anchor_threshold=.98,
                                frame_predicate=same):
            raise hs.PendingError('外部邀请在拒绝前变化，保留原弹窗')
        if not s.wait_absent(QUESTION,ROI,3,threshold=.98):
            raise hs.PendingError('原邀请拒绝尚未确认，不重复点击')
        s.commit_action(account,action,'完整外部邀请及否按钮核实，弹窗已消失')
        s.event('unsolicited_team_invite_declined',account,fingerprint=modal['fingerprint'])
        return True
    finally:
        s._approved_invitation_decline = None
