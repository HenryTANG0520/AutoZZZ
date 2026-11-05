"""Read the fixed OneDragon chain-prompt evidence before allowing battle input."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unicodedata import normalize

import numpy as np
from one_dragon.utils import cv2_utils
from one_dragon.utils.i18_utils import gt

if TYPE_CHECKING:
    from zzz_od.game_data.agent import Agent


@dataclass(frozen=True)
class ChainPortraitEvidence:
    side: int  # OneDragon's 连携技-1 / 连携技-2.
    agent: Agent
    confidence: float


@dataclass(frozen=True)
class ChainPromptEvidence:
    portraits: tuple[ChainPortraitEvidence, ...]
    bar_pixels: int
    full_bar: bool


def _contains(frame, rect):
    return (0 <= rect.x1 < rect.x2 <= frame.shape[1]
            and 0 <= rect.y1 < rect.y2 <= frame.shape[0])


def _read_bar(cv_service, screen, screenshot_time, rect):
    # Unlike AutoBattleContext._check_chain_bar, this does not publish a
    # trigger. The existing SDK pipeline computes the contour and HSV mask.
    result = cv_service.run_pipeline('战斗-连携条', screen, debug_mode=False,
                                     start_time=screenshot_time, timeout=1.0)
    if result is None or result.error_str is not None:
        return 0, False
    mask = result.mask_image
    if (mask is None or mask.shape != (rect.height, rect.width)
            or result.crop_offset != (rect.x1, rect.y1)):
        return 0, False
    columns = np.any(mask != 0, axis=0)
    boundaries = np.flatnonzero(np.diff(np.pad(columns, 1)))
    width = int(max(boundaries[1::2] - boundaries[::2], default=0))
    full = bool(result.is_success and result.contours and width >= rect.width * .85)
    return width, full


def _read_portrait(ctx, screen, rect, candidates, side, threshold):
    crop = cv2_utils.crop_image_only(screen, rect)
    best = None
    for agent, preferred in candidates:
        template_ids = dict.fromkeys([preferred, *agent.template_id_list])
        for template_id in template_ids:
            if template_id is None:
                continue
            name = f'avatar_chain_{template_id}'
            template = ctx.template_loader.get_template('battle', name)
            if template is None or template.raw is None or template.mask is None:
                continue
            match = ctx.tm.match_template(crop, 'battle', name, threshold=threshold).max
            if match is None or match.w < rect.width * .7 or match.h < rect.height * .7:
                continue
            if (abs(match.center.x - rect.width / 2) > rect.width * .10
                    or abs(match.center.y - rect.height / 2) > rect.height * .10):
                continue
            if best is None or match.confidence > best.confidence:
                best = ChainPortraitEvidence(side, agent, match.confidence)
    return best


def _has_label(ctx, frame, screen_name, area_name):
    area = ctx.screen_loader.get_area(screen_name, area_name)
    if area is None or not area.is_text_area or not _contains(frame, area.rect):
        return False
    expected = ''.join(normalize('NFKC', gt(area.text, 'game')).casefold().split())
    results = ctx.ocr_service.get_ocr_result_list(image=frame, rect=area.rect,
                                                color_range=area.color_range)
    return any(''.join(normalize('NFKC', row.data).casefold().split()) == expected for row in results)


def _interrupted_screen(ctx, screen):
    if _has_label(ctx, screen, '战斗-菜单', '按钮-战况'):
        if (_has_label(ctx, screen, '战斗-菜单', '按钮-设置')
                or _has_label(ctx, screen, '战斗-菜单', '按钮-退出战斗')):
            return True
    return any(_has_label(ctx, screen, screen_name, area_name) for screen_name, area_name in (
        ('战斗-挑战结果-失败', '标题-挑战结果'),
        ('战斗画面', '战斗结果-完成'),
        ('战斗画面', '战斗结果-撤退'),
        ('战斗画面', '战斗结果-退出'),
        ('通用-出战', '按钮-出战'),
        ('恢复电量', '标题-恢复电量'),
    ))


def observe_chain_prompt(battle, screen, screenshot_time) -> ChainPromptEvidence | None:
    """Return evidence for one normalized RGB frame without changing battle state."""
    if (not isinstance(screen, np.ndarray) or screen.ndim != 3
            or screen.shape[2] != 3 or screen.dtype != np.uint8):
        return None
    ctx = getattr(battle, 'ctx', None)
    loader = getattr(ctx, 'screen_loader', None)
    if loader is None:
        return None
    project = getattr(ctx, 'project_config', None)
    if project is not None and screen.shape[:2] != (project.screen_standard_height, project.screen_standard_width):
        return None
    bar, left, right = (loader.get_area('战斗画面', name) for name in ('连携条', '连携技-1', '连携技-2'))
    if any(area is None or not _contains(screen, area.rect) for area in (bar, left, right)):
        return None
    cv_service = getattr(ctx, 'cv_service', None)
    if cv_service is None:
        return None

    width, full = _read_bar(cv_service, screen, screenshot_time, bar.rect)
    if not full and width < bar.rect.width * .25:
        return None
    if getattr(ctx, 'ocr_service', None) is None:
        return None

    candidates = battle.agent_context.get_possible_agent_list()
    if not candidates:
        return None
    # The source recognizes a whole bar and can identify only one side when
    # the other is a Bangboo. A shorter bar needs two stronger portraits.
    threshold = .8 if full else .9
    portraits = tuple(portrait for side, area in enumerate((left, right), 1)
                      if (portrait := _read_portrait(ctx, screen, area.rect, candidates, side, threshold)) is not None)
    if not portraits or (not full and len({portrait.agent.agent_id for portrait in portraits}) < 2):
        return None
    if _interrupted_screen(ctx, screen):
        return None
    return ChainPromptEvidence(portraits, width, full)
