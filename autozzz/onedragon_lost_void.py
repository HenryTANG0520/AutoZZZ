"""Keep Lost Void's terminal failure exit distinct from a cleared run."""
from contextlib import contextmanager
import copy
from functools import wraps


@contextmanager
def preserve_failed_exit(ctx):
    from zzz_od.application.hollow_zero.lost_void.operation.lost_void_run_level import LostVoidRunLevel

    original = LostVoidRunLevel.handle_fail_exit

    @wraps(original)
    def finish_exit(operation):
        result = original(operation)
        if (operation.ctx is ctx and result.is_success
                and result.status == LostVoidRunLevel.STATUS_COMPLETE):
            if operation.previous_node.name == "处理战斗失败":
                return operation.round_fail("迷失之地战斗失败，已撤退。")
            return operation.round_fail("迷失之地挑战重试已耗尽，已退出。")
        return result

    node = copy.copy(original.operation_node_annotation)
    node.op_method = finish_exit
    finish_exit.operation_node_annotation = node
    LostVoidRunLevel.handle_fail_exit = finish_exit
    try:
        yield
    finally:
        LostVoidRunLevel.handle_fail_exit = original
