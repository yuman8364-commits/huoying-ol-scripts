"""Evidence, daily boundaries and no-restart regressions for Ninja Puzzle."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

import hyol_scheduler as hs
import hyol_puzzle as p
from hyol_daily_tasks import DEFAULT_SOLO_OPTIONS, DailyRunner
from hyol_multi_solo import MultiSoloRunner
from hyol_runtime import RunRequest, completion_verified


FIXTURES = Path(__file__).parent / 'fixtures' / 'puzzle'


def source_frame(name, offset=(500,210)):
    result = Image.new('RGB', (1920,1032), (20,20,20))
    with Image.open(FIXTURES/(name+'.png')) as source:
        result.paste(source, offset)
    return result


class PixelScheduler:
    def __init__(self):
        self.store = hs.TemplateStore(.95)

    def template(self, name, roi, frame, threshold=None):
        return self.store.match(frame, hs.ASSETS/name, roi, threshold)


class PuzzleTests(unittest.TestCase):
    def setUp(self):
        self.pixel = SimpleNamespace(scheduler=PixelScheduler())

    def test_three_actual_difficulty_rows_are_detected(self):
        frame = source_frame('select')
        self.assertEqual((818,222),p.select_layout(self.pixel,frame))
        self.assertEqual([4,5,6],[row.grid for row in p.DIFFICULTIES])
        layout = p.select_layout(self.pixel,frame)
        self.assertEqual(['gray']*3,[p.claim_state(self.pixel,frame,layout,row) for row in p.DIFFICULTIES])

    def test_one_missing_difficulty_rejects_selection_page(self):
        frame = source_frame('select')
        frame.paste('black',(665,473,760,515))
        self.assertIsNone(p.select_layout(self.pixel,frame))

    def test_game_requires_both_sign_and_specific_close(self):
        frame = source_frame('board',(350,100))
        self.assertEqual((514,244,941,670),p.game_layout(self.pixel,frame)[0])
        frame.paste('black',(1366,160,1410,204))
        self.assertIsNone(p.game_layout(self.pixel,frame))

    def test_dark_overlay_cannot_masquerade_as_claim_success(self):
        frame = source_frame('select')
        dark = Image.fromarray((np.asarray(frame)*.35).astype('uint8'))
        # NCC alone is intensity invariant; the live Start color is also checked.
        layout = p.select_layout(self.pixel,dark)
        self.assertIsNotNone(layout)
        self.assertEqual('unknown',p.claim_state(self.pixel,dark,layout,p.DIFFICULTIES[0]))

    def test_blank_or_partial_board_is_not_assembled(self):
        reference = source_frame('board',(350,100)).crop((514,244,941,670))
        self.assertEqual(0,p.board_error(reference,reference.copy(),4))
        self.assertGreater(p.board_error(reference,Image.new('RGB',reference.size),4),18)
        self.assertEqual(float('inf'),p.board_error(Image.new('RGB',(400,400)),Image.new('RGB',(400,400)),4))

    def test_activity_day_keeps_midnight_and_rolls_after_five(self):
        self.assertEqual('2026-10-10',p.activity_day(datetime(2026,10,11,0,1)))
        self.assertEqual('2026-10-10',p.activity_day(datetime(2026,10,11,4,59)))
        self.assertEqual('2026-10-11',p.activity_day(datetime(2026,10,11,5,0)))

    def test_common_weekend_entry_keeps_monday_puzzle_until_five(self):
        from hyol_weekend import is_weekend
        self.assertTrue(is_weekend(datetime(2026,10,10,0,1)))  # existing Daruma Saturday
        self.assertTrue(is_weekend(datetime(2026,10,12,0,1)))
        self.assertTrue(is_weekend(datetime(2026,10,12,4,59)))
        self.assertFalse(is_weekend(datetime(2026,10,12,5,0)))
        self.assertFalse(is_weekend(datetime(2026,10,9,4,59)))

    def test_closed_puzzle_does_not_skip_independent_open_daruma(self):
        import hyol_weekend as weekend
        rec = {'task_completed': False}
        r = SimpleNamespace(record=lambda _name: rec, scheduler=SimpleNamespace(
            state=SimpleNamespace(accounts={'role': {'actions': {}}})), account=hs.Account(1,'role',180),
            visible=Mock(return_value=False), event=Mock(), save=Mock())
        def daruma(_r):
            rec.update(zero_verified=True, zero_evidence='current-zero.png')
        with patch.object(weekend,'is_weekend',return_value=True), patch('hyol_daruma.is_weekend',return_value=True), \
                patch('hyol_puzzle.run',side_effect=weekend.WeekendUnavailable('拼图日期门关闭')), \
                patch('hyol_daruma.run',side_effect=daruma) as play:
            with self.assertRaises(weekend.WeekendUnavailable):
                weekend.run_all(r)
        play.assert_called_once_with(r)
        self.assertFalse(rec['task_completed'])
        self.assertTrue(rec['legacy_daruma_finished'])

    def test_closed_common_entry_cannot_return_success_after_five(self):
        import hyol_weekend as weekend
        r = SimpleNamespace(event=Mock(), visible=Mock(), record=Mock())
        with patch.object(weekend, 'is_weekend', return_value=False):
            with self.assertRaises(weekend.WeekendUnavailable):
                weekend.run_all(r)
        r.visible.assert_not_called()
        r.record.assert_not_called()

    def test_monday_closed_daruma_cannot_reuse_old_zero_as_completion(self):
        import hyol_weekend as weekend
        rec = {'task_completed': False, 'zero_verified': True, 'zero_evidence': 'older-zero.png'}
        r = SimpleNamespace(record=lambda _name: rec, scheduler=SimpleNamespace(
            state=SimpleNamespace(accounts={'role': {'actions': {}}})), account=hs.Account(1,'role',180),
            visible=Mock(return_value=False), event=Mock(), save=Mock())
        def puzzle(_r):
            rec['puzzle'] = self.complete_record(p.activity_day())
        with patch.object(weekend,'is_weekend',return_value=True), patch('hyol_daruma.is_weekend',return_value=False), \
                patch('hyol_puzzle.run',side_effect=puzzle), patch('hyol_daruma.run') as play:
            with self.assertRaises(weekend.WeekendUnavailable):
                weekend.run_all(r)
        play.assert_not_called()
        self.assertNotIn('daruma_game_day',rec)
        self.assertNotIn('legacy_daruma_finished',rec)
        self.assertFalse(rec['task_completed'])

    def complete_record(self, day='2026-10-10'):
        return dict(status='done',game_day=day,difficulties={
            d.key:dict(solved=True,claimed=True,claim_evidence=d.key+'.png') for d in p.DIFFICULTIES})

    def test_each_day_requires_three_independent_claim_evidences(self):
        record = self.complete_record()
        self.assertTrue(p.progress_complete(record,'2026-10-10'))
        self.assertFalse(p.progress_complete(record,'2026-10-11'))
        record['difficulties']['normal']['claimed'] = False
        self.assertFalse(p.progress_complete(record,'2026-10-10'))

    def test_old_daruma_done_never_skips_new_puzzles_or_resets_strikes(self):
        old = dict(status='done',task_completed=True,home_returned=True,zero_verified=True,
                   zero_evidence='old.png',hits_verified=6,attempts_started=2)
        p.normalize_weekend_record(old,'2026-10-10')
        self.assertEqual('new',old['status'])
        self.assertFalse(old['task_completed'])
        self.assertTrue(old['legacy_daruma_finished'])
        self.assertEqual(6,old['hits_verified'])
        self.assertEqual(2,old['attempts_started'])
        self.assertTrue(old['zero_verified'])

    def test_current_complete_weekend_survives_normalization(self):
        record = dict(status='done',task_completed=True,home_returned=True,puzzle=self.complete_record())
        before = deepcopy(record)
        p.normalize_weekend_record(record,'2026-10-10')
        self.assertEqual(before,record)

    def test_weekend_is_unselected_by_default_and_incomplete_runtime_done_is_rejected(self):
        self.assertNotIn('weekend',DEFAULT_SOLO_OPTIONS)
        runner = SimpleNamespace(records={'weekend':dict(status='done',task_completed=True,home_returned=True)})
        self.assertFalse(completion_verified(RunRequest(mode='weekend',final_managed=False),runner,0))

    def test_weekend_requests_original_puzzle_before_start_return(self):
        coordinator = object.__new__(MultiSoloRunner)
        coordinator.scheduler = SimpleNamespace(state=SimpleNamespace(accounts={'role':{
            'actions':{'puzzle_original_start':{'status':'pending'}}}}))
        account = hs.Account(1,'role',180)
        self.assertEqual('weekend',coordinator.unresolved_combat_task(account))

    def runner(self, directory, frame):
        account = hs.Account(1,'role',180)
        s = Mock()
        s.state.directory = Path(directory)
        s.state.accounts = {account.name:{'actions':{}}}
        s.state.action_status.return_value = 'new'
        s._generic_active_tab_x.return_value=180
        s.capture.return_value = frame
        s.save_raw_evidence.return_value = Path(directory)/'evidence.png'
        s.template = self.pixel.scheduler.template
        records = {'weekend':{}}
        return SimpleNamespace(scheduler=s,account=account,record=lambda name:records.setdefault(name,{}),
                               save=Mock(),click=Mock(),wait=Mock(),event=Mock())

    def test_unresolved_game_is_not_started_or_closed_again(self):
        frame = source_frame('board',(350,100))
        with TemporaryDirectory() as directory:
            r=self.runner(directory,frame)
            factory=Mock()
            run=p.PuzzleRun(r,session_factory=factory,at=datetime(2026,10,10,12))
            run.record['difficulties']['easy']['phase']='solving'
            with self.assertRaisesRegex(hs.PendingError,'不再次调用解题'):
                run.reconcile(p.DIFFICULTIES[0],run.record['difficulties']['easy'])
            factory.assert_not_called()
            r.click.assert_not_called()

    def test_gray_initial_claim_cannot_be_counted(self):
        frame=source_frame('select')
        with TemporaryDirectory() as directory:
            r=self.runner(directory,frame)
            run=p.PuzzleRun(r,at=datetime(2026,10,10,12))
            row=run.record['difficulties']['easy']
            with self.assertRaisesRegex(hs.PendingError,'灰色按钮不能证明'):
                run.claim(p.DIFFICULTIES[0],row,frame,p.select_layout(r,frame))
            r.click.assert_not_called()
            self.assertFalse(row['claimed'])

    def test_unknown_pending_claim_never_repeats_click(self):
        frame=source_frame('select')
        with TemporaryDirectory() as directory:
            r=self.runner(directory,frame)
            r.scheduler.state.action_status.return_value='pending'
            run=p.PuzzleRun(r,at=datetime(2026,10,10,12))
            row=run.record['difficulties']['easy'];row['phase']='claim_pending'
            with self.assertRaisesRegex(hs.PendingError,'禁止重复点击'):
                run.claim(p.DIFFICULTIES[0],row,frame,p.select_layout(r,frame))
            r.click.assert_not_called()

    def test_cross_day_unresolved_ticket_is_preserved(self):
        with TemporaryDirectory() as directory:
            r=self.runner(directory,source_frame('select'))
            prior=dict(game_day='2026-10-10',status='pending',difficulties={
                'easy':{'phase':'solving','start_action':'original'}})
            r.record('weekend')['puzzle']=deepcopy(prior)
            with self.assertRaisesRegex(hs.PendingError,'上个活动日'):
                p.PuzzleRun(r,at=datetime(2026,10,11,12))
            self.assertEqual(prior,r.record('weekend')['puzzle'])
            r.click.assert_not_called()

    def test_daily_record_gate_uses_puzzle_state(self):
        r=object.__new__(DailyRunner)
        r.records={'weekend':{'status':'done','task_completed':True,'home_returned':True}}
        self.assertEqual('new',r.record('weekend')['status'])

    def test_same_page_wrong_account_never_claims(self):
        with TemporaryDirectory() as directory:
            r=self.runner(directory,source_frame('select'))
            r.scheduler._generic_active_tab_x.return_value=310
            run=p.PuzzleRun(r,at=datetime(2026,10,10,12))
            with self.assertRaisesRegex(hs.SafetyError,'账号标签'):
                run.open_selection()
            r.click.assert_not_called()

    def test_old_daruma_proof_keeps_its_actual_activity_day(self):
        record=dict(status='done',task_completed=True,home_returned=True,
                    zero_verified=True,zero_evidence='old.png',finished_at='2026-10-10T23:59:00')
        p.normalize_weekend_record(record,'2026-10-11')
        self.assertEqual('2026-10-10',record['daruma_game_day'])


if __name__=='__main__':
    unittest.main()
