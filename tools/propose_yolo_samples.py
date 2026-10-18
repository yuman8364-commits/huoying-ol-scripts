"""Generate review sheets from known template matches; never approves labels."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
from PIL import Image, ImageDraw
import hyol_scheduler as hs


def main():
    output = hs.ROOT/'datasets'/'pilot-review'
    output.mkdir(parents=True, exist_ok=True)
    targets = {
        'leaf_return_current.bmp': ((1870,69,1920,125), ['leaf_return_current.bmp','leaf_return_hover.bmp']),
        'daily_practice_toolbar.bmp': ((600,69,851,169), ['daily_practice_toolbar.bmp']),
    }
    store = hs.TemplateStore(.97)
    files = sorted((hs.LOGS).glob('*.png'))
    samples = []
    for source in files:
        if not any(day in source.name for day in ('20260907','20260910','20260911','20260912')):
            continue
        # Exclude non-game/UI screenshot names before proposed-label generation.
        if any(token in source.name for token in ('desktop','_ui','client','crop','hover')):
            continue
        with Image.open(source) as im:
            if im.size != (1920,1032):
                continue
            frame = im.convert('RGB')
        day = next(day for day in ('20260907','20260910','20260911','20260912') if day in source.name)
        boxes = []
        for target, (roi, variants) in targets.items():
            matches = [(store.match(frame, hs.ASSETS/name, roi), name) for name in variants]
            (ok, score, x, y), variant = max(matches, key=lambda pair: pair[0][1])
            if ok:
                h,w = store.load(hs.ASSETS/variant).shape
                boxes.append({'target':target,'xyxy':[x,y,x+w,y+h], 'proposal_score':score})
        if not boxes:
            continue
        index = len(samples)
        annotated = frame.copy()
        draw = ImageDraw.Draw(annotated)
        for box in boxes:
            draw.rectangle(box['xyxy'], outline='red', width=4)
        annotated.thumbnail((640,344))
        tile = Image.new('RGB',(640,390),'white')
        tile.paste(annotated,(0,25))
        ImageDraw.Draw(tile).text((5,5),f'{index}: {source.name}',fill='black')
        tile.save(output/f'review_{index:03d}.jpg')
        samples.append({'image':str(source.relative_to(hs.ROOT)).replace('\\','/'), 'session':day,
            'split':'test' if day=='20260912' else 'val' if day=='20260911' else 'train',
            'reviewed':False, 'boxes':boxes})
    # Images in this manifest are relative to the project root, recorded explicitly.
    data = {'classes':list(targets),'image_base':'../..','samples':samples}
    (output/'proposals.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    for start in range(0,len(samples),6):
        sheet = Image.new('RGB',(1280,1170),'white')
        for n in range(start,min(start+6,len(samples))):
            with Image.open(output/f'review_{n:03d}.jpg') as tile:
                sheet.paste(tile,((n-start)%2*640,(n-start)//2*390))
        sheet.save(output/f'sheet_{start//6:02d}.jpg')
    print(json.dumps({'proposals':len(samples),'output':str(output)},ensure_ascii=False))


if __name__=='__main__':
    main()
