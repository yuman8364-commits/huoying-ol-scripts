"""Chinese presentation of events; original diagnostics remain unchanged."""
import json
import re

LABELS = dict(mail='邮件', escort='押镖', ranking='排位赛', survival='生存演习',
              quiz='忍者测验', arena='竞技场', welfare='在线福利', managed='最后托管',
              group='组队副本', group_instance='组队副本', strong='强者', strong_approach='强者',
              wanted='通缉', bond='羁绊', arena_3v3='竞技场三对三', anniversary='薪火签到',
              training_record='修行录', puzzle='拼图', weekend='周末活动', treasure='挖宝',
              daruma='达摩', sealed='封印忍者', rescue_jinchuriki='营救人柱力', team_pvp='组队竞技',
              all='本轮任务', main='木叶村', ready='准备就绪', running='执行中',
              done='已完成', complete='已完成', pending='待核实', skipped='已跳过',
              offline='已掉线', stopped='已停止', new='未执行', unknown='尚未确认')
LEVEL_LABELS = dict(info='信息', success='已确认', warning='注意', error='异常', debug='诊断')
EVENTS = dict(account_bound='账号身份已核实', dynamic_accounts_bound='本轮账号已关联',
              account_verified='账号身份已核实', identity_bound='账号身份已核实',
              return_leaf_verified='已确认返回木叶村', combat_battle_started='战斗已开始',
              combat_battle_finished='本场战斗已结算', unified_idle='当前操作已结束',
              multi_solo_prepared_return_reused='复用本次回村检查，直接开始任务',
              multi_solo_run_started='开始执行本轮任务', multi_solo_run_end='本轮执行结束',
              preparation_return_started='正在返回木叶村', preparation_account_result='本号启动检查结束',
              unified_run_started='已创建本轮运行', unified_stop_requested='已请求停止，正在保存断点',
              daily_task_started='开始执行任务', daily_task_complete='任务完成并已返回木叶村',
              daily_summary='本轮执行结果已保存', unified_finished='本轮执行结束',
              treasure_timer='正在挖宝，等待本轮计时结束',
              survival_rewards_already_verified='生存奖励已核实，复用原领取记录',
              survival_highest_grade_selected='已选择最高可挑战等级',
              survival_second_run_started='生存第二轮已开始',
              survival_first_run_archived='生存第一轮记录已归档',
              survival_reward_observed='生存奖励状态与来源已核实',
              daruma_catalogue_page_observed='达摩活动目录已核实',
              treasure_trap_naturally_advanced='挖宝陷阱已自然推进，继续原流程',
              training_series_boundary_verified='修行录新解锁目标已核实，继续执行本号计划',
              training_scan_progress='正在核实修行录目标列表',
              training_task_resumed='修行录原任务已恢复观察',
              training_scan_panel_reused='复用当前修行录任务页，核实领奖后的目标',
              combat_module_done='本项战斗及收尾已核实',
              irreversible_committed='原操作结果已核实',
              daily_ranking_waiting_result_yield='排位赛等待原场结算，继续观察其他账号',
              daily_ranking_end_sent_yield='排位赛已结束原场，等待结算确认',
              daily_ranking_battle_complete='排位赛本场结果已核实',
              daily_quiz_submitted='测验答案已提交，等待结果',
              log_write_warning='运行日志写入异常', log_write_recovered='运行日志写入已恢复')

_ERRORS = (
    (r'disk full|No space left|磁盘.*(?:满|空间)',
     '磁盘空间不足，记录未能保存。', '请释放客户端所在磁盘的可用空间，保留原文件后重新保存。'),
    (r'身份发生变化|identity mismatch|identity.*changed|identity.*ambiguous|identity.*gap|identity.*conflict',
     '当前标签与已核实账号身份不一致，已停止当前操作。', '请恢复原账号及标签位置，再核实原任务；不要重新发送未决操作。'),
    (r'lost foreground|失去前台|失焦|foreground',
     '游戏窗口失去前台，当前操作已暂停。', '请将原游戏窗口恢复到前台，再检查原任务状态。'),
    (r'daruma_weekend_menu_core|达摩.*入口|周末.*入口',
     '尚未确认周末活动中的达摩入口。', '请检查周末活动是否开放及活动目录位置；原任务保留待核实。'),
    (r'页面未确认|page.*(?:missing|unknown|not.*visible)|anchor.*missing',
     '目标页面尚未确认，当前操作已暂停。', '请查看证据中的页面或弹窗，恢复原任务页面后继续核实。'),
    (r'identity_strip_missing|identity strip|active-tab visual gate|标签.*变化|tab.*(?:layout|missing|conflict)',
     '账号标签或身份区域未能稳定识别，未发送新的操作。', '请保持游戏窗口大小和标签顺序稳定，恢复可见身份区域后重试检查。'),
    (r'offline|manual login|掉线|登录|saved-session Start Game',
     '当前账号需要重新登录，已保留原任务。', '请在原标签完成登录，然后核实原场结果后继续。'),
    (r'unknown.*(?:outcome|prior)|outcome unknown|未决|refusing duplicate',
     '原操作结果尚未核实，未重复发送。', '请先核对原战斗、领奖或出发结果，保留当前断点。'),
    (r'PermissionError|Permission denied|Access is denied|WinError 5|拒绝访问',
     '文件访问被拒绝，记录未能正常保存。', '请检查客户端目录权限或文件占用，保留原文件后重新保存。'),
    (r'FileNotFoundError|No such file|cannot find|找不到',
     '所需文件未找到，当前操作未完成。', '请使用完整客户端并检查资源文件是否齐全。'),
    (r'GetWindowRect|window.*(?:ambiguous|bound|unavailable)|窗口.*(?:不可用|变化)|geometry changed',
     '游戏窗口无法可靠定位，当前操作已暂停。', '请恢复原游戏窗口及大小，关闭遮挡后重新检查。'),
    (r'SetCursorPos|hit test|input.*failed',
     '无法确认游戏输入位置，未继续操作。', '请恢复游戏窗口后重新检查；原任务状态保留。'),
    (r'survival.*reward|生存.*奖励|生存.*领奖',
     '生存奖励结果尚未核实，原领取操作保留。', '请等待原奖励弹窗或已领取状态出现，不要重复领取。'),
    (r'log.*write|日志.*写入|fallback',
     '运行日志写入异常，已尝试保存备用诊断。', '请查看诊断中的保存位置，并检查磁盘空间和文件占用。'),
)


