"""Evidence-driven, non-combat daily actions on the visible game window.

Coordinates are normalized from the 1920 x 1080 reference client area.
Missing controls return partial results; unrecognized pages stop all input.
"""
from __future__ import annotations

import re
import unicodedata

from .models import AutomationError, InputInterrupted, Scene, ScreenshotNotReady, Stopped, TaskResult, normalize
from .policy import coffee_is_free, guard_scene


def roi(x1, y1, x2, y2):
    return (x1 / 1920, y1 / 1080, x2 / 1920, y2 / 1080)


def point(x, y):
    return (x / 1920, y / 1080)


MAP_AREAS = roi(0, 90, 1920, 930)
MAP_POINTS = roi(70, 975, 1848, 1075)
MAP_CONFIRM = roi(1024, 580, 1232, 730)
TABS = roi(708, 115, 1636, 240)
DAILY_REWARDS = roi(400, 225, 1650, 380)
DAILY_TASKS = roi(420, 330, 1710, 930)
DAILY_COLUMNS = (roi(805, 405, 1115, 930), roi(1128, 405, 1438, 930),
                 roi(1450, 405, 1695, 930))
TASK_LABELS = {
    "coffee": ("品尝1次咖啡", "品尝一次咖啡", "喝咖啡", "喝杯咖啡"),
    "scratch": ("进行一次刮刮乐", "刮刮乐", "占卜或抽取饼铺盲盒", "刮刮卡", "刮取"),
    "video": ("开启今日录像店", "今日录像店经营", "开始营业", "录像店营业", "营业一次"),
}
DIALOG = roi(650, 250, 1300, 790)
OPTIONS = roi(1400, 530, 1915, 835)
REGIONS = ("录像店", "六分街", "光映广场", "澄辉坪", "布亚斯特城区",
           "斯科特哨站", "HIA俱乐部", "黑雁工地旧址", "芭莱大厦前", "野火镇",
           "奥蒙德研究站", "绮梦度假村", "热望角", "HAND总部", "厄匹斯港", "星环")
PLACES = ("报刊亭", "咖啡店", "柜台", "房间", "HDD", "喵吉长官", "拉面店", "改装店")
MENU_LABELS = ("快捷手册", "绳网", "邮件", "仓库", "代理人", "设置")
HANDBOOK_TABS = ("训练", "目标", "日常", "作战", "战术")
START_BLOCKERS = ("密码", "验证码", "扫码", "同意", "更新", "下载", "password",
                  "verification code", "captcha", "scan QR", "update", "download", "agree to terms")
MONTHLY_TODAY = roi(650, 345, 1250, 540)
MONTHLY_DAYS = roi(650, 515, 1250, 675)
MONTHLY_AMOUNT = roi(650, 570, 1250, 735)
MONTHLY_PURCHASE = ("购买", "续费", "续购", "充值", "支付", "兑换", "售价", "purchase", "buy",
                    "renew", "top up", "payment")
MONTHLY_CLAIM_LIMIT = 3
PROMOTER_CARDS = roi(520, 130, 1800, 965)
PROMOTER_SLOT = roi(790, 550, 1040, 950)
PROMOTER_PREVIEW = roi(25, 115, 465, 210)
PROMOTER_CONFIRM = roi(1510, 970, 1880, 1075)
PROMOTER_SCROLL_POINT = point(1330, 560)
PROMOTER_SCROLL_LIMIT = 12


