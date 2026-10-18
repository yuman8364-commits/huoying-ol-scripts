import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image

import hyol_daily_tasks as daily
import hyol_scheduler as hs


class DailyTaskTests(unittest.TestCase):
    def test_quiz_is_solo_option_only(self):
        self.assertEqual(daily.selected_sequence('all',['quiz']), ('quiz',))
        with self.assertRaises(ValueError):
            daily.selected_sequence('team',['quiz'])

    def test_treasure_is_solo_option_only(self):
        self.assertEqual(daily.selected_sequence('all', ['treasure']), ('treasure',))
        with self.assertRaises(ValueError):
            daily.selected_sequence('team', ['treasure'])

    def test_treasure_without_existing_map_never_clicks_use_or_buy(self):
        import hyol_treasure
        r = self.runner()
        r.scheduler.state.action_status.return_value = 'new'
        r.visible = Mock(return_value=False)
        r.click = Mock()
        with patch.object(hyol_treasure, 'open_page'), patch.object(hyol_treasure, 'close_page') as close:
            with self.assertRaises(hs.PendingError):
                hyol_treasure.run(r)
        r.click.assert_not_called()
        close.assert_called_once_with(r)

    def test_treasure_result_is_committed_without_resetting_timer(self):
        import hyol_treasure
        r = self.runner()
        r.record('treasure').update(started_at=1000, deadline_at=1300)
        r.scheduler.state.action_status.return_value = 'new'
        r.scheduler.wait_absent.return_value = True
        r.visible = Mock(side_effect=lambda name, *args, **kwargs: name == 'treasure_result_anchor.bmp')
        r.click = Mock()
        slot = hyol_treasure.TreasureRun(r, clock=lambda: 1100)
        self.assertFalse(slot.step())
        r.click.assert_called_once()
        self.assertEqual('treasure_result_confirm.bmp', r.click.call_args.args[0])
        self.assertTrue(r.click.call_args.kwargs['irreversible'])
        self.assertEqual(1300, slot.record['deadline_at'])
        r.scheduler.commit_action.assert_called_once()

    def test_pending_reward_waits_without_second_confirmation(self):
        import hyol_treasure
        r = self.runner()
        r.scheduler.state.action_status.return_value = 'pending'
        r.scheduler.wait_absent.return_value = False
        r.visible = Mock(side_effect=lambda name, *args, **kwargs: name == 'treasure_result_anchor.bmp')
        r.click = Mock()
        with self.assertRaises(hs.PendingError):
            hyol_treasure.settle_result(r, 'daily_treasure_reward_1')
        r.click.assert_not_called()
        r.scheduler.commit_action.assert_not_called()

    def test_quiz_rejects_team_before_any_click(self):
        import hyol_quiz
        r = self.runner()
        r.visible = Mock(return_value=False)
        r.scheduler.template.return_value = (False, 0, -1, -1)
        r.click = Mock()
        with self.assertRaises(hs.PendingError):
            hyol_quiz.run(r)
        r.click.assert_not_called()

    def test_quiz_real_solo_and_team_layouts(self):
        import hyol_quiz
        r = self.runner()
        store = hs.TemplateStore(.90)
        r.visible = lambda name,roi,frame,threshold=.90: store.match(frame,hs.ASSETS/name,roi)[0]
        with Image.open(hs.LOGS/'quiz_single_tab_observe.png') as frame:
            self.assertTrue(hyol_quiz.solo_verified(r,frame))
        with Image.open(hs.LOGS/'strong_team_preflight.png') as frame:
            self.assertFalse(hyol_quiz.solo_verified(r,frame))

    def test_quiz_entry_row_does_not_mean_active_team(self):
        import hyol_quiz
        r = self.runner()
        r.visible = Mock(side_effect=lambda name, *args, **kwargs: name in (
            'quiz_task_link.bmp', 'quiz_solo_layout.bmp'))
        self.assertTrue(hyol_quiz.solo_verified(r, Image.new('RGB', (1920, 1032))))

    def test_quiz_claim_disabled_color_from_live_frames(self):
        from hyol_quiz import CLAIM_BUTTON_COLOR, claim_button_disabled
        fixture = Path('tests/fixtures')
        for name, expected in [('quiz_claim_enabled.png', False),
                               ('quiz_claim_disabled.png', True)]:
            with self.subTest(name=name):
                frame = Image.new('RGB', (1920, 1032))
                with Image.open(fixture / name) as crop:
                    frame.paste(crop, CLAIM_BUTTON_COLOR[:2])
                self.assertEqual(expected, claim_button_disabled(frame))

    def test_quiz_key_ignores_timer_and_reward_animation(self):
        from hyol_quiz import question_key
        a = Image.new('RGB',(1920,1032),'white')
        b = a.copy()
        b.paste('black',(900,300,980,380))
        b.paste('black',(800,488,1100,550))
        self.assertEqual(question_key(a),question_key(b))
        b.paste('black',(800,460,900,480))
        self.assertNotEqual(question_key(a),question_key(b))

    def test_new_group_request_adds_two_without_erasing_history(self):
        r = self.runner()
        r.task = 'group'
        r.group_count = 2
        rec = r.record('group')
        rec.update(status='done', completed=8, reward_exhausted=True)
        r.prepare_request()
        self.assertEqual(rec['completed'], 8)
        self.assertEqual(rec['request_target'], 10)
        self.assertTrue(r.group_needs_more(rec))
        rec['completed'] = 10
        self.assertTrue(r.group_needs_more(rec))
        rec['reward_exhausted'] = True
        self.assertFalse(r.group_needs_more(rec))

    def test_until_zero_can_run_more_than_five(self):
        r = self.runner()
        r.group_count = 0
        self.assertTrue(r.group_needs_more({'completed': 12}))
        self.assertFalse(r.group_needs_more({'completed':12, 'reward_exhausted':True}))

    def test_pending_request_does_not_add_new_target(self):
        r = self.runner()
        r.task = 'group'
        rec = r.record('group')
        rec.update(status='pending', completed=8, request_target=10, phase='running')
        r.prepare_request()
        self.assertEqual(rec['request_target'],10)
        self.assertEqual(rec['phase'],'running')

    def test_combination_resume_keeps_finished_group(self):
        r = self.runner()
        r.records['group'] = {'status':'done','completed':2,'phase':'ready'}
        r.record('wanted').update(status='pending',phase='running')
        r.prepare_request()
        self.assertEqual(r.records['group']['status'],'done')
        self.assertNotIn('request_target',r.records['group'])

    def test_strong_selection_uses_existing_runner_with_two(self):
        r = self.runner()
        r.group_count = 2
        r.ensure_formation_once = Mock()
        r.scheduler._next_team_attempt_index.return_value = 1
        r.strong()
        call = r.scheduler.run_verified_strong_approach.call_args
        self.assertIsNone(call.kwargs['target_count'])
        self.assertEqual(call.args[2].key,'strong_approach')
        self.assertTrue(call.args[2].require_all_member_auto)
        self.assertEqual(call.args[0].members, (r.account.name,))

    def test_strong_resume_only_runs_remaining_requested_attempt(self):
        r = self.runner()
        r.group_count = 2
        r.ensure_formation_once = Mock()
        r.record('strong')['attempt_target'] = 3
        r.scheduler._next_team_attempt_index.return_value = 2
        r.strong()
        self.assertIsNone(r.scheduler.run_verified_strong_approach.call_args.kwargs['target_count'])

    def test_friendship_only_counts_exactly_one_verified_consumption(self):
        r = self.runner()
        r.friendship_remaining = Mock(return_value=4)
        self.assertTrue(r.friendship_settled({'friendship_before':5}))
        self.assertFalse(r.friendship_settled({'friendship_before':4}))
        self.assertFalse(r.friendship_settled({}))
        r.friendship_remaining.return_value = 3
        self.assertFalse(r.friendship_settled({'friendship_before':5}))

    def test_team_leader_top_main_preset_real_frame(self):
        path = hs.ROOT/'logs/team_formation_failure_20260914.png'
        if not path.exists():
            self.skipTest('live replay unavailable')
        with Image.open(path) as frame:
            store = hs.TemplateStore(.9)
            self.assertTrue(store.match(frame,hs.ASSETS/'ninja_main_selected_current.bmp',daily.FORMATION_MAIN_ROI)[0])

    def test_selected_wanted_does_not_run_group(self):
        r = self.runner()
        r.task = 'team'
        r.selected_tasks = daily.selected_sequence('team', ['wanted'])
        r.bind = Mock()
        r.group = Mock()
        r.wanted = Mock()
        with patch.object(hs,'ForegroundRuntimeLease'), patch.object(hs,'atomic_json'):
            self.assertEqual(r.run(), 0)
        r.group.assert_not_called()
        r.wanted.assert_called_once()

    def test_empty_selection_rejected_before_scheduler(self):
        with patch.object(daily,'DailyScheduler') as scheduler:
            with self.assertRaises(ValueError):
                daily.DailyRunner('team', selected_tasks=[])
            scheduler.assert_not_called()

    def test_sealed_limit_and_pending_attempt_block(self):
        from hyol_sealed import next_attempt
        self.assertEqual(next_attempt({}),1)
        self.assertEqual(next_attempt({'sealed_ninja_attempt_1':{'status':'done'}}),2)
        self.assertIsNone(next_attempt({f'sealed_ninja_attempt_{i}':{'status':'done'} for i in (1,2)}))
        with self.assertRaises(ValueError):
            next_attempt({'sealed_ninja_attempt_1':{'status':'pending'}})

    def test_sealed_unresolved_attempt_survives_date_change(self):
        import tempfile, json
        from hyol_sealed import unresolved_previous_days
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root/'2026-09-13'/'leader.json'
            old.parent.mkdir()
            old.write_text(json.dumps({'actions': {'sealed_ninja_attempt_1': {'status': 'pending'}}}))
            self.assertEqual(unresolved_previous_days(root,'leader','2026-09-14'), [str(old)])
            self.assertEqual(unresolved_previous_days(root,'other','2026-09-14'), [])

    def runner(self):
        r = object.__new__(daily.DailyRunner)
        r.scheduler = Mock()
        r.account = hs.Account(2,"test",180)
        r.records = {}
        r.path = None
        r.group_count = 1
        r.arena_wins = 5
        r.group_selection_cancelled = False
        r.generic_current_window = True
        r.formation = "main"
        r.formation_checked = False
        r.task = "all"
        r.current = ""
        r.event = Mock()
        r.verify_main_formation = Mock()
        r.close_page = Mock()
        r.return_to_main = Mock(return_value=True)
        return r

    def test_invalid_count_never_initializes_scheduler(self):
        with patch.object(daily,"DailyScheduler") as scheduler:
            for count in (-1,100):
                with self.assertRaises(ValueError):
                    daily.DailyRunner("group",count)
            scheduler.assert_not_called()

    def test_arena_uses_requested_additional_victories(self):
        r = self.runner()
        r.scheduler.state.module_status.return_value = "done"
        r.arena()
        r.scheduler.prepare_additional_arena_request.assert_called_once_with(r.account, 5)
        self.assertEqual(5, r.scheduler.arena_victory_target)
        r.scheduler.run_combat_interleaved.assert_called_once_with("arena")

    def test_completed_arena_request_is_reopened(self):
        r = self.runner()
        r.selected_tasks = ("arena",)
        r.record("arena").update(status="done", task_completed=True, home_returned=True)
        r.prepare_request()
        self.assertEqual("new", r.record("arena")["status"])
        self.assertFalse(r.record("arena")["task_completed"])

    def test_unknown_action_never_reclicked(self):
        r = self.runner()
        r.scheduler.state.action_status.return_value = "pending"
        with self.assertRaises(hs.PendingError):
            r.once("button",(0,0,100,100),10,10,"start")
        r.scheduler.click_template.assert_not_called()

    def test_done_but_visible_button_never_reclicked(self):
        r = self.runner()
        r.scheduler.state.action_status.return_value = "done"
        with self.assertRaises(hs.PendingError):
            r.once("button",(0,0,100,100),10,10,"start")
        r.scheduler.click_template.assert_not_called()

    def test_no_commit_when_clicked_button_remains(self):
        r = self.runner()
        r.scheduler.state.action_status.return_value = "new"
        r.scheduler.wait_absent.return_value = False
        with self.assertRaises(hs.PendingError):
            r.once("button",(0,0,100,100),10,10,"start")
        r.scheduler.commit_action.assert_not_called()

    def test_missing_detail_never_clicks_participate(self):
        r = self.runner()
        r.visible = Mock(side_effect=lambda name,*a,**k: name in ("daily_practice_anchor.bmp","daily_wanted_current_row.bmp"))
        r.click = Mock()
        r.scheduler.wait_template.side_effect = lambda name,*a,**k: name == "daily_normal_tab_selected.bmp"
        with self.assertRaises(hs.PendingError):
            r.enter("wanted")
        self.assertEqual(["daily_wanted_current_row.bmp"],[c.args[0] for c in r.click.call_args_list])

    def test_disabled_group_participate_is_never_clicked(self):
        r = self.runner()
        visible_names = {"daily_practice_anchor.bmp", "daily_group_current_row.bmp",
                         "daily_participate_disabled_current.bmp"}
        r.visible = Mock(side_effect=lambda name,*a,**k: name in visible_names)
        r.click = Mock()
        r.scheduler.wait_template.side_effect = lambda name,*a,**k: name in (
            "daily_limited_tab_selected.bmp", "daily_group_detail_current.bmp")
        with self.assertRaisesRegex(hs.PendingError, "当前未开放"):
            r.enter("group")
        clicked = [c.args[0] for c in r.click.call_args_list]
        self.assertNotIn("daily_participate.bmp", clicked)

    def test_existing_target_page_does_not_reenter(self):
        r = self.runner()
        r.visible = Mock(return_value=True)
        r.enter("survival")
        r.scheduler.click_template.assert_not_called()

    def test_no_group_credit_without_reward_decrement(self):
        r = self.runner()
        r.enter = Mock()
        r.once = Mock()
        r.poll_group = Mock()
        r.scheduler._read_team_reward_remaining.side_effect = [5,5,5]
        with self.assertRaises(hs.PendingError):
            r.group()
        self.assertEqual(0,r.record("group")["completed"])

    def test_group_credit_requires_return_and_decrement(self):
        r = self.runner()
        r.enter = Mock()
        r.once = Mock()
        r.poll_group = Mock()
        r.close_page = Mock()
        r.scheduler._read_team_reward_remaining.side_effect = [1,1,0,0]
        r.group()
        self.assertEqual(1,r.record("group")["completed"])
        self.assertEqual(2,r.close_page.call_count)
        r.verify_main_formation.assert_called_once()

    def test_interrupted_group_reconciles_multiple_reward_decrements(self):
        r = self.runner()
        r.group_count = 3
        r.records["group"] = {"status":"pending","completed":1,"phase":"running","reward_before":4}
        r.visible = Mock(side_effect=lambda name,*a,**k: name == daily.PAGE_ANCHORS["group"])
        r.enter = Mock()
        r.once = Mock()
        r.poll_group = Mock()
        r.scheduler.capture.return_value = Mock()
        r.scheduler._team_battle_start_visible.return_value = False
        r.scheduler._battle_auto_on_visible.return_value = False
        r.scheduler._team_result_template.return_value = None
        r.scheduler._team_reward_result_visible.return_value = False
        r.scheduler._read_team_reward_remaining.side_effect = [0,0,0]
        r.group()
        self.assertEqual(5,r.record("group")["completed"])
        r.poll_group.assert_not_called()
        r.verify_main_formation.assert_not_called()

    def test_paused_before_actual_entry_reopens_only_uncharged_attempt(self):
        r = self.runner()
        r.group_count = 1
        r.group_needs_more = Mock(return_value=False)
        r.records["group"] = {"status":"pending","completed":0,"phase":"running","reward_before":3}
        r.enter = Mock()
        r.close_page = Mock()
        r.visible = Mock(side_effect=lambda name,*a,**k: name == daily.PAGE_ANCHORS["group"])
        r.scheduler.capture.return_value = Mock()
        r.scheduler._read_team_reward_remaining.return_value = 3
        r.scheduler._team_battle_start_visible.return_value = False
        r.scheduler._battle_auto_on_visible.return_value = False
        r.scheduler._team_result_template.return_value = None
        r.scheduler._team_reward_result_visible.return_value = False
        r.group()
        self.assertEqual("ready",r.record("group")["phase"])
        reset_actions = [call.args[1] for call in r.scheduler.state.set_action.call_args_list]
        self.assertEqual(["daily_group_1_select","daily_group_1_solo_confirm"],reset_actions)

    def test_group_checks_formation_only_once_for_multiple_battles(self):
        r = self.runner()
        r.group_count = 2
        r.enter = Mock()
        r.once = Mock()
        r.poll_group = Mock()
        r.scheduler._read_team_reward_remaining.side_effect = [2,2,1,1,0,0]
        r.group()
        self.assertEqual(2,r.record("group")["completed"])
        r.verify_main_formation.assert_called_once()

    def test_fast_formation_requires_named_selected_preset_and_four_deployed(self):
        r = self.runner()
        r.formation = "fast"
        r.verify_main_formation = daily.DailyRunner.verify_main_formation.__get__(r)
        r.visible = Mock(side_effect=lambda name,*a,**k: name in ("ninja_page_current.bmp","ninja_four_deployed_current.bmp", "ninja_shark_selected_current.bmp"))
        r.scheduler.capture.return_value = Mock()
        r.scheduler.wait_until_main.return_value = True
        r.scheduler.save_raw_evidence.return_value = "evidence.png"
        r.click = Mock()
        r.verify_main_formation()
        self.assertFalse(any(call.args[0] == "ninja_main_unselected_current.bmp" for call in r.click.call_args_list))

        r.visible = Mock(side_effect=lambda name,*a,**k: name in ("ninja_page_current.bmp","ninja_four_deployed_current.bmp"))
        r.scheduler.template.return_value = (False, 0.0, 0, 0)
        with self.assertRaisesRegex(hs.PendingError, "速刷／鲨鱼预设按钮未确认"):
            r.verify_main_formation()

    def test_sealed_checks_formation_before_run(self):
        r = self.runner()
        with patch('hyol_sealed.run') as run:
            r.sealed()
        r.verify_main_formation.assert_called_once()
        run.assert_called_once_with(r)

    def test_shark_selected_template_matches_reference_only(self):
        store = hs.TemplateStore(.96)
        template = hs.ASSETS / 'ninja_shark_selected_current.bmp'
        selected = Image.open(hs.LOGS / 'ninja_shark_preset_reference_20260923.png').convert('RGB')
        main_only = Image.open(hs.LOGS / 'ninja_main_preset_reference_20260923.png').convert('RGB')
        self.assertTrue(store.match(selected, template, (0, 0, *selected.size))[0])
        self.assertFalse(store.match(main_only, template, (0, 0, *main_only.size))[0])

    def test_current_sealed_name_matches_live_scene(self):
        frame = Image.open(hs.LOGS / 'preflight_20260923_230816_282983_fd95c5f0.png').convert('RGB')
        store = hs.TemplateStore(.90)
        self.assertTrue(store.match(frame, hs.ASSETS / 'sealed_name_current.bmp', (0,160,1690,730))[0])

    def test_battle_speed_x1_is_clicked_and_x2_verified(self):
        r = self.runner()
        r.visible = Mock(side_effect=lambda name,*a,**k: name == "battle_speed_x1.png")
        r.click = Mock()
        r.scheduler._battle_auto_on_visible.return_value = True
        r.scheduler.wait_template.return_value = True
        self.assertTrue(r.ensure_battle_x2(Mock(),1))
        r.click.assert_called_once_with(
            "battle_speed_x1.png",daily.SPEED_ROI,14,14,"daily_group_1_speed_x2",
            ["battle_auto_on.bmp","battle_auto_on_current.bmp","battle_auto_on_current_core.bmp"],
            threshold=.84,anchor_threshold=.90)
        r.scheduler.wait_template.assert_called_once_with("battle_speed_x2.png",daily.SPEED_ROI,5,threshold=.84)

    def test_battle_speed_waits_until_auto_is_stably_on(self):
        r = self.runner()
        r.visible = Mock(side_effect=lambda name,*a,**k: name == "battle_speed_x1.png")
        r.click = Mock()
        r.scheduler._battle_auto_on_visible.return_value = False
        self.assertFalse(r.ensure_battle_x2(Mock(),1))
        r.click.assert_not_called()

    def test_battle_already_x2_is_not_clicked(self):
        r = self.runner()
        r.visible = Mock(side_effect=lambda name,*a,**k: name == "battle_speed_x2.png")
        r.click = Mock()
        self.assertFalse(r.ensure_battle_x2(Mock(),1))
        r.click.assert_not_called()

    def test_recorded_x1_and_return_game_templates_are_located(self):
        store = hs.TemplateStore(.90)
        battle = Image.open(hs.LOGS / "current_group_battle_state.png").convert("RGB")
        result = Image.open(hs.LOGS / "daily_group_return_source_20260909.png").convert("RGB")
        self.assertTrue(store.match(battle,hs.ASSETS / "battle_speed_x1.png",daily.SPEED_ROI,.84)[0])
        self.assertFalse(store.match(battle,hs.ASSETS / "battle_speed_x2.png",daily.SPEED_ROI,.84)[0])
        self.assertTrue(store.match(result,hs.ASSETS / "group_return_game_current.bmp",daily.GROUP_RETURN_ROI,.90)[0])

    def test_live_ninja_main_preset_is_inside_guarded_roi(self):
        frame = Image.open(hs.LOGS / "daily_survival_formation_source_20260911.png").convert("RGB")
        store = hs.TemplateStore(.90)
        selected,score,x,y = store.match(
            frame,hs.ASSETS / "ninja_main_selected_current.bmp",daily.FORMATION_MAIN_ROI,.98)
        deployed,deployed_score,_,_ = store.match(
            frame,hs.ASSETS / "ninja_four_deployed_current.bmp",(870,310,1020,360),.95)
        self.assertTrue(selected)
        self.assertGreater(score,.99)
        self.assertEqual((508,390),(x,y))
        self.assertTrue(deployed)
        self.assertGreater(deployed_score,.99)

    def test_closed_group_participate_is_rejected_as_active(self):
        frame = Image.open(hs.LOGS / "daily_group_closed_source_20260910.png").convert("RGB")
        store = hs.TemplateStore(.90)
        disabled,disabled_score,_,_ = store.match(
            frame,hs.ASSETS / "daily_participate_disabled_current.bmp",(1051,569,1351,819),.98)
        active,active_score,_,_ = store.match(
            frame,hs.ASSETS / "daily_participate.bmp",(1051,569,1351,819),.97)
        self.assertTrue(disabled)
        self.assertGreater(disabled_score,.99)
        self.assertFalse(active)
        self.assertLess(active_score,.97)

    def test_live_wanted_detail_is_located_after_row_click(self):
        frame = Image.open(hs.LOGS / "daily_wanted_detail_source_20260909.png").convert("RGB")
        matched,score,_,_ = hs.TemplateStore(.90).match(
            frame,hs.ASSETS / "daily_wanted_detail_current.bmp",(1090,310,1220,350),.90)
        self.assertTrue(matched)
        self.assertGreater(score,.99)

    def test_live_wanted_selected_row_is_stable_with_rotating_detail_art(self):
        frame = Image.open(hs.LOGS / "daily_wanted_selected_source_20260909.png").convert("RGB")
        matched,score,_,_ = hs.TemplateStore(.90).match(
            frame,hs.ASSETS / "daily_wanted_selected_current.bmp",(505,535,705,605),.90)
        self.assertTrue(matched)
        self.assertGreater(score,.99)

    def test_live_wanted_selected_row_is_found_at_bottom_of_daily_list(self):
        frame = Image.open(hs.LOGS / "daily_wanted_selected_bottom_source_20260910.png").convert("RGB")
        matched,score,x,y = hs.TemplateStore(.90).match(
            frame,hs.ASSETS / "daily_wanted_selected_current.bmp",daily.WANTED_SELECTED_ROI,.90)
        self.assertTrue(matched)
        self.assertGreater(score,.99)
        self.assertGreater(y,600)

    def test_checked_box_is_not_toggled_off(self):
        r = self.runner()
        r.visible = Mock(return_value=False)
        r.dismiss_login_warning()
        r.scheduler.click_template.assert_not_called()

    def test_unchecked_box_is_clicked_once_and_verified(self):
        r = self.runner()
        r.visible = Mock(return_value=True)
        r.scheduler.wait_absent.return_value = True
        r.dismiss_login_warning()
        r.scheduler.click_template.assert_called_once()
        r.scheduler.wait_absent.assert_called_once()

    def test_bad_formation_stops_before_survival_entry(self):
        r = self.runner()
        r.verify_main_formation.side_effect = hs.PendingError("not main")
        r.enter = Mock()
        with self.assertRaises(hs.PendingError):
            r.survival()
        r.enter.assert_not_called()

    def test_bad_formation_stops_before_wanted_entry(self):
        r = self.runner()
        r.visible = Mock(return_value=False)
        r.verify_main_formation.side_effect = hs.PendingError("not main")
        r.enter = Mock()
        with self.assertRaises(hs.PendingError):
            r.wanted()
        r.enter.assert_not_called()

    def test_formation_waits_for_delayed_selected_preset_before_clicking(self):
        r = self.runner()
        selected_calls = 0
        def visible(name,*args,**kwargs):
            nonlocal selected_calls
            if name == "ninja_page_current.bmp" or name == "ninja_four_deployed_current.bmp":
                return True
            if name == "ninja_main_selected_current.bmp":
                selected_calls += 1
                return selected_calls > 1
            return False
        r.visible = Mock(side_effect=visible)
        r.scheduler.wait_template.return_value = True
        r.scheduler.wait_until_main.return_value = True
        r.scheduler.capture.return_value = Mock()
        r.scheduler.save_raw_evidence.return_value = "evidence.png"
        r.click = Mock()
        r.verify_main_formation()
        selected_clicks = [call for call in r.click.call_args_list if call.args[0] == "ninja_main_unselected_current.bmp"]
        self.assertEqual([],selected_clicks)

    def test_three_in_one_runs_in_order_with_one_lease(self):
        r = self.runner()
        r.selected_tasks = ('mail', 'welfare', 'ranking', 'survival', 'arena')
        r.bind = Mock()
        calls = []
        for task in r.selected_tasks:
            setattr(r,task,Mock(side_effect=lambda t=task: calls.append(t)))
        with patch.object(hs,"ForegroundRuntimeLease") as lease, patch.object(hs,"atomic_json"):
            self.assertEqual(0,r.run())
            lease.return_value.acquire.assert_called_once()
            lease.return_value.release.assert_called_once()
        self.assertEqual(list(r.selected_tasks),calls)
        self.assertTrue(all(r.record(t)["status"] == "done" for t in r.selected_tasks))

    def test_final_managed_option_skips_only_managed_after_verified_tasks_and_home(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                r = self.runner()
                r.managed_enabled = enabled
                r.selected_tasks = ('mail',)
                r.bind = Mock()
                r.prepare_request = Mock()
                r.mail = Mock()
                r.finish_solo_managed = Mock()
                r.scheduler.main_ready.return_value = True
                with patch.object(hs, 'ForegroundRuntimeLease') as lease, patch.object(hs, 'atomic_json'):
                    lease.return_value.acquire.return_value = True
                    self.assertEqual(0, r.run())
                self.assertTrue(r.record('mail')['task_completed'])
                self.assertTrue(r.record('mail')['home_returned'])
                r.return_to_main.assert_called_once()
                if enabled:
                    r.finish_solo_managed.assert_called_once()
                else:
                    r.finish_solo_managed.assert_not_called()
                    self.assertNotIn('managed', r.records)

    def test_disabling_managed_does_not_turn_unknown_task_or_return_into_success(self):
        for failed_stage in ('mail', 'return_to_main'):
            with self.subTest(failed_stage=failed_stage):
                r = self.runner()
                r.managed_enabled = False
                r.selected_tasks = ('mail',)
                r.bind = Mock()
                r.prepare_request = Mock()
                r.mail = Mock()
                r.finish_solo_managed = Mock()
                getattr(r, failed_stage).side_effect = hs.PendingError('unknown')
                with patch.object(hs, 'ForegroundRuntimeLease'), patch.object(hs, 'atomic_json'):
                    self.assertEqual(2, r.run())
                self.assertEqual('pending', r.record('mail')['status'])
                r.finish_solo_managed.assert_not_called()

    def test_three_in_one_stops_on_unknown_outcome(self):
        r = self.runner()
        r.selected_tasks = ('mail', 'survival', 'arena')
        r.bind = Mock()
        r.mail = Mock(side_effect=hs.PendingError("outcome unknown"))
        r.survival = Mock()
        r.wanted = Mock()
        with patch.object(hs,"ForegroundRuntimeLease"),patch.object(hs,"atomic_json"):
            self.assertEqual(2,r.run())
        r.survival.assert_not_called()
        r.wanted.assert_not_called()
        self.assertEqual("pending",r.record("mail")["status"])

    def test_quiz_team_skip_on_main_does_not_reconcile_unrelated_arena_action(self):
        r = self.runner()
        r.selected_tasks = ('quiz',)
        r.bind = Mock()
        r.prepare_request = Mock()
        r.scheduler.capture.return_value = object()
        r.scheduler.main_ready.return_value = True
        with patch.object(hs,"ForegroundRuntimeLease") as lease, \
             patch.object(hs,"atomic_json"), \
             patch("hyol_quiz.solo_verified", return_value=False):
            lease.return_value.acquire.return_value = True
            self.assertEqual(2, r.run())
        r.return_to_main.assert_not_called()
        self.assertEqual('pending', r.record('quiz')['status'])
        self.assertIn('单人', r.record('quiz')['last_error'])

    def test_return_failure_preserves_completed_task_and_resume_only_returns(self):
        r = self.runner()
        r.task = "wanted"
        r.bind = Mock()
        r.wanted = Mock()
        r.return_to_main.side_effect = hs.PendingError("return not verified")
        with patch.object(hs,"ForegroundRuntimeLease"), patch.object(hs,"atomic_json"):
            self.assertEqual(2, r.run())
            self.assertTrue(r.record("wanted")["task_completed"])
            self.assertFalse(r.record("wanted")["home_returned"])
            r.return_to_main.side_effect = None
            self.assertEqual(0, r.run())
        r.wanted.assert_called_once()
        self.assertTrue(r.record("wanted")["home_returned"])

    def test_new_combination_can_repeat_previously_completed_group(self):
        r = self.runner()
        r.task = 'team'
        r.bind = Mock()
        r.records["group"] = {"status":"done","completed":5,"phase":"ready"}
        r.group = Mock()
        r.survival = Mock()
        r.wanted = Mock()
        with patch.object(hs,"ForegroundRuntimeLease"),patch.object(hs,"atomic_json"):
            self.assertEqual(0,r.run())
        r.group.assert_called_once()
        r.survival.assert_not_called()
        r.wanted.assert_called_once()

    def test_team_client_runs_group_then_wanted_without_survival(self):
        r = self.runner()
        r.task = "team"
        r.bind = Mock()
        calls = []
        r.group = Mock(side_effect=lambda: calls.append("group"))
        r.survival = Mock(side_effect=lambda: calls.append("survival"))
        r.wanted = Mock(side_effect=lambda: calls.append("wanted"))
        with patch.object(hs,"ForegroundRuntimeLease"),patch.object(hs,"atomic_json"):
            self.assertEqual(0,r.run())
        self.assertEqual(["group","wanted"],calls)
        r.survival.assert_not_called()

    def test_team_client_has_no_three_member_start_requirement(self):
        source = Path(daily.__file__).read_text(encoding="utf-8")
        group = source.split("    def group(self):",1)[1].split("    def poll_group",1)[0]
        wanted = source.split("    def wanted(self):",1)[1].split("    def poll_wanted",1)[0]
        self.assertNotIn("verify_three_member_team",group+wanted)
        self.assertNotIn("three_member_template",group+wanted)

    def test_team_build_publishes_into_usable_script_folder(self):
        source = (Path(__file__).parent / "build_team_client.ps1").read_text(encoding="utf-8")
        self.assertIn("'可用脚本'", source)
        self.assertIn("火影OL组队任务.lnk", source)
        self.assertNotIn("（最终版）", source)
        self.assertIn("Team client publish path escaped", source)

    def test_wanted_task_auto_opens_page_for_existing_team(self):
        source = Path(daily.__file__).read_text(encoding="utf-8")
        entry = source.split("    def enter(self, task):",1)[1].split("    def record",1)[0]
        wanted = source.split("    def wanted(self):",1)[1].split("    def poll_wanted",1)[0]
        self.assertIn('"wanted_available_auto_current.bmp"',entry)
        self.assertIn('"daily_open_wanted_from_task_auto"',entry)
        self.assertIn('"daily_recover_wanted_from_task_auto"',wanted)
        self.assertLess(wanted.index('"daily_recover_wanted_from_task_auto"'),wanted.index('action = "daily_wanted_auto"'))

    def test_generic_team_binding_uses_anonymous_current_tab(self):
        r = self.runner()
        r.generic_current_window = True
        r.scheduler.bind_window = Mock()
        r.scheduler.capture.return_value = Mock()
        anonymous = hs.Account(4,"当前窗口标签4",590)
        r.scheduler.bind_generic_current_window.return_value = anonymous
        r.scheduler.accounts = [anonymous]
        r.scheduler.state.day = "2026-09-08"
        r.scheduler.state.account_paths = {anonymous.name: Path('state/2026-09-08/account.json')}
        r.scheduler.main_ready.return_value = True
        r.visible = Mock(return_value=False)
        with patch('hyol_checkpoints.load_tasks', return_value=(Path('state/account_tasks.json'), {})):
            r.bind()
        r.scheduler.bind_generic_current_window.assert_called_once_with(require_game_context=False)
        self.assertEqual(anonymous,r.account)
        r.scheduler.bind_visible_account_only.assert_not_called()

    def test_known_shell_close_rebinds_two_frame_named_owner_before_tasks(self):
        r = self.runner()
        r.generic_current_window = True
        anonymous = hs.Account(2,'当前窗口标签2',310)
        named = hs.Account(2,'verified-role',310)
        r.scheduler.bind_generic_current_window.return_value = anonymous
        r.scheduler.accounts = [anonymous]
        r.scheduler.main_ready.side_effect = lambda frame=None: frame is None
        r.visible = Mock(return_value=False)
        with patch('hyol_checkpoints.bind_current_identity',return_value=named) as bind,patch('hyol_checkpoints.load_tasks',return_value=(Path('tasks.json'),{})):
            r.bind()
        bind.assert_called_once_with(r.scheduler,anonymous)
        self.assertEqual(named,r.account)
        r.scheduler.click_template.assert_not_called()

    def test_daily_defaults_to_anonymous_current_tab_without_identity_binding(self):
        r = self.runner()
        r.task = "all"
        r.generic_current_window = True
        r.scheduler.bind_window = Mock()
        anonymous = hs.Account(2,"当前窗口标签2",310)
        r.scheduler.bind_generic_current_window.return_value = anonymous
        r.scheduler.accounts = [anonymous]
        r.scheduler.state.day = "2026-09-09"
        r.scheduler.state.account_paths = {anonymous.name: Path('state/2026-09-09/account.json')}
        r.scheduler.main_ready.return_value = True
        r.scheduler.capture.return_value = Mock()
        r.visible = Mock(return_value=False)
        with patch('hyol_checkpoints.load_tasks', return_value=(Path('state/account_tasks.json'), {})):
            r.bind()
        r.scheduler.bind_generic_current_window.assert_called_once_with(require_game_context=False)
        r.scheduler.bind_visible_account_only.assert_not_called()
        r.scheduler.verify_identity.assert_not_called()
        self.assertTrue(str(r.path).endswith("_tasks.json"))
        self.assertFalse(str(r.path).endswith("_team_client.json"))

    def test_daily_release_config_contains_no_account_identity_fields(self):
        text = (hs.ROOT / "DAILY_Client.ini").read_text(encoding="utf-8")
        for forbidden in ("TabAccountNames", "Team1", "Team2", "IdentityThreshold", "IdentityMargin"):
            self.assertNotIn(forbidden, text)
        self.assertIn("AccountMode=current", text)

    def test_real_daily_runner_default_configuration_is_anonymous(self):
        r = daily.DailyRunner("all", 1)
        self.assertTrue(r.generic_current_window)
        self.assertEqual("current", r.scheduler.account_mode)
        self.assertEqual(["当前窗口"], [account.name for account in r.scheduler.accounts])
        self.assertEqual([], r.scheduler.teams)


class DailyManagedGuiTests(unittest.TestCase):
    def test_real_checkbox_default_and_runner_parameter(self):
        with patch('hyol_client_tools.add_diagnostic_controls'):
            app = daily.DailyClient('all')
        app.withdraw()
        self.addCleanup(app.destroy)
        self.assertTrue(app.managed_enabled.get())
        self.assertFalse(app.managed_control.instate(('disabled',)))
        app.managed_enabled.set(False)
        with patch.object(daily,'DailyRunner') as runner,patch.object(daily.threading,'Thread'):
            app.start()
        self.assertIs(False,runner.call_args.kwargs['managed_enabled'])


if __name__ == "__main__":
    unittest.main()
