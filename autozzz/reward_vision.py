"""Small, state-bound colour checks for already-earned reward controls."""
from __future__ import annotations

import cv2
import numpy as np


def _patch(scene, area):
    image = scene.image
    if image is None or not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] < 3:
        return None
    height, width = image.shape[:2]
    left, top, right, bottom = area
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        return None
    patch = image[round(top * height):round(bottom * height), round(left * width):round(right * width), :3]
    if patch.size == 0:
        return None
    # Compare in the existing 1920-wide reference space, independent of display DPI.
    size = (max(1, round(patch.shape[1] * 1920 / width)),
            max(1, round(patch.shape[0] * 1920 / width)))
    return cv2.resize(patch, size, interpolation=cv2.INTER_AREA)


def red_notification(scene, area):
    patch = _patch(scene, area)
    if patch is None:
        return False
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 170, 170), (8, 255, 255)) | cv2.inRange(hsv, (172, 170, 170), (179, 255, 255))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    for _, _, width, height, pixels in stats[1:count]:
        if (8 <= width <= 45 and 6 <= height <= 45 and .6 <= width / height <= 1.8
                and .25 <= pixels / (width * height) <= .85):
            return True
    return False


def green_claim(scene, area):
    patch = _patch(scene, area)
    if patch is None:
        return False
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    # City-fund claim captions use yellow as well as lime; callers bind a
    # unique claim caption or an exact weekly card footer before using this.
    mask = cv2.inRange(hsv, (18, 100, 130), (88, 255, 255))
    return bool(np.mean(mask > 0) >= .14)


def milestone_claimable(scene, area):
    patch = _patch(scene, area)
    if patch is None:
        return False
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    # The bright, pale star differs from both the saturated claimed check and
    # the dark unreached circle. A green progress bar alone is insufficient.
    mask = cv2.inRange(hsv, (32, 35, 210), (80, 170, 255))
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    h, w = mask.shape
    for index in range(1, count):
        _, _, width, height, pixels = stats[index]
        cx, cy = centers[index]
        if (6 <= width <= 45 and 6 <= height <= 45 and .75 <= width / height <= 1.3
                and .15 <= pixels / (width * height) <= .50
                and abs(cx - w / 2) <= w * .22 and abs(cy - h / 2) <= h * .22):
            return True
    return False
