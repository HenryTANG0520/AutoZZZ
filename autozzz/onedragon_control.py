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


class ExecutionControl:
    def __init__(self, probe: Callable[[], WindowState], release: Callable[[], None],
                 pause_backend: Callable[[], None], resume_backend: Callable[[], None],
                 stop_backend: Callable[[], None]):
        self.probe = probe
        self.release = release
        self.pause_backend, self.resume_backend, self.stop_backend = pause_backend, resume_backend, stop_backend
        self.lock = threading.RLock()
        self._transitions = threading.RLock()
        self.epoch = 0
        self.run_epoch = 0
        self.observed_epoch: int | None = None
        self.geometry: tuple[int, int, int, int] | None = None
        self.manual_paused = self.focus_waiting = self.stopped = False
        self.backend_paused = False
        self.error = ""
        self.changed = threading.Event()
        self.input_validator: Callable[[], None] | None = None

    @property
    def task_epoch(self) -> int:
        value = _observation_epoch.get()
        return self.epoch if value is None else value

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
        self.changed.set()

    def _apply_pause(self) -> None:
        desired = self.manual_paused or self.focus_waiting
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
            if self.stopped:
                return self.status()
            with self.lock:
                if self.manual_paused != paused:
                    self.manual_paused = paused
                    self._invalidate()
            self._apply_pause()
            return self.status()

    def toggle_pause(self) -> dict:
        return self.set_manual_pause(not self.manual_paused)

    def reset(self) -> None:
        """实际结束上一运行后调用；代次继续增长，旧异步任务不会重新有效。"""
        with self._transitions, self.lock:
            self.manual_paused = self.focus_waiting = self.backend_paused = self.stopped = False
            self.error = ""
            self._invalidate()
            self.run_epoch = self.epoch

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
            state = "stopped" if self.stopped else "paused" if self.manual_paused else "focus_wait" if self.focus_waiting else "running"
            return dict(state=state, manual_paused=self.manual_paused, focus_waiting=self.focus_waiting,
                        observation_epoch=self.epoch, fresh_frame=self.observed_epoch == self.epoch,
                        error=self.error)

    def checkpoint(self, *, require_frame: bool = False) -> None:
        self.poll()
        with self.lock:
            if self.stopped:
                raise ExecutionStopped(self.error or "已停止。")
            if self.manual_paused or self.focus_waiting:
                raise ObservationExpired("等待解除暂停或游戏恢复焦点。")
            if self.task_epoch != self.epoch:
                raise ObservationExpired("该任务来自暂停前的观察，等待重新识别。")
            if require_frame and self.observed_epoch != self.epoch:
                raise ObservationExpired("等待当前窗口的新截图。")

    def observe(self, capture: Callable[[], Any]) -> Any:
        self.checkpoint()
        epoch = self.epoch
        result = capture()
        self.checkpoint()
        with self.lock:
            if epoch != self.epoch:
                raise ObservationExpired("截图期间窗口状态发生变化。")
            self.observed_epoch = epoch
        return result

    def discard_frame(self) -> None:
        """截图失效时松键并换代；重新建立条件执行器后仍须取得新图。"""
        with self._transitions:
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
            if self.stopped or self.manual_paused or self.focus_waiting or self.task_epoch != self.epoch:
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
                if self.stopped or self.manual_paused or self.focus_waiting or epoch != self.epoch:
                    return False
                if self.input_validator is not None:
                    try:self.input_validator()
                    except ObservationExpired:return False
            self.changed.wait(min(.01, max(0, deadline - time.monotonic())))
            self.changed.clear()
        return True


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
            if (not control.stopped and not control.manual_paused and not control.focus_waiting
                    and control.task_epoch == control.epoch):
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
