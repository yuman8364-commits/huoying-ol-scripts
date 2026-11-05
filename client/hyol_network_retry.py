"""Bounded, visually guarded retries for user-authorized network failures."""
import time
import re


def run(s, account, label, title, succeeded, ready, click):
    from hyol_scheduler import PendingError
    actions = s.state.accounts[account.name].get('actions', {})
    family = re.compile(re.escape(label)+r'_network_\d+')
    conflicts = [key for key, value in actions.items()
                 if (key == label or family.fullmatch(key))
                 and isinstance(value, dict) and value.get('checkpoint_conflict')]
    if conflicts:
        # An aggregate result cannot settle several distinct original intents.
        # Check before creating/updating the budget or sending another attempt.
        s.event('network_retry_conflict', account, target=label, conflicts=conflicts)
        raise PendingError('动作存在多个未决来源，领取/重试前停止并保留原预算：' + ', '.join(conflicts))
    records = s.state.accounts[account.name].setdefault('network_retries', {})
    rec = records.setdefault(label, {'attempts': int(s.state.action_status(account.name, label) == 'pending')})
    day = getattr(s.state, 'day', None)
    if isinstance(day, str):
        rec.setdefault('source_day', day)
    attempts = rec.get('attempts')
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 0 <= attempts <= 3:
        raise PendingError('网络重试预算格式异常，保留断点：' + label)
    observed = [int(match.group(1)) for action, value in actions.items()
                if (match := re.fullmatch(re.escape(label)+r'_network_(\d+)', action))
                and isinstance(value, dict) and value.get('status') in ('pending', 'done')]
    if observed and max(observed) > 3:
        raise PendingError('原网络重试链超过安全预算，保留断点：' + label)
    rec['attempts'] = max([attempts, *observed])
    started = time.monotonic()
    last_click = started if rec['attempts'] else started - 10
    while time.monotonic() - started < 45:
        s.check_stop()
        if succeeded():
            s.commit_action(account, label, title + '结果已正向确认')
            for number in range(1, rec['attempts'] + 1):
                attempt_label = f'{label}_network_{number}'
                if s.state.action_status(account.name, attempt_label) == 'pending':
                    s.commit_action(account, attempt_label, '重试链最终结果已确认')
            rec['verified'] = True
            s.state.save_account(account.name)
            return
        if time.monotonic() - last_click >= 10:
            if rec['attempts'] >= 3:
                break
            if ready():
                # Persist BEFORE input; interruption cannot reset the click budget.
                rec['attempts'] += 1
                rec['verified'] = False
                s.state.save_account(account.name)
                s.event('daily_network_retry', account, target=label, attempt=rec['attempts'],
                        detail=f'{title}：第{rec["attempts"]}/3次点击，间隔至少10秒')
                if not click(f'{label}_network_{rec["attempts"]}'):
                    raise PendingError('游戏报错：' + title + '按钮在复核时消失，已停止')
                last_click = time.monotonic()
        s.wait_interruptible(.5)
    s.save_raw_evidence(s.capture(), account, label + '_network_failed', 'pending')
    raise PendingError(f'游戏报错：{title}点击{rec["attempts"]}/3次后仍未确认结果（最多等待45秒）')
