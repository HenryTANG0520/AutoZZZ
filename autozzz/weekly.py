"""Claim already earned New Eridu weekly rewards without doing weekly tasks."""
from __future__ import annotations

import re
import unicodedata

from .models import AutomationError, Scene, TaskResult, normalize
from .reward_vision import green_claim, milestone_claimable, red_notification
from .workflows import DIALOG, DailyWorkflow, roi


WEEKLY_ENTRY = roi(385, 810, 670, 925)
WEEKLY_NOTICE = roi(390, 800, 630, 915)
WEEKLY_GRID = roi(110, 235, 1065, 940)
WEEKLY_COLUMNS = ((.060, .225), (.225, .390), (.390, .555))
WEEKLY_ROWS = ((.220, .435), (.435, .650), (.650, .865))
WEEKLY_DETAIL = roi(1100, 240, 1435, 340)
WEEKLY_DETAIL_CLAIM = roi(1100, 855, 1420, 925)
WEEKLY_MILESTONE_LABELS = roi(1495, 230, 1578, 840)
WEEKLY_MILESTONE_X = 1598 / 1920
WEEKLY_REWARD_CONFIRM = roi(790, 680, 1135, 775)
WEEKLY_REWARD_ITEMS = roi(730, 450, 1210, 665)
WEEKLY_SCROLL_POINT = (1740 / 1920, 580 / 1080)
WEEKLY_SCROLL_LIMIT = 8
WEEKLY_ACTION_LIMIT = 40


def _progress(text):
    text = normalize(unicodedata.normalize("NFKC", text)).replace("／", "/")
    match = re.fullmatch(r"(?:任务目标)?(\d{1,6})/(\d{1,6})", text)
    if match is None:
        return None
    value, maximum = map(int, match.groups())
    return value, maximum


