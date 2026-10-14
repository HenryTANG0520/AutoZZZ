"""Claim already earned City Fund rewards, without purchases or task execution."""
from __future__ import annotations

from .models import TaskResult, Token, normalize
from .reward_vision import green_claim, red_notification
from .workflows import DailyWorkflow


FUND_TABS = (0.59, 0.0, 0.98, 0.09)
GROWTH_PERIODS = (0.60, 0.10, 0.98, 0.185)
GROWTH_ROWS = (0.77, 0.19, 0.98, 0.90)
CLAIM_ALL = (0.62, 0.915, 0.975, 0.985)
LEVEL_NOTIFICATION = (0.609, 0.002, 0.642, 0.050)
HUD_ICON = (0.822, 0.014, 0.889, 0.105)
HUD_ICON_BODY = (0.842, 0.045, 0.870, 0.083)
HUD_NOTIFICATION = (0.831, 0.015, 0.858, 0.053)
HUD_KEY = (0.837, 0.097, 0.875, 0.145)
REWARD_TITLE = (0.40, 0.32, 0.60, 0.46)
REWARD_CONFIRM = (0.40, 0.61, 0.60, 0.73)
SELECTION_DIALOG = (0.18, 0.19, 0.94, 0.89)
PERIOD_LABELS = ("今日任务", "本周任务", "本期任务")
FORBIDDEN_TARGETS = ("购买", "解锁", "兑换", "选择奖励", "支付", "充值")


