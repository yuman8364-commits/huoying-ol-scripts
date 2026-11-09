"""Chinese desktop copies of run evidence, written outside the game input thread."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import threading


def desktop_directory():
    # SHGetFolderPath respects redirected/OneDrive desktops and localized names.
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    if os.name == 'nt' and ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) == 0:
        return Path(buffer.value)
    return Path.home() / 'Desktop'


def log_directory(root):
    override = os.environ.get('HYOL_DESKTOP_LOG_ROOT')
    if override:
        return Path(override)
    # Non-UI test harnesses have no WebView resources and remain isolated.
    if not (Path(root) / 'web/index.html').is_file():
        return Path(root) / 'logs/桌面日志'
    return desktop_directory() / '火影OL日志'


def safe_name(value):
    return re.sub(r'[\\/:*?"<>|\r\n]', '_', str(value))[:150]


class DesktopJournal:
    def __init__(self, root, version, *, enabled=True):
        self.root, self.version = Path(root), version
        self.enabled=enabled
        self.base = log_directory(root)
        self.warning = ''
        self.pending = queue.Queue()
        self.thread = None
        self.folders, self.seen, self.copied = {}, {}, set()
        self.run_versions = {}
        self.edition = '分享版' if 'HYOLShareClient' in str(self.root) else '目录版'
        self.lock = threading.RLock()

    def folder(self, identifier):
        if identifier not in self.folders:
            match = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', identifier)
            day = '-'.join(match.groups()[:3]) if match else datetime.now().strftime('%Y-%m-%d')
            stamp = '{}点{}分{}秒'.format(*match.groups()[3:]) if match else '运行'
            version=self.run_versions.get(identifier,self.version)
            label=version if self.edition in version else version+'_'+self.edition
            name = safe_name(f'{stamp}_{label}_{identifier}')
            path = self.base / day / name
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                path = self.root / 'logs/桌面日志备用' / day / name
                path.mkdir(parents=True, exist_ok=True)
                self.warning = f'桌面日志写入失败，已保存到 {path}：{exc}'
            for child in ('原始诊断', '截图证据', '筛选导出'):
                (path / child).mkdir(exist_ok=True)
            for filename in ('运行记录.txt','问题汇总.txt'):
                target=path/filename
                if not target.exists():target.write_text('',encoding='utf-8-sig')
            readme = path / '本轮结果说明.txt'
            if not readme.exists():
                readme.write_text(f'火影OL运行记录\n客户端版本：{version}\n运行编号：{identifier}\n'
                                  '运行记录：中文业务过程。问题汇总：未开放、条件不足及异常。\n'
                                  '截图证据：本轮已记录图片的副本。原始诊断：完整事件及原回执。\n'
                                  '状态：运行记录尚未收尾；异常退出时以原始凭据为准。\n', 'utf-8-sig')
            self.folders[identifier] = path
            events = path / '原始诊断/完整事件.jsonl'
            self.seen[identifier] = set()
            if events.exists():
                with events.open(encoding='utf-8') as stream:
                    for line in stream:
                        self.seen[identifier].add(hashlib.sha256(line.strip().encode()).hexdigest())
        return self.folders[identifier]

    def observe(self, event, identifier=''):
        if not self.enabled:return
        identifier = str(event.get('run_id') or identifier)
        if not identifier or not re.fullmatch(r'[\w.-]+', identifier):
            return
        value = json.loads(json.dumps(event, ensure_ascii=False, default=str))
        value.setdefault('ts',value.get('at') or datetime.now().astimezone().isoformat(timespec='milliseconds'))
        self.pending.put((identifier, value))
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._worker, daemon=True, name='中文桌面日志')
                self.thread.start()

    def _worker(self):
        while True:
            item = self.pending.get()
            try:
                if item is None:
                    return
                if item[0]=='_archive_':
                    self.archive(*item[1])
                else:
                    self.write(*item)
            except Exception as exc:
                self.warning = '桌面日志归档失败，原运行日志保留：' + str(exc)
            finally:
                self.pending.task_done()

    def write(self, identifier, event):
        from hyol_log_messages import present_event, LEVEL_LABELS
        folder = self.folder(identifier)
        raw = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        key = hashlib.sha256(raw.encode()).hexdigest()
        if key in self.seen[identifier]:
            return
        with (folder / '原始诊断/完整事件.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(raw + '\n')
        self.seen[identifier].add(key)
        display = present_event(event)
        label = event.get('account_label') or (f"标签 {event['tab_index']}" if event.get('tab_index') else '系统')
        line = ' | '.join(str(x) for x in (event.get('ts', ''), LEVEL_LABELS.get(display['level'], '信息'),
                         label, display['task_label'], display['message'], display['suggestion']) if x) + '\n'
        if not display['debug']:
            self._append(folder / '运行记录.txt', line)
        if display['level'] in ('warning', 'error'):
            self._append(folder / '问题汇总.txt', line)
        for source in self._images(event):
            if not source.is_file():
                continue
            token = (identifier, str(source))
            if token in self.copied:
                continue
            self.copied.add(token)
            digest = hashlib.sha256(str(source).encode()).hexdigest()[:8]
            name = safe_name(f'{label}_{display["task_label"] or "页面观察"}_{source.stem}_{digest}') + source.suffix
            target = folder / '截图证据' / name
            if not target.exists():
                shutil.copy2(source, target)
            self._append(folder / '截图证据/图片说明.txt', f'{name}\n{display["message"]}\n原图：{source}\n\n')
        if event.get('event') == 'unified_finished':
            status = {'complete': '已完成', 'pending': '本轮已结束，仍有待处理项目', 'stopped': '已停止'}.get(event.get('status'), '待核实')
            detail = display['message']
            (folder / '本轮结果说明.txt').write_text(
                f'客户端：{self.run_versions.get(identifier,self.version)}\n运行编号：{identifier}\n状态：{status}\n说明：{detail}\n'
                '运行记录.txt：中文业务记录\n问题汇总.txt：条件不足和异常\n'
                '截图证据：本轮图片及说明\n原始诊断：完整事件、原回执\n', 'utf-8-sig')
            (folder / '原始诊断/运行结果.json').write_text(json.dumps(event, ensure_ascii=False, indent=2), 'utf-8')

    @staticmethod
    def _append(path, text):
        encoding = 'utf-8' if path.exists() else 'utf-8-sig'
        with path.open('a', encoding=encoding) as stream:
            stream.write(text)

    @staticmethod
    def _images(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from DesktopJournal._images(item)
        elif isinstance(value, list):
            for item in value:
                yield from DesktopJournal._images(item)
        elif isinstance(value, str) and Path(value).suffix.lower() in ('.png', '.bmp', '.jpg', '.jpeg'):
            yield Path(value)

    def flush(self, timeout=10):
        import time
        deadline = time.monotonic() + timeout
        while self.pending.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(.01)
        return not self.pending.unfinished_tasks

    def close(self):
        if self.thread is not None:
            self.pending.put(None)
            self.thread.join(5)

    def archive(self, log_path, receipt_path=None):
        log_path = Path(log_path)
        identifier = log_path.stem.removeprefix('HYOL_Scheduler_')
        receipt={}
        if receipt_path and Path(receipt_path).exists():
            receipt=json.loads(Path(receipt_path).read_text('utf-8-sig'))
            self.run_versions[identifier]=str(receipt.get('version') or self.version)
        with log_path.open(encoding='utf-8-sig') as stream:
            for line in stream:
                if line.strip() and line.endswith('\n'):
                    self.write(identifier, json.loads(line))
        folder = self.folder(identifier)
        shutil.copy2(log_path, folder / '原始诊断/原始调度日志.jsonl')
        if receipt_path and Path(receipt_path).exists():
            self.write(identifier, dict(receipt, event='unified_finished', ts=receipt.get('at', '')))
            shutil.copy2(receipt_path, folder / '原始诊断/原始运行回执.json')
        return folder

    def recover_recent(self):
        """Rebuild today's copies after a crash from the original durable log."""
        if not self.enabled or not (self.root/'web/index.html').is_file():return
        files=sorted((self.root/'logs').glob('HYOL_Scheduler_'+datetime.now().strftime('%Y%m%d')+'_*.jsonl'))[-10:]
        for path in files:
            identifier=path.stem.removeprefix('HYOL_Scheduler_')
            self.pending.put(('_archive_',(path,self.root/'logs'/('unified_'+identifier+'.json'))))
        if files and self.thread is None:
            self.thread=threading.Thread(target=self._worker,daemon=True,name='中文日志恢复')
            self.thread.start()
