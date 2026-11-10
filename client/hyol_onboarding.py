"""Versioned user-profile walkthrough; visiting settings never starts a task."""
import json
from pathlib import Path

STEPS = ('accounts', 'tasks', 'parameters', 'team', 'grocery', 'finish')


def profile_directory(root):
    from hyol_task_progress import shared_state
    state = shared_state(root)
    return state.parent if state.name == 'state' else Path(root)


class Onboarding:
    def __init__(self, root):
        self.root = Path(root)
        self.path = profile_directory(root) / 'desktop_onboarding.json'

    def _existing_user(self):
        from hyol_desktop_settings import load
        if load(self.root).get('saved'):
            return True
        from hyol_task_progress import shared_state
        for path in (shared_state(self.root)/'runs').glob('*/????-??-??/*_tasks.json'):
            try:
                row=json.loads(path.read_text('utf-8-sig'))
                if any(isinstance(v,dict) and v.get('status') in ('running','done','pending') for v in row.values()):
                    return True
            except (OSError,ValueError,AttributeError):
                continue
        for path in (self.root / 'logs').glob('unified_*.json'):
            try:
                row = json.loads(path.read_text('utf-8-sig'))
                if row.get('run_id') and row.get('status') in ('complete', 'pending', 'stopped'):
                    return True
            except (OSError, ValueError, AttributeError):
                continue
        return False

    def load(self):
        try:
            row = json.loads(self.path.read_text('utf-8-sig'))
            steps = row.get('steps', [])
            if (row.get('version') != 1 or not isinstance(steps, list)
                    or steps != list(STEPS[:len(steps)]) or len(steps) > len(STEPS)):
                raise ValueError('新人引导记录无效')
            row['required'] = not bool(row.get('completed_at') or row.get('skipped_at'))
            return dict(ok=True, **row)
        except FileNotFoundError:
            existing = self._existing_user()
            return dict(ok=True, version=1, steps=list(STEPS) if existing else [],
                        required=not existing, existing_user=existing, completed_at='旧用户' if existing else '')
        except (OSError, ValueError, TypeError, AttributeError):
            return dict(ok=True, version=1, steps=[], required=True, completed_at='',
                        warning='引导记录无法读取，请重新浏览设置；原记录保留。')

    def acknowledge(self, step):
        row = self.load()
        if step not in STEPS:
            raise ValueError('新人引导步骤无效')
        if step not in row['steps']:
            if STEPS.index(step) != len(row['steps']):
                raise ValueError('请按顺序查看新人引导')
            row['steps'].append(step)
        self._save(row)
        return row

    def finish(self):
        from hyol_scheduler import now_iso
        row = self.load()
        if row['steps'] != list(STEPS):
            raise ValueError('请先看完六组设置')
        row.update(required=False, completed_at=now_iso())
        self._save(row)
        return row

    def skip(self):
        """Dismiss the guide without pretending all steps were viewed or saving settings."""
        from hyol_scheduler import now_iso
        row = self.load()
        row.update(required=False, skipped_at=now_iso())
        self._save(row)
        return row

    def _save(self, row):
        from hyol_scheduler import atomic_json
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if row.get('warning') and self.path.is_file():
            import shutil, uuid
            shutil.copy2(self.path,self.path.with_name(self.path.stem+'_损坏备份_'+uuid.uuid4().hex[:8]+'.json'))
        atomic_json(self.path, {k: v for k, v in row.items() if k not in ('ok', 'warning')})
