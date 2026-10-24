"""One explicit Start owns one task ledger and one observed account map."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
import threading

from hyol_diagnostics import run_id


_current = ContextVar('hyol_run_context', default=None)


def current_run():
    return _current.get()


@dataclass
class RunContext:
    root: Path
    identity_directory: Path
    identifier: str = field(default_factory=run_id)
    stop: threading.Event = field(default_factory=threading.Event)
    started: bool = False
    monitor_started: bool = False
    interrupted: bool = False
    binding: dict | None = None
    session_token: str = ''
    hwnd: int = 0
    pid: int = 0
    rebind_count: int = 0
    probing: bool = False

    @classmethod
    def create(cls, root, config=None, **kwargs):
        import hyol_scheduler as hs
        from hyol_accounts import state_directory
        root = Path(root)
        return cls(root, state_directory(config or hs.read_config(root/'HYOL_Daily.ini'), root), **kwargs)

    @property
    def directory(self):
        return self.identity_directory/'runs'/self.identifier

    @contextmanager
    def activate(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    def begin(self):
        """Caller must already own the foreground lease; only explicit Start calls this."""
        if self.stop.is_set():
            raise KeyboardInterrupt('本轮已停止')
        if not self.started:
            from hyol_diagnostics import clear_manual_pause
            clear_manual_pause(self.root, '用户点击开始新运行；上次运行已结束')
            if self.stop.is_set():
                raise KeyboardInterrupt('开始时收到停止请求')
            self.started = True

    def bind_window(self, scheduler):
        import hyol_scheduler as hs
        from hyol_accounts import game_session_token
        token = game_session_token(scheduler)
        if self.session_token and token != self.session_token:
            raise hs.PendingError('游戏窗口会话已改变，本轮结束；请重新点击开始')
        self.session_token, self.hwnd = token, scheduler.hwnd
        self.pid = hs.Win32.pid(scheduler.hwnd)

    def check_window(self, scheduler):
        import hyol_scheduler as hs
        if self.hwnd and (scheduler.hwnd != self.hwnd or hs.Win32.pid(scheduler.hwnd) != self.pid):
            raise hs.PendingError('游戏窗口会话已改变，本轮结束；请重新点击开始')

    def remember(self, centers, mapping, observations, frames, refs, unavailable):
        self.binding = dict(centers=tuple(centers), mapping=dict(mapping),
                            masks={slot:rows[-1] for slot,rows in observations.items()},
                            frames=dict(frames), refs=refs, unavailable=dict(unavailable))

    def adopt_solo(self, scheduler):
        """Transfer observed identities, never authorize an input with an old frame."""
        import hyol_scheduler as hs
        self.check_window(scheduler)
        row = self.binding
        if row is None:
            raise hs.PendingError('本轮尚未完成账号识别')
        scheduler.accounts = [hs.Account(i,key,row['centers'][i-1]) for i,key in sorted(row['mapping'].items())]
        scheduler.by_name = {a.name:a for a in scheduler.accounts}
        scheduler.state = hs.StateStore(list(scheduler.by_name), directory=self.directory, restore_history=False)
        scheduler.available_account_names = set(scheduler.by_name)
        scheduler.joint_identity_bound = set(scheduler.by_name)
        scheduler._dynamic_identity_materials = row['refs']
        scheduler._dynamic_identity_masks = dict(row['masks'])
        scheduler._dynamic_identity_names = dict(row['mapping'])
        scheduler._unavailable_reasons = dict(row['unavailable'])
        scheduler.config['TabXs'] = ','.join(map(str,row['centers']))
        scheduler.config['TabAccountNames'] = ','.join(scheduler.by_name)
        scheduler.account_logs = {a.name:hs.JsonlLogger(hs.LOGS/'accounts'/(hs.slug(a.name)+'.jsonl'))
                                  for a in scheduler.accounts}
        scheduler.state.global_state.update(tab_map={a.name:a.tab_x for a in scheduler.accounts},
            verified_solo_tab_map={a.name:a.tab_x for a in scheduler.accounts})
        scheduler.active = None
        scheduler.event('dynamic_accounts_bound', mapping=row['mapping'], unavailable=row['unavailable'],
                        reused=True, observations=0, content_clicks=0)

    @staticmethod
    def resolve(scheduler, account):
        context = getattr(scheduler,'run_context',None)
        if context and (context.binding is None or context.probing
                        or getattr(scheduler,'_capture_binding_probe',False)):
            return account
        return scheduler.by_name.get(account.name, account)

    def layout_matches(self, frame):
        from hyol_multi_solo import tab_close_positions, active_tab_centers
        positions = tab_close_positions(frame)
        return bool(positions) and active_tab_centers(positions) == self.binding['centers']

    def stable_layout_frame(self, scheduler, frame):
        for attempt in range(3):
            if self.layout_matches(frame):
                return frame
            if attempt < 2:
                scheduler.wait_interruptible(.35)
                frame = scheduler.capture()
        self.rebuild(scheduler)
        return scheduler.capture()

    def rebuild(self, scheduler):
        """One bounded rescan may relocate this run's identities without changing its work."""
        import hyol_scheduler as hs
        from hyol_accounts import observe_accounts, register_observations
        if self.rebind_count:
            raise hs.PendingError('本轮标签再次变化，已停止重复扫描；请整理窗口后重新开始')
        self.rebind_count += 1
        self.probing = True
        previous = self.binding
        scope = {a.name for a in scheduler.accounts}
        accounts = tuple(scheduler.by_name.values())
        owners = {a.index: scheduler.state.aliases.get(a.name, a.name) for a in accounts}
        # Treat rebinding as a transaction: a failed title probe or event sink
        # must not leave the old binding paired with partially updated accounts.
        missing = object()
        fields = ('accounts', 'by_name', 'available_account_names', 'joint_identity_bound',
                  '_bound_identity_names', '_bound_identity_masks', '_known_identity_materials',
                  '_tab_title_masks', '_dynamic_identity_names', '_dynamic_identity_masks',
                  '_dynamic_identity_materials', 'active')
        original_fields = {name: getattr(scheduler, name, missing) for name in fields}
        original_maps = {name: (name in scheduler.state.global_state,
                                scheduler.state.global_state.get(name))
                         for name in ('tab_map', 'verified_solo_tab_map')}
        try:
            centers, observations, frames, unavailable = observe_accounts(scheduler)
            mapping, refs = register_observations(scheduler, observations)
            positions = {name: slot for slot, name in mapping.items()}
            rebound = [replace(a, tab_x=centers[positions[owners[a.index]]-1]
                       if owners[a.index] in positions else 1801+100*a.index) for a in accounts]
            new_accounts = [a for a in rebound if a.name in scope]
            new_by_name = {a.name: a for a in rebound}
            logical = {a.index: owners[a.index] for a in rebound if owners[a.index] in positions}
            masks = {i: observations[positions[key]][-1] for i, key in logical.items()}
            available = {a.name for a in rebound if a.index in logical}
            current = {a.name: a.tab_x for a in rebound if a.index in logical}
            named = hasattr(scheduler, '_bound_identity_names')
            # Title probing can raise. Perform it before publishing ANY new map.
            titles = ({a.index: scheduler._title_mask(frames[positions[owners[a.index]]], a.tab_x)
                       for a in rebound if a.index in logical} if named else None)
            self.remember(centers, mapping, observations, frames, refs, unavailable)
            scheduler.accounts = new_accounts
            scheduler.by_name = new_by_name
            scheduler.available_account_names = available
            scheduler.joint_identity_bound = set(available)
            if named:
                scheduler._bound_identity_names = logical
                scheduler._bound_identity_masks = masks
                scheduler._known_identity_materials = refs
                scheduler._tab_title_masks = titles
            else:
                scheduler._dynamic_identity_names = logical
                scheduler._dynamic_identity_masks = masks
                scheduler._dynamic_identity_materials = refs
            scheduler.state.global_state.update(tab_map=current, verified_solo_tab_map=current)
            scheduler.active = None
            scheduler.event('run_accounts_rebound',
                            mapping={str(a.index): owners[a.index] for a in rebound
                                     if a.index in logical},
                            content_clicks=0, rescans=self.rebind_count)
        except BaseException:
            self.binding = previous
            for name, value in original_fields.items():
                if value is missing:
                    if hasattr(scheduler, name):
                        delattr(scheduler, name)
                else:
                    setattr(scheduler, name, value)
            for name, (existed, value) in original_maps.items():
                if existed:
                    scheduler.state.global_state[name] = value
                else:
                    scheduler.state.global_state.pop(name, None)
            raise
        finally:
            self.probing = False

    @staticmethod
    def loading_scene_visible(scheduler, frame):
        from hyol_treasure import MAP_LOADING_ROI
        return (scheduler.template('map_loading_word_current.bmp',MAP_LOADING_ROI,frame,threshold=.98)[0]
                and any(scheduler.template(name,MAP_LOADING_ROI,frame,threshold=.98)[0] for name in (
                    'map_loading_path_current.bmp','map_loading_npc_path_current.bmp',
                    'map_loading_finished_current.bmp')))

    def hidden_identity_observation(self, scheduler, account, frame):
        """A bound title permits observation only while the role strip is hidden."""
        import numpy as np
        import hyol_scheduler as hs
        from hyol_checkpoints import current_identity_mask
        row = self.binding
        if row is None or account.tab_x not in row['centers']:
            return False
        slot = row['centers'].index(account.tab_x) + 1
        owner = scheduler.state.aliases.get(account.name, account.name)
        if row['mapping'].get(slot) != owner:
            return False
        if int(current_identity_mask(frame, scheduler.templates).sum()) >= 50:
            return False
        reference = row['frames'].get(slot)
        if reference is None:
            return False

        def title_mask(image):
            patch = image.crop((account.tab_x-48, 5, account.tab_x+24, 30)).convert('L')
            return (np.asarray(patch) < 105).astype(np.uint8)

        left, right = title_mask(reference), title_mask(frame)
        return np.array_equal(left, right) or hs.mask_f1(left, right) >= .70

    def select(self, scheduler, account, *, allow_hidden=False, allow_transition=False, combat_mode=None):
        prior = getattr(scheduler,'_capture_account',None)
        scheduler._capture_account = self.resolve(scheduler,account)
        try:
            return self._select(scheduler,account,allow_hidden=allow_hidden,
                                allow_transition=allow_transition,combat_mode=combat_mode)
        finally:
            scheduler._capture_account = prior

    def _select(self, scheduler, account, *, allow_hidden=False, allow_transition=False, combat_mode=None):
        """One selected-tab observation, with fresh frames for redraws and actual switches."""
        import hyol_scheduler as hs
        from hyol_checkpoints import current_identity_mask, identity_mask_scores
        account = self.resolve(scheduler,account)
        scheduler.check_stop()
        frame = scheduler.capture()
        if not self.layout_matches(frame):
            frame = self.stable_layout_frame(scheduler,frame)
            account = self.resolve(scheduler,account)
            scheduler._capture_account = account
        available = getattr(scheduler, 'available_account_names', None)
        if available is None:
            raise hs.PendingError('本轮账号映射尚未初始化，请重新点击开始')
        if account.name not in available:
            raise hs.PendingError('本轮账号已不在可核实标签中')
        try:
            selected = scheduler._generic_active_tab_x(frame) == account.tab_x
        except hs.PendingError:
            selected = False
        if not selected:
            scheduler.chrome_click(account.tab_x,10,f'switch_tab:{account.index}',account)
            scheduler.wait_interruptible(.35)
            frame = scheduler.capture()
        for attempt in range(3):
            scheduler.check_stop()
            if scheduler.offline_dialog_visible(frame):
                raise hs.AccountOffline(account,'当前标签已掉线')
            if not self.layout_matches(frame):
                self.stable_layout_frame(scheduler,frame)
                return self.select(scheduler,account,allow_hidden=allow_hidden,
                                   allow_transition=allow_transition,combat_mode=combat_mode)
            if scheduler.healthy(frame) and scheduler.verify_identity(account,frame)[0]:
                scheduler.active = account
                scheduler.event('account_bound',account,tab_click_suppressed=selected)
                return frame
            if (combat_mode in ('arena','ranking','survival')
                    and scheduler._generic_active_tab_x(frame) == account.tab_x
                    and self.hidden_identity_observation(scheduler,account,frame)
                    and scheduler._combat_ready_visible(combat_mode,frame)):
                scheduler.active = account
                scheduler.event('account_observation_bound',account,content_authorized=False,
                                phase='combat_ready',mode=combat_mode)
                return frame
            if allow_transition and scheduler._generic_active_tab_x(frame) == account.tab_x:
                if (self.hidden_identity_observation(scheduler,account,frame)
                        and self.loading_scene_visible(scheduler,frame)):
                    scheduler.active = account
                    scheduler.event('account_observation_bound',account,
                                    content_authorized=False,phase='map_loading')
                    return frame
            if allow_hidden and scheduler._generic_active_tab_x(frame) == account.tab_x:
                if self.hidden_identity_observation(scheduler,account,frame):
                    # Observation of our running battle is allowed. Content inputs still
                    # require content_guard's fresh identity/positive page-anchor checks.
                    scheduler.active = account
                    scheduler.event('account_observation_bound',account,content_authorized=False)
                    return frame
            if attempt < 2:
                scheduler.wait_interruptible(.35)
                frame = scheduler.capture()
        # A confident different role means tabs were reordered with identical title spacing.
        scores = identity_mask_scores(current_identity_mask(frame,scheduler.templates), self.binding['refs'])
        owner = scheduler.state.aliases.get(account.name,account.name)
        best = max(scores,key=scores.get,default=None)
        if best is not None and best != owner and scores[best] >= .92 and self.rebind_count == 0:
            self.rebuild(scheduler)
            return self.select(scheduler,account,allow_hidden=allow_hidden,
                               allow_transition=allow_transition,combat_mode=combat_mode)
        evidence = scheduler.save_raw_evidence(frame,account,'current_run_identity','pending')
        raise hs.PendingError(f'当前标签身份或页面未稳定确认：{evidence}')
