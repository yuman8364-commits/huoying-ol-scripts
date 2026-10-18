"""Build an offline contact sheet from explicitly selected archived arena frames."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path.home() / 'Desktop/可用脚本/火影OL完整客户端/logs/scheduler_evidence/2026-10-08'

def main():
    paths = sorted(SOURCE.glob('*/**/*before_arena_casual_battle_*.jpg'))
    sheet = Image.new('RGB', (600, max(1, len(paths))*70), '#333333')
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        with Image.open(path) as frame:
            crop = frame.crop((975,295,1070,335)).resize((190,80))
        sheet.paste(crop, (400,index*70))
        draw.text((5,index*70+10), path.parent.name + '/' + path.name[:13], fill='white')
    output = ROOT/'logs/solo_arena_token_contact_20261009.png'
    sheet.save(output)
    print(output)

if __name__ == '__main__':
    main()
