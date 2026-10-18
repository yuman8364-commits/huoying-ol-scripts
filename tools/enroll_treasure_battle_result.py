"""Enroll the current treasure-ambush victory dialog from saved evidence."""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "logs" / "preflight_20260919_233207_358977_04708039.png"

with Image.open(SOURCE) as frame:
    frame.crop((845, 375, 1085, 555)).save(ROOT / "assets" / "treasure_battle_victory.bmp")
    frame.crop((900, 625, 1025, 665)).save(ROOT / "assets" / "treasure_battle_confirm.bmp")
