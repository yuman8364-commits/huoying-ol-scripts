"""Enroll only generic token text from immutable, reviewed October 8 captures."""
import hashlib
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path.home()/'Desktop/可用脚本/火影OL完整客户端/logs/scheduler_evidence/2026-10-08/account_3ed81e0fcf'
SOURCES = {
    0: '214752_872368_before_arena_casual_battle_4.jpg',
    1: '212158_208549_before_arena_casual_battle_3.jpg',
    2: '212058_210086_before_arena_casual_battle_2.jpg',
    3: '211824_718225_before_arena_casual_battle_1.jpg',
}

def main():
    rows = []
    for count, name in SOURCES.items():
        path = SOURCE/name
        with Image.open(path) as frame:
            frame.crop((985,306,1055,324)).save(ROOT/f'assets/arena_tokens_remaining_{count}.bmp')
            fixture = Image.new('RGB', (1920,1032))
            fixture.paste(frame.crop((975,295,1070,340)), (975,295))
            fixture.save(ROOT/f'tests/fixtures/arena_tokens_{count}_20261008.png')
        rows.append({'count': count, 'source': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                     'crop': [985,306,1055,324]})
    (ROOT/'logs/arena_token_sources_20261009.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Enrolled four generic token labels; no game input')

if __name__ == '__main__':
    main()
