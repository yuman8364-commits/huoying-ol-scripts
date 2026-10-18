"""Replay release checkpoint copies and failure screenshots with zero live input."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import hyol_scheduler as hs
from hyol_navigation import return_to_leaf

root = Path.home() / 'Desktop/可用脚本/火影OL日常任务'
results = []
for name in ('当前窗口标签5', '当前窗口标签6'):
    account = hs.Account(1, name, 180)
    path = root / 'state/2026-09-14' / (hs.slug(name) + '.json')
    original = path.read_bytes()
    state = copy.deepcopy(json.loads(original))
    screenshots = sorted((root / 'logs/scheduler_evidence/2026-09-14' / hs.slug(name)).glob('*daily_client_failure.png'))
    screenshot = screenshots[-1]
    frame = Image.open(screenshot).convert('RGB')
    s = object.__new__(hs.Scheduler)
    s.stop = threading.Event()
    s.state = Mock()
    s.state.accounts = {name: state}
    s.state.action_status.side_effect = lambda name, key: state.get('actions', {}).get(key, {}).get('status', 'new')
    s.state.set_action.side_effect = lambda name, key, status, detail: state['actions'][key].update(status=status, detail=detail)
    s.capture = Mock(return_value=frame)
    s.event = Mock()
    s.wait_interruptible = Mock()
    s.save_raw_evidence = Mock(return_value=screenshot)
    s.click_template = Mock(side_effect=AssertionError('Replay expected zero content clicks'))
    store = hs.TemplateStore(.92)
    s.template = lambda name, roi=hs.VIEW, frame=None, threshold=None: store.match(frame if frame is not None else s.capture(), hs.ASSETS / name, roi, threshold)
    before = [key for key, value in state['actions'].items() if value.get('status') == 'pending']
    s.reconcile_survival_result_confirmations(account)
    after = [key for key, value in state['actions'].items() if value.get('status') == 'pending']
    returned = return_to_leaf(s, account)
    assert returned and not after and s.click_template.call_count == 0
    assert path.read_bytes() == original
    results.append({'account': name, 'source': str(path), 'sha256': hashlib.sha256(original).hexdigest(),
                    'screenshot': str(screenshot), 'pending_before': before, 'pending_after': after,
                    'return_replay': returned, 'content_clicks': 0, 'source_unchanged': True})
hs.atomic_json(hs.LOGS / 'survival_return_replay_20260915.json', {'mode': 'offline_checkpoint_copy_and_real_frame', 'results': results})
print(json.dumps(results, ensure_ascii=False))
