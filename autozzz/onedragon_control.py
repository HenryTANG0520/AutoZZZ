"""一条龙执行的暂停、焦点与观察代次；不依赖截图或输入设备。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import threading
import time
from typing import Any, Callable, Iterator

_observation_epoch: ContextVar[int | None] = ContextVar("autozzz_observation_epoch", default=None)


def validate_runtime_timeout(seconds):
    if type(seconds)is not int or seconds<30:raise ValueError('运行时限须为不小于 30 的整数秒。')


def validate_finish_after_battle(app_id, enabled, *, auto_launch=False):
    if type(enabled) is not bool:
        raise ValueError('结算后停止开关须为布尔值。')
    if enabled and (app_id != 'auto_battle' or auto_launch):
        raise ValueError('结算后停止仅用于已进入战斗的独立自动战斗。')


class ObservationExpired(RuntimeError):
    pass


class ExecutionStopped(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowState:
    valid: bool
    focused: bool
    geometry: tuple[int, int, int, int] | None = None
    reason: str = ""


@dataclass(frozen=True, eq=False)
class CaptureReceipt:
    epoch: int
    sequence: int
    started: float
    finished: float
    wall_time: float
    screen: Any


class ExecutionControl:
    def __init__(self, probe: Callable[[], WindowState], release: Callable[[], None],
                 pause_backend: Callable[[], None], resume_backend: Callable[[], None],
                 stop_backend: Callable[[], None]):
        self.probe = probe
        self.release = release
        self.pause_backend, self.resume_backend, self.stop_backend = pause_backend, resume_backend, stop_backend
        self.lock = threading.RLock()
        self.capture_changed = threading.Condition(self.lock)
        self.capture_receipt: CaptureReceipt | None = None
        self._capture_sequence = 0
        self._transitions = threading.RLock()
        self.epoch = 0
        self.run_epoch = 0
        self.observed_epoch: int | None = None
        self.geometry: tuple[int, int, int, int] | None = None
        self.manual_paused = self.focus_waiting = self.stopped = False
        self.backend_paused = False
        self._recovery_blocked = False
        self._recovery_continuing = False
        self.error = ""
        self.changed = threading.Event()
        self.input_validator: Callable[[], None] | None = None
        self.capture_observer: Callable[[Callable[[], Any]], Any] | None = None
        self.recovery_window_invalid: Callable[[], None] | None = None

    @property
    def task_epoch(self) -> int:
        value = _observation_epoch.get()
        return self.epoch if value is None else value

    @property
    def recovery_blocked(self) -> bool:
        with self.lock:
            return self._recovery_blocked

    @property
    def recovery_continuing(self) -> bool:
        with self.lock:
            return self._recovery_continuing

    @contextmanager
    def scope(self, epoch: int | None = None) -> Iterator[None]:
        token = _observation_epoch.set(self.epoch if epoch is None else epoch)
        try:
            yield
        finally:
            _observation_epoch.reset(token)

    def _invalidate(self) -> None:
        self.epoch += 1
        self.observed_epoch = None
        self.capture_receipt = None
        self.capture_changed.notify_all()
        self.changed.set()

    def _apply_pause(self) -> None:
        desired = self.manual_paused or self.focus_waiting or self._recovery_blocked
        if desired and not self.backend_paused:
            try:
                self.release()
                self.backend_paused = self.pause_backend() is not False
            except Exception as exc:
                try:
                    self.stop(f"暂停或释放按键失败：{exc}")
                except Exception:
                    pass  # stop 已保持禁止输入；此处保留最初的暂停异常。
                raise
        elif not desired and self.backend_paused and not self.stopped:
            self.backend_paused = False
            self.resume_backend()

    def poll(self) -> dict:
        with self._transitions:
            if not self.stopped:
                try:
                    state = self.probe()
                except Exception as exc:
                    self.stop(f"无法核实游戏窗口：{exc}")
                else:
                    if not state.valid:
                        self.stop(state.reason or "游戏窗口已关闭或身份发生变化。")
                    elif self._recovery_blocked or self._recovery_continuing:
                        if not state.focused or (self.geometry is not None and state.geometry != self.geometry):
                            if self.recovery_window_invalid is not None:
                                self.recovery_window_invalid()
                            if self._recovery_continuing:
                                self.stop("续接期间失去原窗口焦点或客户区发生变化。")
                    else:
                        with self.lock:
                            geometry_changed = state.geometry is not None and self.geometry != state.geometry
                            had_geometry = self.geometry is not None
                            focus_changed = self.focus_waiting != (not state.focused)
                            if geometry_changed or focus_changed:
                                self._invalidate()
                            if state.geometry is not None:
                                self.geometry = state.geometry
                            self.focus_waiting = not state.focused
                        if geometry_changed:
                            self.release()
                            if had_geometry and not self.backend_paused:
                                self.backend_paused = self.pause_backend() is not False
                        self._apply_pause()
            return self.status()

    def set_manual_pause(self, paused: bool) -> dict:
        with self._transitions:
            if self.stopped or self._recovery_blocked or self._recovery_continuing:
                return self.status()
            with self.lock:
                if self.manual_paused != paused:
                    self.manual_paused = paused
                    self._invalidate()
            self._apply_pause()
            return self.status()

    def toggle_pause(self) -> dict:
        return self.set_manual_pause(not self.manual_paused)

    def block_for_recovery(self) -> dict:
        """冻结原任务的观察和输入，保留原任务供停止和核对。"""
        with self._transitions:
            with self.lock:
                if self._recovery_blocked:
                    return self.status()
                self._recovery_blocked = True
                self._invalidate()
            if not self.stopped:
                try:
                    self.release()
                    if not self.backend_paused:
                        self.backend_paused = self.pause_backend() is not False
                    if not self.backend_paused:
                        raise RuntimeError("未确认原任务已暂停。")
                except Exception as exc:
                    try:
                        self.stop(f"恢复阻塞清理失败：{exc}")
                    except RuntimeError:
                        pass  # stop 已禁止输入并记录错误。
            return self.status()

    def reset(self) -> None:
        """实际结束上一运行后调用；代次继续增长，旧异步任务不会重新有效。"""
        with self._transitions, self.lock:
            if self._recovery_blocked or self._recovery_continuing:
                raise RuntimeError("原任务已因恢复核对阻塞，不能重置。")
            self.manual_paused = self.focus_waiting = self.backend_paused = self.stopped = False
            self.error = ""
            self._invalidate()
            self.run_epoch = self.epoch

    def continue_recovery(self, epoch: int, authorize: Callable[[], bool]) -> bool:
        """Allow the original SDK to acquire a new frame. Device input has a separate gate."""
        with self._transitions:
            self.poll()
            with self.lock:
                if (self.stopped or not self._recovery_blocked or self._recovery_continuing
                        or not self.backend_paused or self.manual_paused or self.focus_waiting
                        or self.epoch != epoch or not authorize()):
                    return False
                self._invalidate()
                self._recovery_blocked = self.backend_paused = False
                self._recovery_continuing = True
            try:
                self.resume_backend()
            except Exception:
                self.stop("原 SDK 续接未完成。")
                return False
            return not self.stopped

    def stop(self, error: str = "") -> dict:
        with self._transitions:
            with self.lock:
                if self.stopped:
                    return self.status()
                self.stopped = True
                self.error = error
                self._invalidate()
            failures = []
            for cleanup in (self.release, self.stop_backend):
                try:
                    cleanup()
                except Exception as exc:
                    failures.append(exc)
            if failures:
                with self.lock:
                    self.error = self.error or f"停止清理失败：{failures[0]}"
                raise RuntimeError(self.error) from failures[0]
            return self.status()

    def status(self) -> dict:
        with self.lock:
            if self.stopped:
                state = "stopped"
            elif self._recovery_blocked:
                state = "recovery_blocked"
            else:
                state = "paused" if self.manual_paused else "focus_wait" if self.focus_waiting else "running"
            result = dict(state=state, manual_paused=self.manual_paused, focus_waiting=self.focus_waiting,
                          observation_epoch=self.epoch, fresh_frame=self.observed_epoch == self.epoch,
                          error=self.error)
            if self._recovery_blocked:
                result["recovery_blocked"] = True
            if self._recovery_continuing:
                result["recovery_continuing"] = True
            return result

    def checkpoint(self, *, require_frame: bool = False) -> None:
        self.poll()
        with self.lock:
            if self.stopped:
                raise ExecutionStopped(self.error or "已停止。")
            if self._recovery_blocked:
                raise ObservationExpired("原任务已因恢复核对阻塞。")
            if self.manual_paused or self.focus_waiting:
                raise ObservationExpired("等待解除暂停或游戏恢复焦点。")
            if self.task_epoch != self.epoch:
                raise ObservationExpired("该任务来自暂停前的观察，等待重新识别。")
            if require_frame and self.observed_epoch != self.epoch:
                raise ObservationExpired("等待当前窗口的新截图。")

    def observe(self, capture: Callable[[], Any], *, managed: bool = False) -> Any:
        self.checkpoint()
        epoch = self.epoch
        if managed:
            with self.lock:
                epoch = self.epoch
                if (self.stopped or self._recovery_blocked or self.manual_paused or self.focus_waiting
                        or self.task_epoch != epoch):
                    raise ObservationExpired("截图开始前状态发生变化。")
                self._capture_sequence += 1
                sequence = self._capture_sequence
                started = time.perf_counter()
                self.capture_receipt = None
                self.capture_changed.notify_all()
        observer = self.capture_observer
        result = capture() if observer is None else observer(capture)
        if managed:
            finished = time.perf_counter()
        self.checkpoint()
        with self.lock:
            if epoch != self.epoch:
                raise ObservationExpired("截图期间窗口状态发生变化。")
            self.observed_epoch = epoch
            if managed and sequence == self._capture_sequence:
                wall_time, screen = result
                self.capture_receipt = CaptureReceipt(epoch, sequence, started, finished, wall_time, screen)
                self.capture_changed.notify_all()
        return result

    def discard_frame(self) -> None:
        """截图失效时松键并换代；重新建立条件执行器后仍须取得新图。"""
        with self._transitions:
            if self._recovery_blocked:
                return
            if self._recovery_continuing:
                self.stop("续接期间截图失效。")
                return
            with self.lock:self._invalidate()
            self.release()
            if self.stopped:return
            with self.scope():
                if not self.backend_paused:self.backend_paused=self.pause_backend() is not False
                self._apply_pause()

    def input(self, action: Callable[[], Any]) -> Any:
        self.checkpoint(require_frame=True)
        with self.lock:
            # 短小的物理输入与代次变更互斥，不能在检查后由另一线程恢复旧输入。
            if (self.stopped or self._recovery_blocked or self.manual_paused or self.focus_waiting
                    or self.task_epoch != self.epoch):
                raise ObservationExpired("输入前状态发生变化。")
            if self.input_validator is not None:self.input_validator()
            return action()

    def hold(self, seconds: float) -> bool:
        """已按下后的等待；中断即返回，调用方用 finally 松键。"""
        epoch = self.task_epoch
        deadline = time.monotonic() + max(0, seconds)
        while time.monotonic() < deadline:
            self.poll()
            with self.lock:
                if self.stopped or self._recovery_blocked or self.manual_paused or self.focus_waiting or epoch != self.epoch:
                    return False
                if self.input_validator is not None:
                    try:self.input_validator()
                    except ObservationExpired:return False
            self.changed.wait(min(.01, max(0, deadline - time.monotonic())))
            self.changed.clear()
        with self.lock:
            return not self._recovery_blocked


class UpstreamControlAdapter:
    """仅在独立进程内适配固定上游的轮次、异步任务与状态发布。"""
    def __init__(self, control: ExecutionControl):
        self.control = control
        self.originals: list[tuple[Any, str, Any]] = []

    def install(self) -> None:
        if self.originals:
            raise RuntimeError("执行适配器已经安装。")
        from concurrent.futures import ThreadPoolExecutor, Future
        from contextvars import copy_context
        from one_dragon.base.operation.operation import Operation
        from one_dragon.base.conditional_operation.state_record_service import StateRecordService
        control = self.control
        submit = ThreadPoolExecutor.submit
        execute_round = Operation._execute_one_round
        publish = StateRecordService.batch_update_states
        add_callback = Future.add_done_callback

        def scoped_submit(executor, fn, *args, **kwargs):
            context = copy_context()
            epoch = control.task_epoch
            def invoke():
                with control.scope(epoch):
                    return fn(*args, **kwargs)
            return submit(executor, context.run, invoke)

        def guarded_round(operation):
            try:
                if control.task_epoch < control.run_epoch:
                    return operation.round_fail("旧运行的任务已作废")
                with control.scope():
                    control.checkpoint()
                    return execute_round(operation)
            except ObservationExpired:
                return operation.round_wait("等待重新识别", wait=.05)
            except ExecutionStopped:
                return operation.round_fail("任务已停止")

        def guarded_publish(service, records):
            with control.lock:
                if (not control.stopped and not control._recovery_blocked and not control.manual_paused
                        and not control.focus_waiting and control.task_epoch == control.epoch):
                    return publish(service, records)
            return None

        def scoped_callback(future, fn):
            context = copy_context()
            epoch = control.task_epoch
            def callback(completed):
                def invoke():
                    with control.scope(epoch):
                        return fn(completed)
                return context.run(invoke)
            return add_callback(future, callback)

        for cls, name, replacement in ((ThreadPoolExecutor, "submit", scoped_submit),
                                       (Operation, "_execute_one_round", guarded_round),
                                       (StateRecordService, "batch_update_states", guarded_publish),
                                       (Future, "add_done_callback", scoped_callback)):
            self.originals.append((cls, name, getattr(cls, name)))
            setattr(cls, name, replacement)

    def close(self) -> None:
        for cls, name, original in reversed(self.originals):
            setattr(cls, name, original)
        self.originals.clear()
