import tempfile
import hashlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
import hyol_scheduler as hs
import hyol_daily_tasks as daily
import hyol_ranking as ranking


class RankingTests(unittest.TestCase):
    def test_real_ten_to_nine_counter_never_reads_plus_as_eight(self):
        store = hs.TemplateStore(.92)
        base = hs.EVIDENCE_DIR/'2026-10-05/account_ae1662240e'
        sources = ((10, '052057_659214_before_daily_ranking_1_start.jpg'),
                   (9, '052244_202757_pending_solo_ranking_pending.png'))
        for count, name in sources:
            with self.subTest(count=count):
                frame = Image.open(base/name).convert('RGB')
                store.begin_frame(frame)
                r = SimpleNamespace(scheduler=SimpleNamespace())
                r.scheduler.template = lambda name, roi, image=None, threshold=.92: store.match(
                    image if image is not None else frame, hs.ASSETS/name, roi, threshold)
                r.visible = lambda name, roi, image=None, threshold=.92: r.scheduler.template(
                    name, roi, image, threshold)[0]
                self.assertEqual(ranking.remaining(r, frame, save_unknown=False), count)
        self.assertLessEqual(ranking.HIGH_DIGIT[2], 1586)

    def test_missing_two_digit_asset_refuses_single_glyph_fallback(self):
        r = SimpleNamespace(visible=Mock(return_value=True), scheduler=Mock())
        original_exists = Path.exists
        with patch.object(Path, 'exists', lambda path: False if path.name == 'ranking_count_10_current.bmp' else original_exists(path)):
            with self.assertRaisesRegex(hs.PendingError, '两位数10'):
                ranking.remaining(r, Image.new('RGB', (1920, 1032)), save_unknown=False)
        self.assertEqual([call.args[0] for call in r.visible.call_args_list],
                         ['ranking_page.bmp', 'ranking_counter_label.bmp'])
        r.scheduler.template.assert_not_called()

    def test_reviewed_six_seven_eight_frames_match_only_their_complete_field(self):
        store = hs.TemplateStore(.985)
        base = hs.EVIDENCE_DIR/'2026-10-05/account_ae1662240e'
        sources = ((6, '111904_013504_observed_ranking_before.png'),
                   (7, '110713_485796_pending_solo_ranking_pending.png'),
                   (8, '110541_035669_observed_ranking_before.png'))
        for expected, filename in sources:
            frame = Image.open(base/filename).convert('RGB')
            r = SimpleNamespace(scheduler=SimpleNamespace())
            r.scheduler.template = lambda name, roi, image=None, threshold=.92: store.match(
                image if image is not None else frame, hs.ASSETS/name, roi, threshold)
            r.visible = lambda name, roi, image=None, threshold=.92: r.scheduler.template(name, roi, image, threshold)[0]
            self.assertEqual(ranking.remaining(r, frame, save_unknown=False), expected)
            matches = [count for count in (6, 7, 8, 9, 10) if r.visible(
                f'ranking_count_{count}_current.bmp', ranking.CURRENT_COUNTER, frame, .985)]
            self.assertEqual(matches, [expected])

    def test_live_toolbar_fault_frames_match_without_lowering_threshold(self):
        store = hs.TemplateStore(.92)
        base = hs.LOGS / 'scheduler_evidence' / '2026-10-01'
        sources = (
            base / 'account_3ed81e0fcf' / '214556_862152_pending_activity_toolbar_unknown.png',
            base / 'account_72cd26215f' / '214604_181087_pending_activity_toolbar_unknown.png',
            base / 'account_a802d1815a' / '214611_428075_pending_activity_toolbar_unknown.png',
            base / 'account_b76494cf2c' / '214618_736317_pending_activity_toolbar_unknown.png',
        )
        for source in sources:
            with self.subTest(source=source.name):
                frame = Image.open(source).convert('RGB')
                matched, score, _, _ = store.match(
                    frame, hs.ASSETS / 'ranking_toolbar.bmp', ranking.TOOLBAR, .92)
                self.assertTrue(matched, f'{source}: score={score:.3f}')

    def test_solo_defaults_exclude_every_team_task(self):
        self.assertEqual(daily.selected_sequence('all'), daily.DEFAULT_SOLO_OPTIONS)
        self.assertNotIn('treasure', daily.selected_sequence('all'))
        self.assertFalse(set(daily.SOLO_OPTIONS) & set(daily.TEAM_OPTIONS))
        for task in daily.TEAM_OPTIONS:
            with self.assertRaises(ValueError):
                daily.selected_sequence('all', [task])

    def runner(self, count=5):
        r = SimpleNamespace()
        r.rec = {'completed': 0, 'phase': 'ready'}
        r.record = lambda task: r.rec
        r.save = Mock()
        r.event = Mock()
        r.wait = Mock()
        r.account = hs.Account(1,'mock',180)
        r.mode, r.count, r.clicks = 'page', count, []
        actions = {}
        s = r.scheduler = Mock()
        s.state.day = hs.date.today().isoformat()
        s.state.action_status.side_effect = lambda account,key: actions.get(key,'new')
        s.state.action_record.side_effect = lambda account,key: ({'status': actions[key],
            'updated_at': s.state.day+'T10:00:00+08:00'} if key in actions else {})
        s.capture.return_value = Image.new('RGB',(1920,1032))
        s.commit_action.side_effect = lambda account,key,detail: actions.update({key:'done'})
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        def save_evidence(frame, *args):
            path = Path(folder.name)/'counter.png'
            frame.save(path)
            return path
        s.save_raw_evidence.side_effect = save_evidence
        s.wait_until_main.return_value = True
        s.main_ready.side_effect = lambda frame=None: r.mode == 'home'
        def visible(name,*args,**kwargs):
            names = {'page': ('ranking_page.bmp','ranking_challenge.bmp'),
                     'battle': ('ranking_battle_anchor.bmp','ranking_end_battle.bmp'),
                     'result': ('ranking_result_confirm.bmp','ranking_result_stats.bmp')}
            return name in names.get(r.mode,())
        r.visible = visible
        def click(name,roi,dx,dy,label,anchors,irreversible=False):
            if irreversible:
                self.assertEqual(actions.get(label,'new'),'new',f'duplicate {label}')
                actions[label]='pending'
            r.clicks.append((name,roi,label))
            if name == 'ranking_challenge.bmp':
                self.assertGreater(r.count,0)
                self.assertEqual(roi,ranking.FIRST)
                r.mode='battle'
            elif name == 'ranking_end_battle.bmp':
                r.mode='result'
            elif name == 'ranking_result_confirm.bmp':
                r.count-=1
                r.mode='page'
            elif name == 'ranking_close.bmp':
                self.assertEqual(r.count,0)
                r.mode='home'
        r.click=click
        r.actions=actions
        return r

    def test_five_end_clicks_and_actual_zero_required(self):
        r=self.runner()
        with patch.object(ranking,'stable_remaining',side_effect=lambda r:r.count):
            ranking.run(r)
        self.assertEqual(r.rec['completed'],5)
        self.assertTrue(r.rec['zero_verified'])
        self.assertEqual(sum(n=='ranking_end_battle.bmp' for n,_,_ in r.clicks),5)
        self.assertEqual(len(r.actions),15)
        self.assertTrue(all(value=='done' for value in r.actions.values()))

    def test_multi_account_mode_yields_immediately_after_end_click(self):
        r = self.runner(2)
        r.mode = 'battle'
        r.rec.update(before=2, phase='confirmed', status='running')
        r.actions.update(daily_ranking_1_start='pending',
                         daily_ranking_1_confirm='pending')
        r.interleave_after_end = True

        result = ranking.run(r)

        self.assertEqual(result, 'yield_after_end')
        self.assertEqual(r.rec['phase'], 'end_sent')
        self.assertEqual([name for name, _, _ in r.clicks],
                         ['ranking_end_battle.bmp'])
        self.assertEqual(r.actions['daily_ranking_1_end'], 'pending')
        self.assertNotIn('daily_ranking_1_result', r.actions)
        r.save.assert_called()

    def test_preexisting_zero_never_starts_battle(self):
        r=self.runner(0)
        with patch.object(ranking,'stable_remaining',return_value=0):
            ranking.run(r)
        self.assertEqual([n for n,_,_ in r.clicks],['ranking_close.bmp'])

    def test_warning_overlay_precedes_dimmed_page_counter(self):
        r=self.runner(1)
        r.mode='warning'
        r.rec.update(before=1,phase='start_pending')
        original_visible,original_click=r.visible,r.click
        def visible(name,*args,**kwargs):
            if r.mode=='warning':
                return name in ('ranking_page.bmp','ranking_power_warning.bmp')
            return original_visible(name,*args)
        def click(name,*args):
            if name=='ranking_power_confirm.bmp':
                r.mode='battle'
                r.clicks.append((name,args[0],args[3]))
            else: original_click(name,*args)
        r.visible,r.click=visible,click
        def count(r):
            self.assertNotEqual(r.mode,'warning')
            return r.count
        with patch.object(ranking,'stable_remaining',side_effect=count):
            ranking.run(r)
        self.assertTrue(r.rec['zero_verified'])

    def test_verified_defeat_result_can_resume_without_end_click(self):
        r=self.runner(1)
        r.mode='result'
        r.rec.update(before=1, phase='confirmed', status='pending')
        r.actions.update(daily_ranking_1_start='pending', daily_ranking_1_confirm='pending')
        original_visible=r.visible
        r.visible=lambda name,*args,**kwargs: (True if name=='arena_defeat_banner.bmp'
                         and r.mode=='result' else original_visible(name,*args,**kwargs))
        with patch.object(ranking,'stable_remaining',side_effect=lambda r:r.count):
            ranking.run(r)
        self.assertEqual(r.rec['completed'],1)
        self.assertEqual(r.rec['remaining'],0)
        self.assertNotIn('daily_ranking_1_end',r.actions)
        self.assertEqual(r.actions['daily_ranking_1_result'],'done')

    def test_end_guard_failure_before_click_retries_same_action(self):
        r=self.runner(1)
        r.mode='battle'
        r.rec.update(before=1, phase='confirmed', status='pending')
        r.actions.update(daily_ranking_1_start='pending',daily_ranking_1_confirm='pending')
        original_click=r.click
        attempts=[]
        def click(name,roi,dx,dy,label,anchors,irreversible=False):
            if name=='ranking_end_battle.bmp':
                attempts.append(tuple(anchors))
                if len(attempts)==1:
                    raise hs.SafetyError('page anchor guard failed: transient animation')
            original_click(name,roi,dx,dy,label,anchors,irreversible)
        r.click=click
        with patch.object(ranking,'stable_remaining',side_effect=lambda r:r.count):
            with self.assertRaises(hs.SafetyError):
                ranking.run(r)
            self.assertEqual(r.rec['phase'],'end_pending')
            self.assertEqual(r.actions.get('daily_ranking_1_end','new'),'new')
            ranking.run(r)
        self.assertEqual(r.rec['completed'],1)
        self.assertEqual(r.rec['remaining'],0)
        self.assertEqual(attempts[-1],('ranking_battle_anchor.bmp','ranking_end_battle.bmp'))

    def test_real_defeat_result_has_three_positive_anchors(self):
        frame=Image.open(hs.LOGS/'preflight_20260928_203115_035934_1f749ecf.png').convert('RGB')
        store=hs.TemplateStore(.90)
        for name,roi,threshold in (
            ('ranking_result_confirm.bmp',ranking.RESULT,.95),
            ('ranking_result_stats.bmp',(890,605,1035,653),.95),
            ('arena_defeat_banner.bmp',(650,320,1250,800),.90),
        ):
            self.assertTrue(store.match(frame,hs.ASSETS/name,roi,threshold)[0],name)

    def test_unreadable_count_has_zero_clicks(self):
        r=self.runner()
        with patch.object(ranking,'stable_remaining',side_effect=hs.PendingError('unknown')):
            with self.assertRaises(hs.PendingError): ranking.run(r)
        self.assertEqual(r.clicks,[])

    def test_counter_decrease_without_end_does_not_count(self):
        r=self.runner(3)
        r.rec.update(before=4,phase='start_pending')
        with patch.object(ranking,'stable_remaining',return_value=3):
            with self.assertRaises(hs.PendingError): ranking.run(r)
        self.assertEqual(r.clicks,[])
        self.assertEqual(r.rec['completed'],0)

    def test_pending_confirm_on_main_keeps_prior_start_unresolved(self):
        r=self.runner(3)
        r.mode='home'
        r.rec.update(before=3, phase='confirmed', status='pending')
        with self.assertRaises(hs.PendingError):
            ranking.run(r)
        self.assertEqual(r.clicks,[])
        self.assertEqual(r.rec['completed'],0)
        r.scheduler.save_raw_evidence.assert_called_once()

    def test_audited_sent_result_on_main_reopens_counter_without_repeating_old_battle(self):
        r = self.runner(1)
        r.mode = 'home'
        r.rec.update(before=2, phase='result_sent', status='pending')
        r.actions.update(daily_ranking_1_start='pending', daily_ranking_1_result='pending')
        original_click = r.click
        def click(name, *args):
            if name == 'ranking_toolbar.bmp':
                r.clicks.append((name, args[0], args[3]))
                r.mode = 'page'
            else:
                original_click(name, *args)
        r.click = click
        with patch.object(ranking, 'sent_result_can_reopen', return_value=True), \
                patch.object(ranking, 'stable_remaining', side_effect=lambda r: r.count):
            ranking.run(r)
        self.assertEqual(r.rec['completed'], 2)
        self.assertTrue(r.rec['zero_verified'])
        self.assertNotIn('daily_ranking_1_end', r.actions)
        self.assertEqual(r.actions['daily_ranking_1_result'], 'done')
        self.assertEqual([label for _, _, label in r.clicks if label.endswith('_start')],
                         ['daily_ranking_2_start'])

    def test_sent_result_counter_mismatch_keeps_original_actions_pending(self):
        r = self.runner(4)
        r.rec.update(before=2, phase='result_sent', status='pending')
        r.actions.update(daily_ranking_1_start='pending', daily_ranking_1_result='pending')
        with patch.object(ranking, 'sent_result_can_reopen', return_value=True), \
                patch.object(ranking, 'stable_remaining', return_value=4):
            with self.assertRaisesRegex(hs.PendingError, '次数变化不一致'):
                ranking.run(r)
        self.assertEqual(r.clicks, [])
        self.assertEqual(r.rec['completed'], 0)
        self.assertEqual(set(r.actions.values()), {'pending'})

    def test_sent_result_reopen_requires_same_day_unique_actions_and_unchanged_counter_proof(self):
        r = self.runner(9)
        source = hs.EVIDENCE_DIR/'2026-10-05/account_ae1662240e/052057_659214_before_daily_ranking_1_start.jpg'
        r.rec.update(before=10, phase='result_sent', counter_before_evidence=str(source),
                     counter_before_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        r.actions.update(daily_ranking_1_start='pending', daily_ranking_1_result='pending')
        store = hs.TemplateStore(.985)
        original_visible = r.visible
        r.visible = lambda name, roi, frame=None, threshold=.92: (store.match(
            frame, hs.ASSETS/name, roi, threshold)[0] if frame is not None else original_visible(name, roi))
        self.assertTrue(ranking.sent_result_can_reopen(r, r.rec))
        r.rec['counter_before_sha256'] = 'changed'
        self.assertFalse(ranking.sent_result_can_reopen(r, r.rec))
        r.rec['counter_before_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        r.scheduler.state.day = '2000-01-01'
        self.assertFalse(ranking.sent_result_can_reopen(r, r.rec))
        r.scheduler.state.day = hs.date.today().isoformat()
        r.scheduler.state.action_record.side_effect = lambda account, key: {
            'status': 'pending', 'updated_at': r.scheduler.state.day+'T10:00:00',
            'checkpoint_conflict': [{'status': 'pending'}]}
        self.assertFalse(ranking.sent_result_can_reopen(r, r.rec))

    def test_audited_manual_defeat_reconciles_only_one_real_counter_decrement(self):
        with tempfile.TemporaryDirectory() as folder:
            witness=Path(folder)/'defeat.png'
            witness.write_bytes(b'verified defeat screenshot')
            r=self.runner(4)
            r.mode='home'
            r.scheduler.main_ready.side_effect=lambda frame=None:r.mode=='home'
            r.rec.update(before=5,phase='confirmed',status='pending',
                manual_defeat_result_evidence=str(witness),
                manual_defeat_result_sha256=hashlib.sha256(witness.read_bytes()).hexdigest())
            r.actions.update(daily_ranking_1_start='pending',daily_ranking_1_confirm='pending')
            original_click=r.click
            def click(name,*args):
                if name=='ranking_toolbar.bmp':
                    r.clicks.append((name,args[0],args[3]))
                    r.mode='page'
                else:
                    original_click(name,*args)
            r.click=click
            with patch.object(ranking,'stable_remaining',side_effect=lambda r:r.count):
                ranking.run(r)
            self.assertEqual(r.rec['completed'],5)
            self.assertTrue(r.rec['zero_verified'])
            self.assertEqual(r.rec['manual_result_reconciled'],str(witness))
            self.assertNotIn('daily_ranking_1_end',r.actions)
            self.assertEqual(r.actions['daily_ranking_1_start'],'done')
            self.assertEqual(r.actions['daily_ranking_1_confirm'],'done')

    def test_manual_defeat_witness_must_match_saved_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            witness=Path(folder)/'defeat.png'
            witness.write_bytes(b'original')
            r=self.runner(4)
            r.mode='home'
            r.rec.update(before=5,phase='confirmed',status='pending',
                manual_defeat_result_evidence=str(witness),
                manual_defeat_result_sha256=hashlib.sha256(witness.read_bytes()).hexdigest())
            witness.write_bytes(b'replaced')
            with self.assertRaises(hs.PendingError):
                ranking.run(r)
            self.assertEqual(r.clicks,[])

    def test_real_counter_templates_do_not_confuse_digits(self):
        store=hs.TemplateStore(.985)
        r=SimpleNamespace()
        r.account=hs.Account(1,'replay',180)
        r.scheduler=Mock()
        r.scheduler.template.side_effect=lambda name,roi,frame,threshold=None: store.match(
            frame,hs.ASSETS/name,roi,threshold=threshold)
        r.visible=lambda name,roi,frame,threshold=.985: store.match(frame,hs.ASSETS/name,roi,threshold=threshold)[0]
        frame=Image.open(hs.LOGS/'ranking_counter4_20260916.png').convert('RGB')
        for digit in range(6):
            path=hs.ASSETS/f'ranking_digit_{digit}.bmp'
            self.assertTrue(path.exists())
            frame.paste(Image.open(path),(1566,159))
            self.assertEqual(ranking.remaining(r,frame),digit)
        frame.paste('black',(1563,156,1585,183))
        with self.assertRaises(hs.PendingError): ranking.remaining(r,frame)

    def test_live_09_counter_and_partial_load_do_not_guess(self):
        store=hs.TemplateStore(.985)
        r=SimpleNamespace(account=hs.Account(1,'replay',180))
        r.scheduler=Mock()
        r.scheduler.template.side_effect=lambda name,roi,frame,threshold=None: store.match(
            frame,hs.ASSETS/name,roi,threshold=threshold)
        r.visible=lambda name,roi,frame,threshold=.985: store.match(
            frame,hs.ASSETS/name,roi,threshold=threshold)[0]
        base=hs.LOGS/'scheduler_evidence'/'2026-09-28'
        nine=Image.open(base/'account_c9ba2069ff'/'193141_332155_pending_ranking_counter_unknown.png').convert('RGB')
        loading=Image.open(base/'account_ae1662240e'/'193433_337979_pending_ranking_counter_unknown.png').convert('RGB')
        self.assertEqual(ranking.remaining(r,nine),9)
        self.assertEqual(ranking.remaining(r,loading),9)
        self.assertFalse(r.visible('ranking_challenge.bmp',ranking.FIRST,loading,.95))

    def test_loading_bar_over_village_is_not_settled_main(self):
        loading=Image.open(hs.LOGS/'scheduler_evidence'/'2026-09-28'/
            'account_ae1662240e'/'202804_586819_pending_ranking_unresolved_on_main.png').convert('RGB')
        main=Image.open(hs.LOGS/'preflight_20260928_202106_550698_a04eaa69.png').convert('RGB')
        self.assertTrue(ranking.transition_loading_visible(loading))
        self.assertFalse(ranking.transition_loading_visible(main))

    def test_partial_page_waits_for_opponents_before_first_click(self):
        r=self.runner(9)
        r.mode='loading'
        original_visible=r.visible
        r.visible=lambda name,*args,**kwargs: (name=='ranking_page.bmp'
                 if r.mode=='loading' else original_visible(name,*args,**kwargs))
        waited=[]
        def after_wait(_):
            if not waited:
                self.assertEqual(r.clicks,[])
                waited.append(True)
                r.mode='page'
        r.scheduler.wait_interruptible.side_effect=after_wait
        with patch.object(ranking,'stable_remaining',side_effect=lambda r:r.count):
            ranking.run(r)
        self.assertTrue(waited)
        self.assertEqual(r.rec['completed'],9)

    def test_atomic_write_retries_rename_without_losing_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.json'
            real=hs.os.replace
            calls=[]
            def replace(src,dst):
                calls.append(src)
                if len(calls)==1: raise PermissionError('sharing violation')
                return real(src,dst)
            with patch.object(hs.os,'replace',side_effect=replace),patch.object(hs.time,'sleep'):
                hs.atomic_json(path,{'completed':4})
            self.assertEqual(daily.read_json(path),{'completed':4})
            self.assertEqual(len(calls),2)

    def test_anonymous_tab_ledgers_are_separate_between_browser_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            first=hs.StateStore(['当前窗口标签1'],directory=root/'session_one')
            first.set_action('当前窗口标签1','daily_ranking_1_start','done','first account')
            second=hs.StateStore(['当前窗口标签1'],directory=root/'session_two')
            self.assertEqual(second.action_status('当前窗口标签1','daily_ranking_1_start'),'new')
            resumed=hs.StateStore(['当前窗口标签1'],directory=root/'session_one')
            self.assertEqual(resumed.action_status('当前窗口标签1','daily_ranking_1_start'),'done')


if __name__ == '__main__': unittest.main()
