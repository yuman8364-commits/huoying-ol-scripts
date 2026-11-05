"""Crop only real, user-provided recovery controls; retain source and coordinates."""
import hashlib
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import hyol_scheduler as hs


def enroll():
    folder = hs.LOGS/'fragment_refinery'
    folder.mkdir(parents=True, exist_ok=True)
    inputs = {
        'user_panel': Path(r'C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-28b1f20a-0c12-4643-9f73-1c5afa05b6fb.png'),
        'user_entry': Path(r'C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-e173de8b-fb39-4f77-a097-c8eff2ca3627.png'),
        'user_cultivation': Path(r'C:\Users\Administrator\AppData\Local\Temp\codex-clipboard-f40ea6b7-ece9-42ea-b57d-5c49145a9035.png'),
        'ninja': hs.LOGS/'scheduler_evidence/2026-10-04/account_3ed81e0fcf/223323_236014_readonly_fragment_refinery_entry_observed.png',
        'cultivation': hs.LOGS/'scheduler_evidence/2026-10-04/account_3ed81e0fcf/224831_447500_readonly_fragment_refinery_entry_observed.png',
        'refinery': hs.LOGS/'scheduler_evidence/2026-10-04/account_3ed81e0fcf/224954_815189_readonly_fragment_refinery_entry_observed.png',
        'exchange_prompt': hs.LOGS/'scheduler_evidence/2026-10-04/account_3ed81e0fcf/225302_993712_readonly_fragment_refinery_entry_observed.png',
        'converted': hs.LOGS/'scheduler_evidence/2026-10-04/account_3ed81e0fcf/231028_675518_pending_fragment_recovery_pending.png',
    }
    sources = {}
    for key, original in inputs.items():
        target = folder/(key+'_20261004.png')
        if not target.exists():
            shutil.copy2(original, target)
        sources[key] = target
    crops = {
        'ninja_collection_button_current.bmp': ('ninja', (1328,104,1398,165)),
        'ninja_cultivation_current.bmp': ('ninja', (1399,107,1457,164)),
        'ninja_cultivation_selected_current.bmp': ('cultivation', (1399,107,1457,164)),
        'ninja_owned_title_current.bmp': ('ninja', (1080,296,1249,332)),
        'fragment_refinery_entry_current.bmp': ('cultivation', (1514,317,1571,332)),
        'fragment_refinery_title_current.bmp': ('refinery', (730,244,886,283)),
        'fragment_surplus_title_current.bmp': ('refinery', (1227,302,1360,322)),
        'fragment_refinery_close_current.bmp': ('refinery', (1112,275,1148,311)),
        'fragment_exchange_current.bmp': ('refinery', (1233,762,1336,795)),
        'fragment_exchange_scope_current.bmp': ('exchange_prompt', (805,518,1118,534)),
        'fragment_exchange_effect_current.bmp': ('exchange_prompt', (894,537,950,552)),
        'fragment_exchange_points_label_current.bmp': ('exchange_prompt', (985,537,1018,552)),
        'fragment_exchange_yes_current.bmp': ('exchange_prompt', (824,569,928,604)),
        'fragment_exchange_no_current.bmp': ('exchange_prompt', (993,569,1097,604)),
        'fragment_surplus_empty_current.bmp': ('converted', (1166,335,1397,735)),
        'fragment_exchange_disabled_current.bmp': ('converted', (1233,762,1336,795)),
    }
    for name, (key, roi) in crops.items():
        source = sources[key]
        with Image.open(source) as frame:
            frame.crop(roi).convert('RGB').save(hs.ASSETS/name)
        hs.atomic_json((hs.ASSETS/name).with_suffix('.source.json'), {
            'source': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'crop': roi, 'classification': 'real_user_or_live_capture', 'live_click_authorized': False})


if __name__ == '__main__':
    enroll()
