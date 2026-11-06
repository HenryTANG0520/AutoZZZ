"""Elapsed-clock evidence for the observed native Area Patrol battle HUD."""
from __future__ import annotations

import math
import re


_ELAPSED = re.compile(r"([0-9]{2})[:：]([0-9]{2})[.．]([0-9]{2})")
_DIGIT_X = (1611, 1633, 1663, 1684, 1714, 1735)
_SEGMENTS = ((5, 12, 70, 73), (14, 16, 74, 79), (14, 16, 84, 90),
             (5, 12, 90, 93), (0, 2, 84, 90), (0, 2, 74, 79), (5, 12, 80, 83))
_DIGITS = {tuple(segment in lit for segment in 'abcdefg'): str(number) for number, lit in enumerate(
    ('abcdef', 'bc', 'abdeg', 'abcdg', 'bcfg', 'acdfg', 'acdefg', 'abc', 'abcdefg', 'abcdfg'))}
_CLOCK_FLOOR_CS = 200
_ROUNDING_SECONDS = .02


def _centiseconds(parts):
    minutes, seconds, fraction = map(int, parts)
    return minutes * 6000 + seconds * 100 + fraction if seconds < 60 else None


def _segment_clock(rgb):
    import cv2
    import numpy as np

    icon = rgb[53:105, 1776:1826]
    yellow = (icon[:, :, 0] > 145) & (icon[:, :, 1] > 145) & (icon[:, :, 2] < 80)
    if np.count_nonzero(yellow) < 600:
        return None
    gray = cv2.cvtColor(rgb[70:93, 1611:1752], cv2.COLOR_RGB2GRAY)
    digits = []
    for origin in _DIGIT_X:
        offset = origin - 1611
        cells = [np.median(gray[y1 - 70:y2 - 70, offset + x1:offset + x2])
                 for x1, x2, y1, y2 in _SEGMENTS]
        if any(100 < value < 160 for value in cells):
            return None
        digit = _DIGITS.get(tuple(value >= 160 for value in cells))
        if digit is None:
            return None
        digits.append(digit)
    return _centiseconds(''.join(digits[index:index + 2]) for index in (0, 2, 4))


def read_patrol_elapsed(ctx, image, captured_at):
    """Require a native usable HUD, stopwatch icon and two agreeing clock reads."""
    import cv2

    from .onedragon_battle_hud import observe_attack_hud

    height, width = image.shape[:2]
    if (min(height, width) <= 0 or image.dtype.name != 'uint8'
            or image.shape != (height, width, 3) or width * 9 != height * 16):
        return None
    if (height, width) != (1080, 1920):
        image = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)
    hud = observe_attack_hud(ctx.auto_battle_context, image, captured_at)
    if not hud.normal_available or hud.blocks_ordinary:
        return None
    segmented = _segment_clock(image)
    if segmented is None:
        return None
    clock = cv2.resize(image[65:97, 1604:1758], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    text = ctx.ocr.run_ocr_single_line(clock, threshold=.60)
    matched = _ELAPSED.fullmatch(''.join(text.split())) if isinstance(text, str) else None
    return segmented if matched and _centiseconds(matched.groups()) == segmented else None


class BattleClockWitness:
    """Reject a reset within a contiguous sequence of fresh elapsed clocks."""

    def __init__(self, gap):
        if type(gap) not in (int, float) or not 0 < gap <= .5:
            raise ValueError('原战斗时钟的连续采样间隔须在 0.5 秒内。')
        self.gap = gap
        self._last = None
        self._advanced_from = None
        self._moves = 0
        self.failure = None

    def observe(self, elapsed, started, finished):
        if self.failure is not None:
            return self.failure
        if type(elapsed) is not int or elapsed < _CLOCK_FLOOR_CS:
            self.failure = 'battle_clock_unreadable'
        elif (type(started) not in (int, float) or type(finished) not in (int, float)
              or not 0 <= started <= finished or not math.isfinite(finished)):
            self.failure = 'battle_clock_time_invalid'
        elif self._last is not None:
            old_elapsed, old_started, old_finished = self._last
            window = finished - old_started
            elapsed_delta = elapsed - old_elapsed
            if started <= old_started or finished <= old_finished or window > self.gap:
                self.failure = 'battle_clock_gap'
            elif elapsed_delta < 0 or elapsed_delta / 100 > window + _ROUNDING_SECONDS:
                self.failure = 'battle_clock_discontinuity'
            elif elapsed_delta:
                self._advanced_from = finished
                self._moves += 1
            elif finished - self._advanced_from > self.gap:
                self.failure = 'battle_clock_stale'
        if self.failure is None:
            if self._last is None:
                self._advanced_from = finished
            self._last = elapsed, started, finished
        return self.failure

    @property
    def progressed(self):
        return self.failure is None and self._moves > 0