class DailyWorkflow:
    def __init__(self, context):
        self.c = context
        self._task_before = {}
        self._startup_checked = False
        self._entry_transition_pending = False
        self._daily_evidence_recorded = False
        self._coffee_ordered = False
        self._coffee_reward_claimed = False
        self._video_opened = False
        self._video_start_pending = False
        self._video_return_pending = False
        self._monthly_claim_clicks = 0
        self._monthly_claim_pending = False

    def _snapshot(self):
        scene = self.c.snapshot()
        guard_scene(scene)
        return scene

    def _template(self, scene, name, area, threshold=0.88):
        return self.c.template(scene, name, area, threshold)

    def _world(self, scene):
        return bool(
            self._template(scene, "world_menu", roi(1440, 28, 1689, 118))
            or self._template(scene, "world_map", roi(1550, 160, 1920, 500))
            or (scene.has("F2", roi(1300, 0, 1920, 200), exact=True)
                and scene.has("Tab", roi(0, 900, 500, 1080), exact=True))
            or (scene.has("星期", roi(0, 0, 420, 140))
                and scene.has("按自己的步调度过这一天", roi(0, 90, 550, 360)))
        )

    def _menu(self, scene):
        return sum(scene.has(label, exact=True) for label in MENU_LABELS) >= 2

    def _battlepass(self, scene):
        return scene.has("丽都城募") and sum(scene.has(label, exact=True) for label in
            ("等级回馈", "成长任务", "城募赠礼")) >= 2

    def _weekly_screen(self, scene):
        return (scene.has("积分特赏", roi(1480, 130, 1835, 220), exact=True)
                and scene.has("累计获取积分", roi(1490, 865, 1835, 940))
                and scene.has("三格连线额外奖励", roi(105, 120, 1080, 225)))

    def _main(self, scene):
        # The monthly ticket overlays the world; its background HUD is not ready for input.
        return not self._monthly_card_visible(scene) and (self._world(scene) or self._menu(scene))

    def _map(self, scene):
        names = sum(scene.has(label, MAP_AREAS, exact=True) for label in REGIONS)
        return (names >= 2 and (scene.has(PLACES, MAP_POINTS) or sum(
            scene.has(label, MAP_POINTS, exact=True) for label in
            ("吼吼饼铺", "片刻闲", "录像店营业点", "未来邦布研究室")) >= 2)) or (
            scene.has("快捷导航", roi(1450, 0, 1850, 100), exact=True)
            and scene.has("区域信息", exact=True) and scene.has("区域切换", exact=True))

    def _interaction(self, scene):
        # This is a visible key hint, not proof of NPC range or an opened dialog.
        return scene.has("F", exact=True) or bool(self._template(
            scene, "interact_f", roi(1400, 990, 1500, 1070)))

    def _bakery_screen(self, scene):
        return scene.has(("每日可领取一次", "查看今天", "今日已领取同类型奖励")) or self._bakery_card(scene)

    def _bakery_card(self, scene):
        return self._bakery_card_confirm(scene) is not None

    def _bakery_card_confirm(self, scene):
        # The card's footer closes the bakery sequence, regardless of its prize.
        if (self._result_screen(scene) or not scene.has("Roarin", roi(800, 580, 1200, 800))
                or not scene.has("分享", roi(1680, 880, 1920, 1020), exact=True)):
            return None
        confirms = [t for t in scene.tokens if t.within(roi(820, 800, 1100, 950))
                    and t.score >= getattr(self.c.settings, "ocr_confidence", .60)
                    and normalize(t.text) == "确定"]
        return confirms[0] if len(confirms) == 1 else None

    def _handbook(self, scene):
        tabs = sum(scene.has(label, TABS, exact=True) for label in HANDBOOK_TABS)
        return tabs >= 2 or (scene.has("快捷手册") and tabs >= 1)

    def _scratch_screen(self, scene):
        return scene.has("每日可刮取一次", roi(650, 600, 1200, 750)) or scene.has("刮刮卡")

    def _coffee_screen(self, scene):
        return (scene.has("点单", roi(1650, 820, 1920, 1060), exact=True)
                and scene.has(("汀曼特调", "新艾利都特调", "浓缩咖啡")))

    def _coffee_dialog(self, scene):
        return (scene.has("汀曼", roi(800, 750, 1100, 850))
                and scene.has(("想要喝点什么", "想喝点什么"), roi(500, 800, 1500, 1040)))

    def _video_screen(self, scene):
        return scene.has("经营状况", roi(1180, 0, 1650, 130)) or scene.has(
            ("昨日账本", "选择宣传员", "上架筛选", "推荐上架", "还未上架录像带", "还未选择宣传员")) or self._video_opening_result(scene)

    def _video_opening_result(self, scene):
        return scene.has(("诚信经营丁尼为本", "开店大吉丁尼遍地"), DIALOG, exact=True)

    def _video_after_opening_ready(self, scene):
        return self._world(scene) or (self._video_business_panel(scene) and scene.has(
            "正在营业", roi(1480, 900, 1840, 1040), exact=True))

    def _resume_video_transition(self, scene):
        # Keep sent confirmations across F9; a loading frame must never trigger
        # a new navigation/opening attempt after a manual pause.
        if self._video_start_pending:
            scene = self._wait(lambda s: self._video_opening_result(s) or (self._video_business_panel(s)
                               and s.has("正在营业", roi(1480, 900, 1840, 1040), exact=True)),
                               "开店成功提示或正在营业面板")
            self._video_start_pending = False
        if self._video_return_pending:
            scene = self._wait(self._video_after_opening_ready, "营业确认后的正在营业面板或大世界")
            self._video_return_pending = False
        return scene

    def _accept_video_opening_result(self, scene):
        confirms = [t for t in scene.tokens if t.within(DIALOG) and normalize(t.text) == "确认"]
        if len(confirms) != 1 or not confirms[0].within(roi(770, 570, 1100, 710)):
            return self._partial("video", scene, "营业结果页没有唯一且位置已校准的确认。")
        self.c.evidence(scene, "video_opened")
        self.c.click(scene, confirms[0])
        self._video_opened = True
        self._video_return_pending = True
        return self._resume_video_transition(scene)

    def _promoter_picker(self, scene):
        # The variable-length description moves the customer-flow section; bind
        # the stable header/footer and a named card in the right-hand grid.
        confidence = getattr(self.c.settings, "ocr_confidence", .60)
        return (scene.has("选择宣传员", roi(20, 975, 400, 1070), exact=True)
                and scene.has("宣传风格", roi(20, 200, 470, 350), exact=True)
                and any(token.score >= confidence and token.within(PROMOTER_CARDS)
                        and (self._promoter_name(token.text) is not None
                             or normalize(token.text) == "empty") for token in scene.tokens))

    def _video_business_panel(self, scene):
        return (scene.has("经营状况", roi(1180, 0, 1650, 130), exact=True)
                and scene.has(("开始营业", "正在营业"), roi(1480, 900, 1840, 1040), exact=True)
                and not self._promoter_picker(scene))

    @staticmethod
    def _promoter_name(text):
        from .characters import canonical_promoter_name
        try:
            return canonical_promoter_name(text) or None
        except ValueError:
            return None

    def _promoter_cards(self, scene):
        """Recognize exact catalog names on card labels, never EMPTY or narrative text."""
        if not self._promoter_picker(scene):
            return []
        cards = []
        for token in scene.tokens:
            if not token.within(PROMOTER_CARDS):
                continue
            # Partial cards at the top/bottom are skipped until a later fresh frame.
            if token.box[1] < PROMOTER_CARDS[1] or token.box[3] > PROMOTER_CARDS[3]:
                continue
            name = self._promoter_name(token.text)
            if name is not None and self._promoter_card_enabled(scene, token):
                cards.append((name, token))
        # One cell must have one label. Duplicate OCR/aliases are ambiguous, not two owned cards.
        cells = {}
        for name, token in cards:
            cell = (round(token.center[0] / .11), round(token.center[1] / .025))
            cells.setdefault(cell, []).append((name, token))
        if any(len(labels) != 1 for labels in cells.values()):
            return []
        return sorted(cards, key=lambda card: (round(card[1].center[1] / .04), card[1].center[0]))

    def _promoter_card_enabled(self, scene, token):
        # Available names are white; locked/EMPTY captions are grey. Limit the sample
        # to the OCR label, so a bright portrait or card border cannot enable it.
        if scene.image is None:
            # Offline scripted contexts have no pixels; live Vision always includes them.
            return token.score >= getattr(self.c.settings, "ocr_confidence", .60)
        image = scene.image
        if len(image.shape) != 3 or image.shape[2] < 3:
            return False
        height, width = image.shape[:2]
        x1, y1, x2, y2 = token.box
        patch = image[max(0, round(y1 * height)):min(height, round(y2 * height)),
                      max(0, round(x1 * width)):min(width, round(x2 * width)), :3]
        if not patch.size:
            return False
        return float((patch.min(axis=2) >= 230).mean()) >= .08

    def _promoter_signature(self, scene):
        return tuple((name, round(token.center[0], 2), round(token.center[1], 2))
                     for name, token in self._promoter_cards(scene))

    def _scroll_promoters(self, scene, amount):
        if not self._promoter_picker(scene):
            self._unknown(scene, "滚动前无法确认宣传员选择页面")
        self.c.move(scene, PROMOTER_SCROLL_POINT)
        self.c.scroll(amount)
        after = self._snapshot()
        if not self._promoter_picker(after):
            self._unknown(after, "滚动后宣传员选择页面发生变化")
        return after

    def _promoter_top(self, scene):
        """Return to the first row with bounded scrolling and fresh card geometry."""
        stable = 0
        previous = self._promoter_signature(scene)
        for _ in range(PROMOTER_SCROLL_LIMIT):
            scene = self._scroll_promoters(scene, 360)
            signature = self._promoter_signature(scene)
            stable = stable + 1 if signature == previous else 0
            if stable >= 2:
                return scene
            previous = signature
        return None

    def _promoter_target(self, scene, wanted):
        matches = [(name, token) for name, token in self._promoter_cards(scene) if name == wanted]
        return matches[0] if len(matches) == 1 else None

    def _promoter_slot_verified(self, scene, wanted):
        if not self._video_business_panel(scene):
            return False
        names = [name for token in scene.tokens if token.within(PROMOTER_SLOT)
                 and (name := self._promoter_name(token.text)) is not None]
        return names == [wanted] and not self._template(
            scene, "video_slot_plus", roi(790, 650, 1030, 900))

    def _configure_video_promoter(self, scene):
        """Set the requested owned promoter, or the first visible card after returning to the top."""
        wanted_text = getattr(self.c.settings, "promoter", "")
        wanted = self._promoter_name(wanted_text) if wanted_text else None
        if (wanted and scene.has("开始营业", roi(1480, 900, 1840, 1040), exact=True)
                and self._promoter_slot_verified(scene, wanted)):
            self.c.evidence(scene, "video_promoter_selected")
            self.c.log(f"经营页已核实指定宣传员{wanted}，无需重复选择。")
            return scene
        if not self._promoter_picker(scene):
            if not self._video_business_panel(scene) or not scene.has(
                    "开始营业", roi(1480, 900, 1840, 1040), exact=True):
                return self._partial("video", scene, "未确认录像店尚未开店，未修改宣传员。")
            plus = self._template(scene, "video_slot_plus", roi(790, 650, 1030, 900))
            heading = scene.find("选择宣传员", PROMOTER_SLOT, exact=True)
            owned = [token for token in scene.tokens if token.within(PROMOTER_SLOT)
                     and self._promoter_name(token.text) is not None]
            if plus:
                entry = plus[:2]
            elif heading:
                entry = heading
            elif len(owned) == 1:
                entry = owned[0]
            else:
                return self._partial("video", scene, "未找到可核实的宣传员槽，未点击其他录像带。")
            self.c.evidence(scene, "video_promoter_entry")
            self.c.click(scene, entry)
            scene = self._wait(self._promoter_picker, "宣传员列表与卡片名字")
        if scene.has("正在营业", PROMOTER_CONFIRM, exact=True):
            return self._ok("video", scene, "录像店已经营业，未修改宣传员。")
        if wanted_text and wanted is None:
            return self._partial("video", scene, "指定宣传员不在内置名单中，未选择其他角色。")
        scene = self._promoter_top(scene)
        if scene is None:
            return self._partial("video", self._snapshot(), "宣传员列表未能在有限滚动内回到顶部，未随意选择。")
        cards = self._promoter_cards(scene)
        if wanted and sum(name == wanted for name, _ in cards) > 1:
            return self._partial("video", scene, "宣传员列表出现重名或重复识别，无法唯一确认指定卡片。")
        selected = self._promoter_target(scene, wanted) if wanted else (cards[0] if cards else None)
        if wanted and selected is None:
            previous = self._promoter_signature(scene)
            stable = 0
            for _ in range(PROMOTER_SCROLL_LIMIT):
                scene = self._scroll_promoters(scene, -360)
                if sum(name == wanted for name, _ in self._promoter_cards(scene)) > 1:
                    return self._partial("video", scene, "宣传员列表出现重名或重复识别，无法唯一确认指定卡片。")
                selected = self._promoter_target(scene, wanted)
                if selected:
                    break
                signature = self._promoter_signature(scene)
                stable = stable + 1 if signature == previous else 0
                if stable >= 2:
                    break
                previous = signature
        if selected is None:
            # Re-read the first row after scrolling back; pre-search Token objects are stale.
            scene = self._promoter_top(scene)
            if scene is None:
                return self._partial("video", self._snapshot(), "未找到指定宣传员，列表回顶未完成。")
            cards = self._promoter_cards(scene)
            if not cards:
                return self._partial("video", scene, "宣传员列表没有可核实的角色卡名字，未点击 EMPTY。")
            selected = cards[0]
            if wanted:
                self.c.log(f"可选列表未找到{wanted}，使用第一位宣传员{selected[0]}。")
        selected_name, card = selected
        self.c.evidence(scene, "video_promoter_candidates")
        self.c.click(scene, card)
        scene = self._snapshot()
        def selected_preview(frame):
            preview = [self._promoter_name(token.text) for token in frame.tokens
                       if token.within(PROMOTER_PREVIEW)]
            return self._promoter_picker(frame) and preview.count(selected_name) == 1
        if not selected_preview(scene):
            try:
                scene = self._wait(selected_preview, "宣传员卡片选择后的页面与所选角色标题")
            except (InputInterrupted, Stopped):
                raise
            except AutomationError:
                latest = getattr(self.c, "last_scene", None) or getattr(self.c, "last", None) or scene
                return self._partial("video", latest, "宣传员介绍标题未匹配所选角色，未确认选择。")
        confirms = [token for token in scene.tokens if token.within(PROMOTER_CONFIRM)
                    and normalize(token.text) in ("确定", "确认")]
        if len(confirms) == 1:
            self.c.evidence(scene, "video_promoter_confirm")
            self.c.click(scene, confirms[0])
        elif not confirms and scene.has("换下", PROMOTER_CONFIRM, exact=True):
            # The currently assigned card needs no change; never click the deselect control.
            self.c.key("esc", scene)
        else:
            return self._partial("video", scene, "宣传员选择页没有唯一确认或已选标记，未开店。")
        scene = self._wait(self._video_business_panel, "宣传员确认后的经营状况")
        if not self._promoter_slot_verified(scene, selected_name):
            return self._partial("video", scene, "已返回经营页，但宣传员槽尚未核实所选名字，未开店。")
        self.c.evidence(scene, "video_promoter_selected")
        self.c.log(f"宣传员已核实：{selected_name}。")
        return scene

    def _video_dialog(self, scene):
        return scene.has(("阿玖", "18号"), roi(800, 750, 1100, 850), exact=True) and any(
            token.within(roi(500, 800, 1500, 1040)) for token in scene.tokens)

    def _result_screen(self, scene):
        return self._result_confirm(scene) is not None

    def _result_confirm(self, scene):
        # Prize names/amounts are variable; identify the popup and its button.
        if not (scene.has(("获得奖励", "奖励获得", "电量恢复", "不可贪杯"), DIALOG)
                or scene.has("获得", DIALOG, exact=True)):
            return None
        confirms = [t for t in scene.tokens if t.within(DIALOG)
                    and t.score >= getattr(self.c.settings, "ocr_confidence", .60)
                    and normalize(t.text) == "确认"]
        return confirms[0] if len(confirms) == 1 else None

    def _coffee_reward(self, scene):
        return (scene.has("获得", DIALOG, exact=True) and scene.has("电量", DIALOG, exact=True)
                and scene.has("确认", DIALOG, exact=True))

    def _accept_coffee_reward(self, scene):
        self.c.evidence(scene, "coffee_reward")
        self.c.click(scene, scene.find("确认", DIALOG, exact=True))
        self._coffee_ordered = True
        self._coffee_reward_claimed = True
        after = self._snapshot()
        return self._ok("coffee", after, "已核实免费每日饮品的电量奖励并确认领取。")

    def _known(self, scene):
        return (self._main(scene) or self._map(scene) or self._handbook(scene)
                or self._scratch_screen(scene) or self._coffee_screen(scene)
                or self._video_screen(scene) or self._result_screen(scene)
                or self._bakery_screen(scene) or self._coffee_dialog(scene) or self._video_dialog(scene)
                or self._battlepass(scene) or self._weekly_screen(scene))

    def _cached_login(self, scene):
        if self.c.settings.server != "global" or scene.has(START_BLOCKERS):
            return None
        buttons = [t for t in scene.tokens if t.within(roi(600, 550, 1350, 700))
                   and normalize(t.text) == normalize("进入游戏")]
        masked_account = any("@" in t.text and "****" in t.text and t.within(
            roi(600, 400, 1300, 600)) for t in scene.tokens)
        if len(buttons) == 1 and masked_account and scene.has("HOYOVERSE", roi(700, 250, 1250, 420), exact=True):
            return buttons[0]
        return None

    def _start_button(self, scene):
        if scene.has(START_BLOCKERS):
            return None
        buttons = [t for t in scene.tokens if t.within(roi(550, 780, 1450, 980))
                   and normalize(t.text) == normalize("点击进入游戏")]
        return buttons[0] if len(buttons) == 1 else None

    @staticmethod
    def _monthly_text(text):
        return normalize(unicodedata.normalize("NFKC", text))

    def _monthly_labels(self, scene):
        confidence = getattr(self.c.settings, "ocr_confidence", .60)
        today, remaining = [], []
        for token in scene.tokens:
            if token.score < confidence:
                continue
            text = self._monthly_text(token.text)
            if token.within(MONTHLY_TODAY) and text.lstrip("+✦★☆✧") == "今日到账":
                today.append(token)
            if token.within(MONTHLY_DAYS) and re.fullmatch(r"月卡剩余(?:\d{1,3}天)?", text):
                remaining.append(token)
        return today, remaining

    def _monthly_card_visible(self, scene):
        today, remaining = self._monthly_labels(scene)
        # Either distinctive ticket label can survive an animation or partial OCR read.
        return bool(today or remaining)

    def _monthly_card(self, scene):
        """Only the daily ticket, never a monthly-card purchase or renewal screen."""
        if scene.has(START_BLOCKERS) or scene.has(MONTHLY_PURCHASE):
            return None
        today, remaining = self._monthly_labels(scene)
        if len(today) != 1 or len(remaining) != 1:
            return None
        amounts = []
        confidence = getattr(self.c.settings, "ocr_confidence", .60)
        for token in scene.tokens:
            if token.score < confidence or not token.within(MONTHLY_AMOUNT):
                continue
            text = self._monthly_text(token.text).replace("×", "x").replace("*", "x").replace("✕", "x")
            if re.fullmatch(r"(?:菲林)?x90", text):
                amounts.append(token)
            elif text == "90" and any(
                    other.score >= confidence and other.within(MONTHLY_AMOUNT)
                    and self._monthly_text(other.text) == "菲林"
                    and abs(other.center[1] - token.center[1]) < .035
                    and abs(other.center[0] - token.center[0]) < .15 for other in scene.tokens):
                amounts.append(token)
        if len(amounts) != 1:
            return None
        # All three anchors must describe the same compact ticket in their observed row order.
        top, days, amount = today[0].center, remaining[0].center, amounts[0].center
        if not (top[1] < days[1] < amount[1]
                and abs(top[0] - days[0]) < .15 and abs(amount[0] - days[0]) < .15):
            return None
        return remaining[0]

    def _monthly_transition_ready(self, scene):
        ready = self._entry_ready(scene)
        if self._monthly_card_visible(scene):
            return ready  # Partial ticket OCR must not expose the dimmed background as ready.
        return ready or self._known(scene)

    def _dismiss_monthly_card(self, scene):
        if self._monthly_card_visible(scene) and scene.has(MONTHLY_PURCHASE):
            self._unknown(scene, "月卡画面出现购买、充值或续费选项")
        if (self._monthly_claim_pending or self._monthly_card_visible(scene)) and not self._monthly_transition_ready(scene):
            # A manual pause after the click can resume on a transient loading frame.
            scene = self._wait(self._monthly_transition_ready, "月卡领取后的已校准界面")
        while True:
            target = self._monthly_card(scene)
            if target is None:
                if self._monthly_claim_pending:
                    self._monthly_claim_pending = False
                    self.c.evidence(scene, "monthly_card_dismissed")
                    self.c.log("月卡领取画面已关闭，继续进入游戏。")
                return scene
            if self._monthly_claim_clicks >= MONTHLY_CLAIM_LIMIT:
                self._unknown(scene, "月卡领取画面在有限点击后仍未关闭")
            self.c.evidence(scene, "monthly_card")
            self.c.log("已识别月卡今日到账、剩余天数与 ×90 奖励，点击领取。")
            self._monthly_claim_pending = True
            self.c.click(scene, target)
            # Context.click only raises InputInterrupted before sending; focus waits after
            # a sent click stay on the same stack. Pre-send pauses must not consume a click.
            self._monthly_claim_clicks += 1
            self.c.sleep(1)
            scene = self._wait(self._monthly_transition_ready, "月卡领取后的已校准界面")

    def _entry_ready(self, scene):
        if scene.has(START_BLOCKERS):
            self._unknown(scene, "需要手动完成登录验证、协议确认或更新")
        if self._monthly_card_visible(scene):
            return self._monthly_card(scene) is not None
        return bool(self._main(scene) or self._start_button(scene) or self._cached_login(scene))

    def _startup_ready(self, scene):
        # Unknown launch frames are observed only; blockers take priority over background UI.
        return self._entry_ready(scene) or self._known(scene)

    def _unknown(self, scene, detail):
        self.c.evidence(scene, "unknown_page")
        raise AutomationError(detail + "；未识别到已校准页面，已停止输入。")

    def _partial(self, task, scene, detail):
        self.c.evidence(scene, task + "_partial")
        return TaskResult(task, "partial", detail)

    def _ok(self, task, scene, detail):
        self.c.evidence(scene, task + "_verified")
        return TaskResult(task, "ok", detail)

    def _wait(self, predicate, description, *, allow_blank=False):
        def checked(scene):
            guard_scene(scene)
            return predicate(scene)
        options = {"allow_blank": True} if allow_blank else {}
        return self.c.wait(checked, timeout=self.c.settings.transition_timeout,
                           description=description, **options)

    def _resume_entry_transition(self, scene):
        if self._entry_transition_pending and not self._startup_ready(scene):
            scene = self._wait(self._startup_ready, "进入游戏后的已校准界面", allow_blank=True)
        if self._main(scene):
            self._entry_transition_pending = False
        return scene

    def _entry_snapshot(self):
        try:
            return self._snapshot()
        except ScreenshotNotReady:
            if not self._entry_transition_pending:
                raise
            return self._wait(self._startup_ready, "进入游戏后的已校准界面", allow_blank=True)

    def ensure_main(self):
        first_call = not self._startup_checked
        try:
            try:
                scene = self._entry_snapshot()
            except ScreenshotNotReady:
                if not ((first_call and self.c.settings.auto_launch) or self._entry_transition_pending):
                    raise
                scene = self._wait(self._startup_ready, "游戏加载后的已校准界面", allow_blank=True)
            scene = self._resume_entry_transition(self._resume_video_transition(scene))
            if first_call and self.c.settings.auto_launch and not self._startup_ready(scene):
                scene = self._wait(self._startup_ready, "游戏启动后的已校准界面或登录入口", allow_blank=True)
        except InputInterrupted:
            raise  # Resume with the initial read-only loading wait still available.
        except AutomationError:
            # A real timeout consumes its finite startup allowance; only a pause retries.
            self._startup_checked = True
            raise
        # A pause while reading/loading must not consume the first startup wait.
        self._startup_checked = True
        for attempt in range(5):
            if attempt:
                scene = self._resume_entry_transition(self._entry_snapshot())
            if scene.has(START_BLOCKERS):
                self._unknown(scene, "需要手动完成登录验证、协议确认或更新")
            scene = self._dismiss_monthly_card(scene)
            if self._monthly_card_visible(scene):
                self._unknown(scene, "月卡领取特征不完整或出现购买选项")
            if self._video_opening_result(scene):
                after = self._accept_video_opening_result(scene)
                if isinstance(after, TaskResult):
                    raise AutomationError(after.detail)
                continue
            if self._result_screen(scene):
                self.c.click(scene, self._result_confirm(scene))
                continue
            if self._bakery_card(scene):
                self.c.evidence(scene, "bakery_card")
                self.c.click(scene, self._bakery_card_confirm(scene))
                continue
            if self._main(scene):
                return TaskResult("main", "ok", "已识别大世界或游戏菜单。")
            login = self._cached_login(scene)
            if login:
                self.c.evidence(scene, "cached_account_login")
                self.c.click(scene, login)
                self._entry_transition_pending = True
                self.c.sleep(3)
                self._wait(self._entry_ready, "已保存账号登录后的启动页", allow_blank=True)
                continue
            enter = self._start_button(scene)
            if enter:
                self.c.click(scene, enter)
                self._entry_transition_pending = True
                self.c.sleep(3)
                self._wait(self._entry_ready, "进入游戏或可重试的启动页", allow_blank=True)
                continue
            if not self._known(scene):
                self._unknown(scene, "无法确认游戏已进入非战斗主界面")
            # Escape is only issued on a recognized non-combat page.
            self.c.key("esc", scene)
            self.c.sleep(0.5)
        scene = self._resume_entry_transition(self._entry_snapshot())
        if scene.has(START_BLOCKERS):
            self._unknown(scene, "需要手动完成登录验证、协议确认或更新")
        scene = self._dismiss_monthly_card(scene)
        if self._monthly_card_visible(scene):
            self._unknown(scene, "月卡领取特征不完整或出现购买选项")
        if self._monthly_claim_clicks and self._result_screen(scene):
            self.c.click(scene, self._result_confirm(scene))
            scene = self._wait(lambda after: self._entry_ready(after) and self._main(after)
                               and not self._result_screen(after), "月卡奖励确认后的大世界或游戏菜单")
        if self._main(scene):
            return TaskResult("main", "ok", "已识别大世界或游戏菜单。")
        self._unknown(scene, "已识别页面未能返回大世界")

    def _travel(self, task, area_name, place_name, npc_labels):
        """Select only visible map labels, then verify a visible interaction."""
        scene = self._snapshot()
        if not self._map(scene):
            self.ensure_main()
            scene = self._snapshot()
            target = scene.find("地图", exact=True)
            if target:
                self.c.click(scene, target)
            else:
                # M is a provisional shortcut: no later action until the map is verified.
                self.c.key("m", scene)
            scene = self._wait(self._map, "地图主区域与底部传送点")
        target = scene.find(area_name, MAP_AREAS, exact=True)
        if not target:
            return self._partial(task, scene, f"地图未显示{area_name}，尚未校准地图翻页。")
        self.c.click(scene, target)
        self.c.sleep(0.5)
        scene = self._snapshot()
        target = scene.find(place_name, MAP_POINTS, exact=True)
        if not target:
            return self._partial(task, scene, f"未找到{area_name}的{place_name}传送点。")
        self.c.click(scene, target)
        scene = self._snapshot()
        confirm = scene.find("确认", MAP_CONFIRM, exact=True)
        if not confirm or not (self._map(scene) or scene.has((area_name, place_name))):
            return self._partial(task, scene, "传送点已选择，但没有可核实的传送确认。")
        self.c.click(scene, confirm)
        scene = self._wait(self._main, f"传送到{area_name}/{place_name}后的大世界")
        # Camera rotation has not been calibrated. Never move toward an unseen NPC.
        if not scene.has(npc_labels):
            return self._partial(task, scene, f"已传送至{place_name}，尚未识别交互对象；需要校准镜头与落点。")
        self.c.key("f", scene, duration=0.2)
        self.c.sleep(1)
        return self._snapshot()

    def _open_daily(self):
        scene = self._snapshot()
        if not self._handbook(scene):
            self.ensure_main()
            scene = self._snapshot()
            target = scene.find("快捷手册", exact=True)
            if target:
                self.c.click(scene, target)
            else:
                anchor = self._template(scene, "world_menu", roi(1400, 28, 1730, 120))
                if anchor:
                    shortcuts = [normalize(t.text) for t in scene.tokens if t.within(
                        (anchor[0] - .025, 100 / 1080, anchor[0] + .025, 165 / 1080))
                        and normalize(t.text) in ("f1", "f2", "f3", "f4")]
                    if len(shortcuts) == 1:
                        self.c.key(shortcuts[0], scene)
                    elif self._menu(scene):
                        self.c.click(scene, (anchor[0], anchor[1]))
                    else:
                        self._unknown(scene, "快捷手册图标下没有唯一可识别的功能键")
                else:
                    # Bindings may differ by account; always verify the resulting page.
                    if self.c.settings.server == "global":
                        self._unknown(scene, "国际服快捷手册图标尚未识别，不能假定 F2 绑定")
                    self.c.key("f2", scene)
            scene = self._wait(self._handbook, "快捷手册标签")
        daily = scene.find("日常", TABS, exact=True)
        if not daily:
            self._unknown(scene, "快捷手册没有日常标签")
        self.c.click(scene, daily)
        scene = self._wait(lambda s: self._handbook(s) and s.has(
            ("今日最大活跃度", "今日活跃度", "每日任务", "活跃度")), "日常任务与活跃度")
        return scene

    def _task_complete(self, scene, labels):
        if not self._handbook(scene):
            return False
        column = self._daily_column(scene, labels)
        if column is None:
            return False
        progress = (column[0], 405 / 1080, column[2], 480 / 1080)
        statuses = [normalize(t.text) for t in scene.tokens if t.within(progress)]
        numbers = {re.sub(r"^进度", "", text).replace("／", "/") for text in statuses
                   if re.fullmatch(r"(?:进度)?[01][/／]1", text)}
        if "0/1" in numbers:
            return False
        if numbers == {"1/1"}:
            return True
        return scene.has(("已完成", "已领取"), column, exact=True)

    def _daily_column(self, scene, labels):
        """Bind a task title to its own card; weekly recommendations are outside."""
        title_area = roi(800, 470, 1700, 660)
        titles = [t for t in scene.tokens if t.within(title_area)
                  and any(normalize(label) in normalize(t.text) for label in labels)]
        columns = [column for column in DAILY_COLUMNS
                   if any(column[0] <= title.center[0] <= column[2] for title in titles)]
        return columns[0] if len(columns) == 1 else None

    def _daily_jump(self, task):
        """Use the task card's visible Go button, without following weekly cards."""
        scene = self._open_daily()
        labels = TASK_LABELS[task]
        if self._daily_verified(scene):
            self._record_daily_complete(scene)
            return TaskResult(task, "ok", "每日已满 400 活跃度且全部奖励已领取，每日任务已完成。")
        if self._task_complete(scene, labels):
            return self._ok(task, scene, "日常手册同列进度已为 1/1 或明确已完成。")
        column = self._daily_column(scene, labels)
        if column is None:
            return self._partial(task, scene, "没有唯一对应的日常任务卡，未点击其他任务。")
        buttons = [t for t in scene.tokens if t.within(
            (column[0], 820 / 1080, column[2], 930 / 1080)) and normalize(t.text) == "前往"]
        if len(buttons) != 1:
            return self._partial(task, scene, "对应日常卡没有唯一前往按钮，未执行传送。")
        self._task_before[task] = self._engagement(scene)
        self.c.evidence(scene, task + "_daily_jump")
        self.c.click(scene, buttons[0])
        destination_labels = {
            "scratch": ("嗷呜", "报刊亭", "刮刮卡", "刮刮乐", "吼吼先生", "吼吼饼铺"),
            "coffee": ("汀曼", "咖啡店", "点单"),
            "video": ("18号", "柜台", "经营状况", "昨日账本", "阿玖", "影像档案架"),
        }[task]
        scene = self._wait(lambda s: not self._handbook(s) and (
            self._main(s) or s.has(destination_labels)), "对应日常任务的目标页面")
        if self._world(scene):
            return self._open_daily_target(task, scene, destination_labels)
        return scene

    def _daily_target_npc(self, task, scene):
        names = {"scratch": ("嗷呜", "吼吼先生"), "coffee": ("汀曼", "汀曼大师"),
                 "video": ("阿玖", "18号")}[task]
        # Only a unique name ahead of the avatar authorizes forward movement.
        # Background shop signs and unrelated NPCs must never be approached.
        candidates = [t for t in scene.tokens if t.within(roi(650, 15, 1300, 700))
                      and t.score >= self.c.settings.ocr_confidence
                      and normalize(t.text).strip("><＜＞《》〈〉「」『』‹›❮❯") in names]
        return candidates[0] if len(candidates) == 1 else None

    def _daily_target_open(self, task, scene):
        return {"scratch": lambda: self._bakery_screen(scene) or self._scratch_screen(scene),
                "coffee": lambda: self._coffee_dialog(scene) or self._coffee_screen(scene),
                "video": lambda: self._video_dialog(scene) or self._video_screen(scene)}[task]()

    def _open_daily_target(self, task, scene, destination_labels):
        if not scene.has(destination_labels):
            return self._partial(task, scene, "已通过日常前往到达大世界，尚未识别目标交互提示。")
        self.c.evidence(scene, task + "_arrival")
        for attempt in range(4):
            if self._daily_target_open(task, scene) or not self._world(scene):
                return scene
            npc = self._daily_target_npc(task, scene)
            if npc is None:
                return self._partial(task, scene, "目标 NPC 名字不在正前方或不唯一，未继续移动或交互。")
            if attempt or not self._interaction(scene):
                self.c.log(f"{task}：重新核实正前方目标 {npc.text}，短距离靠近（0.25 秒）。")
                self.c.key("w", scene, duration=.25)
                self.c.sleep(.5)
                scene = self._snapshot()
                if self._daily_target_open(task, scene) or not self._world(scene):
                    return scene
                npc = self._daily_target_npc(task, scene)
                if npc is None:
                    return self._partial(task, scene, "靠近后目标 NPC 未重新核实，未发送交互。")
            if not self._interaction(scene):
                return self._partial(task, scene, "目标已在画面中，但没有明确 F 交互提示。")
            self.c.log(f"{task}：按 F 与 {npc.text} 交互（{attempt+1}/4），核实目标页面是否打开。")
            self.c.key("f", scene, duration=.2)
            self.c.sleep(1)
            scene = self._snapshot()
            if self._daily_target_open(task, scene) or not self._world(scene):
                return scene
            self.c.log(f"{task}：按 F 后仍在大世界，尚未进入目标交互页面。", "warning")
        return self._partial(task, scene, "有限靠近与交互后仍未打开目标页面；F 图标不表示交互已经成功。")

    def _verify_task_card(self, task, scene, detail):
        """An action alone is insufficient: revisit the specific daily card if possible."""
        if not self._known(scene):
            return self._partial(task, scene, detail)
        daily_scene = self._open_daily()
        if self._daily_verified(daily_scene):
            self._record_daily_complete(daily_scene)
            return TaskResult(task, "ok", "每日已满 400 活跃度且全部奖励已领取，每日任务已完成。")
        if self._task_complete(daily_scene, TASK_LABELS[task]):
            return self._ok(task, daily_scene, "日常手册对应任务同列进度已为 1/1。")
        before, after = self._task_before.get(task), self._engagement(daily_scene)
        if (before is not None and after is not None and after >= before + 100
                and not daily_scene.has(TASK_LABELS[task], roi(800, 470, 1700, 660))):
            return self._ok(task, daily_scene, f"对应日常卡完成后移出当前列表，活跃度已从 {before} 增至 {after}。")
        return self._partial(task, daily_scene, detail)

    def scratch(self):
        scene = self._snapshot()
        if self._daily_verified(scene):
            self._record_daily_complete(scene)
            return TaskResult("scratch", "ok", "每日已满 400 活跃度且全部奖励已领取，每日任务已完成。")
        if self._task_complete(scene, TASK_LABELS["scratch"]):
            return self._ok("scratch", scene, "日常手册已证实刮卡任务完成。")
        if self._bakery_screen(scene) or self._result_screen(scene):
            return self._bakery_daily(scene)
        if not self._scratch_screen(scene) and not self._bakery_screen(scene) and not scene.has("嗷呜"):
            scene = self._daily_jump("scratch")
            if isinstance(scene, TaskResult):
                return scene
        if self._bakery_screen(scene):
            return self._bakery_daily(scene)
        for _ in range(7):
            if scene.has(("今日已刮取", "今天已经刮过", "今日刮卡次数已用完")):
                return self._ok("scratch", scene, "报刊亭明确显示今日已经刮过。")
            if scene.has("每日可刮取一次", roi(650, 600, 1200, 750)):
                break
            if scene.has(("卦象集录", "吼吼饼铺"), DIALOG) and scene.has(
                    ("今日已", "已经领取", "已参与"), DIALOG):
                return self._partial("scratch", scene, "同类每日玩法可能已完成，仍需日常手册验证。")
            target = scene.find("刮刮卡", roi(60, 900, 240, 1080))
            if target is None:
                target = scene.find(("叫醒他", "叫醒嗷呜", "只是来看刮刮卡和报纸"), OPTIONS)
            if target is None:
                target = scene.find("嗷呜被你叫醒了", DIALOG)
            if target is None and scene.has("嗷呜", roi(850, 750, 1100, 870)):
                target = scene.find("嗷呜", roi(850, 750, 1100, 870), exact=True)
            if target is None:
                if self._known(scene) or scene.has("嗷呜"):
                    return self._partial("scratch", scene, "报刊亭没有已识别的刮卡入口或可推进对话。")
                self._unknown(scene, "报刊亭交互出现未知页面")
            self.c.click(scene, target)
            scene = self._snapshot()
        else:
            return self._partial("scratch", scene, "报刊亭对话达到有限重试上限。")
        for start, end in (((780, 522), (1134, 556)), ((782, 556), (1138, 588)),
                           ((788, 590), (1134, 632))):
            scene = self._snapshot()
            if not scene.has("每日可刮取一次", roi(650, 600, 1200, 750)):
                break
            self.c.drag(scene, point(*start), point(*end))
            self.c.sleep(1)
        scene = self._snapshot()
        if scene.has(("获得奖励", "奖励获得", "领取成功", "今日已刮取")):
            return self._ok("scratch", scene, "已刮卡，并识别到奖励或今日已刮取结果。")
        if self._task_complete(scene, TASK_LABELS["scratch"]):
            return self._ok("scratch", scene, "已刮卡，日常任务显示完成。")
        return self._verify_task_card("scratch", scene, "已执行刮取，尚未识别奖励或同列 1/1 进度；不能确认完成。")

    def _bakery_daily(self, scene):
        """The shared daily check-in may lead to a free snack box instead of scratch."""
        for _ in range(6):
            if self._result_screen(scene):
                self.c.evidence(scene, "bakery_reward")
                self.c.click(scene, self._result_confirm(scene))
                scene = self._snapshot()
                continue
            if self._bakery_card(scene):
                self.c.evidence(scene, "bakery_card")
                self.c.click(scene, self._bakery_card_confirm(scene))
                scene = self._snapshot()
                return self._verify_task_card("scratch", scene, "已领取饼铺奖励，仍需共享签到任务同列 1/1。")
            if scene.has(("今日已领取同类型奖励", "已领取同类型奖励")):
                return self._verify_task_card("scratch", scene, "同类签到已经领取，仍需任务卡完成证据。")
            if (scene.has("每日可领取一次", roi(1100, 880, 1650, 1040))
                    and scene.has("零食盲盒") and scene.has("集卡兑换", exact=True)):
                self.c.evidence(scene, "bakery_daily_box")
                self.c.click(scene, point(1180, 500))
            elif scene.has("查看今天", roi(750, 900, 1200, 1020)):
                self.c.click(scene, point(960, 540))
                self.c.sleep(2)
            else:
                return self._partial("scratch", scene, "饼铺出现未校准的领取阶段，已停止点击。")
            scene = self._snapshot()
        return self._partial("scratch", scene, "饼铺领取达到有限重试上限，尚未验证完成。")

    def coffee(self):
        scene = self._snapshot()
        if self._daily_verified(scene):
            self._record_daily_complete(scene)
            return TaskResult("coffee", "ok", "每日已满 400 活跃度且全部奖励已领取，每日任务已完成。")
        if self._coffee_reward(scene):
            return self._accept_coffee_reward(scene)
        if self._coffee_reward_claimed:
            return self._ok("coffee", scene, "已核实免费每日饮品的电量奖励并确认领取，恢复后继续验证。")
        if self._task_complete(scene, TASK_LABELS["coffee"]):
            return self._ok("coffee", scene, "日常手册已证实喝咖啡任务完成。")
        if scene.has(("不可贪杯", "今日已经喝过", "今天已经喝过"), DIALOG):
            return self._ok("coffee", scene, "咖啡店明确显示今日已经喝过。")
        if self._coffee_ordered:
            # A pause may invalidate a pending animation/skip target. Resume
            # the result phase without ordering the daily coffee a second time.
            return self._finish_coffee()
        if not self._coffee_screen(scene) and not self._coffee_dialog(scene):
            scene = self._daily_jump("coffee")
            if isinstance(scene, TaskResult):
                return scene
        for _ in range(3):
            if not self._coffee_dialog(scene):
                break
            option = scene.find("来杯汀曼特调", OPTIONS, exact=True)
            if option:
                if not coffee_is_free(scene):
                    return self._partial("coffee", scene, "没有已校准的免费每日饮品依据，未点单。")
                self.c.evidence(scene, "coffee_daily_dialogue")
                self.c.click(scene, option)
                self._coffee_ordered = True
                return self._finish_coffee()
            self.c.click(scene, scene.find(("想要喝点什么", "想喝点什么"), roi(500, 800, 1500, 1040)))
            scene = self._snapshot()
        if not self._coffee_screen(scene):
            if scene.has(("不可贪杯", "今日已经喝过", "今天已经喝过"), DIALOG):
                return self._ok("coffee", scene, "咖啡店明确显示今日已经喝过。")
            if self._known(scene) or scene.has("汀曼"):
                return self._partial("coffee", scene, "尚未识别六分街咖啡点单面板。")
            self._unknown(scene, "咖啡交互出现未知页面")
        self.c.sleep(1)
        scene = self._snapshot()
        coffee = scene.find("汀曼特调", roi(100, 630, 1200, 1080))
        if coffee is None:
            return self._partial("coffee", scene, "未识别到汀曼特调，未选择其他咖啡。")
        self.c.click(scene, (coffee.center[0], max(0, coffee.center[1] - 50 / 1080)))
        scene = self._snapshot()
        if not coffee_is_free(scene):
            return self._partial("coffee", scene, "点单页面没有明确免费证据，本次没有点单。")
        order = scene.find("点单", roi(1650, 820, 1920, 1060), exact=True)
        if order is None:
            return self._partial("coffee", scene, "没有识别到可点击的免费点单按钮。")
        self.c.click(scene, order)
        self._coffee_ordered = True
        return self._finish_coffee()

    def _finish_coffee(self):
        for _ in range(6):
            scene = self._snapshot()
            if self._coffee_reward(scene):
                return self._accept_coffee_reward(scene)
            if scene.has(("不可贪杯", "今日已经喝过", "今天已经喝过"), DIALOG):
                return self._ok("coffee", scene, "咖啡店明确显示今日已经喝过。")
            if scene.has(("电量恢复", "获得电量", "电量已恢复", "增加电量"), DIALOG):
                return self._ok("coffee", scene, "免费点单后已识别电量恢复结果。")
            if self._task_complete(scene, TASK_LABELS["coffee"]):
                return self._ok("coffee", scene, "免费点单后日常手册显示喝咖啡已完成。")
            skip = scene.find("跳过", roi(1650, 30, 1900, 220), exact=True)
            if skip:
                self.c.move(scene, skip.center)
                scene = self._snapshot()
                skip = scene.find("跳过", roi(1650, 30, 1900, 220), exact=True)
                if skip:
                    self.c.click(scene, skip)
                    continue
            if self._world(scene):
                return self._verify_task_card("coffee", scene, "免费点单已执行，但对应日常卡尚未验证 1/1。")
            self.c.sleep(1)
        return self._partial("coffee", scene, "免费点单已执行，结果动画或弹窗尚未校准。")

    def video(self):
        scene = self._resume_video_transition(self._snapshot())
        promoter_configured = False
        if self._daily_verified(scene):
            self._record_daily_complete(scene)
            return TaskResult("video", "ok", "每日已满 400 活跃度且全部奖励已领取，每日任务已完成。")
        if self._task_complete(scene, TASK_LABELS["video"]):
            return self._ok("video", scene, "日常手册已证实录像店营业完成。")
        if self._video_opened and (self._main(scene) or self._handbook(scene)
                                   or self._video_business_panel(scene)):
            return self._ok("video", scene, "此前已识别并确认营业成功；恢复后保留已完成营业结果。")
        if not self._video_screen(scene) and not self._video_dialog(scene):
            scene = self._daily_jump("video")
            if isinstance(scene, TaskResult):
                return scene
        for _ in range(16):
            if self._video_opening_result(scene):
                scene = self._accept_video_opening_result(scene)
                if isinstance(scene, TaskResult):
                    return scene
                if self._world(scene):
                    return self._ok("video", scene, "已识别营业成功提示、确认并返回大世界。")
                continue
            if scene.has(("还未上架录像带", "还未选择宣传员"), DIALOG):
                confirm = scene.find("确认", DIALOG, exact=True)
                if confirm:
                    self.c.click(scene, confirm)
                    scene = self._snapshot()
                return self._partial("video", scene, "需配置已有录像带或宣传员，选择入口正在校准。")
            if self._video_dialog(scene):
                choice = scene.find("查看经营状况", OPTIONS, exact=True)
                if choice is None:
                    choice = scene.find(("阿玖", "18号"), roi(800, 750, 1100, 850), exact=True)
                opening = normalize(choice.text) == normalize("查看经营状况")
                self.c.click(scene, choice)
                scene = self._wait(self._video_screen, "录像店经营面板加载") if opening else self._snapshot()
                continue
            if scene.has("昨日账本"):
                close = scene.find("关闭", roi(1200, 75, 1550, 380), exact=True)
                if close:
                    self.c.click(scene, close)
                else:
                    anchor = self._template(scene, "close", roi(1200, 75, 1550, 380))
                    if anchor:
                        self.c.click(scene, (anchor[0], anchor[1]))
                    else:
                        self.c.key("esc", scene)
                scene = self._snapshot()
                continue
            if scene.has("正在营业", roi(1480, 900, 1840, 1040), exact=True):
                return self._ok("video", scene, "录像店已显示正在营业；重复执行无需重新开店。")
            if self._promoter_picker(scene) and scene.has("正在营业", PROMOTER_CONFIRM, exact=True):
                return self._ok("video", scene, "录像店已经营业，未修改宣传员。")
            if not self._video_screen(scene):
                self._unknown(scene, "录像店交互出现未知页面")
            if not promoter_configured and (self._video_business_panel(scene) or self._promoter_picker(scene)):
                scene = self._configure_video_promoter(scene)
                if isinstance(scene, TaskResult):
                    return scene
                promoter_configured = True
                continue
            if (self._video_business_panel(scene) and scene.has(
                    "开始营业", roi(1480, 900, 1840, 1040), exact=True)):
                empty_video = self._template(scene, "video_slot_plus", roi(1100, 650, 1320, 900))
                if empty_video:
                    self.c.evidence(scene, "video_empty_slots")
                    self.c.click(scene, (empty_video[0], empty_video[1]))
                    scene = self._snapshot()
                    continue
            recommended = scene.find("推荐上架", roi(1250, 960, 1560, 1080), exact=True)
            if recommended:
                self.c.click(scene, recommended)
                scene = self._snapshot()
                if scene.has(("上架筛选", "推荐上架")):
                    self.c.key("esc", scene)
                    scene = self._snapshot()
                continue
            start = scene.find("开始营业", roi(1480, 900, 1840, 1040), exact=True)
            if start:
                self.c.click(scene, start)
                scene = self._snapshot()
                confirm = scene.find("确认", roi(1000, 570, 1270, 700), exact=True)
                if confirm and scene.has(("营业", "宣传员", "录像带"), DIALOG):
                    self.c.click(scene, confirm)
                    self._video_start_pending = True
                    scene = self._resume_video_transition(scene)
                    continue
                return self._partial("video", scene, "已请求开始营业，但没有可核实的营业确认或宣传员/录像带尚未配置。")
            return self._partial("video", scene, "已识别录像店，但宣传员或录像带设置需要实机校准。")
        return self._partial("video", scene, "录像店达到有限操作上限，尚未识别正在营业。")

    def _engagement(self, scene):
        parts = [token.text for token in scene.tokens if token.within(DAILY_REWARDS)]
        text = " ".join(parts)
        # Threshold labels (100, 200, 300, 400) are not current engagement.
        readings = {int(m.group(1)) for m in re.finditer(
            r"(?<!\d)(\d{1,3})\s*[/／]\s*400(?!\d)", text)}
        for token in scene.tokens:
            if not token.within(DAILY_REWARDS):
                continue
            value = normalize(token.text)
            match = re.fullmatch(r"(?:当前活跃度|今日活跃度)(\d{1,3})(?:[/／]400)?", value)
            if match and 0 <= int(match.group(1)) <= 400:
                readings.add(int(match.group(1)))
        if readings:
            return next(iter(readings)) if len(readings) == 1 and all(0 <= n <= 400 for n in readings) else None
        return self._reward_progress(scene)

    def _reward_progress(self, scene):
        """Read the four green fire/check indicators, never the maximum label."""
        if scene.image is None or not self._handbook(scene) or not scene.has("今日最大活跃度", DAILY_REWARDS):
            return None
        import cv2
        import numpy as np
        height, width = scene.image.shape[:2]
        active = []
        for x in (.3905, .5330, .6751, .8184):
            patch = scene.image[round(.263 * height):round(.290 * height),
                                round((x - .012) * width):round((x + .012) * width)]
            if patch.size == 0:
                return None
            hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            fraction = np.mean(cv2.inRange(hsv, (35, 110, 120), (95, 255, 255)) > 0)
            if .02 < fraction < .15:
                return None
            active.append(fraction >= .15)
        count = int(sum(active))
        return count * 100 if active == [True] * count + [False] * (4 - count) else None

    def _daily_verified(self, scene):
        if not self._handbook(scene):
            return False
        engagement = self._engagement(scene)
        if engagement is not None:
            self.c.report.engagement = engagement
        claimed = scene.has(("全部奖励已领取", "今日奖励已领取"), DAILY_REWARDS, exact=True)
        if engagement is not None and engagement != 400:
            return False
        current_readings = [t for t in scene.tokens if t.within(DAILY_REWARDS)
                            and (re.search(r"\d{1,3}\s*[/／]\s*400", t.text)
                                 or normalize(t.text).startswith(("当前活跃度", "今日活跃度")))]
        if current_readings and engagement is None:
            return False
        tiers = ((0.36, 0.49), (0.49, 0.62), (0.62, 0.75), (0.75, 0.87))
        verified_slots = set()
        for index, (left, right) in enumerate(tiers):
            band = (left, DAILY_REWARDS[1], right, DAILY_REWARDS[3])
            labelled = any(normalize(t.text) == "已领取" and left <= t.center[0] < right
                           and band[1] <= t.center[1] <= band[3] for t in scene.tokens)
            match = self._template(scene, "daily_completed", band)
            if labelled or (match and left <= match[0] < right):
                verified_slots.add(index)
        all_claimed = len(verified_slots) == 4
        if all_claimed:
            self.c.report.engagement = 400
            return True
        return engagement == 400 and claimed

    def _record_daily_complete(self, scene):
        """Publish the authoritative terminal state only after its evidence is saved."""
        if not self._daily_evidence_recorded:
            self.c.evidence(scene, "daily_verified")
            self._daily_evidence_recorded = True
        self.c.report.engagement = 400
        self.c.report.daily_verified = True

    def daily_already_complete(self):
        scene = self._open_daily()
        if self._daily_verified(scene):
            self._record_daily_complete(scene)
            return True
        return False

    def daily(self):
        scene = self._open_daily()
        for _ in range(6):
            if self._daily_verified(scene):
                self._record_daily_complete(scene)
                return TaskResult("daily", "ok", "每日已满 400 活跃度且全部奖励已领取，每日任务已完成。")
            engagement = self._engagement(scene)
            if engagement is not None and engagement < 400:
                return self._partial("daily", scene, f"当前活跃度 {engagement}/400，尚未完成全部每日。")
            claim = scene.find(("全部领取", "一键领取", "领取"), DAILY_REWARDS, exact=True)
            if claim is None:
                claim = scene.find("今日最大活跃度", roi(380, 230, 700, 350))
            if claim is None:
                return self._partial("daily", scene, "日常页面已识别，但未验证满 400 活跃或可领取奖励。")
            self.c.click(scene, claim)
            scene = self._snapshot()
            if scene.has("奖励预览", roi(380, 150, 750, 360)):
                self.c.evidence(scene, "daily_reward_preview")
                self.c.key("esc", scene)
                scene = self._snapshot()
                if self._handbook(scene) and self._daily_verified(scene):
                    self._record_daily_complete(scene)
                    return TaskResult("daily", "ok", "已验证 400 活跃度且全部日常奖励已经领取，每日任务已完成。")
                return self._partial("daily", scene, "显示奖励预览；尚不能区分已领取与活跃度未满。")
            confirm = scene.find("确认", roi(850, 670, 1120, 800), exact=True)
            if confirm and scene.has(("获得奖励", "奖励获得", "领取成功", "菲林", "丁尼"), DIALOG):
                self.c.evidence(scene, "daily_reward_received")
                self.c.click(scene, confirm)
                scene = self._wait(self._handbook, "领取奖励后的日常手册")
                continue
            if self._handbook(scene):
                continue
            self._unknown(scene, "领取日常奖励后出现未知弹窗")
        return self._partial("daily", scene, "已尝试领取奖励，仍缺少满 400 活跃与全部领取的最终证据。")