def task_label(value):
    if value == 'grocery':return '杂货屋'
    return LABELS.get(str(value or ''), '当前任务' if value else '')


def chinese_text(value, fallback='状态尚未核实，请展开诊断查看原始记录。'):
    """Keep Chinese explanations but move identifiers and paths to diagnostics."""
    raw = str(value or '').strip()
    if not raw:
        return ''
    text = re.sub(r'\b(?:SafetyError|PendingError|RuntimeError|ValueError|OSError|TypeError|LeaseBlocked):\s*', '', raw)
    text = re.split(r'[；;]\s*(?:截图|画面|证据|evidence)\s*[=:：]', text, maxsplit=1)[0]
    text = re.sub(r'[A-Za-z]:[\\/][^\n；]+', '（本地文件，见诊断）', text)
    text = re.sub(r'\b[\w.-]+\.(?:bmp|png|jpg|jsonl|json|py)\b', '（识别素材，见诊断）', text)
    for key in sorted(LABELS, key=len, reverse=True):
        text = re.sub(r'(?<![\w])' + re.escape(key) + r'(?![\w])', LABELS[key], text)
    text = text.replace('3v3', '三对三').replace('x2', '二倍速').replace('x1', '一倍速')
    check = re.sub(r'\b(?:SS|S|A|B|C|QQ|F12|VIP|OpenCV|YOLO)\b', '', text)
    if not re.search(r'[\u3400-\u9fff]', text) or re.search(r'[A-Za-z_]{2,}', check):
        return fallback
    return text


def error_message(value, fallback='当前操作未完成。'):
    raw = str(value or '')
    for pattern, message, suggestion in _ERRORS:
        if re.search(pattern, raw, re.I):
            return message, suggestion
    return chinese_text(raw, fallback) or fallback, '请查看原始诊断和图片证据，核实当前状态后继续。'


def present_event(event):
    name = str(event.get('event', ''))
    raw = str(event.get('detail') or event.get('reason') or event.get('message') or '')
    if name == 'daily_status_observed':
        raw = raw or event.get('observation',{}).get('label','日常状态检查结束')
    task = str(event.get('task') or event.get('mode') or '')
    label = task_label(task)
    failure = any(k in name for k in ('error', 'failed', 'failure', 'disconnected', 'offline'))
    pending = any(k in name for k in ('pending', 'skipped', 'stop', 'not_open', 'unavailable'))
    if name.endswith(('summary', 'finished')) and event.get('result') not in (None, 0):
        pending = True
    diagnostic = json.dumps(event, ensure_ascii=False, default=str, sort_keys=True)
    suggestion = ''
    if failure or pending:
        message, suggestion = error_message(raw, '当前操作出现异常。' if failure else '当前任务保留待核实。')
        if 'stop' in name and not failure:
            message, suggestion = chinese_text(raw, EVENTS.get(name, '已停止，原断点保留。')), ''
        level, debug = ('error' if failure else 'warning'), False
    else:
        message = chinese_text(raw, '') if raw else ''
        debug = False
        if not message:
            message = EVENTS.get(name, '')
        if not message:
            for suffix, title in (('_stage_started', '开始任务阶段'), ('_stage_finished', '任务阶段结束'),
                                  ('_task_started', '开始执行'), ('_task_complete', '完成并核实'),
                                  ('_waiting', '等待原操作结果')):
                if name.endswith(suffix):
                    message = title + ('：' + label if label else '')
                    break
        if not message:
            message, debug = '已记录运行诊断。', True
        level = 'debug' if debug else 'success' if any(k in name for k in ('complete', '_done', '_verified')) else 'info'
    repeatable = name in ('account_bound', 'account_verified', 'identity_bound', 'combat_account_bound',
                         'combat_account_bound_by_page', 'treasure_timer', 'daily_ranking_waiting_result_yield') or name.endswith('_waiting')
    return dict(message=message, suggestion=suggestion, diagnostic=diagnostic, level=level,
                debug=debug, task=task, task_label=label, repeatable=repeatable)


def describe_event(event):
    """Compatibility for legacy callers; error visibility never depends on language."""
    row = present_event(event)
    return row['message'], row['debug']
