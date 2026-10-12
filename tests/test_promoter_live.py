"""Unstarted-store selection regressions, using only sanitized OCR and mocked input."""
from dataclasses import replace
import unittest
from unittest.mock import Mock

from autozzz.models import InputInterrupted, Scene, Stopped, Token, normalize
from autozzz.workflows import DailyWorkflow
from tests.test_promoter import ReplayContext, business, fixture, fresh, token


class CurrentPickerContext(ReplayContext):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected_control = "确定"
        self.animate_card = False
        self._blank_pending = False

    def click(self, scene, target):
        if (isinstance(target, Token) and normalize(target.text) == "确定"
                and DailyWorkflow(self)._promoter_picker(scene)):
            self._assert_scene(scene)
            if not any(target is t for t in scene.tokens):
                raise AssertionError("Selection reused an old confirmation target")
            self.inputs.append(("click", "确定"))
            self.current_name = self.slot_override or self.selected_name
            self.frame = business(self.current_name)
            return
        super().click(scene, target)
        if self.animate_card and isinstance(target, Token) and normalize(target.text) == "薇薇安":
            self._blank_pending = True

    def snapshot(self):
        if self._blank_pending:
            self._blank_pending = False
            self.last = fresh(Scene([]))
            return self.last
        return super().snapshot()


class CurrentPromoterTests(unittest.TestCase):
    def context(self):
        return CurrentPickerContext(business(), "薇薇安", pages=[fixture("promoter_selected_long")])

    def test_actual_long_description_picker_is_recognized(self):
        frame = fixture("promoter_selected_long")
        workflow = DailyWorkflow(self.context())
        self.assertTrue(workflow._promoter_picker(frame))
        goal = frame.find("目标客流量", exact=True)
        self.assertGreater(goal.center[1], 550 / 1080)

    def test_customer_flow_position_or_absence_does_not_change_picker_identity(self):
        for y in (.45, .53, .70, .85, None):
            with self.subTest(y=y):
                frame = fixture("promoter_selected_long")
                frame.tokens = [t for t in frame.tokens if t.text != "目标客流量"]
                if y is not None:
                    frame.tokens.append(token("目标客流量", .08, y))
                self.assertTrue(DailyWorkflow(self.context())._promoter_picker(frame))

    def test_picker_needs_both_stable_labels_and_right_hand_grid_identity(self):
        frame = fixture("promoter_selected_long")
        for omitted in ("选择宣传员", "宣传风格", "right_cards"):
            with self.subTest(omitted=omitted):
                changed = fresh(frame)
                changed.tokens = [t for t in changed.tokens if
                                  (t.text != omitted and not (omitted == "right_cards" and t.center[0] > .25))]
                self.assertFalse(DailyWorkflow(self.context())._promoter_picker(changed))

    def test_actual_determine_button_completes_selection_before_any_opening(self):
        context = self.context()
        selected = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertIsInstance(selected, Scene)
        self.assertEqual(context.current_name, "薇薇安")
        self.assertIn(("click", "薇薇安"), context.inputs)
        self.assertIn(("click", "确定"), context.inputs)
        self.assertNotIn(("click", "开始营业"), context.inputs)

    def test_card_animation_waits_for_a_fresh_matching_preview(self):
        context = self.context()
        context.animate_card = True
        context.wait = Mock(wraps=context.wait)
        selected = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertIsInstance(selected, Scene)
        self.assertEqual([item for item in context.inputs if item == ("click", "薇薇安")], [("click", "薇薇安")])
        self.assertIn(("click", "确定"), context.inputs)
        self.assertTrue(any("所选角色标题" in call.kwargs.get("description", "") for call in context.wait.call_args_list))

    def test_mixed_confirm_and_determine_buttons_are_ambiguous(self):
        context = self.context()
        context.confirm_duplicates = True
        result = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "确定"), context.inputs)
        self.assertNotIn(("click", "确认"), context.inputs)
        self.assertNotIn(("click", "开始营业"), context.inputs)

    def test_mismatched_preview_never_gets_confirmed(self):
        context = self.context()
        context.preview_override = "安比"
        result = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "确定"), context.inputs)

    def test_animation_control_interruptions_remain_cancellable_and_resumable(self):
        for error in (InputInterrupted("paused"), Stopped("stopped")):
            with self.subTest(error=type(error).__name__):
                context = self.context()
                context.animate_card = True
                original_wait = context.wait
                def wait(predicate, **kwargs):
                    if "所选角色标题" in kwargs.get("description", ""):
                        raise error
                    return original_wait(predicate, **kwargs)
                context.wait = wait
                with self.assertRaises(type(error)):
                    DailyWorkflow(context)._configure_video_promoter(context.snapshot())
                self.assertNotIn(("click", "确定"), context.inputs)
                self.assertNotIn(("click", "开始营业"), context.inputs)

    def test_already_assigned_explicit_preference_needs_no_selection_input(self):
        context = CurrentPickerContext(business("薇薇安"), "薇薇安", current_name="薇薇安")
        selected = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertIsInstance(selected, Scene)
        self.assertEqual(context.inputs, [])
        self.assertIn("video_promoter_selected", [name for name, _ in context.evidences])

    def test_blank_preference_still_chooses_the_first_card(self):
        context = CurrentPickerContext(business("薇薇安"), "", current_name="薇薇安",
                                       pages=[fixture("promoter_selected_long")])
        selected = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertIsInstance(selected, Scene)
        self.assertEqual(context.current_name, "蕾米埃尔")
        self.assertIn(("click", "蕾米埃尔"), context.inputs)
        self.assertIn(("click", "确定"), context.inputs)
        self.assertNotIn(("click", "开始营业"), context.inputs)

    def test_conflicting_or_repeated_slot_names_never_verify_selection(self):
        for extra in ("安比", "薇薇安"):
            with self.subTest(extra=extra):
                frame = business("薇薇安")
                frame.tokens.append(token(extra, .50, .70))
                context = CurrentPickerContext(frame, "薇薇安", current_name="薇薇安")
                self.assertFalse(DailyWorkflow(context)._promoter_slot_verified(context.snapshot(), "薇薇安"))
                self.assertEqual(context.inputs, [])

    def test_assigned_name_replacing_heading_still_enters_empty_movie_slot_before_opening(self):
        context = CurrentPickerContext(business("薇薇安"), "薇薇安", current_name="薇薇安")
        movie_plus = (.62, .70, .99)
        def template(frame, name, area, threshold=.88):
            if name == "video_slot_plus" and area[0] > .55:
                return movie_plus
            return None
        context.template = template
        def click(frame, target):
            context._assert_scene(frame)
            if target == movie_plus[:2]:
                context.inputs.append(("click", "movie_plus"))
                raise Stopped("Preparation diagnostic stops after entering the movie picker")
            raise AssertionError(f"Unexpected input before preparing empty movies: {target}")
        context.click = click
        with self.assertRaises(Stopped):
            DailyWorkflow(context).video()
        self.assertEqual(context.inputs, [("click", "movie_plus")])


if __name__ == "__main__":
    unittest.main()