class WeeklyRewardsWorkflow(DailyWorkflow):
    """Observe each new frame before choosing an earned task or milestone."""

    def __init__(self, context):
        super().__init__(context)
        self._weekly_card_chosen = None
        self._weekly_pending = None
        self._weekly_actions = 0
        self._weekly_claims = 0
        self._weekly_milestone_chosen = None
        self._weekly_reward_pending = False
        self._weekly_crop_scene = None
        self._weekly_crop_progress = {}

    def _trusted(self, scene):
        confidence = getattr(self.c.settings, "ocr_confidence", .60)
        return Scene([token for token in scene.tokens if token.score >= confidence],
                     scene.image, scene.geometry, scene.captured_at)

    def _trusted_tokens(self, scene):
        confidence = getattr(self.c.settings, "ocr_confidence", .60)
        return [token for token in scene.tokens if token.score >= confidence]

    def _weekly_page(self, scene):
        scene = self._trusted(scene)
        return (self._weekly_screen(scene)
                and scene.has("任务目标", WEEKLY_DETAIL))

    def _weekly_reward(self, scene):
        # A generic confirm or a dimmed weekly background does not identify a reward.
        scene = self._trusted(scene)
        weekly_background = sum((
            scene.has("三格连线额外奖励", roi(105, 120, 1080, 225)),
            scene.has("积分特赏", roi(1480, 130, 1835, 220), exact=True),
            scene.has("累计获取积分", roi(1490, 865, 1835, 940)),
        )) >= 2
        scope = (weekly_background or self._weekly_reward_pending
                 or self._weekly_pending in ("card", "claim", "milestone", "confirm"))
        return (scope and scene.has("获得", roi(750, 380, 1160, 450), exact=True)
                and scene.has(("绳网信用", "日常业绩", "菲林", "邦布券"), WEEKLY_REWARD_ITEMS)
                and len(self._reward_confirms(scene)) == 1)

    def _reward_confirms(self, scene):
        return [token for token in self._trusted_tokens(scene) if token.within(WEEKLY_REWARD_CONFIRM)
                and normalize(token.text) == "确认"]

    def _known(self, scene):
        return self._weekly_page(scene) or self._weekly_reward(scene) or super()._known(scene)

    def _card_progresses(self, scene):
        if not self._weekly_page(scene) or self._weekly_reward(scene):
            return {}
        cells = {}
        for token in self._trusted_tokens(scene):
            if not token.within(WEEKLY_GRID):
                continue
            x, y = token.center
            column = next((index for index, bounds in enumerate(WEEKLY_COLUMNS)
                           if bounds[0] < x < bounds[1]), None)
            row = next((index for index, bounds in enumerate(WEEKLY_ROWS)
                        if bounds[0] < y < bounds[1]), None)
            if row is None or column is None:
                continue
            progress = _progress(token.text)
            left, right = WEEKLY_COLUMNS[column]
            top, bottom = WEEKLY_ROWS[row]
            if (progress is None and re.fullmatch(r"\d{2,12}", normalize(token.text))
                    and token.within((left + .008, top + .075, right - .008, top + .15))
                    and green_claim(scene, (left + .008, bottom - .042, right - .008, bottom - .009))):
                # A lost slash (e.g. 4/4 -> 414) is re-read from current pixels,
                # never inferred from the digits. Keep the original fresh target.
                progress = self._cropped_card_progress(scene, token)
            if progress is None:
                continue
            cells.setdefault((row, column), []).append((token, progress))
        # Repeated OCR values within one card are ambiguous, even if they agree.
        return {cell: values[0] for cell, values in cells.items() if len(values) == 1}

    def _cropped_card_progress(self, scene, token):
        vision = getattr(self.c, "vision", None)
        image = scene.image
        if vision is None or image is None or len(getattr(image, "shape", ())) != 3:
            return None
        if self._weekly_crop_scene is not scene:
            self._weekly_crop_scene, self._weekly_crop_progress = scene, {}
        if token not in self._weekly_crop_progress:
            height, width = image.shape[:2]
            x1, y1, x2, y2 = token.box
            margin = max(2, round(4 * width / 1920))
            crop = image[max(0, round(y1*height)-margin):min(height, round(y2*height)+margin),
                         max(0, round(x1*width)-margin):min(width, round(x2*width)+margin)]
            progresses = ([_progress(t.text) for t in vision.read(crop).tokens
                           if t.score >= self.c.settings.ocr_confidence and _progress(t.text) is not None]
                          if crop.size else [])
            self._weekly_crop_progress[token] = progresses[0] if len(progresses) == 1 else None
        return self._weekly_crop_progress[token]

    def _bingo_cells(self, scene):
        # A completed line replaces card titles/progress with BINGO stamps.
        # These cells only establish coverage; they never authorize a claim.
        tokens = [t for t in self._trusted_tokens(scene) if normalize(t.text) == "bingo"]
        cells = set()
        for row, (top, bottom) in enumerate(WEEKLY_ROWS):
            for column, (left, right) in enumerate(WEEKLY_COLUMNS):
                body = (left + .008, top + .035, right - .008, bottom - .06)
                footer = (left + .008, bottom - .042, right - .008, bottom - .009)
                if (sum(t.within(body) for t in tokens) == 1
                        and sum(t.within(footer) for t in tokens) == 1):
                    cells.add((row, column))
        return cells

    def _card_coverage(self, scene):
        """OCR may confuse an uncompleted 2/4 with 214; card titles still bind all cells."""
        covered = set(self._card_progresses(scene)) | self._bingo_cells(scene)
        for row, (top, _) in enumerate(WEEKLY_ROWS):
            for column, (left, right) in enumerate(WEEKLY_COLUMNS):
                title_area = (left + .008, top + .035, right - .008, top + .09)
                if any(token.within(title_area) and len(re.findall(r"[\u4e00-\u9fff]", token.text)) >= 2
                       for token in self._trusted_tokens(scene)):
                    covered.add((row, column))
        return covered

    def _unverified_green_cells(self, scene):
        accepted = {candidate[:2] for candidate, _ in self._claimable_cards(scene)} | self._bingo_cells(scene)
        uncertain = []
        for row, (_, bottom) in enumerate(WEEKLY_ROWS):
            for column, (left, right) in enumerate(WEEKLY_COLUMNS):
                footer = (left + .008, bottom - .042, right - .008, bottom - .009)
                if (row, column) not in accepted and green_claim(scene, footer):
                    uncertain.append((row, column))
        return uncertain

    def _claimable_cards(self, scene):
        candidates = []
        bingo = self._bingo_cells(scene)
        for (row, column), (token, (value, maximum)) in self._card_progresses(scene).items():
            if (row, column) in bingo or maximum <= 0 or value != maximum:
                continue
            left, right = WEEKLY_COLUMNS[column]
            _, bottom = WEEKLY_ROWS[row]
            footer = (left + .008, bottom - .042, right - .008, bottom - .009)
            if green_claim(scene, footer):
                candidates.append(((row, column, value, maximum), token))
        return sorted(candidates, key=lambda candidate: candidate[0][:2])

    def _detail_claim(self, scene):
        chosen = self._weekly_card_chosen
        if chosen is None or not self._weekly_page(scene) or self._weekly_reward(scene):
            return None
        if not any(cell == chosen for cell, _ in self._claimable_cards(scene)):
            return None
        progresses = [_progress(token.text) for token in self._trusted_tokens(scene)
                      if token.within(WEEKLY_DETAIL) and _progress(token.text) is not None]
        buttons = [token for token in self._trusted_tokens(scene) if token.within(WEEKLY_DETAIL_CLAIM)
                   and normalize(token.text) == "领取"]
        if progresses != [(chosen[2], chosen[3])] or len(buttons) != 1:
            return None
        return buttons[0]

    def _milestone_labels(self, scene):
        if not self._weekly_page(scene) or self._weekly_reward(scene):
            return []
        labels = {}
        for token in self._trusted_tokens(scene):
            text = normalize(unicodedata.normalize("NFKC", token.text))
            if token.within(WEEKLY_MILESTONE_LABELS) and re.fullmatch(r"\d{3,4}", text):
                value = int(text)
                if 100 <= value <= 2100 and value % 100 == 0:
                    labels.setdefault(value, []).append(token)
        rows = [(value, token) for value, tokens in labels.items() for token in tokens]
        # One round marker may have one milestone value. Different numeric labels
        # on the same row, or repeated values, cannot authorize choosing one target.
        if (any(len(tokens) != 1 for tokens in labels.values())
                or any(abs(left.center[1] - right.center[1]) < .045
                       for index, (_, left) in enumerate(rows)
                       for _, right in rows[index + 1:])):
            return []
        return sorted(((value, tokens[0]) for value, tokens in labels.items()
                       if len(tokens) == 1), key=lambda item: item[1].center[1])

    def _milestones(self, scene):
        values = []
        for value, token in self._milestone_labels(scene):
            # Ticket values sit below their ticket; the round progress marker is to the
            # right of that row. A full marker crop is required to distinguish star/check.
            y = token.center[1] - .0145
            marker = (WEEKLY_MILESTONE_X - .017, y - .026,
                      WEEKLY_MILESTONE_X + .017, y + .026)
            if marker[1] < .215 or marker[3] > .81:
                continue
            if milestone_claimable(scene, marker):
                values.append((value, (WEEKLY_MILESTONE_X, y)))
        return values

    def _pane_signature(self, scene):
        return tuple((value, round(token.center[1], 3))
                     for value, token in self._milestone_labels(scene))

    def _send(self, scene, target, kind):
        if self._weekly_actions >= WEEKLY_ACTION_LIMIT:
            raise AutomationError("丽都周纪领取已到有限操作上限，请查看本次截图。")
        self.c.click(scene, target)
        # Context.click may interrupt before sending. Only a returned click starts a
        # transition wait; resume otherwise reads the actual page and chooses afresh.
        self._weekly_pending = kind
        self._weekly_actions += 1

    def _resume_pending(self):
        if self._weekly_pending is None:
            return self._snapshot()
        kind = self._weekly_pending
        chosen = self._weekly_card_chosen

        def ready(scene):
            if self._weekly_reward(scene):
                return kind != "confirm"
            if not self._weekly_page(scene):
                return False
            if kind == "card":
                return (self._detail_claim(scene) is not None
                        or (not any(cell == chosen for cell, _ in self._claimable_cards(scene))
                            and len(self._card_coverage(scene)) == 9))
            if kind == "claim":
                return (not any(cell == chosen for cell, _ in self._claimable_cards(scene))
                        and len(self._card_coverage(scene)) == 9)
            if kind == "confirm" and chosen is not None:
                return (not any(cell == chosen for cell, _ in self._claimable_cards(scene))
                        and len(self._card_coverage(scene)) == 9)
            if kind == "milestone" or (kind == "confirm" and self._weekly_milestone_chosen is not None):
                return not any(value == self._weekly_milestone_chosen
                               for value, _ in self._milestones(scene))
            return True

        scene = self._wait(ready, "丽都周纪领取后的已识别页面")
        self._weekly_reward_pending = self._weekly_reward(scene)
        self._weekly_pending = None
        return scene

    def _confirm_reward(self, scene):
        if not self._weekly_reward(scene):
            return self._partial("weekly", scene, "奖励页缺少已观测的标题、物品或唯一确认。")
        self.c.evidence(scene, "weekly_reward")
        self._send(scene, self._reward_confirms(scene)[0], "confirm")
        self._weekly_claims += 1
        after = self._resume_pending()
        if not self._weekly_reward(after):
            self._weekly_card_chosen = None
            self._weekly_milestone_chosen = None
        return after

    def _open_weekly(self):
        scene = self._resume_pending()
        if self._weekly_page(scene) or self._weekly_reward(scene):
            return scene
        scene = self._open_daily()
        entries = [token for token in self._trusted_tokens(scene) if token.within(WEEKLY_ENTRY)
                   and normalize(token.text) == "丽都周纪"]
        if len(entries) != 1:
            return self._partial("weekly", scene, "日常手册没有唯一的丽都周纪入口。")
        if not red_notification(scene, WEEKLY_NOTICE):
            self.ensure_main()
            after = self._snapshot()
            if not self._main(after):
                return self._partial("weekly", after, "周纪没有奖励红点，但退出手册后未核实主界面。")
            return self._ok("weekly", after, "丽都周纪入口没有奖励红点，当前无需领取。")
        self._send(scene, entries[0], "entry")
        return self._resume_pending()

    def _scroll_pane(self, scene, amount):
        # The weekly milestone list is the only permitted scroll surface here.
        if not self._weekly_page(scene) or self._weekly_reward(scene) or not self._milestone_labels(scene):
            return self._partial("weekly", scene, "积分特赏列表尚未完整识别，未滚动。")
        self.c.move(scene, WEEKLY_SCROLL_POINT)
        self.c.scroll(amount)
        return self._wait(lambda after: self._weekly_page(after) or self._weekly_reward(after),
                          "周纪积分特赏滚动后的页面")

    def weekly(self):
        """Collect eligible weekly cards, then earned milestones, and leave the page."""
        scene = self._open_weekly()
        if isinstance(scene, TaskResult):
            return scene
        while True:
            if self._weekly_reward(scene):
                scene = self._confirm_reward(scene)
                if isinstance(scene, TaskResult):
                    return scene
                continue
            if not self._weekly_page(scene):
                self._unknown(scene, "丽都周纪领取出现未知页面")
            claim = self._detail_claim(scene)
            if claim is not None:
                self.c.evidence(scene, "weekly_task_claimable")
                self._send(scene, claim, "claim")
                scene = self._resume_pending()
                if not self._weekly_reward(scene):
                    self._weekly_card_chosen = None
                continue
            candidates = self._claimable_cards(scene)
            if not candidates:
                self._weekly_card_chosen = None
                break
            self._weekly_card_chosen, target = candidates[0]
            self.c.evidence(scene, "weekly_card_claimable")
            self._send(scene, target, "card")
            scene = self._resume_pending()

        # All nine fixed cards are visible in the observed design. A clipped/failed
        # OCR card is not made clickable by scrolling or by its color alone.
        if len(self._card_coverage(scene)) != 9:
            return self._partial("weekly", scene, "未完整识别九张周纪卡片，已领取的奖励保留，其余未操作。")
        if self._unverified_green_cells(scene):
            return self._partial("weekly", scene, "绿色周纪卡片缺少唯一且已完成的进度，未猜测领取。")

        # First return the scrollable milestone pane to its top, then scan downward.
        previous = None
        for _ in range(WEEKLY_SCROLL_LIMIT):
            labels = self._milestone_labels(scene)
            signature = self._pane_signature(scene)
            if not labels:
                return self._partial("weekly", scene, "未识别积分特赏里程行。")
            if min(value for value, _ in labels) == 100 or signature == previous:
                break
            previous = signature
            scene = self._scroll_pane(scene, 360)
            if isinstance(scene, TaskResult):
                return scene
        else:
            return self._partial("weekly", scene, "积分特赏回顶部达到有限滚动上限。")

        previous = None
        for scroll_count in range(WEEKLY_SCROLL_LIMIT + 1):
            while True:
                if self._weekly_reward(scene):
                    scene = self._confirm_reward(scene)
                    if isinstance(scene, TaskResult):
                        return scene
                    continue
                if not self._weekly_page(scene):
                    self._unknown(scene, "周纪里程领取出现未知页面")
                candidates = self._milestones(scene)
                if not candidates:
                    break
                value, target = candidates[0]
                self.c.evidence(scene, f"weekly_milestone_{value}")
                self._weekly_milestone_chosen = value
                self._send(scene, target, "milestone")
                scene = self._resume_pending()
                if not self._weekly_reward(scene):
                    self._weekly_milestone_chosen = None
            signature = self._pane_signature(scene)
            if not signature:
                return self._partial("weekly", scene, "里程滚动后无法核实积分行。")
            if signature == previous:
                break
            if scroll_count == WEEKLY_SCROLL_LIMIT:
                return self._partial("weekly", scene, "里程奖励达到有限滚动上限，当前领取已保留。")
            previous = signature
            scene = self._scroll_pane(scene, -360)
            if isinstance(scene, TaskResult):
                return scene
        self.c.evidence(scene, "weekly_rewards_finished")
        self.c.key("esc", scene)
        self._wait(lambda frame: self._handbook(frame) or self._main(frame), "周纪退出后的手册或大世界")
        self.ensure_main()
        after = self._snapshot()
        if not self._main(after):
            return self._partial("weekly", after, "周纪领取完成，但返回时未核实主界面。")
        return self._ok("weekly", after, f"已领取可用丽都周纪任务和里程奖励（确认 {self._weekly_claims} 次）；未执行未完成的周纪任务。")