class CityFundRewardsWorkflow(DailyWorkflow):
    def __init__(self, context):
        super().__init__(context)
        self._city_stage = "enter"
        self._city_pending = None
        self._city_period = 0
        self._city_claims = 0
        self._city_complete = None

    def _city_tokens(self, scene, label, area):
        threshold = getattr(self.c.settings, "ocr_confidence", .60)
        return [t for t in scene.tokens if t.score >= threshold and t.within(area)
                and normalize(t.text) == normalize(label)]

    def _city_unique(self, scene, label, area):
        targets = self._city_tokens(scene, label, area)
        return targets[0] if len(targets) == 1 else None

    def _city_page(self, scene):
        # The three local tabs identify the fund page. Its changing level/XP
        # panel is irrelevant: level rewards are gated by the tab's red dot.
        return all(self._city_unique(scene, label, FUND_TABS) for label in
                   ("等级回馈", "成长任务", "城募赠礼"))

    def _city_tab_selected(self, scene, label, area):
        target = self._city_unique(scene, label, area)
        return bool(target and green_claim(scene, target.box))

    def _city_growth_page(self, scene):
        return (self._city_page(scene)
                and self._city_tab_selected(scene, "成长任务", FUND_TABS)
                and all(self._city_unique(scene, label, GROWTH_PERIODS)
                        for label in PERIOD_LABELS)
                and sum(self._city_tab_selected(scene, label, GROWTH_PERIODS)
                        for label in PERIOD_LABELS) == 1
                and scene.has(("领取", "已领取", "前往", "EMPTY", "-EMPTY-"), GROWTH_ROWS, exact=True))

    def _city_growth(self, scene):
        return (self._city_growth_page(scene) and self._city_period < len(PERIOD_LABELS)
                and self._city_tab_selected(scene, PERIOD_LABELS[self._city_period], GROWTH_PERIODS))

    def _city_level(self, scene):
        return self._city_page(scene) and self._city_tab_selected(scene, "等级回馈", FUND_TABS)

    def _city_all_claim(self, scene):
        target = self._city_unique(scene, "全部领取", CLAIM_ALL)
        return target if target and green_claim(scene, target.box) else None

    def _city_popup_visible(self, scene):
        return bool(self._city_tokens(scene, "获得", REWARD_TITLE)
                    or self._city_tokens(scene, "获得奖励", REWARD_TITLE))

    def _city_selection(self, scene):
        # The ordinary top-left "选择奖励" navigation remains untouched.
        return scene.has(("选择奖励", "请选择奖励", "请选择一个奖励", "奖励自选", "奖励选择"),
                         SELECTION_DIALOG, exact=True)

    def _city_ready(self, scene):
        pending = self._city_pending
        if pending == "menu":
            return self._menu(scene)
        if pending == "world":
            return self._world(scene)
        if pending == "fund":
            return self._city_page(scene)
        if pending == "growth":
            return self._city_growth_page(scene)
        if pending == "period":
            return self._city_growth(scene)
        if pending in ("growth_claim", "level_claim"):
            if self._city_selection(scene) or self._city_popup_visible(scene):
                return True
            return ((self._city_growth(scene) and not self._city_all_claim(scene))
                    if pending == "growth_claim" else
                    (self._city_level(scene) and not red_notification(scene, LEVEL_NOTIFICATION)))
        if pending == "confirmed_growth":
            return self._city_growth(scene) and not self._city_all_claim(scene)
        if pending == "level":
            return self._city_level(scene)
        if pending == "confirmed_level":
            return self._city_level(scene) and not red_notification(scene, LEVEL_NOTIFICATION)
        if pending == "exit":
            return self._main(scene)
        return True

    def _city_click(self, scene, target):
        if isinstance(target, Token):
            text = normalize(target.text)
            if any(normalize(label) in text for label in FORBIDDEN_TARGETS):
                return self._partial("city_fund", scene, "城募目标涉及购买、解锁、兑换或奖励自选，需手动处理。")
        self.c.click(scene, target)
        return None

    def _city_enter(self, scene):
        if self._city_page(scene):
            self._city_stage = "growth_tab"
            return None
        if self._menu(scene):
            entry = self._city_unique(scene, "丽都城募", (0, 0.08, 1, .94))
            if entry:
                self._city_click(scene, entry)
                self._city_stage, self._city_pending = "growth_tab", "fund"
                return None
            # Close only an identified menu before using the observed world HUD.
            self.c.key("esc", scene)
            self._city_stage, self._city_pending = "hud_entry", "world"
            return None
        if self._world(scene):
            # Prefer a unique named menu entry; the same account may bind F2 differently.
            self.c.key("esc", scene)
            self._city_stage, self._city_pending = "menu_entry", "menu"
            return None
        self.ensure_main()
        self._city_stage = "enter"
        return None

    def _city_accept_reward(self, scene, destination):
        confirms = self._city_tokens(scene, "确认", REWARD_CONFIRM)
        if (not self._city_popup_visible(scene) or len(confirms) != 1
                or self._city_selection(scene)):
            return self._partial("city_fund", scene, "城募奖励弹窗没有唯一且位置匹配的确认，需手动处理。")
        self.c.evidence(scene, "city_fund_reward")
        self._city_click(scene, confirms[0])
        self._city_stage = destination
        self._city_pending = "confirmed_growth" if destination == "next_period" else "confirmed_level"
        return None

    def city_fund(self) -> TaskResult:
        if self._city_complete is not None:
            return self._city_complete
        scene = self._snapshot()
        # Each iteration either advances a finite stage or makes one observed input.
        for _ in range(35):
            if self._city_pending:
                scene = self._wait(self._city_ready, "城募操作后的已识别页面")
                self._city_pending = None
            if (self._city_popup_visible(scene) or self._city_selection(scene)) and self._city_stage not in (
                    "growth_result", "level_result"):
                return self._partial("city_fund", scene, "存在未经本次城募领奖绑定的弹窗，需用户先处理。")
            if self._city_stage == "enter":
                result = self._city_enter(scene)
            elif self._city_stage == "menu_entry":
                result = self._city_enter(scene)
            elif self._city_stage == "hud_entry":
                hints = [t for t in scene.tokens if t.within(HUD_KEY) and
                         normalize(t.text) in ("f1", "f2", "f3", "f4") and
                         t.score >= getattr(self.c.settings, "ocr_confidence", .60)]
                if (not self._world(scene) or len(hints) != 1
                        or not green_claim(scene, HUD_ICON_BODY)):
                    return self._partial("city_fund", scene, "未找到唯一城募名称入口或已校准且带红点的城募图标。")
                if not red_notification(scene, HUD_NOTIFICATION):
                    self._city_complete = self._ok("city_fund", scene, "城募入口没有奖励红点，当前无需领取。")
                    return self._city_complete
                # World HUD icons are not clickable while the game's camera
                # owns the pointer; use only this verified icon's shown binding.
                self.c.key(normalize(hints[0].text), scene)
                self._city_stage, self._city_pending = "growth_tab", "fund"
                result = None
            elif self._city_stage == "growth_tab":
                if not self._city_page(scene):
                    return self._partial("city_fund", scene, "城募页的三标签和等级信息尚未核实。")
                if self._city_growth_page(scene):
                    self._city_stage = "select_period"
                    result = None
                else:
                    target = self._city_unique(scene, "成长任务", FUND_TABS)
                    self._city_click(scene, target)
                    self._city_stage, self._city_pending = "select_period", "growth"
                    result = None
            elif self._city_stage == "select_period":
                if not self._city_page(scene):
                    return self._partial("city_fund", scene, "成长任务切换前页面身份未核实。")
                target = self._city_unique(scene, PERIOD_LABELS[self._city_period], GROWTH_PERIODS)
                if target is None:
                    return self._partial("city_fund", scene, "成长任务周期标签缺失或重复。")
                if self._city_growth(scene):
                    self._city_stage = "growth_claim"
                else:
                    self._city_click(scene, target)
                    self._city_stage, self._city_pending = "growth_claim", "period"
                result = None
            elif self._city_stage == "growth_claim":
                if not self._city_growth(scene):
                    return self._partial("city_fund", scene, "成长任务周期和奖励状态尚未核实。")
                if len(self._city_tokens(scene, "全部领取", CLAIM_ALL)) > 1:
                    return self._partial("city_fund", scene, "成长任务的全部领取按钮重复，未发送领取。")
                target = self._city_all_claim(scene)
                if target:
                    self.c.evidence(scene, "city_fund_growth_claimable")
                    self._city_click(scene, target)
                    self._city_claims += 1
                    self._city_stage, self._city_pending = "growth_result", "growth_claim"
                elif any(green_claim(scene, t.box) for t in self._city_tokens(scene, "领取", GROWTH_ROWS)):
                    return self._partial("city_fund", scene, "存在可领取成长奖励，但全部领取尚未核实。")
                else:
                    self._city_stage = "next_period"
                result = None
            elif self._city_stage in ("growth_result", "level_result"):
                if self._city_selection(scene):
                    return self._partial("city_fund", scene, "城募奖励需要用户自选，未替用户选择。")
                destination = "next_period" if self._city_stage == "growth_result" else "exit"
                if self._city_popup_visible(scene):
                    result = self._city_accept_reward(scene, destination)
                else:
                    self._city_stage = destination
                    result = None
            elif self._city_stage == "next_period":
                self._city_period += 1
                self._city_stage = "select_period" if self._city_period < len(PERIOD_LABELS) else "level_check"
                result = None
            elif self._city_stage == "level_check":
                if not self._city_page(scene):
                    return self._partial("city_fund", scene, "复查等级奖励前城募页身份未核实。")
                if not red_notification(scene, LEVEL_NOTIFICATION):
                    self._city_stage = "exit"
                else:
                    target = self._city_unique(scene, "等级回馈", FUND_TABS)
                    self._city_click(scene, target)
                    self._city_stage, self._city_pending = "level_claim", "level"
                result = None
            elif self._city_stage == "level_claim":
                if not self._city_level(scene):
                    return self._partial("city_fund", scene, "等级回馈页尚未核实。")
                target = self._city_all_claim(scene)
                if target is None:
                    return self._partial("city_fund", scene, "等级回馈仍有红点，但没有唯一亮色全部领取；未点击解锁或购买。")
                self.c.evidence(scene, "city_fund_level_claimable")
                self._city_click(scene, target)
                self._city_claims += 1
                self._city_stage, self._city_pending = "level_result", "level_claim"
                result = None
            elif self._city_stage == "exit":
                if not self._city_page(scene):
                    return self._partial("city_fund", scene, "退出前城募页身份未核实。")
                self.c.key("esc", scene)
                self._city_stage, self._city_pending = "complete", "exit"
                result = None
            elif self._city_stage == "complete":
                if not self._main(scene):
                    return self._partial("city_fund", scene, "城募奖励检查后尚未返回大世界。")
                self._city_complete = self._ok("city_fund", scene,
                    f"已检查城募成长任务与等级回馈，完成 {self._city_claims} 次全部领取并返回主场景。")
                return self._city_complete
            else:
                return self._partial("city_fund", scene, "城募操作阶段无法确认，未继续输入。")
            if isinstance(result, TaskResult):
                return result
            if not self._city_pending:
                scene = self._snapshot()
        return self._partial("city_fund", scene, "城募领奖达到有限步骤上限，未继续输入。")
