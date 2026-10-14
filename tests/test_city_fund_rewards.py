"""Sanitized City Fund scene replay; no game, account data, or full screenshots."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

from autozzz.city_fund import CityFundRewardsWorkflow, HUD_KEY, LEVEL_NOTIFICATION, PERIOD_LABELS
from autozzz.models import AutomationError, InputInterrupted, RunReport, Scene, Token, normalize


FIXTURES = Path(__file__).parent / "fixtures" / "rewards"
PROVENANCE = {item["file"]: item for item in json.loads(
    (FIXTURES / "provenance.json").read_text(encoding="utf-8"))["entries"]}


def token(text, x, y, width=.08, height=.025):
    return Token(text, .98, (x-width/2, y-height/2, x+width/2, y+height/2))


def paint(image, area, bgr=(0, 235, 195)):
    x1, y1, x2, y2 = area
    image[round(y1*1080):round(y2*1080), round(x1*1920):round(x2*1920)] = bgr


def paste(image, name):
    crop = cv2.imread(str(FIXTURES / name))
    if crop is None:
        raise AssertionError("Missing sanitized reward-control crop: " + name)
    x1, y1, x2, y2 = PROVENANCE[name]["normalized_source_box"]
    left, top, right, bottom = round(x1*1920), round(y1*1080), round(x2*1920), round(y2*1080)
    image[top:bottom, left:right] = cv2.resize(crop, (right-left, bottom-top))


def fund_page(tab="等级回馈", period=0, claim=False, level_dot=False,
              duplicate_all=False, individual=False, grey_all=False, max_level=False):
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    tabs = [token("等级回馈", .665, .048), token("成长任务", .789, .048),
            token("城募赠礼", .904, .048)]
    tokens = tabs + [token("丽都城募", .102, .49),
                     token("本期城募经验已满" if max_level else "本周经验上限", .095, .76),
                     token("购买等级", .11, .84), token("解锁丽都城募", .88, .952, .145)]
    for t in tabs:
        if t.text == tab:
            paint(image, t.box)
    if level_dot:
        paste(image, "city_level_notification.png")
        # The retained P8 crop is selected; preserve the current mocked tab state.
        if tab != "等级回馈":
            paint(image, tabs[0].box, (75, 75, 75))
    if tab == "成长任务":
        for index, label in enumerate(PERIOD_LABELS):
            t = token(label, .68 + .109 * index, .143)
            tokens.append(t)
            if index == period:
                paint(image, t.box)
        row = token("领取" if claim or individual else "已领取", .885, .38)
        tokens.append(row)
        if claim or individual:
            paint(image, row.box)
        if claim:
            paste(image, "city_claim_all_enabled.png")
            tokens.append(token("全部领取", .897, .953, .105, .041))
        elif grey_all:
            tokens.append(token("全部领取", .897, .953, .105, .041))
            paint(image, tokens[-1].box, (90, 90, 90))
    elif claim:
        paste(image, "city_level_claim_all_enabled.png")
        tokens.append(token("全部领取", .713, .953, .105, .041))
    if duplicate_all:
        tokens.append(token("全部领取", .81, .953, .09))
        paint(image, tokens[-1].box)
    return Scene(tokens, image)


def popup(duplicate=False, selection=False):
    tokens = [token("获得", .5, .391), token("城募经验", .5, .58),
              token("确认", .505, .677)]
    if duplicate:
        tokens.append(token("确认", .55, .677))
    if selection:
        tokens.append(token("请选择奖励", .5, .45))
    return Scene(tokens, np.zeros((1080, 1920, 3), dtype=np.uint8))


def world(hint="F2", dot=False, green=True, ambiguous=False):
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    tokens = [token("星期四", .12, .05), token("按自己的步调度过这一天", .18, .2),
              token(hint, .855, .118, .025)]
    if ambiguous:
        tokens.append(token("F1", .851, .121, .023))
    if dot:
        cv2.fillPoly(image, [np.array([[1611, 30], [1632, 30], [1625, 49]])], (0, 0, 255))
        cv2.circle(image, (1623, 35), 2, (0, 0, 0), -1)
    if green:
        paint(image, (.845, .045, .870, .095))
    return Scene(tokens, image)


def menu(entry=True, duplicate=False):
    tokens = [token("快捷手册", .65, .6), token("邮件", .75, .6)]
    if entry:
        tokens.append(token("丽都城募", .80, .60))
    if duplicate:
        tokens.append(token("丽都城募", .85, .75))
    return Scene(tokens, np.zeros((1080, 1920, 3), dtype=np.uint8))


class FundReplay:
    def __init__(self, initial=None, earned=(True, False, False), level_after=True):
        self.frame = initial or fund_page()
        self.settings = SimpleNamespace(ocr_confidence=.60, transition_timeout=45,
                                        auto_launch=False, server="cn")
        self.report = RunReport()
        self.last = None
        self.inputs = []
        self.evidences = []
        self.earned = list(earned)
        self.level_earned = False
        self.level_after = level_after
        self.period = 0
        self.return_tab = None
        self.menu_entry = True
        self.menu_duplicate = False
        self.world_frame = world()
        self.duplicate_confirm = False
        self.selection = False
        self.duplicate_all = False
        self.individual_only = False
        self.grey_all = False
        self.before_claim_interrupt = False
        self.after_claim_interrupt = False
        self.interrupted = False
        self.response_delay = 0
        self.delayed_frame = None
        self.delay_steps = 0
        self.max_level = False
        self.max_after_growth = False
        self.growth_popup = True
        self.hide_level_info = False

    def snapshot(self):
        self.last = Scene([Token(t.text, t.score, t.box) for t in self.frame.tokens],
                          self.frame.image.copy() if self.frame.image is not None else None)
        if self.hide_level_info:
            self.last.tokens = [t for t in self.last.tokens if not t.within((.015, .43, .19, .88))]
        return self.last

    def growth(self):
        return fund_page("成长任务", self.period, self.earned[self.period], self.level_earned,
                         duplicate_all=self.duplicate_all, individual=self.individual_only,
                         grey_all=self.grey_all, max_level=self.max_level)

    def level(self):
        return fund_page(claim=self.level_earned, level_dot=self.level_earned, max_level=self.max_level)

    def _fresh(self, scene):
        if scene is not self.last:
            raise AssertionError("Used stale scene for input")

    def click(self, scene, target):
        self._fresh(scene)
        if isinstance(target, Token):
            if not any(target is t for t in scene.tokens):
                raise AssertionError("Used old Token instead of fresh OCR target")
            label = normalize(target.text)
        else:
            label = "city_hud"
        if label == "全部领取" and self.before_claim_interrupt and not self.interrupted:
            self.interrupted = True
            raise InputInterrupted("Pause occurred before click was sent")
        if any(word in label for word in ("购买", "解锁", "兑换", "选择奖励")):
            raise AssertionError("Attempted purchase/choice target: " + label)
        self.inputs.append(("click", label))
        if label in ("丽都城募", "city_hud"):
            self.frame = self.level()
        elif label == "成长任务":
            self.frame = self.growth()
        elif label in PERIOD_LABELS:
            self.period = PERIOD_LABELS.index(label)
            self.frame = self.growth()
        elif label == "等级回馈":
            self.frame = self.level()
        elif label == "全部领取":
            if scene.has("今日任务"):
                self.earned[self.period] = False
                self.level_earned = self.level_after
                self.max_level = self.max_level or self.max_after_growth
                self.return_tab = "成长任务"
            else:
                self.level_earned = False
                self.return_tab = "等级回馈"
            next_frame = popup(self.duplicate_confirm, self.selection)
            if self.return_tab == "成长任务" and not self.growth_popup:
                next_frame = self.growth()
            if self.response_delay:
                self.delayed_frame = next_frame
                self.frame = scene
            else:
                self.frame = next_frame
        elif label == "确认":
            self.frame = self.growth() if self.return_tab == "成长任务" else self.level()
        else:
            raise AssertionError("Unexpected target " + label)

    def key(self, name, scene, **kwargs):
        self._fresh(scene)
        if name != "esc":
            hints = [normalize(t.text) for t in scene.tokens if t.within(HUD_KEY)
                     and normalize(t.text) in ("f1", "f2", "f3", "f4")]
            if hints != [name]:
                raise AssertionError("Shortcut did not match the uniquely observed HUD hint")
            self.inputs.append(("key", name))
            self.frame = self.level()
            return
        self.inputs.append(("key", name))
        workflow = CityFundRewardsWorkflow(self)
        if workflow._world(scene):
            self.frame = menu(self.menu_entry, self.menu_duplicate)
        else:
            self.frame = self.world_frame

    def wait(self, predicate, **kwargs):
        if self.after_claim_interrupt and self.return_tab and not self.interrupted:
            self.interrupted = True
            raise InputInterrupted("Pause discarded the post-click snapshot")
        for _ in range(8):
            scene = self.snapshot()
            if predicate(scene):
                return scene
            if self.delayed_frame is not None:
                self.delay_steps += 1
                if self.delay_steps >= self.response_delay:
                    self.frame = self.delayed_frame
                    self.delayed_frame = None
        raise AutomationError("Replay transition timed out")

    def template(self, *args, **kwargs):
        return None

    def evidence(self, scene, name):
        self.evidences.append(name)

    def log(self, text):
        pass

    def sleep(self, seconds):
        pass


class CityFundRewardTests(unittest.TestCase):
    def test_reaching_max_level_during_growth_claim_still_claims_level_reward(self):
        # 2026-10-14 capture: growth's All Claim changed Lv.59 to MAX/Lv.60,
        # replaced the weekly-cap label, and added the level-tab notification.
        for has_popup in (False, True):
            with self.subTest(growth_popup=has_popup):
                context = FundReplay()
                context.max_after_growth = True
                context.growth_popup = has_popup
                result = CityFundRewardsWorkflow(context).city_fund()
                self.assertEqual(result.status, "ok", result.detail)
                self.assertTrue(context.max_level)
                self.assertEqual(context.inputs.count(("click", "全部领取")), 2)
                self.assertIn(("click", "本周任务"), context.inputs)
                self.assertIn(("click", "本期任务"), context.inputs)
                self.assertIn(("click", "等级回馈"), context.inputs)
                self.assertFalse(context.level_earned)
                self.assertTrue(CityFundRewardsWorkflow(context)._world(context.snapshot()))

    def test_restart_from_max_level_growth_page_claims_existing_level_reward(self):
        context = FundReplay(fund_page("成长任务", max_level=True, level_dot=True),
                             earned=(False, False, False))
        context.max_level = context.level_earned = True
        result = CityFundRewardsWorkflow(context).city_fund()
        self.assertEqual(result.status, "ok", result.detail)
        self.assertEqual(context.inputs.count(("click", "全部领取")), 1)
        self.assertIn(("click", "等级回馈"), context.inputs)

    def test_max_level_without_rewards_returns_to_world(self):
        context = FundReplay(fund_page(max_level=True), earned=(False, False, False))
        context.max_level = True
        result = CityFundRewardsWorkflow(context).city_fund()
        self.assertEqual(result.status, "ok", result.detail)
        self.assertNotIn(("click", "全部领取"), context.inputs)
        self.assertNotIn(("click", "等级回馈"), context.inputs)

    def test_level_rewards_depend_on_red_dot_without_any_left_level_info(self):
        context = FundReplay(fund_page("成长任务", level_dot=True), earned=(False, False, False))
        context.level_earned = True
        context.hide_level_info = True
        result = CityFundRewardsWorkflow(context).city_fund()
        self.assertEqual(result.status, "ok", result.detail)
        self.assertIn(("click", "等级回馈"), context.inputs)
        self.assertEqual(context.inputs.count(("click", "全部领取")), 1)
        self.assertFalse(context.level_earned)

    def test_city_identity_requires_unique_local_tabs_before_checking_their_red_dot(self):
        for invalid in ("missing_tab", "duplicate_tab", "wrong_tab_position"):
            with self.subTest(invalid=invalid):
                frame = fund_page(max_level=True)
                if invalid == "missing_tab":
                    frame.tokens = [t for t in frame.tokens if t.text != "成长任务"]
                elif invalid == "duplicate_tab":
                    frame.tokens.append(token("成长任务", .81, .06))
                else:
                    frame.tokens = [token(t.text, .5, .5) if t.text == "成长任务"
                                    else t for t in frame.tokens]
                self.assertFalse(CityFundRewardsWorkflow(FundReplay(frame))._city_page(frame))

    def test_growth_then_new_level_rewards_claimed_and_return_to_world(self):
        context = FundReplay()
        result = CityFundRewardsWorkflow(context).city_fund()
        self.assertEqual((result.task, result.status), ("city_fund", "ok"))
        self.assertEqual(context.inputs.count(("click", "全部领取")), 2)
        self.assertEqual(context.inputs.count(("click", "确认")), 2)
        self.assertLess(context.inputs.index(("click", "成长任务")),
                        context.inputs.index(("click", "等级回馈")))
        self.assertTrue(CityFundRewardsWorkflow(context)._world(context.snapshot()))

    def test_all_three_existing_period_rewards_claimed_without_performing_tasks(self):
        context = FundReplay(earned=(True, True, True))
        result = CityFundRewardsWorkflow(context).city_fund()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.inputs.count(("click", "全部领取")), 4)
        self.assertEqual(context.earned, [False, False, False])
        self.assertFalse(any(label in ("前往", "领取") for _, label in context.inputs))

    def test_no_rewards_exits_without_level_or_claim_click(self):
        context = FundReplay(earned=(False, False, False), level_after=False)
        result = CityFundRewardsWorkflow(context).city_fund()
        self.assertEqual(result.status, "ok")
        self.assertNotIn(("click", "等级回馈"), context.inputs)
        self.assertNotIn(("click", "全部领取"), context.inputs)

    def test_growth_page_already_on_weekly_still_checks_today(self):
        context = FundReplay(fund_page("成长任务", period=1))
        context.period = 1
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "ok")
        self.assertIn(("click", "今日任务"), context.inputs)

    def test_purchase_and_unlock_labels_do_not_block_free_claims(self):
        context = FundReplay()
        self.assertTrue(CityFundRewardsWorkflow(context)._city_page(context.snapshot()))
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "ok")
        self.assertFalse(any("解锁" in label or "购买" in label for _, label in context.inputs))

    def test_duplicate_all_claims_are_rejected_without_any_claim(self):
        context = FundReplay()
        context.duplicate_all = True
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "partial")
        self.assertNotIn(("click", "全部领取"), context.inputs)

    def test_disabled_grey_all_is_never_clicked(self):
        context = FundReplay(earned=(False, False, False), level_after=False)
        context.grey_all = True
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "ok")
        self.assertNotIn(("click", "全部领取"), context.inputs)

    def test_individual_available_but_all_missing_needs_user(self):
        context = FundReplay(earned=(False, False, False))
        context.individual_only = True
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "partial")
        self.assertNotIn(("click", "全部领取"), context.inputs)
        self.assertNotIn(("click", "领取"), context.inputs)

    def test_ambiguous_reward_confirm_is_not_clicked_or_closed(self):
        context = FundReplay()
        context.duplicate_confirm = True
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "partial")
        self.assertNotIn(("click", "确认"), context.inputs)
        self.assertNotIn(("key", "esc"), context.inputs)

    def test_reward_selection_stops_without_confirm_or_choice(self):
        context = FundReplay()
        context.selection = True
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "partial")
        self.assertNotIn(("click", "确认"), context.inputs)
        self.assertNotIn(("key", "esc"), context.inputs)

    def test_post_send_pause_resumes_without_repeating_all_claim(self):
        context = FundReplay(level_after=False)
        context.after_claim_interrupt = True
        workflow = CityFundRewardsWorkflow(context)
        with self.assertRaises(InputInterrupted):
            workflow.city_fund()
        self.assertEqual(workflow.city_fund().status, "ok")
        self.assertEqual(context.inputs.count(("click", "全部领取")), 1)

    def test_pre_send_pause_recaptures_target_before_single_claim(self):
        context = FundReplay(level_after=False)
        context.before_claim_interrupt = True
        workflow = CityFundRewardsWorkflow(context)
        with self.assertRaises(InputInterrupted):
            workflow.city_fund()
        self.assertEqual(workflow.city_fund().status, "ok")
        self.assertEqual(context.inputs.count(("click", "全部领取")), 1)

    def test_network_delay_waits_read_only_without_replaying_claim(self):
        context = FundReplay(level_after=False)
        context.response_delay = 3
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "ok")
        self.assertEqual(context.inputs.count(("click", "全部领取")), 1)
        self.assertEqual(context.delay_steps, 3)

    def test_menu_named_entry_preferred_and_no_function_key_assumption(self):
        context = FundReplay(world("F4"), earned=(False, False, False), level_after=False)
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "ok")
        self.assertIn(("click", "丽都城募"), context.inputs)
        self.assertFalse(any(action == "key" and label.startswith("f") for action, label in context.inputs))

    def test_red_hud_icon_with_f4_hint_uses_observed_binding_not_f2(self):
        context = FundReplay(world("F4", True), earned=(False, False, False), level_after=False)
        context.menu_entry = False
        context.world_frame = world("F4", True)
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "ok")
        self.assertIn(("key", "f4"), context.inputs)
        self.assertNotIn(("click", "city_hud"), context.inputs)
        self.assertNotIn(("key", "f2"), context.inputs)

    def test_hud_requires_unique_hint_and_identified_green_icon(self):
        for frame in (world("F2", True, False), world("F2", True, True, True)):
            with self.subTest(tokens=frame.text):
                context = FundReplay(frame)
                context.menu_entry = False
                context.world_frame = frame
                self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "partial")
                self.assertNotIn(("click", "city_hud"), context.inputs)

    def test_identified_hud_without_red_notification_is_verified_without_opening_fund(self):
        frame = world("F2", False)
        context = FundReplay(frame)
        context.menu_entry = False
        context.world_frame = frame
        result = CityFundRewardsWorkflow(context).city_fund()
        self.assertEqual(result.status, "ok")
        self.assertIn("没有奖励红点", result.detail)
        self.assertFalse(any(action == "key" and label.startswith("f") for action, label in context.inputs))
        self.assertNotIn(("click", "city_hud"), context.inputs)

    def test_missing_pixels_never_enable_claim_or_selected_tab(self):
        frame = fund_page("成长任务", claim=True)
        frame.image = None
        workflow = CityFundRewardsWorkflow(FundReplay(frame))
        self.assertIsNone(workflow._city_all_claim(frame))
        self.assertFalse(workflow._city_growth(frame))

    def test_top_tab_text_elsewhere_cannot_establish_city_page(self):
        frame = fund_page()
        frame.tokens = [Token(t.text, t.score, (.1, .3, .2, .4))
                        if t.text in ("等级回馈", "成长任务", "城募赠礼") else t for t in frame.tokens]
        self.assertFalse(CityFundRewardsWorkflow(FundReplay(frame))._city_page(frame))

    def test_preexisting_popup_blocks_background_tabs_without_input(self):
        frame = fund_page()
        frame.tokens += popup().tokens
        context = FundReplay(frame)
        self.assertEqual(CityFundRewardsWorkflow(context).city_fund().status, "partial")
        self.assertEqual(context.inputs, [])

    def test_actual_level_crop_only_enables_exact_all_claim_not_unlock(self):
        frame = fund_page(claim=True, level_dot=True)
        workflow = CityFundRewardsWorkflow(FundReplay(frame))
        self.assertEqual(workflow._city_all_claim(frame).text, "全部领取")
        unlock = frame.find("解锁丽都城募", exact=True)
        self.assertEqual(workflow._city_click(frame, unlock).status, "partial")
        self.assertEqual(workflow.c.inputs, [])


if __name__ == "__main__":
    unittest.main()
