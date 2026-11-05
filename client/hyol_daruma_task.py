"""Run the verified Daruma engine inside the current account and run directory."""
from __future__ import annotations

import hashlib
from datetime import datetime

import hyol_scheduler as hs
from hyol_accounts import game_session_token
from daruma_standalone import VERSION
from daruma_standalone.engine import DarumaRunner
from daruma_standalone.vision import IDENTITY, identity_mask


def verified(record):
    return (record.get('zero_verified') is True
            and type(record.get('remaining')) is int and record['remaining'] == 0
            and bool(record.get('zero_evidence')) and bool(record.get('checkpoint')))


def run(r):
    from hyol_daruma import _open_weekend
    # Explicit selection follows the live free activity page. Its reward and
    # chance controls, rather than the computer weekday, authorize each start.
    s, account = r.scheduler, r.account
    if s.state.day != datetime.now().date().isoformat():
        raise hs.PendingError('达摩运行日期已改变，请结束本轮后重新开始')
    _open_weekend(r)
    frame = s.content_guard(account, ['daruma_weekend_page.bmp', 'daruma_game_page.bmp'], anchor_threshold=.93)
    token = game_session_token(s)
    expected = dict(key=hashlib.sha256(f'{token}:{account.tab_x}'.encode()).hexdigest()[:24],
                    session=token, identity=identity_mask(frame.crop(IDENTITY)).astype(int).tolist())
    directory = s.shared_state_directory / 'daruma' / hs.slug(account.name)
    rec = r.record('daruma')

    def emit(row):
        fields = {key:value for key,value in row.items() if key not in ('event', 'run_id', 'at')}
        s.event('daruma_' + row['event'], account, task='daruma', child_run_id=row.get('run_id'), **fields)

    # Windows mutex ownership is recursive for this same worker thread.
    # Desktop takes/releases its own recursion, retaining the parent lease.
    request = getattr(r, 'training_request', None)
    request = request if isinstance(request, dict) and request.get('task') == 'daruma' else None
    quantity = dict(round_limit=request['required'] if request.get('unit') == 'count' else 1,
                    quantity_request_id=request['id']) if request else {}
    engine = DarumaRunner(s.hwnd, stop=s.stop, root=directory, sink=emit, expected_binding=expected, **quantity)
    try:
        result = engine.run()
    finally:
        desktop = engine.desktop
        if desktop is not None:
            s.content_click_count += desktop.content_clicks
            s.click_count += desktop.content_clicks
        if engine.journal is not None:
            data = engine.journal.data
            for action in [*data.get('actions', []), data.get('intent')]:
                if action and action.get('sent'):
                    s.event('content_click', account, target='daruma_' + action['id'],
                            irreversible=True, source='daruma_standalone', kind=action['kind'],
                            checkpoint=str(engine.journal.path))
    rec.update(engine_version=VERSION, checkpoint=result.get('checkpoint'), last_report=result)
    r.save()
    s.check_stop()
    if result.get('status') == 'request_satisfied' and request and engine.journal is not None:
        data = engine.journal.data
        if data.get('intent') or data.get('round_active'):
            raise hs.PendingError('达摩原局或敲击尚未结算，保留数量请求')
        count_unit = request.get('unit') == 'count'
        request.update(quantity_state='verified' if count_unit else 'requires_goal_rescan', available=data.get('chance'),
                       settled=quantity['round_limit'] if count_unit else None, scheduled=quantity['round_limit'],
                       quota_unit='round_chances',
                       evidence=str(engine.journal.path))
        rec.update(status='pending', task_completed=False, remaining=data.get('remaining'),
                   training_request_satisfied=request['id'])
        r.click('daruma_weekend_close.bmp', (1330,288,1385,345),16,16,
                'daruma_training_close',['daruma_weekend_page.bmp'],threshold=.93,anchor_threshold=.93)
        r.return_to_main();r.save()
        return
    if result.get('status') == 'stopped':
        raise hs.SafetyError(result.get('reason', '达摩安全停止，原动作保留'))
    if result.get('status') != 'done' or engine.journal is None:
        raise hs.PendingError(result.get('reason', '达摩结果仍未确认'))
    data = engine.journal.data
    if data.get('status') != 'done' or data.get('intent'):
        raise hs.PendingError('达摩存在未决动作，不能标记完成')
    rec.update(remaining=data.get('remaining'), zero_verified=data.get('zero_verified'),
               zero_evidence=data.get('zero_evidence'), completed=data.get('round', 0),
               chance=data.get('chance'), layers=data.get('layers'))
    r.save()
    if not verified(rec):
        raise hs.PendingError('达摩奖励归零证据不完整，不能标记完成')
    r.click('daruma_weekend_close.bmp', (1330,288,1385,345), 16,16,
            'daruma_close_verified_zero', ['daruma_weekend_page.bmp'], threshold=.93, anchor_threshold=.93)
    r.return_to_main()
    r.event('daruma_reward_zero_complete', task='daruma', completed=rec['completed'],
            remaining=0, checkpoint=rec['checkpoint'], evidence=rec['zero_evidence'])
