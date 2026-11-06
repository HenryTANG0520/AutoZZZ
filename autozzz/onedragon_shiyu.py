"""式舆防卫战在退场清理后保留挑战与领奖结果。"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import copy
from functools import wraps
import re
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from one_dragon.base.operation.operation_round_result import OperationRoundResult
    from zzz_od.context.zzz_context import ZContext


_SCREEN = "式舆防卫战"
_PROGRESS_UNVERIFIED = "式舆防卫战剧变节点进度未核实"
_NEXT_UNVERIFIED = "式舆防卫战下一节点未核实"
_CLAIM_UNVERIFIED = "式舆防卫战领取奖励点击未核实"
_PENDING_REWARD_DAY = "autozzz_shiyu_reward_pending_day"
_PROGRESS = re.compile(r"(?:剧变节点[：:\s]*)?(\d{1,2})\s*([/／1１])\s*(\d{1,2})")


def _progress_pair(reads):
    pairs = set()
    for text in reads:
        match = _PROGRESS.fullmatch(text.strip())
        if match is None:
            continue
        current, separator, total = match.groups()
        # The documented slash-as-one OCR error has one digit on each side.
        if separator in "/／" or len(current) == len(total) == 1:
            pairs.add((int(current), int(total)))
    return pairs.pop() if len(pairs) == 1 else None


def _clear_pending_reward(record):
    if _PENDING_REWARD_DAY in record.data:
        record.data.pop(_PENDING_REWARD_DAY)
        record.save()


def _patch_node(stack, ctx, cls, name, handler, *, incoming=()):
    original = getattr(cls, name)

    @wraps(original)
    def wrapped(app):
        if app.ctx is ctx:
            return handler(app, original)
        return original(app)

    node = copy(original.operation_node_annotation)
    node.op_method = wrapped
    wrapped.operation_node_annotation = node
    if incoming:
        wrapped.operation_edge_annotation = [*original.operation_edge_annotation, *incoming]
    stack.callback(setattr, cls, name, original)
    setattr(cls, name, wrapped)


@contextmanager
def preserve_battle_failure(ctx: ZContext) -> Iterator[None]:
    from one_dragon.base.geometry.point import Point
    from one_dragon.base.operation.operation_edge import OperationEdgeDesc
    from zzz_od.application.shiyu_defense.shiyu_defense_app import MULTI_ROOM_NODES, ShiyuDefenseApp

    def choose_node(app, _original):
        if getattr(app, "node_clicked", False):
            # The SDK accepted this node's selection on the previous round.
            ready = app.round_by_find_area(app.last_screenshot, _SCREEN, "下一步")
            if ready.is_success:
                return app.round_success(ready.status, wait=1)
            return app.round_retry(_PROGRESS_UNVERIFIED, wait=1)
        area = app.ctx.screen_loader.get_area(_SCREEN, "剧变节点进度")
        reads = app.ctx.ocr.crop_and_run_ocr(app.last_screenshot, area.rect)
        record = app.run_record
        pending_day = record.get(_PENDING_REWARD_DAY)
        confirmed = False
        if reads:
            pair = _progress_pair(reads)
            if pair is None:
                return app.round_retry(_PROGRESS_UNVERIFIED, wait=1)
            current, total = pair
            confirmed = 0 < total <= 10 and current <= total
            if not confirmed and not record.critical_history:
                return app.round_retry(_PROGRESS_UNVERIFIED, wait=1)
            if confirmed:
                if pending_day and pending_day == record.get_current_dt() and current < total:
                    return app.round_retry(_PROGRESS_UNVERIFIED, wait=1)
                app.config.critical_max_node_idx = total
                next_idx = current + 1 if current < total else None
                if next_idx is not None:
                    _clear_pending_reward(record)
        # SDK resets node history every game day. Selecting a node from a
        # fallback needs saved progress and no pending claim.
        if not confirmed:
            next_idx = record.next_node_idx()
            if next_idx is not None and (pending_day or not record.critical_history):
                return app.round_retry(_PROGRESS_UNVERIFIED, wait=1)
        if next_idx is None:
            return app.round_success(ShiyuDefenseApp.STATUS_ALL_FINISHED)

        app.current_node_idx = next_idx
        area_name = f"节点-{next_idx:02d}"
        if next_idx in MULTI_ROOM_NODES:
            app.current_room_idx = 0
            app.room_teams = []
            result = app.round_by_find_and_click_area(app.last_screenshot, _SCREEN, area_name)
            return app.round_success(result.status, wait=1) if result.is_success else app.round_retry(result.status, wait=1)

        result = app.round_by_find_and_click_area(app.last_screenshot, _SCREEN, area_name)
        if result.is_success:
            return app.round_wait(result.status, wait=1)
        ready = app.round_by_find_area(app.last_screenshot, _SCREEN, "下一步")
        if ready.is_success:
            return app.round_success(ready.status, wait=1)
        start = app.ctx.screen_loader.get_area(_SCREEN, "节点区域").rect.center
        app.ctx.controller.drag_to(start=start, end=start + Point(-300, 0))
        return app.round_retry(result.status, wait=1)

    def next_node(app, original):
        if (app.current_node_idx == app.config.critical_max_node_idx
                or app.current_node_idx + 1 in MULTI_ROOM_NODES):
            return original(app)
        result = app.round_by_find_area(app.last_screenshot, _SCREEN, "下一步")
        if result.is_success:
            app.current_node_idx += 1
            return app.round_success(result.status, wait=1)
        result = app.round_by_find_and_click_area(app.last_screenshot, _SCREEN, "战斗结束-下一防线")
        if result.is_success:
            return app.round_wait(result.status, wait=1)
        return app.round_retry(_NEXT_UNVERIFIED, wait=1)

    def finish_nodes(app, original):
        result = original(app)
        if (result.is_success and not app.previous_node.is_success
                and app.current_node_idx < app.config.critical_max_node_idx):
            return app.round_success(_NEXT_UNVERIFIED)
        return result

    def claim_reward(app, _original):
        panel = app.round_by_find_area(app.last_screenshot, _SCREEN, "领取奖励-界面")
        if panel.is_success:
            click = app.round_by_click_area(_SCREEN, "全部领取", success_wait=1)
            if click.is_success:
                _clear_pending_reward(app.run_record)
                return click
            app.run_record.update(_PENDING_REWARD_DAY, app.run_record.get_current_dt())
            return app.round_fail(_CLAIM_UNVERIFIED, data=click.status)
        entry = app.round_by_click_area(_SCREEN, "奖励入口")
        if entry.is_success:
            return app.round_wait(entry.status, wait=.5)
        return app.round_retry(entry.status, wait=1)

    def close_reward(app, original):
        result = original(app)
        if result.is_success and app.previous_node.status == _CLAIM_UNVERIFIED:
            return app.round_success(_CLAIM_UNVERIFIED, data=app.previous_node.data)
        return result

    def finish(app, original) -> OperationRoundResult:
        result = original(app)
        if result.is_success:
            previous = app.previous_node
            if previous.status in (_PROGRESS_UNVERIFIED, _NEXT_UNVERIFIED, _CLAIM_UNVERIFIED):
                return app.round_fail(previous.status, data=previous.data)
            if (previous.name == "自动战斗" and previous.status is None) or previous.name == "多间-战斗失败":
                return app.round_fail("式舆防卫战战斗失败，已返回大世界。", data=result.data)
        return result

    with ExitStack() as stack:
        cls = ShiyuDefenseApp
        _patch_node(stack, ctx, cls, "choose_node_idx", choose_node)
        _patch_node(stack, ctx, cls, "to_next_node", next_node)
        _patch_node(stack, ctx, cls, "all_node_finished", finish_nodes)
        _patch_node(stack, ctx, cls, "claim_reward", claim_reward)
        _patch_node(stack, ctx, cls, "close_reward", close_reward,
                    incoming=(OperationEdgeDesc("领取奖励", success=False, status=_CLAIM_UNVERIFIED),))
        _patch_node(stack, ctx, cls, "back_after_all", finish,
                    incoming=(OperationEdgeDesc("选择节点", success=False, status=_PROGRESS_UNVERIFIED),
                              OperationEdgeDesc("所有节点完成", status=_NEXT_UNVERIFIED)))
        yield
