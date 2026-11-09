"""Stable current-account activity journals; old unknown inputs stay owned."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import uuid

import hyol_scheduler as hs
from hyol_checkpoints import (read_checkpoint, decode_mask, encode_mask,
                              source_token, merge_pending_action, action_versions,resolve_action,
                              combine_action_versions)

TASK_PREFIXES = {'bond':('bond_',), 'arena_3v3':('arena3v3_',), 'grocery':('grocery_',)}
ALL_PREFIXES = ('bond_','arena3v3_','grocery_')


def _material(value):
    if not isinstance(value,dict):
        raise hs.PendingError('当前账号活动缺少已核实身份素材')
    shape=value.get('shape')
    if (not isinstance(shape,(list,tuple)) or len(shape)!=2
            or any(type(n) is not int or not 1<=n<=512 for n in shape)):
        raise hs.PendingError('当前账号活动身份尺寸无效')
    mask=decode_mask(value.get('mask'),tuple(shape))
    if int(mask.sum())<50:
        raise hs.PendingError('当前账号活动身份文字不足，不能关联历史记录')
    kind=value.get('kind','world')
    if not isinstance(kind,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}',kind):
        raise hs.PendingError('当前账号活动身份类型无效')
    return dict(mask=encode_mask(mask),shape=list(shape),kind=kind)


def _score(left,right):
    if left['shape']!=right['shape']:return 0.
    shape=tuple(left['shape'])
    return float(hs.mask_f1(decode_mask(left['mask'],shape),decode_mask(right['mask'],shape)))


def _pending(actions,prefixes=ALL_PREFIXES):
    return {key:row for key,row in actions.items() if isinstance(key,str) and key.startswith(prefixes)
            and isinstance(row,dict) and any(v.get('status')=='pending' for v in action_versions(row))}


def _request(value):
    try:
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
        if len(raw)>100000:raise ValueError('request too long')
        return json.loads(raw)
    except (ValueError,TypeError) as exc:
        raise hs.PendingError('当前账号活动请求参数无效') from exc


class CurrentTeamLedger:
    def __init__(self,runner,task,request_key,material=None,*,activity_day=None):
        if task not in TASK_PREFIXES:
            raise hs.PendingError('当前账号持久活动类型无效')
        self.runner,self.scheduler,self.account=runner,runner.scheduler,runner.account
        self.task,self.request_key=task,_request(request_key)
        self.root=Path(self.scheduler.identity_directory).resolve()
        self.base=self._path(self.root/'activities/current_team')
        self.day=self.scheduler.state.day if activity_day is None else activity_day
        try:valid_day=isinstance(self.day,str) and date.fromisoformat(self.day).isoformat()==self.day
        except ValueError:valid_day=False
        if not valid_day:
            raise hs.PendingError('当前账号活动执行日期无效')
        self.source=self._path(self.scheduler.state.account_paths[self.account.name],file=True)
        if material is None:
            material=getattr(self.scheduler,'_dynamic_identity_materials',{}).get(self.account.name)
            if material is None:
                material=getattr(runner,'_current_team_binding',{}).get('material')
        self.materials=[_material(material)]
        self._read_cache={}
        self.registry_path=self._path(self.base/'identities.json',file=True)
        self.registry=read_checkpoint(self.registry_path)
        if self.registry and (self.registry.get('version')!=1 or not isinstance(self.registry.get('owners'),dict)):
            raise hs.PendingError('当前账号活动身份目录格式未知，保留原文件')
        self.registry=self.registry or dict(version=1,owners={})
        self._registry_digest=self._digest(self.registry_path)
        candidates=[]
        for owner,row in self.registry['owners'].items():
            self._validate_owner(owner)
            materials=[_material(v) for v in row.get('materials',[])]
            if not materials:raise hs.PendingError('历史活动身份素材缺失，不能关联')
            candidates.append((max(_score(self.materials[0],v) for v in materials),owner,materials))
        candidates.sort(reverse=True)
        best=candidates[0] if candidates else (0.,None,[])
        second=candidates[1][0] if len(candidates)>1 else 0.
        if best[0]>=.92:
            if best[0]-second<.08:raise hs.PendingError('当前角色与多个活动身份接近，保留原轮次')
            self.owner=best[1];self.materials=list(best[2])
            observed=_material(material)
            if observed not in self.materials:self.materials.append(observed)
        else:
            if any(score>=.80 and self._owner_unfinished(owner) for score,owner,_ in candidates):
                raise hs.PendingError('当前角色与历史未决活动身份相似，不能新建活动轮次')
            self.owner='current_'+uuid.uuid4().hex
        self.path=self._path(self.base/task/self.owner/(self.day+'.json'),file=True)
        self.data=read_checkpoint(self.path)
        self._ledger_digest=self._digest(self.path)
        if self.data:
            if (self.data.get('version')!=1 or self.data.get('owner')!=self.owner
                    or self.data.get('task')!=task or self.data.get('day')!=self.day
                    or self.data.get('request_key')!=self.request_key):
                raise hs.PendingError('本号已有活动请求与当前选择不同，保留原场次和原目标')
            if not isinstance(self.data.get('record'),dict) or not isinstance(self.data.get('actions'),dict):
                raise hs.PendingError('本号活动原记录格式未知，保留原文件')
        self.record=runner.record(task)
        if not isinstance(self.record,dict):raise hs.PendingError('当前任务记录格式无效')
        self._unstarted_history={}
        if task=='arena_3v3' and activity_day is not None and not self.data:
            self._separate_activity_record()
        if self.data:
            previous=deepcopy(self.data['record'])
            current=deepcopy(self.record)
            if (current.get('status') not in (None,'new') and current.get('owner') not in (None,self.owner)
                    and current!=previous):
                raise hs.PendingError('当前任务记录与原活动身份冲突')
            self.record.clear();self.record.update(previous)
        self._sources={str(self.source)}
        for value in self.data.get('source_state_files',[]):
            self._sources.add(str(self._path(value,file=True)))
        if self.data.get('source_state_file'):
            self._sources.add(str(self._path(self.data['source_state_file'],file=True)))
        self._restored_tokens=set()
        self._restore_actions()
        # Check all original runs before the first shared cursor is published.
        self.check_history()
        self.save()

    def _unstarted_deferred(self,data):
        """Only a proven never-started closed-window observation may expire."""
        rec=data.get('record',{})
        if (data.get('task')!='arena_3v3' or rec.get('phase')!='waiting_open'
                or type(rec.get('completed')) is not int or rec['completed']!=0
                or rec.get('task_completed') is True
                or rec.get('availability',{}).get('status')!='not_open'):return False
        proofs=rec.get('party_evidence',[])
        members=rec.get('members',{})
        if (not isinstance(proofs,list) or len(proofs)<2
                or not all(isinstance(p,str) and Path(p).is_file() for p in proofs)
                or not isinstance(members,dict) or self.owner not in members):return False
        for row in members.values():
            if (not isinstance(row,dict) or type(row.get('script_claims')) is not int
                    or row['script_claims']!=0 or not isinstance(row.get('reward_evidence'),str)
                    or not Path(row['reward_evidence']).is_file()):return False
        intent_fields={'match_action','match_sent_at','match_sequence','battle_seen','battle_auto_checks',
                       'battle_last_visible','awaiting','intents','claim_intents','reward_intents',
                       'script_claims','battle_results','verified_results'}
        def contains_intent(value):
            if isinstance(value,dict):
                return any(key in intent_fields and bool(item) or contains_intent(item) for key,item in value.items())
            return isinstance(value,(list,tuple)) and any(contains_intent(item) for item in value)
        if contains_intent(rec):return False
        def unissued(actions):
            if not isinstance(actions,dict):return False
            for key,row in actions.items():
                if not isinstance(key,str) or not key.startswith(ALL_PREFIXES):continue
                if not isinstance(row,dict):return False
                for item in action_versions(row):
                    status=item.get('status')
                    if status not in ('new','done'):return False
                    if (status=='done' and key.startswith(TASK_PREFIXES['arena_3v3'])
                            and any(word in key for word in ('match','claim','result','battle','auto'))):return False
                    if status=='new' and any(item.get(k) for k in ('before_evidence','origin','operation_id')):return False
            return True
        if not unissued(data.get('actions')):return False
        source=data.get('source_state_file')
        if not isinstance(source,str):return False
        paths=[source,*data.get('source_state_files',[])]
        owners={self.owner}
        for value in dict.fromkeys(paths):
            path=self._path(value,file=True)
            if not path.is_file():return False
            original=self._read(path)
            if not unissued(original.get('actions')):return False
            owners.update(v for v in (original.get('account'),original.get('account_id')) if isinstance(v,str))
        old_day=data.get('day') or rec.get('activity_day')
        if not isinstance(old_day,str):return False
        try:
            if date.fromisoformat(old_day).isoformat()!=old_day:return False
        except ValueError:return False
        intents=self._read(self.root/'arena3v3'/old_day/'rewards.json')
        return not any(any(str(token).startswith(owner+':') for owner in owners) and bool(row)
                       for token,row in intents.items())

    def _separate_activity_record(self):
        """Keep an old in-memory completion out of a different activity day."""
        old=deepcopy(self.record)
        if old.get('activity_day')==self.day:return
        meaningful=(old.get('activity_day') is not None or old.get('status')=='pending'
                    or old.get('phase') not in (None,'ready','entering')
                    or bool(old.get('completed')) or bool(old.get('members')) or 'claim_action_scope' in old)
        if not meaningful:return
        actions=self.scheduler.state.accounts[self.account.name].get('actions',{})
        if _pending(actions):raise hs.PendingError('旧活动日仍有未决动作，不能创建新活动日目标')
        done=old.get('status')=='done' and old.get('task_completed') is True
        deferred=dict(task=self.task,day=old.get('activity_day'),record=old,actions=actions,
                      source_state_file=str(self.source),source_state_files=[])
        if not done and not self._unstarted_deferred(deferred):
            raise hs.PendingError('旧活动日进度尚未核实，保留原记录，不新建活动日目标')
        archive=dict(task=self.task,owner=self.owner,original_record=old,actions=deepcopy(actions),
                     source_state_file=str(self.source),counted_as_current_completion=False)
        digest=hashlib.sha256(json.dumps(archive,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        path=self._path(self.base/self.task/self.owner/'history'/(digest+'.json'),file=True)
        if not path.exists():hs.atomic_json(path,archive)
        self.record.clear()
        self.record.update(status='new',phase='ready',completed=0,task_completed=False,
                           activity_day=self.day,completion_scope='current_account',
                           previous_activity_record=str(path))

    def _path(self,value,*,file=False):
        path=Path(value).resolve()
        if not path.is_relative_to(self.root) or (file and path.suffix.lower()!='.json'):
            raise hs.PendingError('活动记录路径越出共享状态目录，保留原文件')
        return path

    @staticmethod
    def _validate_owner(owner):
        if not isinstance(owner,str) or not re.fullmatch(r'current_[0-9a-f]{32}',owner):
            raise hs.PendingError('活动稳定身份编号无效')

    @staticmethod
    def _digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    def _read(self,path):
        path=self._path(path,file=True)
        if not path.is_file():return {}
        stat=path.stat();fingerprint=(stat.st_mtime_ns,stat.st_size)
        cached=self._read_cache.get(str(path))
        if cached and cached[0]==fingerprint:return deepcopy(cached[1])
        data=read_checkpoint(path)
        after=path.stat()
        if fingerprint!=(after.st_mtime_ns,after.st_size):
            raise hs.PendingError('活动原记录正在变化，请等待原执行者结束')
        self._read_cache[str(path)]=(fingerprint,deepcopy(data))
        return data

    def _owner_unfinished(self,owner):
        for task in TASK_PREFIXES:
            directory=self._path(self.base/task/owner)
            for path in directory.glob('????-??-??.json'):
                data=self._read(path)
                if _pending(data.get('actions',{})) or data.get('record',{}).get('status') in ('running','pending'):
                    return True
                source=data.get('source_state_file')
                if source and _pending(self._read(source).get('actions',{})):return True
        return False

    def _resolutions(self):
        result={}
        paths=[self.root/'checkpoint_resolutions.json',self.scheduler.state.directory/'checkpoint_resolutions.json']
        paths.extend((self.root/'runs').glob('*/checkpoint_resolutions.json'))
        for path in dict.fromkeys(paths):
            for token,row in self._read(path).items():
                if (isinstance(row,dict) and row.get('token')==token and row.get('resolved_at')
                        and isinstance(row.get('path'),str)):
                    self._path(row['path'],file=True)
                    result[token]=row
        return result

    def _restore_actions(self):
        actions=self.scheduler.state.accounts[self.account.name].setdefault('actions',{})
        sources=[]
        if self.data:
            sources.append((self.path,self.data['actions']))
        # The last account journal is authoritative after dispatch even if the
        # process died before updating this shared activity cursor.
        for value in sorted(self._sources):
            path=Path(value)
            if path!=self.source:sources.append((path,self._read(path).get('actions',{})))
        resolved=self._resolutions()
        for path,rows in sources:
            for key,row in rows.items():
                if not isinstance(key,str) or not key.startswith(TASK_PREFIXES[self.task]) or not isinstance(row,dict):continue
                value=deepcopy(row)
                if value.get('status')=='pending':
                    token=source_token(path,key,value)
                    if token in resolved:continue
                    value['origin']=value.get('origin') or dict(path=str(path),action=key,token=token)
                    original=self._path(value['origin']['path'],file=True)
                    latest=self._read(original).get('actions',{}).get(key,{})
                    same_operation=(bool(value.get('operation_id')) and value.get('operation_id')==latest.get('operation_id')
                                    or bool(value.get('before_evidence')) and value.get('before_evidence')==latest.get('before_evidence'))
                    if (latest.get('status')=='done' and not latest.get('checkpoint_conflict')
                            and same_operation):
                        # A crash after the original commit but before the
                        # derived cursor save must not resurrect that pending
                        # snapshot. A matching operation or exact before-image
                        # receipt is mandatory; a same action name is not proof.
                        completed=deepcopy(latest);completed['origin']=value['origin']
                        if not actions.get(key) or actions[key].get('status')=='new':
                            actions[key]=completed
                            resolve_action(self.scheduler.state,self.account.name,key,
                                           '原来源同一操作已正向提交完成，恢复原完成凭据')
                            resolved[token]=completed['origin']
                        elif actions[key].get('status')=='done':
                            continue
                        else:
                            actions[key]=merge_pending_action(actions[key],value)
                        continue
                    self._restored_tokens.update(source_token(path,key,v) for v in action_versions(value)
                                                 if v.get('status')=='pending')
                    present=actions.get(key,{})
                    # A same-run restart reads the raw account row and its
                    # derived copy carrying an origin. Adopt that origin only
                    # for an exact dispatch receipt from this same source;
                    # otherwise retain the distinct intents and block payment.
                    if present:
                        originals=action_versions(present)
                        for raw in originals:
                            if raw.get('origin') or not raw.get('before_evidence'):continue
                            for carried in action_versions(value):
                                origin=carried.get('origin',{})
                                if (origin.get('path')==str(self.source) and origin.get('action')==key
                                        and origin.get('token')==source_token(self.source,key,raw)
                                        and raw=={k:v for k,v in carried.items() if k!='origin'}):
                                    raw['origin']=deepcopy(origin)
                                    break
                        present=combine_action_versions(originals)
                        actions[key]=present
                    # Shared snapshots can precede the same source row. Match
                    # operation IDs as well as origin tokens before merging.
                    if (present.get('operation_id') and present.get('operation_id')==value.get('operation_id')
                            and present.get('status')=='pending'):
                        actions[key]=value
                    elif present==row:
                        actions[key]=value
                    else:actions[key]=merge_pending_action(present,value)
                elif key not in actions or actions[key].get('status')=='new':
                    actions[key]=value
        self.scheduler.state.save_account(self.account.name)

    def attach_material(self,material):
        observed=_material(material)
        for owner,row in self.registry['owners'].items():
            if owner==self.owner:continue
            best=max((_score(observed,_material(v)) for v in row.get('materials',[])),default=0.)
            if best>=.92 or best>=.80 and self._owner_unfinished(owner):
                raise hs.PendingError('活动页姓名与其他角色历史身份冲突，保留当前队伍')
        if observed not in self.materials:self.materials.append(observed)
        self.check_history()

    def _history_accounts(self,run):
        refs=self._read(run/'account_identities.json')
        scored=[]
        for key,row in refs.items():
            material=_material(row)
            score=max((_score(v,material) for v in self.materials),default=0.)
            if score>=.80:scored.append((score,key))
        scored.sort(reverse=True)
        margin=scored[0][0]-scored[1][0] if len(scored)>1 else 1.
        for score,key in scored:
            exact=score>=.92 and margin>=.08 and key==scored[0][1]
            stems={hs.slug(key)}
            if isinstance(key,str) and re.fullmatch(r'account_[0-9a-f]{32}',key):stems.add(key)
            for day in run.glob('????-??-??'):
                if not day.is_dir():continue
                for stem in stems:
                    path=self._path(day/(stem+'.json'),file=True)
                    if path.is_file():yield path,self._read(path),exact

    def check_history(self):
        """Inspect old role-owned intent; only this ledger's origins may resume."""
        resolved=self._resolutions()
        allowed=set(self._restored_tokens)
        current_actions=self.scheduler.state.accounts[self.account.name].get('actions',{})
        if (_pending(current_actions,TASK_PREFIXES[self.task])
                and (self.record.get('phase')=='done' or self.record.get('task_completed') is True)):
            raise hs.PendingError('本号活动完成记录仍有未决动作，不能当作已完成或重新开始')
        for key,row in _pending(current_actions).items():
            if not key.startswith(TASK_PREFIXES[self.task]):
                raise hs.PendingError('本号其他组队活动仍有未决动作，先处理原场：'+key)
            if not self.data and source_token(self.source,key,row) not in self._restored_tokens:
                raise hs.PendingError('本号原活动动作缺少对应共享游标，不能新建目标：'+key)
        for row in current_actions.values():
            if isinstance(row,dict):
                allowed.update(v['origin']['token'] for v in action_versions(row)
                               if isinstance(v.get('origin'),dict) and v['origin'].get('token'))
        sources=[]
        for run in (self.root/'runs').glob('*'):
            if run.is_dir():sources.extend(self._history_accounts(self._path(run)))
        for task in TASK_PREFIXES:
            for path in self._path(self.base/task/self.owner).glob('????-??-??.json'):
                if path.resolve()==self.path:continue
                data=self._read(path)
                sources.append((path,dict(actions=data.get('actions',{})),True))
                if data.get('source_state_file'):
                    original=self._path(data['source_state_file'],file=True)
                    sources.append((original,self._read(original),True))
                rec=data.get('record',{})
                if rec.get('status') in ('running','pending') and not _pending(data.get('actions',{})):
                    if (task=='arena_3v3' and str(data.get('day',''))<self.day
                            and self._unstarted_deferred(data)):
                        self._unstarted_history[str(path)]=dict(source=str(path),sha256=self._digest(path),
                            old_activity_day=data['day'],outcome='not_started',counted_as_completed=False)
                    else:raise hs.PendingError('本号旧日期活动仍未完成，不能用新一天清除原轮次')
        for path,data,exact in sources:
            if path.resolve()==self.source:continue
            for key,row in _pending(data.get('actions',{})).items():
                for version in action_versions(row):
                    if version.get('status')!='pending':continue
                    token=source_token(path,key,version)
                    if token in resolved or exact and token in allowed:continue
                    reason='本号旧活动存在未决动作' if exact else '当前角色与旧未决活动身份相似'
                    raise hs.PendingError(reason+'，保留原战斗或领奖，不重新开始：'+key)
        return True

    def save(self):
        if self._digest(self.path)!=self._ledger_digest:
            raise hs.PendingError('本号活动游标被另一入口修改，保留双方原记录')
        if self._digest(self.registry_path)!=self._registry_digest:
            raise hs.PendingError('活动身份目录被另一入口修改，保留原身份')
        actions={key:deepcopy(row) for key,row in self.scheduler.state.accounts[self.account.name].get('actions',{}).items()
                 if isinstance(key,str) and key.startswith(TASK_PREFIXES[self.task]) and isinstance(row,dict)}
        if _pending(actions) and (self.record.get('phase')=='done' or self.record.get('task_completed') is True):
            raise hs.PendingError('本号活动仍有未决动作，不能保存完成状态')
        self.runner.save()
        # Give every persisted pending row the account-file token, not a token
        # based on this derived activity snapshot.
        for key,row in actions.items():
            if row.get('status')=='pending' and not row.get('origin'):
                row['origin']=dict(path=str(self.source),action=key,token=source_token(self.source,key,row))
        data=dict(version=1,owner=self.owner,task=self.task,day=self.day,request_key=self.request_key,
                  record=deepcopy(self.record),actions=actions,source_state_file=str(self.source),
                  source_state_files=sorted(self._sources),updated_at=hs.now_iso())
        previous=dict(self.data.get('unstarted_previous_scopes',{}));previous.update(self._unstarted_history)
        if previous:data['unstarted_previous_scopes']=previous
        if self.registry['owners'].get(self.owner,{}).get('materials')!=self.materials:
            registry=deepcopy(self.registry)
            registry['owners'][self.owner]=dict(materials=deepcopy(self.materials),updated_at=hs.now_iso())
            hs.atomic_json(self.registry_path,registry)
            self.registry=registry;self._registry_digest=self._digest(self.registry_path)
        comparable=lambda value:{k:v for k,v in value.items() if k!='updated_at'}
        if comparable(self.data)!=comparable(data):
            hs.atomic_json(self.path,data)
            self.data=data;self._ledger_digest=self._digest(self.path)
