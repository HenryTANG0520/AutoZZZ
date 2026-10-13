"""Pixel regressions using cropped controls from user-supplied reward pages.

Fixtures contain no account identifiers, portraits, or full screenshots.
The crops are put back at their recorded positions on an otherwise black
canvas so DPI/size checks exercise real control dimensions.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

import cv2
import numpy as np

from autozzz.models import Scene
from autozzz.reward_vision import green_claim, milestone_claimable, red_notification


FIXTURES = Path(__file__).parent / "fixtures" / "rewards"
MANIFEST = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
ENTRIES = {item["file"]: item for item in MANIFEST["entries"]}


def control_scene(name, scale=1):
    item = ENTRIES[name + ".png"]
    width, height = item["source_dimensions"]
    # Build directly at the requested resolution without resampling a large
    # full screenshot. Only the tiny, sanitized control pixels are retained.
    image = np.zeros((round(height * scale), round(width * scale), 3), np.uint8)
    left, top, right, bottom = (round(value * scale) for value in item["source_box"])
    crop = cv2.imread(str(FIXTURES / item["file"]))
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    crop = cv2.resize(crop, (right - left, bottom - top), interpolation=interpolation)
    image[top:bottom, left:right] = crop
    return Scene([], image), tuple(item["normalized_source_box"])


class RewardVisionTests(unittest.TestCase):
    def test_fixture_provenance_hashes_and_boxes_are_preserved(self):
        self.assertEqual(len(MANIFEST["sources"]), 8)
        for item in MANIFEST["entries"]:
            with self.subTest(file=item["file"]):
                data = (FIXTURES / item["file"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
                image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                left, top, right, bottom = item["source_box"]
                self.assertEqual(image.shape[:2], (bottom - top, right - left))
                self.assertLess(image.size, 300_000)

    def test_weekly_claim_star_is_distinct_from_claimed_check_and_unreached_circle(self):
        for scale in (.5, 1, 2):
            for name, expected in (("weekly_milestone_claimable", True),
                                   ("weekly_milestone_claimed", False),
                                   ("weekly_milestone_locked", False)):
                with self.subTest(scale=scale, control=name):
                    scene, area = control_scene(name, scale)
                    self.assertEqual(milestone_claimable(scene, area), expected)

    def test_weekly_green_footer_excludes_blue_completed_and_incomplete_cards(self):
        for scale in (.5, 1, 2):
            for name, expected in (("weekly_task_claimable", True),
                                   ("weekly_task_claimed", False),
                                   ("weekly_task_incomplete", False)):
                with self.subTest(scale=scale, control=name):
                    scene, area = control_scene(name, scale)
                    self.assertEqual(green_claim(scene, area), expected)

    def test_real_red_notifications_exclude_plain_labels_and_yellow_new_markers(self):
        for scale in (.5, 1, 2):
            for name, expected in (("weekly_entry_notification", True),
                                   ("weekly_entry_plain", False),
                                   ("city_growth_notification", True),
                                   ("city_level_notification", True),
                                   ("city_level_plain", False),
                                   ("city_new_marker", False)):
                with self.subTest(scale=scale, control=name):
                    scene, area = control_scene(name, scale)
                    self.assertEqual(red_notification(scene, area), expected)

    def test_city_fund_claim_caption_accepts_yellow_growth_and_lime_level_text(self):
        # These are the actual RapidOCR caption boxes, not the entire black
        # button. The two pages place their enabled claim button differently.
        captions = (("city_claim_all_enabled", (.8708333333, .9390791454, .9265625, .9677886657)),
                    ("city_level_claim_all_enabled", (.684375, .9390791454, .7401041667, .9677886657)))
        for scale in (.5, 1, 2):
            for name, area in captions:
                with self.subTest(scale=scale, control=name):
                    scene, _ = control_scene(name, scale)
                    self.assertTrue(green_claim(scene, area))

    def test_unlock_caption_cannot_be_mistaken_for_an_enabled_claim(self):
        for scale in (.5, 1, 2):
            scene, area = control_scene("city_level_unlock", scale)
            with self.subTest(scale=scale):
                self.assertFalse(green_claim(scene, area))
                self.assertFalse(red_notification(scene, area))
                self.assertFalse(milestone_claimable(scene, area))

    def test_notification_is_bound_to_the_requested_control_region(self):
        scene, _ = control_scene("city_level_notification")
        self.assertTrue(red_notification(scene, (.61, .002, .642, .05)))
        self.assertFalse(red_notification(scene, (.73, .002, .756, .05)))

    def test_plain_red_rectangle_and_tiny_red_noise_are_not_notification_icons(self):
        image = np.zeros((1080, 1920, 3), np.uint8)
        image[100:125, 100:125] = (0, 0, 255)
        image[110:113, 145:148] = (0, 0, 255)
        self.assertFalse(red_notification(Scene([], image), (90/1920, 90/1080, 160/1920, 140/1080)))

    def test_progress_green_or_pale_rectangle_without_star_is_not_claimable_milestone(self):
        for color in ((0, 255, 0), (140, 255, 231)):
            with self.subTest(color=color):
                image = np.zeros((1080, 1920, 3), np.uint8)
                image[100:125, 100:125] = color
                self.assertFalse(milestone_claimable(Scene([], image), (90/1920, 90/1080, 140/1920, 140/1080)))

    def test_missing_or_non_color_capture_is_rejected(self):
        for image in (None, "missing", np.zeros((100, 100), np.uint8),
                      np.zeros((0, 0, 3), np.uint8), np.zeros((100, 100, 1), np.uint8)):
            for helper in (red_notification, green_claim, milestone_claimable):
                with self.subTest(image_type=type(image).__name__, helper=helper.__name__):
                    self.assertFalse(helper(Scene([], image), (0, 0, 1, 1)))

    def test_out_of_bounds_or_empty_regions_are_rejected(self):
        scene = Scene([], np.full((108, 192, 3), (0, 255, 0), np.uint8))
        for area in ((-.1, 0, 1, 1), (0, 0, 1.1, 1), (0, 0, 0, 1), (0, .8, 1, .7)):
            for helper in (red_notification, green_claim, milestone_claimable):
                with self.subTest(area=area, helper=helper.__name__):
                    self.assertFalse(helper(scene, area))


if __name__ == "__main__":
    unittest.main()
