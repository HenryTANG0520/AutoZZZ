"""Offline weekly reward replay: synthetic pixels and anonymous OCR only."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from autozzz.models import AutomationError, InputInterrupted, RunReport, Scene, Token
from autozzz.weekly import (WEEKLY_COLUMNS, WEEKLY_GRID, WEEKLY_MILESTONE_X,
                            WEEKLY_ROWS, WeeklyRewardsWorkflow)


def token(text, x, y, score=.98):
    return Token(text, score, (x - .023, y - .011, x + .023, y + .011))


def paint(image, area, color):
    height, width = image.shape[:2]
    x1, y1, x2, y2 = area
    image[round(y1 * height):round(y2 * height), round(x1 * width):round(x2 * width)] = color


def sample(scene, area):
    x = round((area[0] + area[2]) * .5 * scene.image.shape[1])
    y = round((area[1] + area[3]) * .5 * scene.image.shape[0])
    return tuple(scene.image[y, x])


class WeeklyContext:
    """Inputs require the newest scene; claims change the rendered eligibility."""
    def __init__(self, *, start="weekly", ready=(7,), stars=(300,), popup=True):
        self.settings = SimpleNamespace(transition_timeout=45, auto_launch=False,
                                        server="cn", ocr_confidence=.60)
        self.report = RunReport()
        self.page = start
        self.ready = set(ready)
        self.stars = set(stars)
        self.popup = popup
        self.selected = 0
        self.total = 200
        self.last = None
        self.actions = []
        self.evidence_frames = []
        self.before_action = None
        self.after_action = None
        self.interrupt_wait_for = None
        self.omit = set()
        self.extra = []
        self.progress = {index: "0/1" for index in range(9)}
        self.progress.update({0: "1/1", 7: "600/600", 8: "2/2"})
        self.offset = 0
        self.scrolls = 0

    def frame(self):
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        if self.page == "world":
            return Scene([token("星期四", .12, .05),
                          token("按自己的步调度过这一天", .12, .18)], image)
        if self.page == "handbook":
            return Scene([token("目标", .43, .17), token("日常", .51, .17),
                          token("今日最大活跃度", .26, .28),
                          token("丽都周纪", .265, .805)], image)
        tokens = [token("三格连线额外奖励", .20, .17), token("积分特赏", .86, .175),
                  token(f"累计获取积分{self.total}", .86, .845),
                  token(f"任务目标{self.progress[self.selected]}", .66, .25)]
        for index in range(9):
            row, column = divmod(index, 3)
            left, right = WEEKLY_COLUMNS[column]
            top, bottom = WEEKLY_ROWS[row]
            x, y = (left + right) / 2, top + .1
            if index not in self.omit:
                tokens.append(token(self.progress[index], x, y))
                tokens.append(token(f"匿名任务{index}", x, y - .033))
            paint(image, (left + .005, bottom - .052, right - .005, bottom - .005),
                  (0, 220, 50) if index in self.ready else (180, 80, 0))
        tokens.append(token("领取" if self.selected in self.ready else "已完成", .65, .825))
        for index, base in enumerate((.284, .400, .518, .635, .751)):
            value = 100 * (index + 1) + self.offset
            tokens.append(token(str(value), .806, base + .0145))
            marker = (WEEKLY_MILESTONE_X - .015, base - .022,
                      WEEKLY_MILESTONE_X + .015, base + .022)
            paint(image, marker, (70, 230, 220) if value in self.stars else (0, 110, 20))
        if self.page == "reward":
            tokens += [token("获得", .50, .393), token("绳网信用", .46, .58),
                       token("日常业绩", .535, .58), token("确认", .50, .676)]
        tokens += self.extra
        return Scene(tokens, image)

    def snapshot(self):
        self.last = self.frame()
        return self.last

    def click(self, scene, target):
        self.assert_fresh(scene, target)
        if self.before_action:
            callback, self.before_action = self.before_action, None
            callback()
        label = target.text if isinstance(target, Token) else "milestone"
        self.actions.append(("click", label))
        if label == "丽都周纪":
            self.page = "weekly"
        elif label == "日常":
            self.page = "handbook"
        elif label == "领取":
            if self.selected not in self.ready:
                raise AssertionError("Claimed an ineligible task")
            self.ready.remove(self.selected)
            self.total += 100
            if self.popup:
                self.page = "reward"
        elif label == "确认":
            self.page = "weekly"
        elif label == "milestone":
            value = min(self.stars)
            self.stars.remove(value)
            if self.popup:
                self.page = "reward"
        else:
            choices = [(index, t) for index in range(9) for t in scene.tokens
                       if t is target and t.within(WEEKLY_GRID)
                       and abs(t.center[0] - sum(WEEKLY_COLUMNS[index % 3]) / 2) < .01
                       and abs(t.center[1] - WEEKLY_ROWS[index // 3][0] - .1) < .01]
            if len(choices) != 1:
                raise AssertionError(f"Unexpected weekly input: {label}")
            self.selected = choices[0][0]
        self.last = None
        if self.after_action:
            callback, self.after_action = self.after_action, None
            callback()

    def assert_fresh(self, scene, target=None):
        if scene is not self.last:
            raise AssertionError("Used an obsolete scene")
        if isinstance(target, Token) and not any(t is target for t in scene.tokens):
            raise AssertionError("Used an obsolete OCR target")

    def key(self, name, scene):
        self.assert_fresh(scene)
        if name != "esc" or self.page not in ("weekly", "handbook"):
            raise AssertionError("Unexpected navigation")
        self.actions.append(("key", name))
        self.page = "handbook" if self.page == "weekly" else "world"
        self.last = None

    def move(self, scene, point):
        self.assert_fresh(scene)
        if self.page != "weekly":
            raise AssertionError("Scrolled outside weekly page")
        self.actions.append(("move", point))

    def scroll(self, amount):
        self.assert_fresh(self.last)
        self.actions.append(("scroll", amount))
        self.scrolls += 1
        self.last = None

    def wait(self, predicate, **kwargs):
        if self.interrupt_wait_for and self.interrupt_wait_for(self):
            self.interrupt_wait_for = None
            raise InputInterrupted("paused after returned input")
        for _ in range(4):
            frame = self.snapshot()
            if predicate(frame):
                return frame
        raise AutomationError("Replay transition timed out")

    def template(self, *args, **kwargs):
        return None

    def evidence(self, scene, label):
        self.evidence_frames.append((scene, label))

    def log(self, *args, **kwargs):
        pass

    def sleep(self, *args, **kwargs):
        pass


class WeeklyRewardsTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch("autozzz.weekly.red_notification", return_value=True),
            patch("autozzz.weekly.green_claim", side_effect=lambda scene, area: sample(scene, area) == (0, 220, 50)),
            patch("autozzz.weekly.milestone_claimable", side_effect=lambda scene, area: sample(scene, area) == (70, 230, 220)),
        ]
        self.mocks = [item.start() for item in self.patches]
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def test_earned_task_selected_then_detail_claimed_then_milestone(self):
        context = WeeklyContext()
        result = WeeklyRewardsWorkflow(context).weekly()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.ready, set())
        self.assertEqual(context.stars, set())
        clicks = [label for kind, label in context.actions if kind == "click"]
        self.assertEqual(clicks, ["600/600", "领取", "确认", "milestone", "确认"])
        self.assertEqual(context.page, "world")
        self.assertEqual(context.report.stamina_spent, 0)
        self.assertFalse(context.report.daily_verified)

    def test_claim_directly_updates_page_without_popup(self):
        context = WeeklyContext(popup=False)
        self.assertEqual(WeeklyRewardsWorkflow(context).weekly().status, "ok")
        self.assertFalse(any(action == ("click", "确认") for action in context.actions))
        self.assertEqual(context.ready, set())

    def test_entry_red_dot_opens_only_a_unique_known_handbook_entry(self):
        context = WeeklyContext(start="handbook", ready=(), stars=())
        self.assertEqual(WeeklyRewardsWorkflow(context).weekly().status, "ok")
        self.assertEqual([action for action in context.actions if action[0] == "click"],
                         [("click", "日常"), ("click", "丽都周纪")])

    def test_no_red_dot_skips_weekly_page_and_returns_main(self):
        self.mocks[0].return_value = False
        context = WeeklyContext(start="handbook", ready=(), stars=())
        self.assertEqual(WeeklyRewardsWorkflow(context).weekly().status, "ok")
        self.assertNotIn(("click", "丽都周纪"), context.actions)
        self.assertEqual(context.page, "world")

    def test_green_card_requires_complete_unique_nonzero_progress(self):
        for progress in ("0/1", "0/0", "599/600", "600/6000", "600/600领取"):
            with self.subTest(progress=progress):
                context = WeeklyContext(stars=())
                context.progress[7] = progress
                workflow = WeeklyRewardsWorkflow(context)
                self.assertEqual(workflow._claimable_cards(context.snapshot()), [])
        context = WeeklyContext(stars=())
        context.extra = [token("600/600", .3075, .75)]
        self.assertEqual(WeeklyRewardsWorkflow(context)._claimable_cards(context.snapshot()), [])

    def test_blue_completed_card_is_not_a_claim_candidate(self):
        context = WeeklyContext(ready=(), stars=())
        workflow = WeeklyRewardsWorkflow(context)
        self.assertEqual(workflow._claimable_cards(context.snapshot()), [])
        self.assertEqual(workflow.weekly().status, "ok")
        self.assertFalse(any(kind == "click" for kind, _ in context.actions))

    def test_missing_grid_progress_returns_partial_without_blind_scrolling(self):
        context = WeeklyContext(ready=(), stars=())
        context.omit = {3}
        result = WeeklyRewardsWorkflow(context).weekly()
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.actions, [])

    def test_blue_uncompleted_ocr_fraction_can_be_missing_without_blocking_earned_milestone(self):
        context = WeeklyContext(ready=())
        context.progress[6] = "214"
        result = WeeklyRewardsWorkflow(context).weekly()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.stars, set())
        self.assertNotIn(("click", "214"), context.actions)

    def test_green_card_with_unreadable_fraction_is_partial_without_guessing(self):
        context = WeeklyContext(stars=())
        context.progress[7] = "600600"
        result = WeeklyRewardsWorkflow(context).weekly()
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.actions, [])

    def test_wrong_or_duplicate_detail_progress_cannot_claim(self):
        for extra in (token("任务目标1/1", .66, .26), token("领取", .65, .825)):
            context = WeeklyContext(stars=())
            context.extra = [extra]
            workflow = WeeklyRewardsWorkflow(context)
            with self.assertRaises(AutomationError):
                workflow.weekly()
            self.assertNotIn(("click", "领取"), context.actions)

    def test_stars_bind_only_to_unique_same_row_milestone_values(self):
        context = WeeklyContext(ready=())
        workflow = WeeklyRewardsWorkflow(context)
        candidates = workflow._milestones(context.snapshot())
        self.assertEqual([value for value, _ in candidates], [300])
        context.extra = [token("300", .806, .5325)]
        self.assertEqual(workflow._milestones(context.snapshot()), [])

    def test_claimed_checks_and_unreached_points_are_not_clicked(self):
        context = WeeklyContext(ready=(), stars=())
        self.assertEqual(WeeklyRewardsWorkflow(context).weekly().status, "ok")
        self.assertFalse(any(kind == "click" for kind, _ in context.actions))
        self.assertEqual(context.scrolls, 1)

    def test_after_sent_task_click_pause_resumes_actual_claim_state(self):
        context = WeeklyContext(stars=())
        workflow = WeeklyRewardsWorkflow(context)
        context.interrupt_wait_for = lambda replay: replay.actions[-1] == ("click", "600/600")
        with self.assertRaises(InputInterrupted):
            workflow.weekly()
        self.assertEqual(workflow.weekly().status, "ok")
        self.assertEqual(context.actions.count(("click", "600/600")), 1)
        self.assertEqual(context.actions.count(("click", "领取")), 1)

    def test_after_sent_claim_pause_does_not_claim_the_same_task_twice(self):
        context = WeeklyContext(stars=())
        workflow = WeeklyRewardsWorkflow(context)
        context.interrupt_wait_for = lambda replay: replay.actions[-1] == ("click", "领取")
        with self.assertRaises(InputInterrupted):
            workflow.weekly()
        self.assertEqual(workflow.weekly().status, "ok")
        self.assertEqual(context.actions.count(("click", "领取")), 1)

    def test_after_sent_milestone_pause_does_not_repeat_the_earned_point(self):
        context = WeeklyContext(ready=())
        workflow = WeeklyRewardsWorkflow(context)
        context.interrupt_wait_for = lambda replay: replay.actions[-1] == ("click", "milestone")
        with self.assertRaises(InputInterrupted):
            workflow.weekly()
        self.assertEqual(workflow.weekly().status, "ok")
        self.assertEqual(context.actions.count(("click", "milestone")), 1)

    def test_interruptions_before_send_do_not_wait_for_unsent_transitions(self):
        for interrupted in ("丽都周纪", "600/600", "领取", "milestone", "确认"):
            with self.subTest(interrupted=interrupted):
                context = WeeklyContext(start="handbook" if interrupted == "丽都周纪" else "weekly")
                workflow = WeeklyRewardsWorkflow(context)
                original_click = context.click
                once = [True]
                def click(frame, target):
                    label = target.text if isinstance(target, Token) else "milestone"
                    if once[0] and label == interrupted:
                        once[0] = False
                        raise InputInterrupted("paused before send")
                    original_click(frame, target)
                context.click = click
                with self.assertRaises(InputInterrupted):
                    workflow.weekly()
                self.assertIsNone(workflow._weekly_pending)
                self.assertEqual(workflow.weekly().status, "ok")
                expected = 2 if interrupted == "确认" else 1
                self.assertEqual(context.actions.count(("click", interrupted)), expected)

    def test_low_confidence_anchors_candidates_and_confirm_are_rejected(self):
        context = WeeklyContext(ready=(), stars=())
        workflow = WeeklyRewardsWorkflow(context)
        frame = context.snapshot()
        frame.tokens[1] = replace(frame.tokens[1], score=.59)
        self.assertFalse(workflow._weekly_page(frame))
        context = WeeklyContext(stars=())
        frame = context.snapshot()
        frame.tokens = [replace(t, score=.59) if t.text == "600/600" else t for t in frame.tokens]
        self.assertEqual(WeeklyRewardsWorkflow(context)._claimable_cards(frame), [])
        context.page = "reward"
        frame = context.snapshot()
        frame.tokens = [replace(t, score=.59) if t.text == "确认" else t for t in frame.tokens]
        self.assertFalse(WeeklyRewardsWorkflow(context)._weekly_reward(frame))

    def test_generic_reward_with_no_weekly_scope_is_not_accepted(self):
        context = WeeklyContext(ready=(), stars=())
        workflow = WeeklyRewardsWorkflow(context)
        frame = Scene([token("获得", .50, .393), token("绳网信用", .46, .58),
                       token("确认", .50, .676)])
        self.assertFalse(workflow._weekly_reward(frame))
        self.assertEqual(workflow._confirm_reward(frame).status, "partial")
        self.assertEqual(context.actions, [])

    def test_unsent_task_or_milestone_intent_cannot_authorize_generic_reward(self):
        context = WeeklyContext(ready=(), stars=())
        frame = Scene([token("获得", .50, .393), token("绳网信用", .46, .58),
                       token("确认", .50, .676)])
        for kind in ("card", "milestone"):
            with self.subTest(kind=kind):
                workflow = WeeklyRewardsWorkflow(context)
                if kind == "card":
                    workflow._weekly_card_chosen = (2, 1, 600, 600)
                else:
                    workflow._weekly_milestone_chosen = 300
                self.assertIsNone(workflow._weekly_pending)
                self.assertFalse(workflow._weekly_reward(frame))
                self.assertEqual(workflow._confirm_reward(frame).status, "partial")
                self.assertEqual(context.actions, [])

    def test_different_milestone_values_on_the_same_row_are_partial_without_input(self):
        context = WeeklyContext(ready=())
        original_frame = context.frame
        def frame():
            result = original_frame()
            result.tokens = [replace(t, box=(t.box[0], .5215, t.box[2], .5435))
                             if t.text == "400" else t for t in result.tokens]
            return result
        context.frame = frame
        workflow = WeeklyRewardsWorkflow(context)
        self.assertEqual(workflow._milestones(context.snapshot()), [])
        result = workflow.weekly()
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.actions, [])

    def test_three_stale_green_frames_after_confirmation_do_not_repeat_task_claim(self):
        for pause_after_confirm in (False, True):
            with self.subTest(pause_after_confirm=pause_after_confirm):
                context = WeeklyContext(stars=())
                workflow = WeeklyRewardsWorkflow(context)
                original_snapshot = context.snapshot
                stale = [3]
                def snapshot():
                    if context.page == "weekly" and ("click", "确认") in context.actions and stale[0] > 0:
                        context.ready.add(7)
                        frame = original_snapshot()
                        context.ready.remove(7)
                        stale[0] -= 1
                        return frame
                    return original_snapshot()
                context.snapshot = snapshot
                if pause_after_confirm:
                    context.interrupt_wait_for = lambda replay: replay.actions[-1] == ("click", "确认")
                    with self.assertRaises(InputInterrupted):
                        workflow.weekly()
                self.assertEqual(workflow.weekly().status, "ok")
                self.assertEqual(stale[0], 0)
                self.assertEqual(context.actions.count(("click", "600/600")), 1)
                self.assertEqual(context.actions.count(("click", "领取")), 1)
                self.assertEqual(context.actions.count(("click", "确认")), 1)

    def test_unknown_page_has_no_input(self):
        context = WeeklyContext(ready=(), stars=())
        workflow = WeeklyRewardsWorkflow(context)
        workflow._open_weekly = lambda: Scene([token("开始挑战", .5, .5)])
        with self.assertRaises(AutomationError):
            workflow.weekly()
        self.assertEqual(context.actions, [])

    def test_fixed_page_anchors_cannot_be_relocated(self):
        context = WeeklyContext(ready=(), stars=())
        workflow = WeeklyRewardsWorkflow(context)
        frame = context.snapshot()
        frame.tokens[1] = replace(frame.tokens[1], box=(.05, .05, .10, .10))
        self.assertFalse(workflow._weekly_page(frame))


if __name__ == "__main__":
    unittest.main()
