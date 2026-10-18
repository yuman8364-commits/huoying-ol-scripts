"""Read-only consolidation of this scheduled run's real evidence and limits."""
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hyol_scheduler as hs


def load(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def build():
    logs = ROOT/'logs'
    current_hashes = {path.name:hashlib.sha256(path.read_bytes()).hexdigest()
        for path in ROOT.glob('hyol*.py')}
    history = []
    latest = {}
    for path in sorted(logs.glob('solo_*_acceptance_20261005_*.json')):
        row = load(path)
        name = row.get('account') or row.get('expected_account')
        if not name or not row.get('task'):
            continue
        row['report_path'] = str(path)
        run_id = row.get('run_id', path.stem.split('acceptance_')[-1])
        journal = logs/('HYOL_Scheduler_'+run_id+'.jsonl')
        events = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()] if journal.exists() else []
        row['irreversible_inputs'] = [event['target'] for event in events
            if event.get('event') == 'content_click' and event.get('irreversible')]
        row['recognition_retries'] = sum(event.get('event') == 'recognition_retry' for event in events)
        row['page_anchor_retries'] = sum(event.get('event') == 'page_anchor_retry' for event in events)
        row['source_differences'] = [name for name,digest in row.get('source_sha256',{}).items()
            if current_hashes.get(name) != digest]
        row['captured_source_snapshot_current'] = bool(row.get('source_sha256')) and not row['source_differences']
        row['confirmed_events'] = [{key:event[key] for key in ('event','target','detail','evidence','remaining','completed') if key in event}
            for event in events if event.get('event') in ('mail_empty_attachments', 'mail_claim_response',
                'welfare_all_claims_verified', 'daily_quiz_submitted', 'daily_ranking_battle_complete',
                'daily_formation_verified', 'managed_exited_for_market', 'network_retry_conflict')]
        history.append(row)
        key = (name, row['task'])
        if key not in latest or run_id > latest[key].get('run_id',''):
            latest[key] = row
    performance = load(logs/'solo_recognition_paired_20261005_0400.json')
    frames = performance.get('frames', [])
    before = statistics.median(row['reference_median_ms'] for row in frames)
    after = statistics.median(row['median_ms'] for row in frames)
    releases = sorted(logs.glob('solo_release_verified_20261005_*.json'))
    release_path = releases[-1] if releases else logs/'solo_release_verified_20261005_0400.json'
    report = {'status': 'partial', 'updated_at': hs.now_iso(), 'source_version': __import__('hyol_daily_tasks').VERSION,
        'scope': 'single_player', 'multiplayer_started': False, 'purchase_enabled': False,
        'scheduled_trigger': load(logs/'codex_schedule_setup/triggered_20261005_0400.json'),
        'source_sha256': current_hashes,
        'latest': list(latest.values()), 'history': history,
        'original_ledger': load(logs/'solo_original_ledger_20261005_0400.json'),
        'recognition_comparison': {'before_median_ms': before, 'after_median_ms': after,
            'reduction_percent': (1-after/before)*100, 'frames': len(frames),
            'all_scores_decisions_coordinates_equal': all(row['paired_decisions_scores_coordinates_identical'] for row in frames),
            'evidence': str(logs/'solo_recognition_paired_20261005_0400.json'),
            'limit': 'fixed real screenshots; this is not a full game-task speed comparison'},
        'release': load(release_path), 'release_evidence': str(release_path),
        'original_pending_audit': load(sorted(logs.glob('solo_original_pending_audit_20261005_*.json'))[-1]),
        'diagnostic_ledger_recovery': load(logs/'solo_ledger_recovery_latest.json'),
        'final_managed': 'deferred_original_combat_results_pending',
        'remaining': ['原竞技场/生存结果与冲突逐项对账，不能用木叶画面结清',
            '生存每场最高可挑战、自动战斗、明确结果、220分领奖及完整回村',
            '竞技场追加胜场与两项奖励完整闭环',
            '单人挖宝已有地图300秒、实际暂停及回村；不执行多人同时挖宝',
            '周末三个难度与达摩逐账号活动日实机',
            '全部所选任务和回村完成后的真实checkbox托管链',
            '单人组合、其他重要脚本及三尺寸/DPI的实机验收']}
    output = logs/'SOLO_OPTIMIZATION_20261005_0400.json'
    hs.atomic_json(output, report)
    lines = ['# 2026-10-05 04:00单人优先优化与实测', '',
        f"更新时间：{report['updated_at']}；源码版本：{report['source_version']}；总体：pending。", '',
        'Codex原生一次性任务已触发，COUNT=1，next_run_at=null；旧Windows定时保持Disabled。',
        '本轮保持单人范围，禁购、不登录、不清原断点。身份、页面或次数不明保留pending。', '',
        '本轮改动：同帧ROI复用FFT/积分数组；退出托管确认停止后关闭设置页；旧竞技场缺结果继续pending；网络重试在改预算和领取前拒绝多来源冲突；周末总入口包含拼图公告05:00边界，各活动分别验收。', '',
        f"9张真实截图的同帧对照：{before:.0f}→{after:.0f}ms，减少{(1-after/before)*100:.1f}%；分数、判定、坐标全部一致。此数值仅代表识别阶段，未宣称整轮游戏同幅提速。", '',
        '| 账号 | 项目 | 实测源码版本 | 本轮状态 | 总耗时秒 | 真实不可逆输入 | 证据 |',
        '|---|---|---|---|---:|---|---|']
    for row in sorted(latest.values(), key=lambda item:(item.get('account') or item['expected_account'],item['task'])):
        name = row.get('account') or row['expected_account']
        inputs = ', '.join(row['irreversible_inputs']) or '0'
        reason = row.get('reason','').replace('|','/')
        status = row['status']+(('：'+reason) if reason else '')
        lines.append(f"| {name} | {row['task']} | {row.get('source_version','unknown')} | {status} | {row.get('elapsed_seconds',0):.1f} | {inputs} | [{Path(row['report_path']).name}](<{row['report_path']}>) |")
    lines.extend(['', '通过项均保留入口/操作JSONL以及三帧身份、主界面、木叶证据。无附件和既有已领取态属于对应状态/关页核验，不能称为新领取。页面观察、次数已用尽、日历门跳过均不能替代战斗/活动闭环。',
        '耗时含账号重新绑定和安全复核；metrics中的嵌套调用耗时不能相加。每阶段耗时、截图/识别/重试次数保存在逐项JSON中。', '',
        '该账号原邮件多来源冲突本轮触发后未结清，旧根动作及新发送的network_2均保留pending，已消耗预算为2，未恢复为0；修复后冲突检查先于领取。所有未决竞技场/生存均未补胜、未重置。', '',
        '诊断批次工具曾误用路径覆盖1区/2区任务账本；已保存原文件、恢复历史未决及本轮邮件/福利完成证据，动作文件哈希不变。排位开战前真实10曾被单字形识别误报8，原战已发送结算但未重复发起，按证据恢复result_sent等待当前结果核对。恢复及原57个未决动作审计见JSON。', '',
        '每项实拍表明采集时的源码版本；旧快照和已完成票据保留，其source_differences列明与当前源码的差异，不能整体冒充新版全流程通过。', '',
        '当前回归：`solo_final_release_regression_20261005_0546.log`（756单测+39拼图专项、11功能/静态/模板PASS，270.4秒）；周末入口208项交互回归：`solo_weekend_boundary_regression_20261005_0456.log`。',
        f"发布核验：`{release_path.name}`（{report['release'].get('Result','pending')}）。五EXE源码/桌面-dist哈希、依赖、素材和七快捷方式实际启动逐项记录；启动时未点击开始，不能代替游戏闭环。", '',
        '仍待验：']+['- '+item for item in report['remaining']]+['', f"完整逐项数据：`{output.name}`；当前入口：项目根目录`PROGRESS.md`和`ACCEPTANCE.md`。"])
    document = logs/'SOLO_OPTIMIZATION_20261005_0400.md'
    document.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'json':str(output),'report':str(document),'latest_items':len(latest),
        'live_pass':sum(row.get('status')=='live_pass' for row in latest.values())},ensure_ascii=False))


if __name__ == '__main__':
    build()
