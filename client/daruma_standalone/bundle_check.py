"""Offline resource check usable from the built executable; no desktop input."""
from pathlib import Path
import sys

from . import VERSION
from .journal import atomic_json
from .paths import ROOT, RUNTIME, SAMPLES
from .vision import Vision


REQUIRED = (
    'daruma_game_page.bmp', 'daruma_game_over.bmp', 'daruma_start.bmp',
    'daruma_weekend_page.bmp', 'daruma_weekend_menu.bmp', 'daruma_hotspot.bmp',
    'daruma_go.bmp', 'daruma_game_close.bmp', 'daruma_reward_completed.bmp',
    'home_leave.bmp', 'microclient_tab_close_core.bmp',
    'offline_login_prompt.bmp', 'offline_login_prompt_current.bmp', 'offline_login_text_current.bmp',
    *(f'daruma_{kind}_digit_{digit}.bmp' for kind in ('chance', 'reward') for digit in range(4)),
)


def check(destination):
    vision = Vision()
    missing = [name for name in REQUIRED if not vision.asset(name).is_file()]
    glyphs = {str(number): len(refs) for number, refs in vision.glyphs.items()}
    frozen = bool(getattr(sys, 'frozen', False))
    outside_library = not frozen or RUNTIME == Path(sys.executable).resolve().parent/'runtime'
    report = dict(version=VERSION, status='pass' if not missing and all(glyphs.values())
                  and outside_library else 'fail', frozen=frozen, root=str(ROOT),
                  runtime=str(RUNTIME), samples=str(SAMPLES), missing_assets=missing,
                  digit_references=glyphs, runtime_outside_library=outside_library,
                  content_clicks=0, game_task_started=False)
    atomic_json(Path(destination), report)
    return report
