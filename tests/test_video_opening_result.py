"""Known opening-result captions are proof only with safe confirmation and post-state.

The current result anchors are sanitized OCR geometry from the reported failed
run. All screen reads and inputs below are scripted; no account identifiers or
live desktop APIs are used.
"""
from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import Mock

from autozzz.models import AutomationError, InputInterrupted, Scene, Stopped, Token
from autozzz.workflows import DailyWorkflow
from tests.test_core import (WorkflowContext, calibrated_video_opened, calibrated_video_panel,
                            scene, token, world_with_npc)


def current_opening_result():
    frame = scene()
    frame.tokens = [
        Token("开店大吉，丁尼遍地！", .9951247572898865,
              (.4239583333333333, .4703703703703704, .5682291666666667, .5037037037037037)),
        Token("确认", .9996997714042664,
              (.4947916666666667, .5657407407407408, .525, .5972222222222222)),
    ]
    return frame


def result_frames():
    return (current_opening_result(), calibrated_video_opened())


class OpeningResultTests(unittest.TestCase):
    def assert_not_opened(self, frame):
        context = WorkflowContext([frame])
        workflow = DailyWorkflow(context)
        try:
            result = workflow.video()
        except AutomationError:
            result = None
        if result is not None:
            self.assertNotEqual(result.status, "ok")
        self.assertFalse(workflow._video_opened)
        self.assertEqual(context.actions, [])
        self.assertNotIn("video_opened", [name for _, name in context.evidence_frames])
        self.assertNotIn("video_verified", [name for _, name in context.evidence_frames])

    def test_current_actual_result_is_recognized_on_resume_without_navigating_or_reopening(self):
        opened, world = current_opening_result(), world_with_npc()
        context = WorkflowContext([opened, world])
        workflow = DailyWorkflow(context)
        workflow._daily_jump = Mock(side_effect=AssertionError("An opening result must not navigate to open again"))
        result = workflow.video()
        self.assertEqual(result.status, "ok")
        self.assertTrue(workflow._video_opened)
        self.assertEqual(context.actions, [("click", "确认")])
        self.assertEqual(context.evidence_frames, [(opened, "video_opened"), (world, "video_verified")])
        workflow._daily_jump.assert_not_called()
        self.assertFalse(context.report.daily_verified)
        self.assertEqual(context.report.stamina_spent, 0)

    def test_current_and_legacy_results_wait_for_operating_panel_and_save_post_state(self):
        for opened in result_frames():
            with self.subTest(caption=opened.tokens[0].text):
                operating = calibrated_video_panel(operating=True)
                context = WorkflowContext([opened, scene(), operating])
                context.wait = Mock(wraps=context.wait)
                workflow = DailyWorkflow(context)
                result = workflow.video()
                self.assertEqual(result.status, "ok")
                self.assertTrue(workflow._video_opened)
                self.assertEqual(context.actions, [("click", "确认")])
                self.assertEqual(context.evidence_frames, [(opened, "video_opened"), (operating, "video_verified")])
                context.wait.assert_called_once()
                self.assertEqual(context.wait.call_args.kwargs["timeout"], context.settings.transition_timeout)

    def test_result_without_confirmation_or_with_confirmation_outside_dialog_is_not_success(self):
        for opened in result_frames():
            for confirm in (None, token("确认", .85, .90)):
                with self.subTest(caption=opened.tokens[0].text, confirm=confirm):
                    frame = scene()
                    frame.tokens = [opened.tokens[0]] + ([confirm] if confirm else [])
                    self.assert_not_opened(frame)

    def test_two_confirmation_targets_in_result_dialog_are_ambiguous(self):
        for opened in result_frames():
            with self.subTest(caption=opened.tokens[0].text):
                opened.tokens.append(token("确认", .54, .62))
                self.assert_not_opened(opened)

    def test_unrelated_confirmation_outside_dialog_does_not_replace_or_duplicate_the_real_target(self):
        for opened in result_frames():
            with self.subTest(caption=opened.tokens[0].text):
                actual = opened.tokens[1]
                opened.tokens.append(token("确认", .87, .92))
                context = WorkflowContext([opened, world_with_npc()])
                self.assertEqual(DailyWorkflow(context).video().status, "ok")
                self.assertIs(context.targets[0], actual)
                self.assertEqual(context.actions, [("click", "确认")])

    def test_result_caption_outside_its_dialog_never_authorizes_confirmation(self):
        for opened in result_frames():
            with self.subTest(caption=opened.tokens[0].text):
                opened.tokens[0] = replace(opened.tokens[0], box=(.03, .04, .25, .09))
                self.assert_not_opened(opened)

    def test_partial_or_embedded_success_words_are_not_the_known_result_caption(self):
        for text in ("开店大吉", "丁尼遍地", "开始营业", "确认开始营业", "是否开始营业",
                     "等待开店大吉，丁尼遍地！", "说明：诚信经营，丁尼为本！"):
            with self.subTest(text=text):
                frame = current_opening_result()
                frame.tokens[0] = replace(frame.tokens[0], text=text)
                self.assert_not_opened(frame)

    def test_paid_or_battle_dialog_does_not_become_safe_due_to_success_caption(self):
        for text in ("确认支付", "是否消耗菲林", "使用以太电池"):
            with self.subTest(text=text):
                frame = current_opening_result()
                frame.tokens.append(token(text, .5, .4))
                self.assert_not_opened(frame)

    def test_plain_start_confirmation_after_request_does_not_publish_opening_success(self):
        ready = calibrated_video_panel()
        confirmation = scene()
        confirmation.tokens = [token("确认开始营业", .5, .45), token("确认", .59, .60)]
        context = WorkflowContext([ready, confirmation, confirmation])
        workflow = DailyWorkflow(context)
        workflow._configure_video_promoter = Mock(side_effect=lambda frame: frame)
        try:
            result = workflow.video()
        except AutomationError:
            result = None
        if result is not None:
            self.assertNotEqual(result.status, "ok")
        self.assertEqual(context.actions, [("click", "开始营业"), ("click", "确认")])
        self.assertFalse(workflow._video_opened)
        self.assertNotIn("video_opened", [name for _, name in context.evidence_frames])
        self.assertNotIn("video_verified", [name for _, name in context.evidence_frames])

    def test_pause_after_sent_success_confirmation_preserves_proof_without_opening_again(self):
        for opened in result_frames():
            for destination in (world_with_npc(), calibrated_video_panel(operating=True)):
                with self.subTest(caption=opened.tokens[0].text, destination=destination.text):
                    context = WorkflowContext([opened])
                    wait = context.wait
                    context.wait = Mock(side_effect=InputInterrupted("manual pause after result confirmation"))
                    workflow = DailyWorkflow(context)
                    with self.assertRaises(InputInterrupted):
                        workflow.video()
                    self.assertTrue(workflow._video_opened)
                    self.assertEqual(context.actions, [("click", "确认")])
                    context.frames = [destination]
                    context.wait = wait
                    workflow._daily_jump = Mock(side_effect=AssertionError("Confirmed store must not be opened twice"))
                    self.assertEqual(workflow.video().status, "ok")
                    self.assertEqual(context.actions, [("click", "确认")])
                    self.assertEqual(context.evidence_frames[0], (opened, "video_opened"))
                    self.assertEqual(context.evidence_frames[-1], (destination, "video_verified"))
                    workflow._daily_jump.assert_not_called()

    def test_confirmed_result_resumes_label_free_loading_without_navigating_or_reopening(self):
        for opened in result_frames():
            for destination in (world_with_npc(), calibrated_video_panel(operating=True)):
                with self.subTest(caption=opened.tokens[0].text, destination=destination.text):
                    context = WorkflowContext([opened])
                    wait = context.wait
                    context.wait = Mock(side_effect=InputInterrupted("manual pause during result transition"))
                    workflow = DailyWorkflow(context)
                    with self.assertRaises(InputInterrupted):
                        workflow.video()
                    self.assertTrue(workflow._video_opened)
                    self.assertEqual(context.actions, [("click", "确认")])
                    context.frames = [scene(), scene(), scene(), destination]
                    context.wait = Mock(wraps=wait)
                    workflow._daily_jump = Mock(side_effect=AssertionError("Confirmed opening must not restart navigation"))
                    self.assertEqual(workflow.video().status, "ok")
                    self.assertEqual(context.actions, [("click", "确认")])
                    self.assertEqual(context.evidence_frames[-1], (destination, "video_verified"))
                    context.wait.assert_called_once()
                    workflow._daily_jump.assert_not_called()

    def test_initial_success_popup_is_confirmed_by_ensure_main_then_video_retains_success(self):
        for opened in result_frames():
            with self.subTest(caption=opened.tokens[0].text):
                world = world_with_npc()
                context = WorkflowContext([opened, world, world])
                workflow = DailyWorkflow(context)
                self.assertEqual(workflow.ensure_main().status, "ok")
                self.assertTrue(workflow._video_opened)
                workflow._daily_jump = Mock(side_effect=AssertionError("Initialization already confirmed successful opening"))
                self.assertEqual(workflow.video().status, "ok")
                self.assertEqual(context.actions, [("click", "确认")])
                self.assertEqual(context.evidence_frames[0], (opened, "video_opened"))
                self.assertEqual(context.evidence_frames[-1], (world, "video_verified"))
                workflow._daily_jump.assert_not_called()

    def test_ensure_main_resumes_confirmed_popup_through_label_free_loading(self):
        opened, world = current_opening_result(), world_with_npc()
        context = WorkflowContext([opened])
        wait = context.wait
        context.wait = Mock(side_effect=InputInterrupted("pause after initial popup confirmation"))
        workflow = DailyWorkflow(context)
        with self.assertRaises(InputInterrupted):
            workflow.ensure_main()
        self.assertTrue(workflow._video_opened)
        context.wait = wait
        context.frames = [scene(), scene(), world, world]
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(workflow.video().status, "ok")
        self.assertEqual(context.actions, [("click", "确认")])
        self.assertEqual(context.evidence_frames[-1], (world, "video_verified"))

    def test_pause_after_start_confirmation_waits_for_success_without_replaying_start(self):
        for opened in result_frames():
            with self.subTest(caption=opened.tokens[0].text):
                ready, world = calibrated_video_panel(), world_with_npc()
                confirmation = scene()
                confirmation.tokens = [token("确认开始营业", .5, .45), token("确认", .59, .60)]
                context = WorkflowContext([ready, confirmation])
                wait = context.wait
                context.wait = Mock(side_effect=InputInterrupted("pause after sending start confirmation"))
                workflow = DailyWorkflow(context)
                workflow._configure_video_promoter = Mock(side_effect=lambda frame: frame)
                with self.assertRaises(InputInterrupted):
                    workflow.video()
                self.assertFalse(workflow._video_opened)
                self.assertEqual(context.actions, [("click", "开始营业"), ("click", "确认")])
                context.frames = [scene(), scene(), opened, scene(), world]
                context.wait = wait
                workflow._daily_jump = Mock(side_effect=AssertionError("An already sent opening must not navigate or start twice"))
                self.assertEqual(workflow.video().status, "ok")
                self.assertEqual(context.actions, [("click", "开始营业"), ("click", "确认"), ("click", "确认")])
                self.assertTrue(workflow._video_opened)
                self.assertEqual(context.evidence_frames[-1], (world, "video_verified"))
                workflow._configure_video_promoter.assert_called_once()
                workflow._daily_jump.assert_not_called()

    def test_world_without_opening_proof_keeps_start_pending_and_never_restarts_business(self):
        ready, world = calibrated_video_panel(), world_with_npc()
        confirmation = scene()
        confirmation.tokens = [token("确认开始营业", .5, .45), token("确认", .59, .60)]
        context = WorkflowContext([ready, confirmation])
        wait = context.wait
        context.wait = Mock(side_effect=InputInterrupted("pause after sent start confirmation"))
        workflow = DailyWorkflow(context)
        workflow._configure_video_promoter = Mock(side_effect=lambda frame: frame)
        with self.assertRaises(InputInterrupted):
            workflow.video()
        context.wait = wait
        context.frames = [scene(), world, ready]
        workflow._daily_jump = Mock(side_effect=AssertionError("World alone must not trigger navigation or a second opening"))
        with self.assertRaises(AutomationError):
            workflow.video()
        self.assertTrue(workflow._video_start_pending)
        self.assertFalse(workflow._video_opened)
        self.assertEqual(context.actions, [("click", "开始营业"), ("click", "确认")])
        workflow._configure_video_promoter.assert_called_once()
        workflow._daily_jump.assert_not_called()

    def test_world_before_actual_success_is_observed_without_repeating_start_confirmation(self):
        for opened in result_frames():
            with self.subTest(caption=opened.tokens[0].text):
                ready, world = calibrated_video_panel(), world_with_npc()
                confirmation = scene()
                confirmation.tokens = [token("确认开始营业", .5, .45), token("确认", .59, .60)]
                context = WorkflowContext([ready, confirmation])
                wait = context.wait
                context.wait = Mock(side_effect=InputInterrupted("pause after sent start confirmation"))
                workflow = DailyWorkflow(context)
                workflow._configure_video_promoter = Mock(side_effect=lambda frame: frame)
                with self.assertRaises(InputInterrupted):
                    workflow.video()
                context.wait = wait
                context.frames = [scene(), world, opened, world]
                workflow._daily_jump = Mock(side_effect=AssertionError("Transient world must not cause a second opening"))
                self.assertEqual(workflow.video().status, "ok")
                self.assertFalse(workflow._video_start_pending)
                self.assertTrue(workflow._video_opened)
                self.assertEqual(context.actions, [("click", "开始营业"), ("click", "确认"), ("click", "确认")])
                self.assertEqual(context.evidence_frames[-1], (world, "video_verified"))
                workflow._configure_video_promoter.assert_called_once()
                workflow._daily_jump.assert_not_called()

    def test_stop_before_result_confirmation_does_not_publish_confirmed_state(self):
        opened = current_opening_result()
        context = WorkflowContext([opened])
        context.click = Mock(side_effect=Stopped("Stopped before sending confirmation"))
        workflow = DailyWorkflow(context)
        with self.assertRaises(Stopped):
            workflow.video()
        self.assertFalse(workflow._video_opened)
        self.assertNotIn("video_verified", [name for _, name in context.evidence_frames])
        self.assertEqual(context.actions, [])

    def test_already_operating_on_resume_is_verified_without_any_input(self):
        operating = calibrated_video_panel(operating=True)
        context = WorkflowContext([operating])
        workflow = DailyWorkflow(context)
        result = workflow.video()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [])
        self.assertEqual(context.evidence_frames, [(operating, "video_verified")])
        self.assertFalse(context.report.daily_verified)


if __name__ == "__main__":
    unittest.main()
