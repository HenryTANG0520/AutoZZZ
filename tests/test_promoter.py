"""Promoter and daily-terminal regressions. All input uses an offline scene replay."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autozzz.characters import canonical_promoter_name
from autozzz.models import AutomationError, RunReport, Scene, Token, normalize
from autozzz.workflows import DailyWorkflow, PROMOTER_SCROLL_LIMIT

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def token(text, x, y):
    return Token(text, .98, (x - .025, y - .012, x + .025, y + .012))


def fresh(scene):
    return Scene([Token(t.text, t.score, t.box) for t in scene.tokens], scene.image)


def fixture(name):
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    image = None
    if data.get("image_fixture"):
        import cv2
        image = cv2.imread(str(FIXTURES / data["image_fixture"]))
        if image is None:
            raise AssertionError("Missing retained screenshot name-strip fixture")
    return Scene([Token(t["text"], t["score"], tuple(t["box"])) for t in data["tokens"]], image)


def picker(names=("蕾米埃尔", "爱芮", "安比"), preview="蕾米埃尔", control="确认", row=.374):
    tokens = [token("选择宣传员", .087, .95), token("宣传风格", .07, .23),
              token("目标客流量", .08, .45), token(preview, .10, .145)]
    tokens += [token(name, .326 + .11 * i, row) for i, name in enumerate(names)]
    if control:
        tokens.append(token(control, .89, .951))
    return Scene(tokens)


def business(name="", running=False):
    tokens = [token("经营状况", .74, .055), token("无人咨询", .89, .05),
              token("正在营业" if running else "开始营业", .875, .915)]
    tokens += [token(name if name else "选择宣传员", .47, .58)]
    return Scene(tokens)


def daily_frame(activity="400/400", claimed=True):
    tokens = [token("目标", .4, .17), token("日常", .51, .17),
              token("今日活跃度" + activity, .57, .28)]
    if claimed:
        tokens.append(token("全部奖励已领取", .66, .32))
    return Scene(tokens)


class ReplayContext:
    """Fresh frames and strict Token identity model the real Context freshness guards."""
    def __init__(self, initial, promoter="", pages=None, current_name=""):
        self.frame = initial
        self.settings = SimpleNamespace(promoter=promoter, transition_timeout=45, server="cn")
        self.report = RunReport()
        self.last = None
        self.inputs = []
        self.evidences = []
        self.logs = []
        self.pages = pages or [picker()]
        self.page_index = 0
        self.selected_name = None
        self.current_name = current_name
        self.selected_control = "确认"
        self.preview_override = None
        self.slot_override = None
        self.scroll_hook = None
        self.confirm_duplicates = False
        self.selected_missing_confirm = False
        self.start_step = 0

    def snapshot(self):
        self.last = fresh(self.frame)
        return self.last

    def _assert_scene(self, scene):
        if scene is not self.last:
            raise AssertionError("Action used a stale scene")

    def click(self, scene, target):
        self._assert_scene(scene)
        if isinstance(target, Token):
            if not any(target is t for t in scene.tokens):
                raise AssertionError("Action reused a pre-scroll Token")
            label = normalize(target.text)
        else:
            label = "promoter_plus"
        self.inputs.append(("click", label))
        workflow = DailyWorkflow(self)
        if workflow._promoter_picker(scene):
            if label == "确认":
                self.current_name = self.slot_override or self.selected_name
                self.frame = business(self.current_name)
            else:
                self.selected_name = canonical_promoter_name(target.text)
                self.frame = fresh(self.pages[self.page_index])
                self.frame.tokens = [t for t in self.frame.tokens if not t.within((.01, .106, .25, .195))
                                     and not t.within((.78, .897, .98, .995))]
                self.frame.tokens.append(token(self.preview_override or self.selected_name, .1, .145))
                if not self.selected_missing_confirm:
                    self.frame.tokens.append(token(self.selected_control, .89, .951))
                if self.confirm_duplicates:
                    self.frame.tokens.append(token("确认", .94, .951))
        elif workflow._video_business_panel(scene):
            if label == "开始营业":
                self.start_step = 1
                self.frame = Scene([token("是否开始营业", .5, .5), token("确认", .59, .59)])
            else:
                self.frame = fresh(self.pages[self.page_index])
        elif label == "确认" and self.start_step == 1:
            self.start_step = 2
            self.frame = Scene([token("诚信经营丁尼为本", .5, .5), token("确认", .49, .6)])
        elif label == "确认" and self.start_step == 2:
            self.start_step = 3
            self.frame = Scene([token("F2", .81, .12), token("Tab", .1, .94)])
        else:
            raise AssertionError(f"Unexpected click in replay: {label}")

    def key(self, name, scene, **kwargs):
        self._assert_scene(scene)
        self.inputs.append(("key", name))
        if name != "esc":
            raise AssertionError("Only closing an already-assigned selection is expected")
        self.current_name = self.slot_override or self.selected_name
        self.frame = business(self.current_name)

    def move(self, scene, point):
        self._assert_scene(scene)
        self.inputs.append(("move", point))

    def scroll(self, amount):
        if amount not in (-360, 360):
            raise AssertionError("Replay only permits bounded three-detent scrolls")
        self.inputs.append(("scroll", amount))
        if self.scroll_hook:
            self.frame = self.scroll_hook(amount)
            return
        self.page_index = max(0, min(len(self.pages) - 1, self.page_index + (1 if amount < 0 else -1)))
        self.frame = fresh(self.pages[self.page_index])

    def wait(self, predicate, timeout=45, description=""):
        frame = self.snapshot()
        if not predicate(frame):
            raise AutomationError("Replay did not reach " + description)
        return frame

    def evidence(self, scene, name):
        self.evidences.append((name, self.report.daily_verified))

    def template(self, scene, name, area, threshold=.88):
        if name == "video_slot_plus" and DailyWorkflow(self)._video_business_panel(scene) and not self.current_name:
            return (.473, .702, .987)
        return None

    def sleep(self, seconds):
        pass

    def log(self, text):
        self.logs.append(text)


class PromoterRecognitionTests(unittest.TestCase):
    def test_actual_user_picker_ocr_recognizes_only_five_right_hand_cards(self):
        frame = fixture("promoter_picker")
        context = ReplayContext(frame)
        workflow = DailyWorkflow(context)
        self.assertTrue(workflow._promoter_picker(frame))
        self.assertEqual([name for name, _ in workflow._promoter_cards(frame)],
                         ["蕾米埃尔", "爱芮", "安比", "丝琪", "邦布18"])

    def test_actual_user_business_is_already_running_and_has_no_picker_anchor(self):
        frame = fixture("promoter_business")
        workflow = DailyWorkflow(ReplayContext(frame))
        self.assertTrue(workflow._video_business_panel(frame))
        self.assertFalse(workflow._promoter_picker(frame))

    def test_actual_grey_locked_caption_cannot_enable_a_known_name(self):
        frame = fixture("promoter_picker")
        empty = next(t for t in frame.tokens if t.text == "EMPTY" and t.center[1] < .4)
        frame.tokens.remove(empty)
        # The grey pixel sample comes from the real locked slot. OCR recognizing a
        # known character in a locked caption must still not make it selectable.
        frame.tokens.append(Token("艾莲", .99, empty.box))
        names = [name for name, _ in DailyWorkflow(ReplayContext(frame))._promoter_cards(frame)]
        self.assertNotIn("艾莲", names)
        self.assertEqual(names, ["蕾米埃尔", "爱芮", "安比", "丝琪", "邦布18"])

    def test_bright_portrait_outside_grey_label_does_not_enable_it(self):
        import numpy as np
        frame = fixture("promoter_picker")
        empty = next(t for t in frame.tokens if t.text == "EMPTY" and t.center[1] < .4)
        frame.tokens.remove(empty)
        frame.tokens.append(Token("艾莲", .99, empty.box))
        frame.image[140:365, 1630:1740] = np.full((225, 110, 3), 255, dtype=np.uint8)
        names = [name for name, _ in DailyWorkflow(ReplayContext(frame))._promoter_cards(frame)]
        self.assertNotIn("艾莲", names)

    def test_left_preview_description_and_empty_cards_are_never_selectable(self):
        frame = picker(("安比", "EMPTY", "尚未公布的新角色"), preview="爱芮")
        frame.tokens.append(token("爱芮", .13, .36))
        cards = DailyWorkflow(ReplayContext(frame))._promoter_cards(frame)
        self.assertEqual([name for name, _ in cards], ["安比"])

    def test_exact_names_keep_anby_and_soldier_anby_separate(self):
        frame = picker(("银心锡兵·安比", "安比"))
        workflow = DailyWorkflow(ReplayContext(frame))
        self.assertEqual(workflow._promoter_target(frame, "安比")[1].text, "安比")
        self.assertIsNone(workflow._promoter_target(picker(("零号·安比",)), "安比"))

    def test_explicit_full_name_alias_can_match_the_same_card(self):
        frame = picker(("安比·德玛拉",))
        workflow = DailyWorkflow(ReplayContext(frame))
        self.assertEqual(workflow._promoter_target(frame, "安比")[0], "安比")

    def test_duplicate_ocr_in_one_cell_is_ambiguous(self):
        frame = picker(("安比",))
        frame.tokens.append(token("安比·德玛拉", .326, .374))
        self.assertEqual(DailyWorkflow(ReplayContext(frame))._promoter_cards(frame), [])

    def test_same_name_in_two_cards_does_not_choose_arbitrarily(self):
        frame = picker(("安比", "安比"))
        self.assertIsNone(DailyWorkflow(ReplayContext(frame))._promoter_target(frame, "安比"))

    def test_bottom_toolbar_name_is_outside_the_card_grid(self):
        frame = picker(("爱芮",))
        frame.tokens.append(token("安比", .86, .95))
        self.assertEqual([name for name, _ in DailyWorkflow(ReplayContext(frame))._promoter_cards(frame)], ["爱芮"])


class PromoterSelectionTests(unittest.TestCase):
    def context(self, preference="", pages=None, current_name=""):
        return ReplayContext(business(current_name), preference, pages, current_name)

    def test_default_selects_first_card_then_confirms_and_verifies_slot(self):
        context = self.context()
        result = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertIsInstance(result, Scene)
        self.assertEqual(context.current_name, "蕾米埃尔")
        self.assertIn(("click", "蕾米埃尔"), context.inputs)
        self.assertIn(("click", "确认"), context.inputs)
        self.assertIn("video_promoter_selected", [name for name, _ in context.evidences])
        self.assertNotIn(("click", "开始营业"), context.inputs)

    def test_user_preference_selects_matching_card_instead_of_first(self):
        context = self.context("爱芮")
        self.assertIsInstance(DailyWorkflow(context)._configure_video_promoter(context.snapshot()), Scene)
        self.assertEqual(context.current_name, "爱芮")
        self.assertNotIn(("click", "蕾米埃尔"), context.inputs)

    def test_duplicate_desired_names_stop_without_arbitrary_fallback(self):
        context = self.context("安比", pages=[picker(("安比", "安比"))])
        result = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "安比"), context.inputs)

    def test_user_preference_overrides_previously_assigned_promoter(self):
        context = self.context("安比", current_name="蕾米埃尔")
        self.assertIsInstance(DailyWorkflow(context)._configure_video_promoter(context.snapshot()), Scene)
        self.assertEqual(context.current_name, "安比")

    def test_target_on_next_page_is_found_after_fresh_scroll(self):
        context = self.context("艾莲", pages=[picker(), picker(("朱鸢", "艾莲"), preview="朱鸢")])
        self.assertIsInstance(DailyWorkflow(context)._configure_video_promoter(context.snapshot()), Scene)
        self.assertEqual(context.current_name, "艾莲")
        self.assertIn(("scroll", -360), context.inputs)

    def test_missing_preference_returns_to_top_and_uses_fresh_first_card(self):
        context = self.context("艾莲", pages=[picker(), picker(("朱鸢", "青衣"), preview="朱鸢")])
        self.assertIsInstance(DailyWorkflow(context)._configure_video_promoter(context.snapshot()), Scene)
        self.assertEqual(context.current_name, "蕾米埃尔")
        self.assertEqual(context.page_index, 0)
        self.assertTrue(any("未找到艾莲" in text for text in context.logs))
        # ReplayContext enforces identity and rejects any pre-search cached Token.

    def test_default_resets_a_remembered_scroll_position_before_selecting_first(self):
        context = self.context(pages=[picker(), picker(("朱鸢", "青衣"), preview="朱鸢")])
        context.page_index = 1
        self.assertIsInstance(DailyWorkflow(context)._configure_video_promoter(context.snapshot()), Scene)
        self.assertEqual(context.current_name, "蕾米埃尔")

    def test_already_assigned_target_is_closed_without_clicking_deselect(self):
        context = self.context("安比", current_name="安比")
        context.selected_control = "换下"
        self.assertIsInstance(DailyWorkflow(context)._configure_video_promoter(context.snapshot()), Scene)
        self.assertIn(("key", "esc"), context.inputs)
        self.assertNotIn(("click", "换下"), context.inputs)

    def test_empty_grid_returns_partial_without_clicking_a_placeholder(self):
        context = self.context(pages=[picker(("EMPTY",))])
        result = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "empty"), context.inputs)
        self.assertNotIn(("click", "开始营业"), context.inputs)

    def test_misspelled_preference_is_not_silently_treated_as_default(self):
        context = self.context("安比比")
        result = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "蕾米埃尔"), context.inputs)

    def test_missing_confirm_stops_before_opening_store(self):
        context = self.context()
        context.selected_missing_confirm = True
        result = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "确认"), context.inputs)
        self.assertNotIn(("click", "开始营业"), context.inputs)

    def test_two_confirm_buttons_are_ambiguous(self):
        context = self.context()
        context.confirm_duplicates = True
        result = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "确认"), context.inputs)

    def test_preview_name_mismatch_stops_without_confirm(self):
        context = self.context("爱芮")
        context.preview_override = "安比"
        result = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "确认"), context.inputs)

    def test_returned_slot_mismatch_stops_before_store_open(self):
        context = self.context("爱芮")
        context.slot_override = "安比"
        result = DailyWorkflow(context)._configure_video_promoter(context.snapshot())
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "开始营业"), context.inputs)

    def test_actual_already_running_screenshot_is_read_only(self):
        context = ReplayContext(fixture("promoter_business"), "安比")
        result = DailyWorkflow(context).video()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.inputs, [])

    def test_actual_already_running_picker_is_read_only(self):
        context = ReplayContext(fixture("promoter_picker"), "安比")
        result = DailyWorkflow(context).video()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.inputs, [])

    def test_full_video_replay_opens_only_after_slot_verification(self):
        context = self.context("爱芮")
        result = DailyWorkflow(context).video()
        self.assertEqual(result.status, "ok")
        labels = [label for kind, label in context.inputs if kind == "click"]
        self.assertLess(labels.index("爱芮"), labels.index("开始营业"))
        self.assertLess(labels.index("确认"), labels.index("开始营业"))
        self.assertEqual(context.current_name, "爱芮")

    def test_wrong_scroll_page_has_no_scroll_input(self):
        context = self.context()
        workflow = DailyWorkflow(context)
        with self.assertRaises(AutomationError):
            workflow._scroll_promoters(context.snapshot(), -360)
        self.assertEqual(context.inputs, [])

    def test_unknown_page_after_scroll_stops_input(self):
        context = ReplayContext(picker())
        context.scroll_hook = lambda amount: Scene([token("未知页面", .5, .5)])
        with self.assertRaises(AutomationError):
            DailyWorkflow(context)._scroll_promoters(context.snapshot(), -360)
        self.assertEqual(sum(kind == "scroll" for kind, _ in context.inputs), 1)

    def test_top_scroll_has_a_hard_bound_if_content_never_stabilizes(self):
        context = ReplayContext(picker())
        count = 0
        def changing(amount):
            nonlocal count
            count += 1
            return picker(("安比",), preview="安比", row=.20 + count * .025)
        context.scroll_hook = changing
        self.assertIsNone(DailyWorkflow(context)._promoter_top(context.snapshot()))
        self.assertEqual(count, PROMOTER_SCROLL_LIMIT)


class DailyTerminalEvidenceTests(unittest.TestCase):
    def workflow(self, frame):
        context = ReplayContext(frame)
        workflow = DailyWorkflow(context)
        workflow._open_daily = Mock(side_effect=context.snapshot)
        return workflow, context

    def test_complete_daily_publishes_terminal_flags_after_evidence(self):
        workflow, context = self.workflow(daily_frame())
        self.assertTrue(workflow.daily_already_complete())
        self.assertEqual(context.evidences, [("daily_verified", False)])
        self.assertTrue(context.report.daily_verified)
        self.assertEqual(context.report.engagement, 400)
        self.assertEqual(context.inputs, [])

    def test_400_without_claimed_rewards_is_not_complete(self):
        workflow, context = self.workflow(daily_frame(claimed=False))
        self.assertFalse(workflow.daily_already_complete())
        self.assertFalse(context.report.daily_verified)
        self.assertEqual(context.evidences, [])

    def test_conflicting_activity_cannot_publish_terminal_flags(self):
        frame = daily_frame()
        frame.tokens.append(token("今日活跃度300/400", .60, .30))
        workflow, context = self.workflow(frame)
        self.assertFalse(workflow.daily_already_complete())
        self.assertFalse(context.report.daily_verified)

    def test_daily_jump_records_completion_before_individual_card_status(self):
        frame = daily_frame()
        frame.tokens += [token("刮刮乐", .50, .50), token("进度1/1", .50, .42)]
        workflow, context = self.workflow(frame)
        self.assertEqual(workflow._daily_jump("scratch").status, "ok")
        self.assertTrue(context.report.daily_verified)
        self.assertEqual(context.inputs, [])

    def test_each_task_initial_scene_publishes_global_complete_without_more_input(self):
        for task in ("scratch", "coffee", "video"):
            with self.subTest(task=task):
                workflow, context = self.workflow(daily_frame())
                self.assertEqual(getattr(workflow, task)().status, "ok")
                self.assertTrue(context.report.daily_verified)
                self.assertEqual(context.inputs, [])

    def test_authoritative_evidence_is_saved_once(self):
        workflow, context = self.workflow(daily_frame())
        self.assertTrue(workflow.daily_already_complete())
        self.assertEqual(workflow.daily().status, "ok")
        self.assertEqual(context.evidences, [("daily_verified", False)])

    def test_failed_evidence_save_does_not_publish_completion(self):
        workflow, context = self.workflow(daily_frame())
        context.evidence = Mock(side_effect=OSError("write failed"))
        with self.assertRaises(OSError):
            workflow.daily_already_complete()
        self.assertFalse(context.report.daily_verified)

    def test_task_reverification_can_publish_global_complete(self):
        workflow, context = self.workflow(daily_frame())
        result = workflow._verify_task_card("video", daily_frame(), "not yet known")
        self.assertEqual(result.status, "ok")
        self.assertTrue(context.report.daily_verified)


if __name__ == "__main__":
    unittest.main()
