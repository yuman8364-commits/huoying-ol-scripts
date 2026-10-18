"""Read-only progress, once-only arena inputs and evidence-based reset recovery."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
import hyol_scheduler as hs
import hyol_multi_solo as multi
from hyol_combat_watchdog import BattleWatchdog
from hyol_task_progress import ProgressReader, survival_battle_counts


class Ledger:
    def __init__(self, account='a'):
        self.accounts = {account: {'actions': {}}}
        self.day = '2026-10-12'
        self.save_account = Mock()

    def action_record(self, name, key):
        return self.accounts[name]['actions'].get(key, {})

    def action_status(self, name, key):
        return self.action_record(name, key).get('status', 'new')

    def set_action(self, name, key, status, detail=''):
        self.accounts[name]['actions'][key] = dict(status=status, detail=detail)


class ReadOnlyProgressTests(unittest.TestCase):
    def battle_records(self, number=2):
        actions = {}
        for index in range(1, number+1):
            for key in (f'survival_battle_step_{index}', f'survival_battle_step_{index}_start',
                        f'survival_result_confirm_{index}'):
                actions[key] = dict(status='done', operation_id='current:'+key,
                                    updated_at='2026-10-13T20:00:00+08:00')
        return actions

    def test_survival_counts_both_verified_rounds_and_deduplicates_archive(self):
        first = self.battle_records()
        current = self.battle_records()
        current['survival_second_run_started'] = dict(status='done')
        events = [dict(account='a', event='survival_first_run_archived', actions=first)]*2
        value = survival_battle_counts(events, 'a', '2026-10-13', current)
        self.assertEqual(dict(verified_battles=4, verified_first_round_battles=2,
                              verified_current_round_battles=2), value)

    def test_score_and_pending_or_previous_day_actions_do_not_invent_battles(self):
        actions = self.battle_records()
        actions['survival_battle_step_1_start']['status'] = 'pending'
        actions['survival_battle_step_2']['updated_at'] = '2026-10-12T20:00:00+08:00'
        events = [dict(account='a', event='survival_score_observed', score=220)]
        self.assertEqual(0, survival_battle_counts(events, 'a', '2026-10-13', actions)['verified_battles'])

    def test_archive_transition_does_not_double_count_first_round(self):
        first = self.battle_records()
        events = [dict(account='a', event='survival_first_run_archived', actions=first)]
        value = survival_battle_counts(events, 'a', '2026-10-13', first)
        self.assertEqual(2, value['verified_battles'])
        self.assertEqual(0, value['verified_current_round_battles'])

    def test_selected_run_counts_its_own_results_not_inherited_archive_actions(self):
        events = [dict(account='a',event='survival_first_run_archived',actions=self.battle_records()),
                  dict(account='a',event='irreversible_committed',target='survival_battle_step_1'),
                  dict(account='a',event='irreversible_committed',target='survival_battle_step_1')]
        value = survival_battle_counts(events, 'a', '2026-10-13')
        self.assertEqual(1, value['verified_battles'])
        self.assertEqual(0, value['verified_first_round_battles'])

    def test_today_pending_task_is_read_without_a_companion_action_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);day='2026-10-13';name='verified moon'
            state=root/'state'/day;state.mkdir(parents=True);logs=root/'logs';logs.mkdir()
            stem='account_'+hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]
            task_path=state/(stem+'_tasks.json')
            task_path.write_text(json.dumps({'survival':dict(status='pending',completed=1,
                followup_check_due=True,last_error='original page pending')}),encoding='utf-8')
            events=[dict(event='multi_solo_run_started',run_id='today',tasks=['survival'],ts=day+'T15:49:10+08:00'),
                    dict(event='multi_solo_tabs_counted',run_id='today',count=3,ts=day+'T15:49:11+08:00'),
                    dict(event='account_bound',run_id='today',account=name,tab_index=3,ts=day+'T15:50:00+08:00')]
            (logs/'HYOL_Scheduler_20261013_fixture.jsonl').write_text('\n'.join(json.dumps(e) for e in events),encoding='utf-8')
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            value=ProgressReader(root).load(day)
            rec=next(row for row in value['rows'] if row['account']==name)['tasks']['survival']
            self.assertEqual('pending',rec['status'])
            self.assertEqual('original page pending',rec['last_error'])
            self.assertEqual(1,rec['completed'])
            self.assertEqual(before,{p:p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def fixture(self, root):
        state, logs = root/'state', root/'logs'
        (state/'2026-10-12').mkdir(parents=True)
        logs.mkdir()
        (state/'2026-10-12.json').write_text(json.dumps({'verified_solo_tab_map': {'a':180,'b':450}}))
        (state/'2026-10-12/account_a.json').write_text(json.dumps({'account':'a','actions': {
            'survival_battle_step_2': {'status':'pending','operation_id':'original'}}}))
        (state/'2026-10-12/account_a_tasks.json').write_text(json.dumps({
            'mail': dict(status='done',task_completed=True,home_returned=True),
            'survival': dict(status='done',task_completed=True,home_returned=True),
            'managed': dict(status='running')}))
        events = []
        for run, names in [('one', ['a',None,'b']), ('two',['b',None,'a'])]:
            events += [dict(ts='2026-10-12T12:00:00+08:00',event='multi_solo_run_started',run_id=run,
                            tasks=['mail','survival'],managed_enabled=False),
                       dict(ts='2026-10-12T12:00:01+08:00',event='multi_solo_tabs_counted',run_id=run,
                            count=3,title_centers=[180,315,450])]
            for i, name in enumerate(names,1):
                if name:
                    events.append(dict(ts='2026-10-12T12:00:02+08:00',event='account_bound',
                                       run_id=run,account=name,tab_index=i))
            events += [dict(ts='2026-10-12T12:01:00+08:00',event='multi_solo_task_started',
                            run_id=run,account='a',task='survival'),
                       dict(ts='2026-10-12T12:02:00+08:00',event='multi_solo_run_end',run_id=run)]
        path = logs/'HYOL_Scheduler_20261012_fixture.jsonl'
        path.write_text('\n'.join(json.dumps(e) for e in events),encoding='utf-8')
        for run in ('one','two'):
            (logs/('HYOL_Scheduler_'+run+'.jsonl')).write_text('\n'.join(
                json.dumps(e) for e in events if e['run_id']==run),encoding='utf-8')
        return path

    def test_refresh_is_read_only_and_preserves_original_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            reader = ProgressReader(root)
            for _ in range(3):
                value = reader.load('2026-10-12')
            a = next(row for row in value['rows'] if row['account']=='a')
            self.assertEqual('done', a['tasks']['mail']['status'])
            self.assertEqual('pending', a['tasks']['survival']['status'])
            self.assertEqual(['survival_battle_step_2'], a['tasks']['survival']['pending_actions'])
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_each_run_uses_its_own_physical_binding_and_keeps_unknown_slot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); self.fixture(root); reader = ProgressReader(root)
            one = reader.load('2026-10-12','one')['rows']
            two = reader.load('2026-10-12','two')['rows']
            self.assertEqual(['a',None,'b'], [row['account'] for row in one])
            self.assertEqual(['b',None,'a'], [row['account'] for row in two])
            self.assertEqual('pending', one[0]['tasks']['survival']['status'])
            self.assertEqual('unselected', one[0]['tasks']['managed']['status'])

    def test_partial_log_and_incomplete_state_keep_last_good_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); log = self.fixture(root); reader = ProgressReader(root)
            reader.load('2026-10-12')
            path = root/'state/2026-10-12/account_a_tasks.json'
            path.write_text('{"mail":',encoding='utf-8')
            with log.open('a') as handle: handle.write('\n{"event":')
            value = reader.load('2026-10-12')
            a = next(row for row in value['rows'] if row['account']=='a')
            self.assertEqual('done',a['tasks']['mail']['status'])
            self.assertTrue(value['warnings'])


class ArenaOpeningTests(unittest.TestCase):
    def scheduler(self, ledger=None):
        s = object.__new__(hs.Scheduler)
        s.state = ledger or Ledger()
        s._battle_auto_on_visible = Mock(return_value=False)
        s._arena_victory_visible = s._arena_defeat_visible = Mock(return_value=False)
        s._combat_active_visible = Mock(return_value=True)
        s.template = Mock(side_effect=lambda name,*a,**k: (name in (
            'arena_battle_frame_anchor.bmp','arena_skill2_key.bmp','battle_auto_off.bmp'), 1, 895,927))
        s.capture = Mock(return_value=Image.new('RGB',(1920,1032)))
        s.event = Mock()
        def point(account,x,y,key,*args,**kwargs):
            self.assertTrue(kwargs['irreversible'])
            s.state.set_action(account.name,key,'pending')
        def template(account,name,roi,dx,dy,key,*args,**kwargs):
            self.assertTrue(kwargs['irreversible'])
            s.state.set_action(account.name,key,'pending')
            return True
        s.click_point = Mock(side_effect=point)
        s.click_template = Mock(side_effect=template)
        return s

    def test_polling_switching_and_restart_send_each_action_at_most_once(self):
        s=self.scheduler(); a=hs.Account(1,'a',180); frame=s.capture()
        for _ in range(5): s._arena_opening_once(a,3,frame)
        restarted=self.scheduler(s.state)
        for _ in range(5): restarted._arena_opening_once(a,3,frame)
        self.assertEqual(1,s.click_point.call_count)
        self.assertEqual((904,909),s.click_point.call_args.args[1:3])
        self.assertEqual(1,s.click_template.call_count)
        restarted.click_point.assert_not_called();restarted.click_template.assert_not_called()
        s._arena_opening_once(a,4,frame)
        self.assertEqual(2,s.click_point.call_count)

    def test_crash_after_write_ahead_does_not_resend_skill(self):
        s=self.scheduler();a=hs.Account(1,'a',180)
        def interrupt(*args,**kwargs):
            s.state.set_action('a','arena_opening_1_skill2','pending')
            raise KeyboardInterrupt('F12 after input')
        s.click_point.side_effect=interrupt
        with self.assertRaises(KeyboardInterrupt):s._arena_opening_once(a,1,s.capture())
        restarted=self.scheduler(s.state)
        restarted._arena_opening_once(a,1,s.capture())
        restarted.click_point.assert_not_called()
        self.assertEqual(1,restarted.click_template.call_count)

    def test_missing_skill_hud_or_positive_result_sends_zero_inputs(self):
        for missing in ('arena_skill2_key.bmp','arena_battle_frame_anchor.bmp'):
            s=self.scheduler();old=s.template.side_effect
            s.template.side_effect=lambda name,*args,**kwargs: (False,0,0,0) if name==missing else old(name,*args,**kwargs)
            s._arena_opening_once(hs.Account(1,'a',180),1,s.capture())
            s.click_point.assert_not_called();s.click_template.assert_not_called()

    def test_already_auto_on_is_observed_without_toggle(self):
        s=self.scheduler();s._battle_auto_on_visible.return_value=True
        s._arena_opening_once(hs.Account(1,'a',180),1,s.capture())
        self.assertEqual(1,s.click_point.call_count)
        s.click_template.assert_not_called()


class RepairBoundaryTests(unittest.TestCase):
    def test_idle_verified_main_releases_other_tasks_and_preserves_unknown_battle(self):
        owner=object.__new__(multi.MultiSoloRunner);s=object.__new__(hs.Scheduler)
        a=hs.Account(1,'a',180);s.active=a;s.state=Ledger();s.hwnd=None
        s.state.accounts['a']['actions']['arena_casual_battle_9']=dict(status='pending',operation_id='original')
        s.capture=Mock(return_value=Image.new('RGB',(1920,1032)))
        s.main_ready=Mock(return_value=True);s.verify_identity=Mock(return_value=(True,{}))
        s._combat_active_visible=Mock(return_value=False);s._combat_ready_visible=Mock(return_value=False)
        s.template=Mock(side_effect=lambda name,*a,**k:(name=='leaf_village_name_current.bmp',1,0,0))
        s.save_raw_evidence=Mock(return_value=Path('home.png'));s.event=Mock();s.wait_interruptible=Mock()
        s.click_template=Mock();s.check_stop=Mock();s._saved_tab_mapping=Mock(return_value={'a':180})
        owner.scheduler=s;owner.excluded=set();owner.deferred={};owner.deferred_at={};owner.startup_returned=set()
        owner.runner_for=Mock(return_value=SimpleNamespace(records={'arena':{'status':'pending'},'quiz':{'status':'new'}}))
        owner.event=Mock();owner.save_progress=Mock();original=dict(s.state.accounts['a']['actions'])
        self.assertTrue(owner._allow_other_selected_tasks(a,'结果未知'))
        self.assertEqual(original,s.state.accounts['a']['actions'])
        self.assertEqual(['arena'],owner.blocked_tasks['a'])
        self.assertGreaterEqual(s.verify_identity.call_count,5)
        s.click_template.assert_not_called()
        s.main_ready.return_value=False
        self.assertFalse(owner._allow_other_selected_tasks(a,'页面变化'))

    def test_pause_discards_observation_without_counting_paused_time(self):
        s=object.__new__(hs.Scheduler);s.stop=SimpleNamespace(is_set=lambda:True)
        a=hs.Account(1,'a',180);old=s.begin_task_observation(a,'quiz')
        watch=s._task_observation[2];watch.observe(Image.new('RGB',(1920,1032)),0)
        s.end_task_observation(old)
        self.assertIsNone(s._task_observation)
        s.stop.is_set=lambda:False
        s.begin_task_observation(a,'quiz')
        self.assertIsNot(watch,s._task_observation[2])
        self.assertIsNone(s._task_observation[2].observe(Image.new('RGB',(1920,1032)),600))

    def reset_scheduler(self, before_path):
        s=object.__new__(hs.Scheduler);s.templates=hs.TemplateStore(.98);s.detector=None;s.state=Ledger()
        s.state.accounts['a']['actions']['survival_reset']=dict(status='pending',
            operation_id='original:survival_reset',before_evidence=str(before_path))
        s.save_raw_evidence=Mock(return_value=Path('verified.png'));s.event=Mock()
        return s

    def test_partial_reset_requires_original_full_reward_and_one_to_zero_evidence(self):
        before=hs.ROOT/'tests/fixtures/survival_reset_before_20261012.png'
        after=Image.open(hs.ROOT/'tests/fixtures/survival_partial_after_20261012.png').convert('RGB')
        s=self.reset_scheduler(before)
        self.assertTrue(s.reconcile_survival_partial_reset(hs.Account(1,'a',180),after))
        self.assertEqual('done',s.state.action_status('a','survival_reset'))
        self.assertFalse(any(key.startswith('survival_battle_') for key in s.state.accounts['a']['actions']))

    def test_partial_score_never_settles_an_unknown_battle_or_missing_reset_witness(self):
        after=Image.open(hs.ROOT/'tests/fixtures/survival_partial_after_20261012.png').convert('RGB')
        for mode in ('unknown_battle','missing_before','unclaimed_before'):
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'before.png'
                before=Image.open(hs.ROOT/'tests/fixtures/survival_reset_before_20261012.png').convert('RGB')
                if mode=='unclaimed_before':before.paste((0,0,0),(1151,769,1451,880))
                if mode!='missing_before':before.save(path)
                s=self.reset_scheduler(path)
                if mode=='unknown_battle':s.state.set_action('a','survival_battle_step_2','pending')
                original=dict(s.state.accounts['a']['actions'])
                self.assertFalse(s.reconcile_survival_partial_reset(hs.Account(1,'a',180),after))
                self.assertEqual(original,s.state.accounts['a']['actions'])

    def test_offline_prompt_is_immediate_and_preserves_task_result(self):
        s=Mock();s.offline_dialog_visible.return_value=True
        s.save_raw_evidence.return_value=Path('offline.png')
        a=hs.Account(1,'a',180)
        with self.assertRaises(hs.AccountOffline):
            hs.check_battle_watchdog(s,BattleWatchdog(),a,Image.new('RGB',(1920,1032)),'quiz',mode='quiz',now=0)
        s.state.set_action.assert_not_called();s.click_template.assert_not_called()

    def test_real_change_restarts_30_seconds_and_other_tabs_do_not(self):
        black=Image.new('RGB',(1920,1032));white=Image.new('RGB',(1920,1032),'white')
        a,b=BattleWatchdog(),BattleWatchdog()
        a.observe(black,0);b.observe(black,0)
        self.assertIsNone(a.observe(black,29))
        self.assertIsNone(a.observe(white,29))
        self.assertEqual('unchanged_frame',b.observe(black,30)['reason'])
        self.assertIsNone(a.observe(white,58.999))
        self.assertEqual('unchanged_frame',a.observe(white,59)['reason'])

    def test_announcement_chat_and_mouse_do_not_extend_timer(self):
        a=Image.new('RGB',(1920,1032));b=a.copy();draw=ImageDraw.Draw(b)
        for box in ((0,0,1920,160),(650,220,1300,260),(300,700,630,1000),(800,500,805,505)):
            draw.rectangle(box,fill='white')
        watch=BattleWatchdog();watch.observe(a,0)
        self.assertIsNotNone(watch.observe(b,30))

    def test_reward_page_can_repaint_for_more_than_twelve_polls_without_claim(self):
        s=object.__new__(hs.Scheduler);s.state=Ledger();s.config={};s.offline_dialog_visible=Mock(return_value=False)
        s._arena_reward_claim_done=Mock(return_value=False);s._arena_daily_rewards_complete=Mock(return_value=False)
        counter=[0]
        def capture():
            counter[0]+=1; frame=Image.new('RGB',(1920,1032));frame.info['ready']=counter[0]>16;return frame
        s.capture=Mock(side_effect=capture)
        s.template=Mock(side_effect=lambda name,roi,frame,*a,**kw:
                        (name=='arena_ready_anchor.bmp' and frame.info['ready'],1,0,0))
        s.wait_interruptible=Mock();s.click_template=Mock();s.event=Mock()
        s._claim_arena_tasks(hs.Account(1,'a',180))
        self.assertGreater(s.capture.call_count,16)
        s.click_template.assert_not_called()


if __name__=='__main__':unittest.main()
