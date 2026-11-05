"""Build a session-separated multiclass review queue from real evidence images."""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
import hyol_scheduler as hs

CATALOG = {
    'leaf_return_current.bmp': ((1870,69,1920,125), ('leaf_return_current.bmp','leaf_return_hover.bmp'), 'return'),
    'daily_practice_toolbar.bmp': ((600,69,851,169), (), 'main'),
    'battle_auto_off.bmp': (hs.BATTLE_AUTO_ROI, (), 'battle'),
    'battle_auto_on_current_core.bmp': (hs.BATTLE_AUTO_ROI, (), 'battle'),
    'arena_result_confirm.bmp': ((651,449,1151,829), (), 'arena'),
    'survival_anchor.bmp': ((201,69,601,419), (), 'survival'),
    'survival_result_confirm.bmp': ((701,519,1151,819), (), 'survival'),
    'ranking_page.bmp': ((790,95,1080,195), (), 'ranking'),
    'ranking_result_confirm.bmp': ((860,640,1060,710), (), 'ranking'),
    'mail_ready_anchor.bmp': ((801,199,1101,369), (), 'mail'),
    'mail_warehouse_empty_disabled.bmp': ((1001,719,1251,919), (), 'mail'),
    'welfare_online_anchor.bmp': ((451,319,751,469), (), 'welfare'),
    'treasure_page_anchor.bmp': ((560,250,1380,810), (), 'treasure'),
    'treasure_result_anchor.bmp': ((760,440,1170,670), (), 'treasure'),
    'sealed_name_current.bmp': ((0,160,1690,730), (), 'sealed'),
    'daruma_game_page.bmp': ((920,220,1300,325), (), 'daruma'),
    'daruma_game_over.bmp': ((900,400,1210,700), (), 'daruma'),
    'rescue_jinchuriki_anchor.bmp': (hs.VIEW, (), 'rescue'),
    'team_pvp_anchor.bmp': (hs.VIEW, (), 'team_pvp'),
    'offline_login_text_current.bmp': ((650,350,1300,780), (), 'offline'),
}


def propose(output, per_split=3):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('use a new immutable review folder')
    output.mkdir(parents=True)
    store = hs.TemplateStore(.97)
    selected, counts, hashes = [], defaultdict(int), set()
    # A whole acquisition day stays together, stricter than a run-id split.
    candidates = []
    quotas = defaultdict(int)
    root = hs.LOGS/'scheduler_evidence'
    for path in sorted(root.rglob('*'), reverse=True):
        if path.suffix.lower() not in ('.jpg','.png'):
            continue
        day = path.relative_to(root).parts[0]
        if len(day) != 10 or day[4] != '-':
            continue
        topic = next((v[2] for v in CATALOG.values() if v[2] in path.name.lower()), None)
        if topic is None:
            continue
        key = day, topic
        if quotas[key] >= 3:
            continue
        quotas[key] += 1
        candidates.append((day,path))
    for n, (day, source) in enumerate(candidates):
        split = 'test' if day >= '2026-09-28' else 'val' if day >= '2026-09-22' else 'train'
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest in hashes:
            continue
        with Image.open(source) as image:
            if image.size != (1920,1032):
                continue
            frame = image.convert('RGB')
        boxes = []
        for target, (roi, variants, topic) in CATALOG.items():
            matches = [(store.match(frame,hs.ASSETS/name,roi,.97),name)
                       for name in (variants or (target,)) if (hs.ASSETS/name).exists()]
            if not matches:
                continue
            (ok,score,x,y), variant = max(matches,key=lambda item:item[0][1])
            if ok:
                h,w = store.load(hs.ASSETS/variant).shape
                boxes.append({'target':target,'xyxy':[x,y,x+w,y+h],'proposal_score':score})
        needed = [box for box in boxes if counts[split,box['target']] < per_split]
        if not needed:
            continue
        hashes.add(digest)
        selected.append({'image':os.path.relpath(source,output).replace('\\','/'),
                         'session':day,'split':split,'reviewed':False,'boxes':boxes})
        for box in boxes:
            counts[split,box['target']] += 1
        if n % 30 == 0:
            print(f'observed={n+1}/{len(candidates)} proposed={len(selected)}',flush=True)
    supported = [target for target in CATALOG if all(counts[split,target] for split in ('train','val','test'))]
    pending = {target:{split:counts[split,target] for split in ('train','val','test')}
               for target in CATALOG if target not in supported}
    for row in selected:
        row['boxes'] = [box for box in row['boxes'] if box['target'] in supported]
    data = {'classes':supported,'samples':selected,'pending_classes':pending,
            'review_rule':'Review every image and all boxes; proposals never authorize labels or clicks.'}
    (output/'proposals.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    for start in range(0,len(selected),6):
        sheet = Image.new('RGB',(1440,1260),'white')
        for offset,row in enumerate(selected[start:start+6]):
            with Image.open(output/row['image']) as source:
                tile = source.convert('RGB')
            draw = ImageDraw.Draw(tile)
            for box in row['boxes']:
                draw.rectangle(box['xyxy'],outline='red',width=3)
                draw.text((box['xyxy'][0],max(69,box['xyxy'][1]-13)),box['target'],fill='red')
            tile.thumbnail((720,387))
            x,y = offset%2*720, offset//2*420
            sheet.paste(tile,(x,y+24))
            ImageDraw.Draw(sheet).text((x+5,y+3),f'{start+offset} {row["session"]} {row["split"]}',fill='black')
        sheet.save(output/f'sheet_{start//6:02d}.jpg')
    return {'images':len(selected),'classes':supported,'pending_classes':pending,'output':str(output)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    args = parser.parse_args()
    print(json.dumps(propose(args.output),ensure_ascii=False))
