"""核实并监视启动 SDK worker 的原 Windows 宿主。"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import json
import os
import threading
import time

from .task_lease import _created


HOST_LOSS_GRACE_SECONDS = 30.0
_EOF_EXIT_RACE_SECONDS = .15
_POLL_SECONDS = .02
_WAIT_EXITED = 0
_WAIT_ALIVE = 258
_SYNCHRONIZE = 0x100000
_PROCESS_ACCESS = _SYNCHRONIZE | 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION


class _ProcessEntry(C.Structure):
    _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD), ('heap', C.c_size_t),
                ('module', W.DWORD), ('threads', W.DWORD), ('parent', W.DWORD), ('priority', W.LONG),
                ('flags', W.DWORD), ('exe', W.WCHAR * 260)]


def _kernel():
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
    for entry in (kernel.Process32FirstW, kernel.Process32NextW):
        entry.argtypes = [W.HANDLE, C.POINTER(_ProcessEntry)]
        entry.restype = W.BOOL
    kernel.CreateEventW.argtypes = [C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR]
    kernel.CreateEventW.restype = W.HANDLE
    kernel.DuplicateHandle.argtypes = [W.HANDLE, W.HANDLE, W.HANDLE, C.POINTER(W.HANDLE),
                                       W.DWORD, W.BOOL, W.DWORD]
    kernel.DuplicateHandle.restype = W.BOOL
    kernel.SetEvent.argtypes = [W.HANDLE]
    kernel.SetEvent.restype = W.BOOL
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    kernel.WaitForSingleObject.restype = W.DWORD
    kernel.OpenThread.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenThread.restype = W.HANDLE
    kernel.CancelSynchronousIo.argtypes = [W.HANDLE]
    kernel.CancelSynchronousIo.restype = W.BOOL
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.CloseHandle.restype = W.BOOL
    return kernel


def _snapshot_parents(kernel):
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if not snapshot or snapshot == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        parents = {}
        item = _ProcessEntry()
        item.size = C.sizeof(item)
        read = kernel.Process32FirstW
        while read(snapshot, C.byref(item)):
            parents[item.pid] = item.parent
            read = kernel.Process32NextW
        if C.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise C.WinError(C.get_last_error())
        return parents
    finally:
        kernel.CloseHandle(snapshot)


def _original_ancestor(kernel, pid, created):
    return _ancestor_of(kernel, pid, created, os.getpid(), _created(kernel, kernel.GetCurrentProcess()))


def _ancestor_of(kernel, pid, created, current, current_created):
    parents = _snapshot_parents(kernel)
    seen = {current}
    for _ in range(12):
        parent = parents.get(current)
        if not parent or parent in seen:
            return False
        if parent == pid:
            return created <= current_created
        handle = kernel.OpenProcess(_PROCESS_ACCESS, False, parent)
        if not handle:
            return False
        try:
            parent_created = _created(kernel, handle)
            if parent_created > current_created or kernel.WaitForSingleObject(handle, 0) != _WAIT_ALIVE:
                return False
        finally:
            kernel.CloseHandle(handle)
        # Windows 的 venv 启动器可能是宿主与解释器之间的一层父进程。
        seen.add(parent)
        current, current_created = parent, parent_created
    return False


def _duplicate_wait_handle(kernel, source, handle):
    duplicate = W.HANDLE()
    if not kernel.DuplicateHandle(source, handle, kernel.GetCurrentProcess(), C.byref(duplicate),
                                  _SYNCHRONIZE, False, 0):
        raise C.WinError(C.get_last_error())
    return duplicate.value


class HostLossOwner:
    """由真正调用 Popen 的宿主创建，关闭意图在宿主退出后仍可由 worker 读取。"""

    def __init__(self):
        self.kernel = _kernel()
        self.created = _created(self.kernel, self.kernel.GetCurrentProcess())
        self._lock = threading.Lock()
        self._close_event = self._stop_event = None
        try:
            self._close_event = self._event()
            self._stop_event = self._event()
        except BaseException:
            self.close()
            raise

    def _event(self):
        event = self.kernel.CreateEventW(None, True, False, None)
        if not event:
            raise C.WinError(C.get_last_error())
        return event

    def descriptor(self) -> str:
        with self._lock:
            if self._close_event is None or self._stop_event is None:
                raise RuntimeError('原 SDK 宿主的关闭信号已释放。')
            return json.dumps(dict(version=1, pid=os.getpid(), created=self.created,
                                   close_handle=self._close_event, stop_handle=self._stop_event),
                              separators=(',', ':'))

    def _signal(self, name: str) -> bool:
        with self._lock:
            handle = getattr(self, name)
            return handle is not None and bool(self.kernel.SetEvent(handle))

    def signal_stop(self) -> bool:
        return self._signal('_stop_event')

    def signal_close(self) -> bool:
        return self._signal('_close_event')

    def close(self) -> None:
        with self._lock:
            for name in ('_close_event', '_stop_event'):
                handle = getattr(self, name)
                if handle:
                    self.kernel.CloseHandle(handle)
                    setattr(self, name, None)


class WorkerHost:
    """只在内部显式启用。保留同一宿主进程句柄，监视线程与 stdio 分开。"""

    def __init__(self, descriptor: str, *, recovery_scope: str | None = None, trusted_handoff: str | None = None):
        if (recovery_scope is None) != (trusted_handoff is None):
            raise ValueError('本机恢复需要独立可信启动方。')
        if recovery_scope is not None:
            from .recovery_discovery import validate_recovery_scope
            validate_recovery_scope(recovery_scope)
        try:
            identity = json.loads(descriptor) if len(descriptor) <= 512 else None
            if (type(identity) is not dict or set(identity) != {'version', 'pid', 'created', 'close_handle', 'stop_handle'}
                    or type(identity['version']) is not int or identity['version'] != 1
                    or type(identity['pid']) is not int or not 0 < identity['pid'] < 2**32
                    or type(identity['created']) is not int or not 0 < identity['created'] < 2**64
                    or any(type(identity[key]) is not int or not 0 < identity[key] < 2 ** (8 * C.sizeof(W.HANDLE))
                           for key in ('close_handle', 'stop_handle'))
                    or identity['close_handle'] == identity['stop_handle']):
                raise ValueError()
        except (TypeError, ValueError, RecursionError):
            raise ValueError('原 SDK 宿主身份格式无效。') from None

        self.kernel = _kernel()
        self._process = self._close_event = self._stop_event = self._dispatcher = None
        self._handle_lock = threading.Lock()
        self._stdio_lock = threading.Lock()
        self._in_stdio = False
        self._transport_done = threading.Event()
        self._prevent = threading.Event()
        self._lost = threading.Event()
        self._monitor = None
        self._recovery_scope = recovery_scope
        self._handoff = None
        try:
            if trusted_handoff is not None:
                from .trusted_handoff import WorkerHandoff
                self._handoff = WorkerHandoff(trusted_handoff)
            pid, created = identity['pid'], identity['created']
            original = self.kernel.OpenProcess(_PROCESS_ACCESS | 0x0040, False, pid)  # PROCESS_DUP_HANDLE
            if not original:
                raise RuntimeError('无法核实原 SDK 宿主进程。')
            try:
                if _created(self.kernel, original) != created or not _original_ancestor(self.kernel, pid, created):
                    raise RuntimeError('原 SDK 宿主进程身份或父进程链不符。')
                if recovery_scope is not None:
                    from .recovery_pipe import verify_recovery_owner
                    verify_recovery_owner(original, pid)
                self._close_event = _duplicate_wait_handle(self.kernel, original, identity['close_handle'])
                self._stop_event = _duplicate_wait_handle(self.kernel, original, identity['stop_handle'])
                self._process = _duplicate_wait_handle(self.kernel, self.kernel.GetCurrentProcess(), original)
            finally:
                self.kernel.CloseHandle(original)
            self._dispatcher = self.kernel.OpenThread(0x0001, False, threading.get_native_id())  # THREAD_TERMINATE
            if not self._dispatcher or not self.input_allowed():
                raise RuntimeError('无法核实原 SDK 宿主的活动状态和关闭信号。')
        except BaseException:
            self.close()
            raise

    def start(self, service) -> None:
        monitor = threading.Thread(target=self._watch, args=(service,), name='AutoZZZ-WorkerHost', daemon=False)
        monitor.start()
        self._monitor = monitor

    @property
    def continuity_enabled(self) -> bool:
        return self._recovery_scope is not None

    def can_dispatch(self) -> bool:
        with self._handle_lock:
            return (not self._lost.is_set() and self._process is not None
                    and self.kernel.WaitForSingleObject(self._process, 0) == _WAIT_ALIVE
                    and self._close_not_requested())

    def input_allowed(self) -> bool:
        if self._prevent.is_set() or self._transport_done.is_set() or self._lost.is_set():
            return False
        with self._handle_lock:
            return (self._process is not None and self.kernel.WaitForSingleObject(self._process, 0) == _WAIT_ALIVE
                    and self._retainable())

    def prevent_retention(self) -> None:
        self._prevent.set()

    @contextmanager
    def stdio(self):
        with self._stdio_lock:
            allowed = not self._lost.is_set()
            self._in_stdio = allowed
        try:
            yield allowed
        finally:
            with self._stdio_lock:
                self._in_stdio = False

    def _wake_stdio(self) -> None:
        with self._stdio_lock:
            if self._in_stdio:
                self.kernel.CancelSynchronousIo(self._dispatcher)

    def _close_not_requested(self) -> bool:
        return self._close_event is not None and self.kernel.WaitForSingleObject(self._close_event, 0) == _WAIT_ALIVE

    def _retainable(self) -> bool:
        return (not self._prevent.is_set() and self._close_not_requested() and self._stop_event is not None
                and self.kernel.WaitForSingleObject(self._stop_event, 0) == _WAIT_ALIVE)

    @staticmethod
    def _request_stop(service, *, retained=None) -> None:
        execution = getattr(service, '_execution', None)
        worker = getattr(execution, 'worker', None)
        if worker is None or not worker.is_alive():
            return
        if retained is not None:
            if execution is not retained[0] or worker is not retained[1]:
                return
            execution.cancelled.set()
        elif execution.cancelled.is_set():
            return
        try:
            execution.stop()
        except Exception:
            # 原清理路径仍负责完成停止与 join。
            pass

    def _recovery_available(self, service, execution, worker) -> bool:
        if (self._handoff is None or not self._handoff.alive()
                or service._execution is not execution or execution.worker is not worker or not worker.is_alive()
                or execution.cancelled.is_set() or execution.control.stopped):
            return False
        with self._handle_lock:
            return (self._lost.is_set() and self._process is not None
                    and self.kernel.WaitForSingleObject(self._process, 0) == _WAIT_EXITED and self._retainable())

    def _retain(self, service, deadline: float) -> None:
        execution = getattr(service, '_execution', None)
        worker = getattr(execution, 'worker', None)
        if (worker is None or not worker.is_alive() or execution.cancelled.is_set() or execution.control.stopped
                or getattr(service, '_mcp_service', None) is not None or not self._retainable()):
            return
        if self._recovery_scope is not None and (self._handoff is None or not self._handoff.alive()):
            return
        suspend = getattr(execution, 'suspend_for_host_loss', None)
        if suspend is None:
            return
        # 冻结调用可能正在等待执行锁或账本 I/O。期限到时独立发出停止意图。
        finished = threading.Event()

        def expire():
            if not finished.wait(max(0.0, deadline - time.monotonic())):
                self._request_stop(service, retained=(execution, worker))

        expiry = threading.Thread(target=expire, name='AutoZZZ-WorkerHostDeadline', daemon=False)
        expiry.start()
        channel = None
        try:
            if time.monotonic() >= deadline or suspend() is not True:
                return
            if self._recovery_scope is not None:
                try:
                    from .recovery_channel import RecoveryServer
                    execution.start_recovery_observation(deadline)
                    channel = RecoveryServer(
                        self._recovery_scope, deadline,
                        available=lambda: self._recovery_available(service, execution, worker),
                        snapshot=execution.recovery_snapshot,
                        handoff=self._handoff,
                    )
                except (ImportError, OSError, RuntimeError, ValueError):
                    return
            while self._retainable() and service._execution is execution and execution.worker is worker:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or execution.cancelled.is_set() or execution.control.stopped:
                    return
                if self._handoff is not None and not self._handoff.alive():
                    self._request_stop(service, retained=(execution, worker))
                    return
                if channel is not None and channel.stop_requested.is_set():
                    self._request_stop(service, retained=(execution, worker))
                    return
                self._wake_stdio()
                worker.join(min(remaining, _POLL_SECONDS))
                if not worker.is_alive():
                    return
        finally:
            if time.monotonic() < deadline:
                finished.set()
            try:
                if self._recovery_scope is not None:
                    execution.end_recovery_observation()
                if channel is not None:
                    channel.close()
            finally:
                expiry.join()

    def _watch(self, service) -> None:
        while not (self._transport_done.is_set() and self._prevent.is_set()):
            state = self.kernel.WaitForSingleObject(self._process, round(_POLL_SECONDS * 1000))
            if state == _WAIT_ALIVE and self._close_not_requested():
                if not self._retainable():
                    self._request_stop(service)
                continue
            deadline = time.monotonic() + HOST_LOSS_GRACE_SECONDS
            self._lost.set()
            try:
                self._wake_stdio()
                if state == _WAIT_EXITED:
                    self._retain(service, deadline)
            finally:
                if not self._transport_done.wait(_POLL_SECONDS):
                    self._request_stop(service)
                # 若 stdio 恰在检查后进入阻塞，重复取消。RPC 内的文件 I/O 不在此范围。
                while not self._transport_done.wait(_POLL_SECONDS):
                    self._wake_stdio()
            return

    def finish_transport(self, *, may_retain: bool) -> None:
        self._transport_done.set()
        if self._monitor is None:
            return
        if may_retain and self._retainable():
            # 进程退出期间管道可能先关闭。只等待原进程句柄发信号。
            self._lost.wait(_EOF_EXIT_RACE_SECONDS)
        if not may_retain or not self._lost.is_set():
            self.prevent_retention()
        self._monitor.join()

    def close(self) -> None:
        self.prevent_retention()
        self._transport_done.set()
        if self._monitor is not None:
            self._monitor.join()
        if self._handoff is not None:
            self._handoff.close()
            self._handoff = None
        with self._handle_lock:
            for name in ('_dispatcher', '_close_event', '_stop_event', '_process'):
                handle = getattr(self, name)
                if handle:
                    self.kernel.CloseHandle(handle)
                    setattr(self, name, None)
