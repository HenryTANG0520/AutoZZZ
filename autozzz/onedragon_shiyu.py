"""式舆防卫战在完成退场清理后保留战斗失败结果。"""
from __future__ import annotations

from contextlib import contextmanager
from copy import copy
from functools import wraps
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from one_dragon.base.operation.operation_round_result import OperationRoundResult
    from zzz_od.context.zzz_context import ZContext


@contextmanager
def preserve_battle_failure(ctx: ZContext) -> Iterator[None]:
    from zzz_od.application.shiyu_defense.shiyu_defense_app import ShiyuDefenseApp

    original = ShiyuDefenseApp.back_after_all

    @wraps(original)
    def finish(app: ShiyuDefenseApp) -> OperationRoundResult:
        result = original(app)
        if app.ctx is ctx and result.is_success:
            previous = app.previous_node
            if (previous.name == "自动战斗" and previous.status is None) or previous.name == "多间-战斗失败":
                return app.round_fail("式舆防卫战战斗失败，已返回大世界。", data=result.data)
        return result

    node = copy(original.operation_node_annotation)
    node.op_method = finish
    finish.operation_node_annotation = node
    ShiyuDefenseApp.back_after_all = finish
    try:
        yield
    finally:
        ShiyuDefenseApp.back_after_all = original
