"""Local Windows Chinese OCR with bounded execution and no visible helper window."""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import hyol_scheduler as hs


def recognize(image):
    script=hs.ASSETS/'windows_ocr.ps1'
    if not script.is_file():
        script=hs.ROOT/'tools/windows_ocr.ps1'
    if not script.is_file():
        raise hs.PendingError('本地文字识别组件缺失，未执行修行录任务')
    with TemporaryDirectory(prefix='hyol_ocr_') as folder:
        path=Path(folder)/'frame.png'
        image.convert('RGB').resize((image.width*2,image.height*2)).save(path)
        try:
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                                   '-File',str(script),'-ImagePath',str(path)],
                                  capture_output=True,timeout=20,creationflags=0x08000000)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise hs.PendingError('本地文字识别不可用，保留修行录现场') from exc
    if result.returncode:
        raise hs.PendingError('本地中文文字识别失败：'+result.stderr.decode('utf-8',errors='replace')[-500:])
    try:
        data=json.loads(result.stdout.decode('utf-8-sig'))
    except (ValueError,UnicodeError) as exc:
        raise hs.PendingError('修行录文字识别结果格式未知') from exc
    try:
        for line in data.get('lines',[]):
            for word in line.get('words',[]):
                for field in ('x','y','width','height'):
                    word[field]=word[field]/2
    except (TypeError,KeyError) as exc:
        raise hs.PendingError('修行录文字识别坐标格式未知') from exc
    return data
