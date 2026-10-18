"""Real Daruma replays plus interruption/ownership tests. Never drives a game."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from hyol_geometry import GameGeometry
from daruma_standalone import vision as v
from daruma_standalone.engine import DarumaRunner
from daruma_standalone.journal import Journal, atomic_json
from daruma_standalone.runtime import Desktop, selected_tab
from daruma_standalone.trace import crop_roi, save_trace, measured_latency, REWARDS


FIXTURES = Path(__file__).parent/'daruma_standalone/tests/fixtures'


def image(name):
    with Image.open(FIXTURES/(name+'.png')) as source:
        return source.convert('RGB')


def binding(key='same'):
    role = np.zeros((20, 160), int)
    role[2:14, 20:48] = 1
    return dict(key=key, session='123:456:abc', hwnd=456, pid=123, tab_x=180,
                identity=role.tolist(), geometry=dict(window=[0, 0, 1920, 1032], content=[1, 69, 1919, 1031]))


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vision = v.Vision()

    def test_real_pages_and_absolute_reward_tiles(self):
        for name, page, layers, chance in (('ready', 'ready', 0, 1), ('playing', 'playing', 0, 0),
                                           ('weak', 'playing', 4, 0), ('over', 'over', 6, 0),
                                           ('current_ready', 'ready', 0, 3),
                                           ('current_playing_2', 'playing', 0, 2),
                                           ('current_over_2', 'over', 6, 2)):
            with self.subTest(name=name):
                frame = image(name)
                self.assertEqual(page, self.vision.page(frame))
                self.assertEqual(layers, v.layer_count(frame))
                self.assertEqual(chance, self.vision.digit(frame, 'chance'))

    def test_real_reward_one_and_three(self):
        frame = image('weekend')
        self.assertEqual('weekend', self.vision.page(frame))
        self.assertEqual(1, self.vision.digit(frame, 'reward'))
        with Image.open(v.ASSETS/'daruma_reward_digit_3.bmp') as digit:
            frame.paste(digit, (1296, 643))
        self.assertEqual(3, self.vision.digit(frame, 'reward'))

    def test_current_third_row_reward_digit_is_not_clipped_at_the_top(self):
        frame = image('current_weekend')
        self.assertEqual('weekend', self.vision.page(frame))
        self.assertEqual(3, self.vision.digit(frame, 'reward'))

    def test_current_remaining_reward_two_and_no_borrowing_other_activity_counts(self):
        frame = image('current_reward_2')
        self.assertEqual(2, self.vision.digit(frame, 'reward'))
        frame.paste((239, 223, 170), v.REWARD)
        with self.assertRaises(v.Pending):
            self.vision.digit(frame, 'reward')

    def test_zero_requires_real_counter_and_completed_badge_in_daruma_row(self):
        frame = image('current_reward_0')
        self.assertEqual(0, self.vision.digit(frame, 'reward'))
        self.assertTrue(self.vision.reward_completed(frame))
        self.assertFalse(self.vision.reward_completed(image('current_reward_2')))
        frame.paste((239, 223, 170), v.REWARD)
        with self.assertRaises(v.Pending):
            self.vision.digit(frame, 'reward')

    def test_blank_or_unrelated_counter_is_unknown_never_zero(self):
        for color in ('black', 'white', (150, 40, 10)):
            frame = Image.new('RGB', (1920, 1032), color)
            for kind in ('reward', 'chance'):
                with self.subTest(color=color, kind=kind), self.assertRaises(v.Pending):
                    self.vision.digit(frame, kind)

    def test_icons_under_animation_or_out_of_order_are_unknown(self):
        with self.assertRaises(v.Pending):
            v.layer_count(Image.new('RGB', (1920, 1032), 'white'))
        frame = image('over')
        frame.paste((90, 25, 0), v.SLOTS[0])
        with self.assertRaises(v.Pending):
            v.layer_count(frame)

    def test_real_global_announcement_over_reward_slots_is_unknown(self):
        frame = image('second_account_announcement')
        self.assertEqual('playing', self.vision.page(frame))
        self.assertEqual(1, self.vision.digit(frame, 'chance'))
        with self.assertRaises(v.Pending):
            v.layer_count(frame)

    def test_perfect_zone_shrinks_with_layers(self):
        wide = v.perfect_zone(np.asarray(image('playing').crop(v.BAR)))
        narrow = v.perfect_zone(np.asarray(image('weak').crop(v.BAR)))
        self.assertEqual((1025, 1064), wide)
        self.assertEqual((1040, 1049), narrow)

    def test_lowest_selected_rim_tracks_sideways_shift(self):
        self.assertEqual((960, 1078), v.bottom_bounds(np.asarray(image('weak').crop(v.BOTTOM))))
        self.assertIsNone(v.bottom_bounds(np.zeros((80, 350, 3), np.uint8)))

    def test_static_perfect_word_does_not_supply_a_pointer(self):
        with Image.open(Path(__file__).parent/'fixtures/daruma_bar.png') as bar:
            self.assertEqual([], v.flame_columns(np.asarray(bar)))

    def test_real_trace_recovers_late_judgment(self):
        with np.load(FIXTURES/'late_trace.npz', allow_pickle=False) as trace:
            self.assertEqual(1067, v.judgment_from_frames(list(trace['frames']), (1040, 1049), tuple(trace['roi'])))

    def test_current_trace_ignores_a_fading_marker_outlier(self):
        with np.load(FIXTURES/'current_first_trace.npz', allow_pickle=False) as trace:
            self.assertEqual(1030, v.judgment_from_frames(list(trace['frames']), (1025, 1039), tuple(trace['roi'])))

    def test_pointer_occlusion_cannot_move_target_to_half_a_band(self):
        with self.assertRaises(v.Pending):
            v.perfect_zone(np.asarray(image('current_first_before').crop(v.BAR)))

    def test_identity_is_stable_in_current_ready_frames(self):
        mask = v.identity_mask(image('current_ready').crop(v.IDENTITY))
        self.assertGreater(mask.sum(), 30)
        self.assertEqual(1, v.f1(mask, mask))
        self.assertLess(v.f1(mask, v.identity_mask(image('ready').crop(v.IDENTITY))), .94)

    def test_current_tab_uses_observed_markers(self):
        self.assertEqual(720, selected_tab(image('current_ready'), self.vision))

    def test_same_role_survives_modal_dimming_without_lowering_identity_floor(self):
        before = v.identity_mask(image('current_ready').crop(v.IDENTITY))
        after = v.identity_mask(image('current_field').crop(v.IDENTITY))
        self.assertGreaterEqual(v.f1(before, after), .99)
        other = v.identity_mask(image('ready').crop(v.IDENTITY))
        self.assertLess(v.f1(before, other), .94)

    def test_stamina_bar_sparkle_is_excluded_from_role_name(self):
        before = v.identity_mask(image('current_ready').crop(v.IDENTITY))
        after = v.identity_mask(image('current_stamina_sparkle').crop(v.IDENTITY))
        self.assertGreaterEqual(v.f1(before, after), .99)

    def test_new_account_is_distinct_and_its_counter_uses_current_page(self):
        frame = image('second_account_weekend')
        self.assertEqual('weekend', self.vision.page(frame))
        self.assertEqual(3, self.vision.digit(frame, 'reward'))
        self.assertLess(v.f1(v.identity_mask(frame.crop(v.IDENTITY)),
                            v.identity_mask(image('current_ready').crop(v.IDENTITY))), .94)

    def test_explicit_account_pin_rejects_same_tab_with_another_role(self):
        expected = binding()
        current = deepcopy(expected)
        self.assertTrue(v.binding_matches(expected, current))
        current['identity'] = v.identity_mask(image('second_account_weekend').crop(v.IDENTITY)).astype(int).tolist()
        self.assertFalse(v.binding_matches(expected, current))
        current = deepcopy(expected)
        current['session'] = 'restarted'
        self.assertFalse(v.binding_matches(expected, current))

    def test_moved_scaled_geometry_maps_all_game_rois(self):
        for window, content in (((100, 50, 1700, 950), (101, 110, 1699, 949)),
                                ((0, 0, 1536, 864), (1, 55, 1535, 863))):
            geometry = GameGeometry(window, content)
            for roi in (v.BAR, v.BOTTOM, v.TRACE, v.CHANCE, v.IDENTITY):
                left, top = geometry.point(*roi[:2])
                right, bottom = geometry.point(roi[2]-1, roi[3]-1)
                self.assertTrue(content[0] <= left < right < content[2])
                self.assertTrue(content[1] <= top < bottom < content[3])


class TimingTests(unittest.TestCase):
    def test_verified_old_marker_does_not_force_wait_for_a_new_sweep(self):
        self.assertEqual(810, v.moving_column([810, 1041], 1041))
        self.assertIsNone(v.moving_column([810, 1020], 1041))
        self.assertIsNone(v.moving_column([1038, 1044], 1041))

    def test_one_way_sweep_and_wrap(self):
        samples = [(10+i*.03, round(900+160*i*.03), .006) for i in range(12)]
        samples.insert(0, (9.97, 1300, .006))
        motion = v.estimate_motion(samples)
        self.assertIsNotNone(motion)
        self.assertAlmostEqual(160, motion.velocity, delta=3)
        self.assertIsNotNone(v.dispatch_at(motion, (1040, 1049), .15, 10.33))

    def test_reverse_or_stalled_motion_never_authorizes_strike(self):
        for velocity in (0, -160):
            self.assertIsNone(v.estimate_motion([(10+i*.03, round(1150+velocity*i*.03), .006)
                                                for i in range(12)]))

    def test_repeated_capture_of_one_flash_frame_does_not_dominate_the_fit(self):
        samples = [(10+i*.005, 900+5*(i//6), .004) for i in range(100)]
        motion = v.estimate_motion(samples)
        self.assertIsNotNone(motion)
        self.assertLess(motion.sample_count, 20)
        self.assertAlmostEqual(motion.velocity, 5/.03, delta=2)

    def test_capture_latency_uncertainty_skips_narrow_window(self):
        motion = v.Motion(980, 160, 10, .080, 2)
        self.assertIsNone(v.dispatch_at(motion, (1040, 1049), .15, 10))
        self.assertIsNone(v.dispatch_at(v.Motion(1080, 160, 10, .006, .5), (1040, 1049), .15, 10))

    def test_regression_uncertainty_averages_frame_quantization_but_not_bad_fits(self):
        samples = [(10+i*.012, round((900+160*i*.012)/5)*5, .005) for i in range(40)]
        motion = v.estimate_motion(samples)
        self.assertIsNotNone(motion)
        at = v.dispatch_at(motion, (1041, 1048), .13, samples[-1][0])
        self.assertIsNotNone(at)
        self.assertGreater(at, samples[-1][0]+.035)

    def test_missing_or_ambiguous_perfect_color_is_unknown(self):
        for value in (0, 255):
            with self.assertRaises(v.Pending):
                v.perfect_zone(np.full((45, 559, 3), value, np.uint8))


class JournalAndInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daruma_test_')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_resume_retains_pending_intent_and_original_evidence(self):
        journal = Journal(self.root, binding())
        intent = journal.begin('strike', before_layers=4, before_evidence='original.png')
        journal.sent()
        journal.save(status='stopped')
        resumed = Journal(self.root, binding())
        self.assertEqual(journal.path, resumed.path)
        self.assertEqual(intent, resumed.data['intent'])
        with self.assertRaises(v.Pending):
            resumed.begin('strike')
        self.assertFalse((self.root/'state').exists())

    def test_other_window_gets_a_separate_journal(self):
        first = Journal(self.root, binding('first'))
        first.begin('start', chance_before=3)
        second = Journal(self.root, binding('second'))
        self.assertNotEqual(first.path, second.path)
        self.assertEqual('start', json.loads(first.path.read_text(encoding='utf-8'))['intent']['kind'])

    def test_changed_role_or_session_preserves_original_checkpoint_bytes(self):
        journal = Journal(self.root, binding())
        journal.begin('strike', before_layers=4, before_evidence='original.png')
        journal.sent()
        journal.save(status='stopped', reason='original stop')
        original = journal.path.read_bytes()
        changed_role = binding()
        changed_role['identity'] = v.identity_mask(image('second_account_weekend').crop(v.IDENTITY)).astype(int).tolist()
        changed_session = {**binding(), 'session': 'restarted'}
        for changed in (changed_role, changed_session):
            with self.subTest(session=changed['session']):
                with self.assertRaises(v.Pending):
                    Journal(self.root, changed)
                self.assertEqual(original, journal.path.read_bytes())
                self.assertEqual(1, len(list((self.root/'runs').glob('*/checkpoint.json'))))

    def test_result_and_layer_count_are_atomic_even_if_event_display_fails(self):
        journal = Journal(self.root, binding(), sink=Mock(side_effect=RuntimeError('display failed')))
        journal.begin('strike', before_layers=4)
        with self.assertRaises(RuntimeError):
            journal.resolve('layer_hit', state=dict(layers=5, retries=0), evidence='verified.png')
        saved = json.loads(journal.path.read_text(encoding='utf-8'))
        self.assertIsNone(saved['intent'])
        self.assertEqual(5, saved['layers'])
        self.assertEqual('layer_hit', saved['actions'][-1]['outcome'])

    def test_transient_windows_replace_denial_retries_without_deleting_checkpoint(self):
        import os
        path = self.root/'checkpoint.json'
        atomic_json(path, {'original': True})
        replacement = os.replace
        denied = PermissionError('temporary reader lock')
        denied.winerror = 5
        attempts = []
        def locked_once(source, target):
            attempts.append(1)
            self.assertTrue(json.loads(path.read_text(encoding='utf-8'))['original'])
            if len(attempts) == 1:
                raise denied
            replacement(source, target)
        with patch('daruma_standalone.journal.os.replace', side_effect=locked_once), patch(
                'daruma_standalone.journal.time.sleep'):
            atomic_json(path, {'replaced': True})
        self.assertEqual(2, len(attempts))
        self.assertEqual({'replaced': True}, json.loads(path.read_text(encoding='utf-8')))

    def test_failed_result_write_retains_sent_intent_and_retry_commits_only_once(self):
        journal = Journal(self.root, binding())
        journal.begin('strike', before_layers=1)
        journal.sent()
        with patch('daruma_standalone.journal.replace_file', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                journal.resolve('layer_hit', state=dict(layers=2))
        self.assertTrue(journal.data['intent']['sent'])
        self.assertEqual(0, journal.data['layers'])
        saved = json.loads(journal.path.read_text(encoding='utf-8'))
        self.assertTrue(saved['intent']['sent'])
        self.assertTrue(list(journal.directory.glob('checkpoint.json.*.tmp')))
        journal.resolve('layer_hit', state=dict(layers=2))
        self.assertEqual(1, len(journal.data['actions']))
        self.assertIsNone(journal.data['intent'])

    def test_compact_trace_preserves_real_judgment_and_observation_regions(self):
        with np.load(FIXTURES/'current_first_trace.npz', allow_pickle=False) as old:
            full = list(old['frames'])
            times = old['timestamps']
        path = self.root/'compact.npz'
        save_trace(path, [crop_roi(f, v.BAR) for f in full], [crop_roi(f, v.BOTTOM) for f in full],
                   [crop_roi(f, REWARDS) for f in full], times)
        with np.load(path, allow_pickle=False) as recording:
            self.assertEqual(1030, v.judgment_from_frames(list(recording['frames']), (1025, 1039),
                                                        tuple(recording['roi'])))
            np.testing.assert_array_equal(crop_roi(full[0], v.BOTTOM), recording['bottom_frames'][0])
            np.testing.assert_array_equal(crop_roi(full[0], REWARDS), recording['reward_frames'][0])

    def test_real_weak_hit_judgment_survives_shorter_trace(self):
        with np.load(FIXTURES/'late_trace.npz', allow_pickle=False) as recording:
            times = recording['timestamps']
            frames = recording['frames'][times <= times[0]+.75]
            self.assertEqual(1067, v.judgment_from_frames(list(frames), (1040, 1049), tuple(recording['roi'])))

    def test_latency_median_rejects_one_frame_jitter_and_deduplicates_resume(self):
        record = {}
        for i, estimate in enumerate((.13, .14, .165)):
            action = dict(id=str(i), latency=estimate, zone=[1040, 1049], velocity=160)
            latency, history = measured_latency(record, action, 1044)
            record.update(latency=latency, latency_history=history)
        self.assertLess(record['latency'], .145)
        again, history = measured_latency(record, action, 1044)
        self.assertEqual(3, len(history))
        self.assertEqual(record['latency'], again)

    def fake_desktop(self):
        desktop = Desktop.__new__(Desktop)
        desktop.readonly = False
        desktop.stop = threading.Event()
        desktop._mouse_down = False
        desktop.content_clicks = 0
        desktop.captures = {}
        desktop.journal = Journal(self.root, binding())
        desktop.journal.begin('strike', before_layers=0)
        desktop.geometry = GameGeometry((0, 0, 1920, 1032), (1, 69, 1919, 1031))
        desktop.guard_fast = Mock()
        desktop.guard_window = Mock()
        desktop.wait = Mock()
        desktop.check_stop = Mock()
        desktop.cursor = Mock(return_value=desktop.geometry.point(1025, 650))
        return desktop

    def test_f12_before_down_sends_nothing_and_retains_original_task(self):
        desktop = self.fake_desktop()
        desktop.guard_fast.side_effect = v.Stopped('F12')
        with patch('daruma_standalone.runtime.Win32.user32.mouse_event') as send:
            with self.assertRaises(v.Stopped):
                desktop.pulse((1025, 650))
            send.assert_not_called()
        self.assertEqual(0, desktop.content_clicks)
        self.assertEqual('not_sent', desktop.journal.data['actions'][-1]['outcome'])

    def test_stop_during_down_always_releases_up_and_keeps_unresolved_sent_intent(self):
        desktop = self.fake_desktop()
        desktop.wait.side_effect = lambda seconds: (_ for _ in ()).throw(v.Stopped('F12')) if seconds == .018 else None
        with patch('daruma_standalone.runtime.Win32.move'), patch('daruma_standalone.runtime.Win32.user32.mouse_event') as send:
            with self.assertRaises(v.Stopped):
                desktop.pulse((1025, 650))
        self.assertEqual([2, 4], [call.args[0] for call in send.call_args_list])
        self.assertFalse(desktop._mouse_down)
        self.assertTrue(desktop.journal.data['intent']['sent'])
        self.assertEqual(1, desktop.content_clicks)

    def test_readonly_never_sends_down(self):
        desktop = self.fake_desktop()
        desktop.readonly = True
        with patch('daruma_standalone.runtime.Win32.user32.mouse_event') as send:
            with self.assertRaises(v.Pending):
                desktop.pulse((1025, 650))
            send.assert_not_called()

    def test_external_mouse_movement_blocks_down(self):
        desktop = self.fake_desktop()
        desktop.cursor.return_value = (1, 1)
        with patch('daruma_standalone.runtime.Win32.move'), patch('daruma_standalone.runtime.Win32.user32.mouse_event') as send:
            with self.assertRaises(v.Stopped):
                desktop.pulse((1025, 650))
            send.assert_not_called()

    def test_focus_geometry_or_process_change_before_strike_sends_nothing(self):
        cases = (
            dict(foreground=777), dict(window=(1, 0, 1921, 1032)),
            dict(content=(1, 70, 1919, 1031)), dict(pid=999), dict(iconic=True))
        for changes in cases:
            desktop = self.fake_desktop()
            desktop.hwnd, desktop.acquired, desktop.binding = 456, True, binding()
            desktop.guard_window = lambda: Desktop.guard_window(desktop)
            desktop.guard_fast = desktop.guard_window
            with self.subTest(changes=changes), patch(
                    'daruma_standalone.runtime.Win32.foreground', return_value=changes.get('foreground', 456)), patch(
                    'daruma_standalone.runtime.Win32.rect', return_value=changes.get('window', desktop.geometry.window)), patch(
                    'daruma_standalone.runtime.Win32.game_content_rect', return_value=changes.get('content', desktop.geometry.content)), patch(
                    'daruma_standalone.runtime.Win32.pid', return_value=changes.get('pid', 123)), patch(
                    'daruma_standalone.runtime.Win32.user32.IsIconic', return_value=changes.get('iconic', False)), patch(
                    'daruma_standalone.runtime.Win32.user32.mouse_event') as send:
                with self.assertRaises(v.Stopped):
                    desktop.pulse((1025, 650))
                send.assert_not_called()
            self.assertEqual(0, desktop.content_clicks)

    def test_role_or_selected_tab_change_at_final_guard_sends_nothing(self):
        known = np.asarray(image('current_ready').crop(v.IDENTITY))
        other = np.asarray(image('second_account_weekend').crop(v.IDENTITY))
        for role in (other, known):
            desktop = self.fake_desktop()
            desktop.binding = {**binding(), 'tab_x': 720,
                'identity': v.identity_mask(Image.fromarray(known)).astype(int).tolist()}
            desktop.roi = Mock(side_effect=[(role, 0, .006), (np.zeros((16, 25, 3), np.uint8), 0, .006)])
            desktop.guard_fast = lambda: Desktop.guard_fast(desktop)
            with self.subTest(changed_role=role is other), patch(
                    'daruma_standalone.runtime.Win32.user32.mouse_event') as send:
                with self.assertRaises(v.Stopped):
                    desktop.pulse((1025, 650))
                send.assert_not_called()
            self.assertEqual(0, desktop.content_clicks)

    def test_disconnect_observation_aborts_capture_before_any_input(self):
        desktop = self.fake_desktop()
        desktop.vision = Mock()
        desktop.vision.offline.return_value = True
        with patch('daruma_standalone.runtime.GDICapture') as capture, patch(
                'daruma_standalone.runtime.Win32.user32.mouse_event') as send:
            capture.return_value.grab.return_value = (np.asarray(image('current_ready')), 0, .01)
            with self.assertRaisesRegex(v.Stopped, '掉线'):
                Desktop.capture(desktop)
            send.assert_not_called()
        self.assertEqual(0, desktop.content_clicks)

    def test_bound_capture_reuses_tab_location_and_still_rejects_tab_change(self):
        frame = image('current_ready')
        desktop = self.fake_desktop()
        desktop.vision = v.Vision()
        desktop.binding = {**binding(), 'tab_x': 720,
            'identity': v.identity_mask(frame.crop(v.IDENTITY)).astype(int).tolist()}
        with patch('daruma_standalone.runtime.GDICapture') as capture, patch(
                'daruma_standalone.runtime.selected_tab', side_effect=AssertionError('full rescan forbidden')):
            capture.return_value.grab.return_value = (np.asarray(frame), 0, .01)
            result = Desktop.capture(desktop)
            self.assertEqual('ready', desktop.vision.page(result))
            frame.paste((200, 200, 200), (708, 12, 733, 28))
            capture.return_value.grab.return_value = (np.asarray(frame), 0, .01)
            with self.assertRaisesRegex(v.Stopped, '标签'):
                Desktop.capture(desktop)
        self.assertEqual(0, desktop.content_clicks)

    def test_expired_prediction_is_cancelled_without_a_strike(self):
        desktop = self.fake_desktop()
        with patch('daruma_standalone.runtime.time.perf_counter', return_value=10), patch(
                'daruma_standalone.runtime.Win32.user32.mouse_event') as send:
            self.assertIsNone(desktop.pulse((1025, 650), at=9.9))
            send.assert_not_called()
        self.assertIsNone(desktop.journal.data['intent'])

    def test_stop_during_precise_wait_never_sends_down(self):
        desktop = self.fake_desktop()
        desktop.wait_until = Mock(side_effect=v.Stopped('F12 during timer wait'))
        with patch('daruma_standalone.runtime.time.perf_counter', return_value=10), patch(
                'daruma_standalone.runtime.Win32.user32.mouse_event') as send:
            with self.assertRaises(v.Stopped):
                desktop.pulse((1025, 650), at=10.2)
            send.assert_not_called()

    def test_busy_lease_never_activates_or_clicks_window(self):
        with patch('daruma_standalone.runtime.ForegroundRuntimeLease') as lease, patch(
                'daruma_standalone.runtime.Win32.activate') as activate:
            lease.return_value.acquire.return_value = False
            result = DarumaRunner(456, root=self.root).run()
            self.assertEqual('pending', result['status'])
            self.assertEqual(0, result['content_clicks'])
            activate.assert_not_called()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daruma_flow_')
        self.runner = DarumaRunner(456, root=Path(self.temp.name))
        self.runner.journal = Journal(Path(self.temp.name), binding())
        self.runner.desktop = Mock()

    def tearDown(self):
        self.temp.cleanup()

    def test_wrong_page_cannot_click_a_known_coordinate(self):
        self.runner.desktop.vision.page.return_value = 'unknown'
        with self.assertRaises(v.Pending):
            self.runner.click_template('daruma_start.bmp', v.START, (105, 46), ('ready',), ('playing',), 'start')
        self.runner.desktop.pulse.assert_not_called()

    def test_reward_occlusion_breaks_consecutive_counter_confirmation(self):
        self.runner.desktop.capture.return_value = image('current_reward_0')
        self.runner.desktop.vision.page.return_value = 'weekend'
        self.runner.desktop.vision.reward_completed.return_value = True
        for remaining in (0, 2):
            with self.subTest(remaining=remaining):
                self.runner.journal.save(remaining=None)
                self.runner.desktop.vision.digit.side_effect = [remaining, v.Pending('occluded'), remaining]
                with patch('daruma_standalone.engine.time.monotonic', side_effect=[0, 0, 1, 2, 5]):
                    with self.assertRaises(v.Pending):
                        self.runner.stable_reward()
                self.assertIsNone(self.runner.journal.data['remaining'])
                self.runner.desktop.pulse.assert_not_called()

    def test_zero_requires_completed_badge_in_both_consecutive_frames(self):
        self.runner.desktop.capture.return_value = image('current_reward_0')
        self.runner.desktop.vision.page.return_value = 'weekend'
        self.runner.desktop.vision.digit.return_value = 0
        self.runner.desktop.vision.reward_completed.side_effect = [False, True, False]
        with patch('daruma_standalone.engine.time.monotonic', side_effect=[0, 0, 1, 2, 5]):
            with self.assertRaises(v.Pending):
                self.runner.stable_reward()
        self.assertIsNone(self.runner.journal.data['remaining'])
        self.runner.desktop.pulse.assert_not_called()

    def test_two_clear_reward_frames_save_actual_counter_and_evidence(self):
        self.runner.desktop.vision = v.Vision()
        for name, remaining in (('current_reward_0', 0), ('current_reward_2', 2)):
            with self.subTest(remaining=remaining):
                self.runner.desktop.capture.reset_mock()
                self.runner.desktop.capture.return_value = image(name)
                self.assertEqual(remaining, self.runner.stable_reward())
                self.assertEqual(2, self.runner.desktop.capture.call_count)
                self.assertEqual(remaining, self.runner.journal.data['remaining'])
                self.assertTrue(Path(self.runner.journal.data['reward_evidence']).is_file())
                self.runner.desktop.pulse.assert_not_called()

    def test_reward_occlusion_waits_without_input_then_rechecks_current_count(self):
        covered, clear = image('second_account_announcement'), image('current_playing_2')
        self.runner.desktop.capture.side_effect = [covered, clear]
        self.runner.desktop.vision.page.return_value = 'playing'
        with patch('daruma_standalone.engine.time.perf_counter', return_value=10):
            frame, waited = self.runner.playing_frame(0, deadline=17)
        self.assertTrue(waited)
        self.assertIs(clear, frame)
        self.runner.desktop.wait.assert_called_once()
        self.runner.desktop.pulse.assert_not_called()

    def test_persistent_occlusion_or_known_count_change_never_sends_input(self):
        self.runner.desktop.vision.page.return_value = 'playing'
        self.runner.desktop.capture.return_value = image('second_account_announcement')
        with patch('daruma_standalone.engine.time.perf_counter', return_value=17), self.assertRaises(v.Pending):
            self.runner.playing_frame(0, deadline=17)
        self.runner.desktop.capture.return_value = image('weak')
        with self.assertRaises(v.Pending):
            self.runner.playing_frame(0, deadline=17)
        self.runner.desktop.pulse.assert_not_called()

    def test_result_occlusion_invalidates_earlier_stable_observations(self):
        clear = image('current_playing_2')
        self.runner.desktop.capture.side_effect = [clear, clear, image('second_account_announcement')]
        self.runner.desktop.vision.page.return_value = 'playing'
        self.runner.desktop.vision.digit.return_value = 2
        with patch('daruma_standalone.engine.time.monotonic', side_effect=[0, 0, .1, .2, 5]):
            with self.assertRaises(v.Pending):
                self.runner.stable_game(wait_for_change_from=0)
        self.runner.desktop.pulse.assert_not_called()

    def test_unresolved_navigation_cannot_be_replayed(self):
        self.runner.journal.begin('go_game', expected=['ready'])
        self.runner.desktop.vision.page.return_value = 'weekend'
        with self.assertRaises(v.Pending):
            self.runner.reconcile()
        self.runner.desktop.pulse.assert_not_called()

    def test_sent_strike_on_completed_activity_can_close_without_fabricating_hits(self):
        self.runner.journal.save(layers=1, round=3)
        self.runner.journal.begin('strike', before_layers=1)
        self.runner.journal.sent()
        self.runner.desktop.vision.page.return_value = 'weekend'
        def zero():
            self.runner.journal.save(remaining=0, reward_evidence='positive_zero.png')
            return 0
        self.runner.stable_reward = Mock(side_effect=zero)
        self.runner.drive()
        self.assertEqual('done', self.runner.journal.data['status'])
        self.assertEqual(1, self.runner.journal.data['layers'])
        self.assertEqual('superseded_by_verified_reward_zero', self.runner.journal.data['actions'][-1]['outcome'])
        self.assertTrue(self.runner.journal.data['layer_details_incomplete'])
        self.runner.desktop.pulse.assert_not_called()

    def test_sent_strike_outside_game_with_remaining_rewards_stays_unresolved(self):
        intent = self.runner.journal.begin('strike', before_layers=1)
        self.runner.journal.sent()
        self.runner.desktop.vision.page.return_value = 'weekend'
        self.runner.stable_reward = Mock(return_value=1)
        with self.assertRaises(v.Pending):
            self.runner.reconcile()
        self.assertEqual(intent, self.runner.journal.data['intent'])
        self.runner.desktop.pulse.assert_not_called()

    def test_start_button_on_game_over_restarts_without_closing_game(self):
        frame = image('current_over_2')
        self.runner.journal.save(remaining=3, reward_check_round=0, round=1,
            round_results=[dict(round=1, six_icons_verified=True)])
        self.runner.stable_game = Mock(return_value=(('over', 6, 2), frame))
        self.runner.desktop.vision.match.return_value = (True, 1.0, 920, 490)
        self.runner.verify_start = Mock()
        self.runner.open_activity = Mock()
        self.runner.start_round()
        self.runner.desktop.pulse.assert_called_once()
        self.runner.open_activity.assert_not_called()
        self.assertEqual('start', self.runner.journal.data['intent']['kind'])

    def test_three_rounds_stay_in_game_until_final_reward_check(self):
        self.runner.journal.save(remaining=3, reward_check_round=0, round=1, round_active=True)
        self.runner.desktop.vision.page.return_value = 'over'
        self.runner.stable_game = Mock(side_effect=[(('over', 6, n), image('current_over_2')) for n in (2, 1, 0)])
        allowances = []
        def restart():
            allowances.append(self.runner.round_allowance())
            self.assertEqual(3, self.runner.journal.data['remaining'])
            self.runner.journal.save(round=self.runner.journal.data['round']+1, round_active=True)
        def reward():
            self.runner.journal.save(remaining=0, reward_evidence='actual_zero.png')
            return 0
        self.runner.start_round = Mock(side_effect=restart)
        self.runner.open_activity = Mock()
        self.runner.stable_reward = Mock(side_effect=reward)
        self.runner.drive()
        self.assertEqual([2, 1], allowances)
        self.runner.open_activity.assert_called_once()
        self.assertEqual(3, len(self.runner.journal.data['round_results']))
        self.assertTrue(self.runner.journal.data['zero_verified'])

    def test_estimated_zero_cannot_replace_actual_nonzero_reward_counter(self):
        self.runner.journal.save(remaining=1, reward_check_round=2, round=3, round_active=True)
        self.runner.desktop.vision.page.return_value = 'over'
        self.runner.stable_game = Mock(return_value=(('over', 6, 0), image('over')))
        self.runner.open_activity = Mock()
        self.runner.stable_reward = Mock(return_value=1)
        with self.assertRaises(v.Pending):
            self.runner.drive()
        self.assertEqual(0, self.runner.journal.data['remaining_estimate'])
        self.assertNotEqual('done', self.runner.journal.data['status'])
        self.runner.desktop.pulse.assert_not_called()

    def test_fast_trace_finishes_when_real_reward_and_cylinder_are_ready(self):
        with np.load(FIXTURES/'fast_layer_trace.npz', allow_pickle=False) as recording:
            full = []
            for bar, bottom, reward in zip(recording['frames'], recording['bottom_frames'], recording['reward_frames']):
                region = np.zeros((v.TRACE[3]-v.TRACE[1], v.TRACE[2]-v.TRACE[0], 3), np.uint8)
                for pixels, roi in ((bar, v.BAR), (bottom, v.BOTTOM), (reward, REWARDS)):
                    region[roi[1]-v.TRACE[1]:roi[3]-v.TRACE[1], roi[0]-v.TRACE[0]:roi[2]-v.TRACE[0]] = pixels
                full.append(region)
            times = recording['timestamps']-recording['timestamps'][0]
        before = self.runner.journal.evidence(image('fast_layer_before'), 'before')
        intent = self.runner.journal.begin('strike', before_layers=0, zone=[1025, 1064], before_evidence=before)
        self.runner.desktop.roi.side_effect = [(frame, float(at), .009) for frame, at in zip(full, times)]
        with patch('daruma_standalone.engine.time.perf_counter', return_value=0):
            self.runner.record_trace(intent)
        with np.load(self.runner.journal.data['last_trace'], allow_pickle=False) as shortened:
            self.assertLess(len(shortened['frames']), len(full))
            self.assertLess(shortened['timestamps'][-1], .6)
            self.assertEqual(1041, v.judgment_from_frames(list(shortened['frames']), (1025, 1064), v.BAR))
        self.assertTrue(self.runner.motion_samples)
        self.assertTrue(all(row[2] == .009 for row in self.runner.motion_samples))
        self.assertIsNotNone(self.runner.journal.data['intent'])
        self.runner.desktop.pulse.assert_not_called()

    def test_zero_chances_in_active_round_still_reaches_layer_engine(self):
        self.runner.desktop.vision.page.return_value = 'playing'
        self.runner.stable_game = Mock(return_value=(('playing', 4, 0), image('weak')))
        self.runner.play_layer = Mock(side_effect=v.Stopped('test boundary'))
        with self.assertRaises(v.Stopped):
            self.runner.drive()
        self.runner.play_layer.assert_called_once()
        self.assertEqual(4, self.runner.journal.data['layers'])
        self.assertTrue(self.runner.journal.data['round_active'])

    def test_completion_requires_observed_zero_and_preserves_activity_page(self):
        self.runner.desktop.vision.page.return_value = 'weekend'
        def reward():
            self.runner.journal.save(remaining=0, reward_evidence='observed_zero.png')
            return 0
        self.runner.stable_reward = Mock(side_effect=reward)
        self.runner.drive()
        self.assertEqual('done', self.runner.journal.data['status'])
        self.assertEqual('observed_zero.png', self.runner.journal.data['zero_evidence'])
        self.runner.desktop.pulse.assert_not_called()

    def test_remaining_rewards_without_new_chance_remain_pending(self):
        self.runner.journal.save(chance=0, round_active=False)
        self.runner.desktop.vision.page.return_value = 'weekend'
        self.runner.stable_reward = Mock(return_value=1)
        with self.assertRaises(v.Pending):
            self.runner.drive()
        self.assertNotEqual('done', self.runner.journal.data['status'])
        self.runner.desktop.pulse.assert_not_called()

    def test_unknown_strike_result_never_toggles_side_or_retries(self):
        self.runner.stable_game = Mock(return_value=(('playing', 4, 0), image('weak')))
        path = self.runner.journal.evidence(image('weak'), 'before')
        intent = self.runner.journal.begin('strike', before_layers=4, before_chance=0,
            before_evidence=path, zone=[1040, 1049], before_bottom=[960, 1078], side='left', velocity=160, latency=.15)
        with self.assertRaises(v.Pending):
            self.runner.verify_strike(intent)
        self.assertEqual(intent, self.runner.journal.data['intent'])
        self.assertEqual(0, self.runner.journal.data['retries'])
        self.runner.desktop.pulse.assert_not_called()

    def weak_intent(self):
        self.runner.stable_game = Mock(return_value=(('playing', 4, 0), image('weak')))
        path = self.runner.journal.evidence(image('weak'), 'before')
        trace = self.runner.journal.directory/'weak_trace.npz'
        np.savez(trace, frames=np.zeros((1, v.TRACE[3]-v.TRACE[1], v.TRACE[2]-v.TRACE[0], 3), np.uint8),
                 roi=np.asarray(v.TRACE))
        intent = self.runner.journal.begin('strike', before_layers=4, before_chance=0,
            before_evidence=path, zone=[1040, 1049], before_bottom=[987, 1105], side='left', velocity=160,
            latency=.15, trace=str(trace))
        self.runner.desktop.roi.return_value = (np.zeros((45, 559, 3), np.uint8), 0, 0)
        return intent

    def test_two_stationary_flames_do_not_prove_pointer_motion(self):
        intent = self.weak_intent()
        with patch('daruma_standalone.engine.judgment_from_frames', return_value=1067), patch(
                'daruma_standalone.engine.flame_columns', side_effect=[[1067, 800], [1067, 800]]):
            with self.assertRaises(v.Pending):
                self.runner.verify_strike(intent)
        self.assertIsNotNone(self.runner.journal.data['intent'])
        self.assertEqual(0, self.runner.journal.data['retries'])

    def test_confirmed_weak_hit_changes_side_once(self):
        intent = self.weak_intent()
        with patch('daruma_standalone.engine.judgment_from_frames', return_value=1067), patch(
                'daruma_standalone.engine.flame_columns', side_effect=[[1067, 800], [1067, 820]]):
            self.runner.verify_strike(intent)
        self.assertIsNone(self.runner.journal.data['intent'])
        self.assertEqual('right', self.runner.journal.data['side'])
        self.assertEqual(1, self.runner.journal.data['retries'])
        self.runner.desktop.pulse.assert_not_called()

    def test_two_corrections_cannot_be_reset_by_resume(self):
        intent = self.weak_intent()
        self.runner.journal.save(retries=2)
        with patch('daruma_standalone.engine.judgment_from_frames', return_value=1067), patch(
                'daruma_standalone.engine.flame_columns', side_effect=[[1067, 800], [1067, 820]]):
            with self.assertRaises(v.Pending):
                self.runner.verify_strike(intent)
        self.assertTrue(self.runner.journal.data['correction_exhausted'])
        with self.assertRaises(v.Pending):
            self.runner.play_layer(image('weak'))
        self.runner.desktop.pulse.assert_not_called()


class DisplayTests(unittest.TestCase):
    def test_new_observation_clears_previous_account_counts_and_checkpoint(self):
        from types import SimpleNamespace
        from daruma_standalone.gui import DarumaApp
        app = SimpleNamespace(worker=None, selector=Mock(), targets=[dict(hwnd=456)],
            last_result={'status': 'done'}, busy=Mock(), run_worker=Mock(),
            status=Mock(), layers=Mock(), chance=Mock(), remaining=Mock(), checkpoint=Mock())
        app.selector.current.return_value = 0
        with patch('daruma_standalone.gui.threading.Thread') as worker:
            DarumaApp.start(app, readonly=True)
            worker.return_value.start.assert_called_once()
        app.layers.set.assert_called_once_with('已获奖励：— / 6')
        app.chance.set.assert_called_once_with('剩余机会：—')
        app.remaining.set.assert_called_once_with('剩余奖励：—')
        app.checkpoint.set.assert_called_once_with('')
        self.assertIsNone(app.last_result)
        app.run_worker.assert_not_called()


class BundleTests(unittest.TestCase):
    def test_frozen_data_uses_executable_directory_and_keeps_runtime_outside_library(self):
        from daruma_standalone.paths import locations
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)/'client'
            root, runtime, samples = locations(package/'_internal/daruma_standalone/paths.py',
                                               frozen=True, executable=package/'daruma.exe')
            self.assertEqual(package.resolve(), root)
            self.assertEqual(package.resolve()/'runtime', runtime)
            self.assertEqual(package.resolve()/'_internal/daruma_standalone/samples', samples)
            self.assertFalse(runtime.is_relative_to(package.resolve()/'_internal'))

    def test_source_mode_keeps_existing_independent_runtime_location(self):
        from daruma_standalone.paths import locations
        module = Path(__file__).parent/'daruma_standalone/paths.py'
        root, runtime, samples = locations(module, frozen=False, executable='unrelated.exe')
        self.assertEqual(Path(__file__).resolve().parent, root)
        self.assertEqual(root/'daruma_standalone/runtime', runtime)
        self.assertEqual(root/'daruma_standalone/samples', samples)

    def test_bundle_check_requires_every_template_and_never_starts_desktop(self):
        from daruma_standalone.bundle_check import check
        with tempfile.TemporaryDirectory() as folder, patch('daruma_standalone.bundle_check.Vision') as vision:
            vision.return_value.asset.return_value = Path(folder)/'missing.bmp'
            vision.return_value.glyphs = {i: [np.ones((2, 2))] for i in range(4)}
            report = check(Path(folder)/'check.json')
            self.assertEqual('fail', report['status'])
            self.assertTrue(report['missing_assets'])
            self.assertFalse(report['game_task_started'])
            self.assertEqual(0, report['content_clicks'])


if __name__ == '__main__':
    unittest.main()
