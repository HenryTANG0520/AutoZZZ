"""Keep a Withered Domain battle timeout distinct from a cleared mission."""
from contextvars import ContextVar
import copy
from functools import wraps


class WitheredExitControl:
    def __init__(self, ctx, control):
        self.ctx, self.control = ctx, control
        self._exit_reason = ContextVar("autozzz_withered_exit_reason", default=None)
        self._originals = []

    def _patch(self, cls, name, handler):
        original = getattr(cls, name)

        @wraps(original)
        def wrapped(operation):
            if operation.ctx is not self.ctx:
                return original(operation)
            return handler(operation, original)

        annotation = getattr(original, "operation_node_annotation", None)
        if annotation is not None:
            wrapped.operation_node_annotation = copy.copy(annotation)
            wrapped.operation_node_annotation.op_method = wrapped
        self._originals.append((cls, name, vars(cls).get(name)))
        setattr(cls, name, wrapped)

    def install(self):
        from one_dragon.base.screen import screen_utils
        from zzz_od.hollow_zero.hollow_battle import HollowBattle
        from zzz_od.hollow_zero.hollow_exit_by_menu import HollowExitByMenu
        from zzz_od.hollow_zero.hollow_runner import HollowRunner

        def run_hollow(runner, original):
            token = self._exit_reason.set("")
            try:
                return original(runner)
            finally:
                self._exit_reason.reset(token)

        def begin_failed_exit(battle, original):
            if self._exit_reason.get() is not None:
                # This node is reached only from battle timeout or movement failure.
                self._exit_reason.set(battle.previous_node.status)
            return original(battle)

        def check_screen(runner, original):
            reason = self._exit_reason.get()
            if not reason:
                return original(runner)
            frame = runner.last_screenshot
            unconfirmed = f"枯萎之都战斗退出（{reason}），未确认返回街区。"
            # The Matrix entry also has a Street button in the shared exit area.
            if screen_utils.is_target_screen(self.ctx, frame, "迷失之地-矩阵行动"):
                return runner.round_retry(unconfirmed, wait=1)
            if not screen_utils.is_target_screen(self.ctx, frame, "大世界-普通"):
                # Reuse the SDK's exit cleanup. The shared button carries no victory evidence.
                result = HollowExitByMenu.click_finish(runner)
                if not result.is_success:
                    result.status = unconfirmed
                    return result
            # The SDK counts entries on confirmed return, including voluntary exits.
            self.control.checkpoint(require_frame=True)
            runner.run_record.add_daily_times()
            return runner.round_fail(f"枯萎之都战斗退出（{reason}），已返回街区。")

        self._patch(HollowRunner, "execute", run_hollow)
        self._patch(HollowBattle, "move_fail", begin_failed_exit)
        self._patch(HollowRunner, "check_screen", check_screen)

    def close(self):
        for cls, name, original in reversed(self._originals):
            if original is None:
                delattr(cls, name)
            else:
                setattr(cls, name, original)
        self._originals.clear()
