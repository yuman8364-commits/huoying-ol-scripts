"""Write a source-audited offline model report without changing activation.

Each run keeps a new machine-readable evidence file. MODEL_EVALUATION.md is the
latest report pointer; previous evidence and frozen model assets are preserved.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_model_gate import ROOT, audit_model


def render_report(result, evidence_path):
    """Render audited counts rather than hard-coding a particular data revision."""
    gates = result['gate_cases']
    total = result['total']
    splits = result['splits']
    recall = total['tp']/(total['tp']+total['fn']) if total['tp']+total['fn'] else None
    recall_text = f'{recall:.1%}' if recall is not None else '无正样本'
    validation = result['validation_replay']
    lines = ['# 多类模型评估', '', f"> 更新：{result['generated_at']}", '',
             '模型质量与实机验收保持 pending；候选与当前发布模型均为 shadow，点击授权为空。', '',
             '## 数据与复验', '',
             f"- {splits['unique_images']} 幅经复核的实拍，{len(result['test'])} 类；训练 {splits['images']['train']} 幅、验证 {splits['images']['val']} 幅、独立测试 {splits['images']['test']} 幅。",
             '- 原图、导出图、标注、日期分组及哈希复核通过；训练参数与真实训练/验证目录对账通过。',
             '- 当前只有采集日分组，未提供精确拍摄会话 ID；日期隔离不能证明跨午夜会话未泄漏，用户要求的会话隔离仍待补证。',
             f"- 已完成 {result['completed_epochs']} 轮训练；本次 CPU ONNX 重跑 {validation['images']} 幅验证图的 {validation['trial_count']} 项原阈值试验，并与冻结表逐项一致；未修改阈值。",
             f"- 本次重跑 {splits['images']['test']} 幅独立测试，计数与冻结测试结果一致；测试图未用于选择阈值。",
             f"- 合计 TP {total['tp']}、FP {total['fp']}、FN {total['fn']}；召回率 {recall_text}。", '',
             '| 类别 | TP | FP | FN | 固定阈值 | 运行状态 |', '|---|---:|---:|---:|---:|---|']
    for target, row in result['test'].items():
        state = '校准禁用' if target in result['disabled'] else 'shadow'
        lines.append(f"| `{target}` | {row['tp']} | {row['fp']} | {row['fn']} | {result['thresholds'][target]:.3f} | {state} |")
    lines += ['', '## 安全门', '',
              f"- {gates['authorization_contract']} 项授权契约、{gates['fusion']} 项融合拒绝、{gates['real_held_out_model_only']} 项真实测试画面模型独立候选检查通过，失败 {len(gates['failures'])}。",
              '- 上述审计覆盖缺失/禁用素材、歧义候选、模板与模型冲突、错误模型哈希及缺少实机凭据；拒绝场景均未授权模型点击。',
              '- 灰按钮与第二帧不一致的真实画面重放由 `test_hyol_model_gate.SchedulerModelGateTests` 单独验证，不计入上述纯审计项，也不冒充逐类实机验收。',
              '- 合成实机凭据仅存在于单元测试内存，用于验证契约；实际模型点击授权未修改，实际游戏输入为 0。', '',
              '## 仍缺的类别', '', '| 类别 | 训练 | 验证 | 测试 |', '|---|---:|---:|---:|']
    for target, row in result['pending_classes'].items():
        lines.append(f"| `{target}` | {row['train']} | {row['val']} | {row['test']} |")
    latency = result['current_cpu_latency_ms']
    lines += ['', '## 性能与边界', '',
              f"本次 CPU 推理均值 {latency['mean']:.1f} ms、最大 {latency['max']:.1f} ms；这是离线单图推理，不是完整 GUI 或游戏任务耗时。",
              f"{total['fp']} 次误检仅适用于这 {splits['images']['test']} 幅测试图，不能推断所有游戏场景零误点击。{len(result['disabled'])} 类禁用、{len(result['pending_classes'])} 类缺少三分集样本、{total['fn']} 次漏检、精确采集会话记录及逐类实机闭环仍未完成，模型不得晋级为可点击。", '',
              f"模型 SHA256：`{result['hashes']['model']}`。", '',
              f'机器证据：`{evidence_path}`；复跑入口：`tools/write_model_evaluation.py`。',
              '最终报告仅在全部范围内实机验收完成后生成。', '']
    return '\n'.join(lines)


def write_report(root=ROOT, *, evidence_path=None):
    root = Path(root)
    generated_at = datetime.now().astimezone()
    output = Path(evidence_path) if evidence_path is not None else root/'logs'/(
        'model_gate_'+generated_at.strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid.uuid4().hex[:8]+'.json')
    if output.exists():
        raise ValueError('preserve prior evidence; use a new output path')
    result = audit_model(root, replay=True)
    result['generated_at'] = generated_at.isoformat(timespec='seconds')
    if result['offline_result'] != 'PASS':
        raise RuntimeError('Model evidence audit failed; report not upgraded')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        evidence_label = output.relative_to(root).as_posix()
    except ValueError:
        evidence_label = str(output)
    rendered = render_report(result, evidence_label)
    # Exclusive create preserves the prior record even if two offline audit runs
    # choose the same explicit path between the precheck and final write.
    with output.open('x', encoding='utf-8') as source:
        source.write(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    (root/'MODEL_EVALUATION.md').write_text(rendered, encoding='utf-8')
    return {'report': str(root/'MODEL_EVALUATION.md'), 'evidence': str(output), 'offline_result': result['offline_result'],
                      'model_quality_pass': result['model_quality_pass'], 'live_pass': False,
                      'total': result['total'], 'activation_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--evidence', type=Path, help='new evidence file; an existing file is never overwritten')
    args = parser.parse_args()
    print(json.dumps(write_report(args.root, evidence_path=args.evidence), ensure_ascii=False))


if __name__ == '__main__':
    main()
