"""Read-only continuity evidence for the original frozen native battle."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
from functools import lru_cache
import math
import threading
import time
from typing import Any, Callable
import weakref


MAX_GAP_SECONDS = .5
MAX_CAPTURE_SECONDS = .25
MIN_FROZEN_SAMPLES = 2
_MAX_PIXELS = 36_000_000
_UNSAFE_PHASES = frozenset({'unknown', 'loading', 'settlement', 'occluded', 'unproven'})


@dataclass(frozen=True)
class WindowIdentity:
    hwnd: int
    pid: int
    process_created: int
    thread_id: int
    client_rect: tuple[int, int, int, int]


@dataclass(frozen=True)
class CapturedFrame:
    """One capture interval in the time.perf_counter() clock domain."""

    identity: WindowIdentity
    image: Any
    started_at: float
    finished_at: float


@dataclass(frozen=True)
class EncounterEvidence:
    phase: str
    authority: str = ''
    instance_id: str = ''


class ObservationRejected(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def native_encounter_evidence(_frame: CapturedFrame) -> EncounterEvidence:
    # The pinned SDK exposes battle/result HUD recognition. Its plan UID and HUD
    # labels can recur on successive battles and provide no encounter identity.
    return EncounterEvidence('unproven')


def _valid_identity(value: WindowIdentity) -> bool:
    if type(value) is not WindowIdentity:
        return False
    rect = value.client_rect
    if (any(type(field) is not int or field <= 0
            for field in (value.hwnd, value.pid, value.process_created, value.thread_id))
            or type(rect) is not tuple or len(rect) != 4 or any(type(edge) is not int for edge in rect)):
        return False
    left, top, right, bottom = rect
    return right > left and bottom > top and (right - left) * (bottom - top) <= _MAX_PIXELS


class RecoveryObservation:
    """One monotonic chain, from the original frame through the first resumed frame.

    The evidence reader is trusted in-process code. An authority must attest
    the game-side instance, never a plan UID, a HUD fingerprint or a local UUID.
    """

    def __init__(self, anchor: CapturedFrame, capture: Callable[[], CapturedFrame],
                 evidence: Callable[[CapturedFrame], EncounterEvidence], frozen: Callable[[], bool],
                 *, max_gap: float = MAX_GAP_SECONDS, clock: Callable[[], float] = time.perf_counter,
                 elapsed_clock: Callable[[CapturedFrame], int | None] | None = None):
        if type(max_gap) not in (int, float) or not 0 < max_gap <= MAX_GAP_SECONDS:
            raise ValueError('恢复观察间隔须大于零且不超过 0.5 秒。')
        self._capture, self._evidence, self._frozen = capture, evidence, frozen
        self._probe = getattr(capture, 'identity', None)
        self._gap, self._clock = max_gap, clock
        self._lock = threading.Lock()
        self._sample_lock = threading.Lock()
        self._failure = None
        self._finished = False
        self._first_frame_reserved_at: float | None = None
        self._last_victory: weakref.ReferenceType[CapturedFrame] | None = None
        self._sample_count = 0
        self._identity = None
        self._encounter = None
        self._last_times = None
        self._elapsed = elapsed_clock
        self._clock_witness = None
        if elapsed_clock is not None:
            from .recovery_battle_clock import BattleClockWitness
            self._clock_witness = BattleClockWitness(max_gap)
        if type(anchor) is not CapturedFrame or not _valid_identity(anchor.identity):
            self.invalidate('invalid_anchor')
            return
        try:
            self._check_frame(anchor)
            self._check_frozen()
            if self._clock_witness is None:
                self._encounter = self._read_evidence(anchor)
            else:
                self._check_elapsed(anchor)
            self._check_frozen()
            self._check_recent(anchor.finished_at)
        except ObservationRejected as exc:
            self.invalidate(exc.reason)
            return
        except Exception:
            self.invalidate('observation_error')
            return
        self._identity = anchor.identity
        self._last_times = anchor.started_at, anchor.finished_at

    @property
    def failure(self) -> str | None:
        with self._lock:
            return self._failure

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._finished

    @property
    def clock_only(self) -> bool:
        return self._clock_witness is not None

    def invalidate(self, reason: str) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = reason if type(reason) is str and reason else 'observation_error'

    def _state(self) -> str:
        with self._lock:
            if self._failure is not None:
                return 'failed'
            if self._finished:
                return 'finished'
            if self._first_frame_reserved_at is not None:
                return 'awaiting_first_frame'
            advanced = self._clock_witness is None or self._clock_witness.progressed
            return 'ready' if self._sample_count >= MIN_FROZEN_SAMPLES and advanced else 'pending'

    def _check_recent(self, finished_at: float) -> float:
        checked_at = self._clock()
        if not 0 <= checked_at - finished_at <= self._gap:
            raise ObservationRejected('capture_gap')
        return checked_at

    def _check_frame(self, frame: CapturedFrame, *, first_resumed: bool = False, repeat: bool = False) -> None:
        if type(frame) is not CapturedFrame or frame.image is None:
            raise ObservationRejected('capture_invalid')
        started, finished = frame.started_at, frame.finished_at
        if (any(type(stamp) not in (int, float) or not math.isfinite(stamp) for stamp in (started, finished))
                or not 0 <= started <= finished or finished - started > MAX_CAPTURE_SECONDS):
            raise ObservationRejected('capture_time_invalid')
        self._check_recent(finished)
        if not _valid_identity(frame.identity) or (self._identity is not None and frame.identity != self._identity):
            raise ObservationRejected('window_changed')
        reservation = self._first_frame_reserved_at if first_resumed else None
        if reservation is not None and started < reservation:
            raise ObservationRejected('capture_before_reservation')
        if self._last_times is not None:
            old_start, old_finish = self._last_times
            if repeat and (started, finished) == self._last_times:
                return
            overlap = first_resumed and reservation is None and started <= old_finish and old_start <= finished
            if overlap and (started, finished) == self._last_times:
                raise ObservationRejected('capture_out_of_order')
            if not overlap and (started <= old_start or finished <= old_finish):
                raise ObservationRejected('capture_out_of_order')
            # Pixels may have been read anywhere within each capture interval.
            if finished - old_start > self._gap:
                raise ObservationRejected('capture_gap')

    def _check_frozen(self) -> None:
        if self._frozen() is not True:
            raise ObservationRejected('frozen_scope_changed')

    def _check_current_window(self) -> None:
        if callable(self._probe) and self._probe() != self._identity:
            raise ObservationRejected('window_changed')

    def _read_evidence(self, frame: CapturedFrame, *, phase: str = 'battle') -> tuple[str, str]:
        witness = self._evidence(frame)
        if type(witness) is not EncounterEvidence or type(witness.phase) is not str:
            raise ObservationRejected('unknown')
        if witness.phase != phase:
            raise ObservationRejected(witness.phase if witness.phase in _UNSAFE_PHASES else 'unknown')
        if (type(witness.authority) is not str or not witness.authority.strip() or len(witness.authority) > 128
                or type(witness.instance_id) is not str or not witness.instance_id.strip() or len(witness.instance_id) > 256):
            raise ObservationRejected('unproven')
        return witness.authority, witness.instance_id

    def _check_elapsed(self, frame):
        problem = self._clock_witness.observe(self._elapsed(frame), frame.started_at, frame.finished_at)
        if problem is not None:
            raise ObservationRejected(problem)

    def _consume(self, frame: CapturedFrame, *, first_resumed: bool = False,
                 victory: bool = False, repeat: bool = False) -> None:
        self._check_frame(frame, first_resumed=first_resumed, repeat=repeat)
        if self._clock_witness is not None:
            if victory:
                raise ObservationRejected('battle_terminal_unproven')
            self._check_elapsed(frame)
        elif self._read_evidence(frame, phase='victory' if victory else 'battle') != self._encounter:
            raise ObservationRejected('encounter_changed')
        self._check_frozen()
        self._check_current_window()
        self._check_recent(frame.finished_at)

    def sample(self) -> str:
        if not self._sample_lock.acquire(blocking=False):
            state = self._state()
            return 'pending' if state == 'ready' else state
        try:
            state = self._state()
            if state not in ('pending', 'ready'):
                return state
            try:
                self._check_frozen()
                self._check_recent(self._last_times[1])
                frame = self._capture()
                self._consume(frame)
                with self._lock:
                    if self._failure is None:
                        self._last_times = frame.started_at, frame.finished_at
                        self._sample_count += 1
            except ObservationRejected as exc:
                self.invalidate(exc.reason)
            except Exception:
                self.invalidate('observation_error')
            return self._state()
        finally:
            self._sample_lock.release()

    def verified(self, *, timeout: float = 0.0) -> bool:
        return self._check_ready(timeout=timeout, reserve=False)

    def reserve_first_frame(self, *, timeout: float = MAX_CAPTURE_SECONDS) -> bool:
        """Stop freeze sampling so the original thread can capture its first fresh frame."""
        return self._check_ready(timeout=timeout, reserve=True)

    def _check_ready(self, *, timeout: float, reserve: bool) -> bool:
        if type(timeout) not in (int, float) or not 0 <= timeout <= MAX_GAP_SECONDS:
            raise ValueError('核对恢复观察最多可等待 0.5 秒。')
        if not self._sample_lock.acquire(timeout=min(timeout, self._gap)):
            if reserve:
                self.invalidate('capture_busy')
            return False
        try:
            state = self._state()
            if state not in ('pending', 'ready'):
                return False
            try:
                self._check_frozen()
                self._check_current_window()
                checked_at = self._check_recent(self._last_times[1])
                with self._lock:
                    if self._failure is not None or state != 'ready':
                        return False
                    if reserve:
                        self._first_frame_reserved_at = checked_at
                    return True
            except ObservationRejected as exc:
                self.invalidate(exc.reason)
            except Exception:
                self.invalidate('observation_error')
            return False
        finally:
            self._sample_lock.release()

    def finish(self, frame: CapturedFrame) -> bool:
        """Seal sampling with the first native frame captured after resume was authorized."""
        if not self._sample_lock.acquire(timeout=self._gap):
            self.invalidate('capture_busy')
            return False
        try:
            state = self._state()
            if state in ('failed', 'finished'):
                return False
            if state not in ('ready', 'awaiting_first_frame'):
                self.invalidate('insufficient_samples')
                return False
            try:
                self._check_frozen()
                self._check_recent(self._last_times[1])
                self._consume(frame, first_resumed=True)
                with self._lock:
                    if self._failure is not None:
                        return False
                    self._last_times = frame.started_at, frame.finished_at
                    self._finished = True
                    return True
            except ObservationRejected as exc:
                self.invalidate(exc.reason)
            except Exception:
                self.invalidate('observation_error')
            return False
        finally:
            self._sample_lock.release()

    def continue_frame(self, frame: CapturedFrame, *, victory: bool = False) -> bool:
        """Advance a sealed chain through the original battle or its witnessed victory."""
        if type(victory) is not bool:
            self.invalidate('invalid_victory_evidence')
            return False
        return self._advance_sealed(frame, victory=victory)

    def confirm_victory(self, frame: CapturedFrame) -> bool:
        """Recheck settlement evidence after the chain has accepted its victory."""
        return self._advance_sealed(frame, victory=True, confirming=True)

    def _advance_sealed(self, frame: CapturedFrame, *, victory: bool, confirming: bool = False) -> bool:
        if not self._sample_lock.acquire(timeout=self._gap):
            self.invalidate('capture_busy')
            return False
        try:
            with self._lock:
                last_victory = self._last_victory
                if self._failure is not None or not self._finished or confirming != (last_victory is not None):
                    return False
            repeat = last_victory is not None and last_victory() is frame
            try:
                self._check_frozen()
                self._check_recent(self._last_times[1])
                self._consume(frame, victory=victory, repeat=repeat)
                with self._lock:
                    if self._failure is not None:
                        return False
                    if not repeat:
                        self._last_times = frame.started_at, frame.finished_at
                        if victory:
                            self._last_victory = weakref.ref(frame)
                    return True
            except ObservationRejected as exc:
                self.invalidate(exc.reason)
            except Exception:
                self.invalidate('observation_error')
            return False
        finally:
            self._sample_lock.release()


class _MonitorInfo(C.Structure):
    _fields_ = [('size', W.DWORD), ('monitor', W.RECT), ('work', W.RECT), ('flags', W.DWORD)]


@lru_cache(maxsize=1)
def _win32():
    from .windows import geometry, kernel32, user32

    user32.GetAncestor.argtypes = [W.HWND, W.UINT]
    user32.GetAncestor.restype = W.HWND
    user32.GetTopWindow.argtypes = [W.HWND]
    user32.GetTopWindow.restype = W.HWND
    user32.GetWindow.argtypes = [W.HWND, W.UINT]
    user32.GetWindow.restype = W.HWND
    user32.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
    user32.GetWindowRect.restype = W.BOOL
    user32.GetWindowThreadProcessId.restype = W.DWORD
    user32.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
    user32.MonitorFromWindow.restype = W.HANDLE
    user32.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(_MonitorInfo)]
    user32.GetMonitorInfoW.restype = W.BOOL
    kernel32.GetProcessId.argtypes = [W.HANDLE]
    kernel32.GetProcessId.restype = W.DWORD
    dwmapi = C.WinDLL('dwmapi', use_last_error=True)
    dwmapi.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, W.LPVOID, W.DWORD]
    dwmapi.DwmGetWindowAttribute.restype = W.LONG
    return user32, kernel32, dwmapi, geometry


def _cloaked(dwmapi, hwnd: int) -> bool:
    value = W.DWORD()
    if dwmapi.DwmGetWindowAttribute(hwnd, 14, C.byref(value), C.sizeof(value)) != 0:
        raise ObservationRejected('visibility_unknown')
    return bool(value.value)


def _check_visible_client(user32, dwmapi, hwnd: int, rect: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = rect
    monitor = user32.MonitorFromWindow(hwnd, 0)
    area = _MonitorInfo(size=C.sizeof(_MonitorInfo))
    if not monitor or not user32.GetMonitorInfoW(monitor, C.byref(area)):
        raise ObservationRejected('visibility_unknown')
    bounds = area.monitor
    if left < bounds.left or top < bounds.top or right > bounds.right or bottom > bounds.bottom:
        raise ObservationRejected('client_not_fully_visible')
    top_hwnd = user32.GetTopWindow(None)
    seen = set()
    while top_hwnd != hwnd:
        if not top_hwnd or top_hwnd in seen or len(seen) >= 512:
            raise ObservationRejected('visibility_unknown')
        seen.add(top_hwnd)
        if user32.IsWindowVisible(top_hwnd) and not user32.IsIconic(top_hwnd):
            above = W.RECT()
            if not user32.GetWindowRect(top_hwnd, C.byref(above)):
                raise ObservationRejected('visibility_unknown')
            overlaps = left < above.right and above.left < right and top < above.bottom and above.top < bottom
            if overlaps and not _cloaked(dwmapi, top_hwnd):
                raise ObservationRejected('occluded')
        top_hwnd = user32.GetWindow(top_hwnd, 2)  # GW_HWNDNEXT


@lru_cache(maxsize=1)
def _visible_capture_class():
    from one_dragon.base.controller.pc_screenshot.bitblt_screencapper import BitBltScreencapper

    gdi32 = C.WinDLL('gdi32', use_last_error=True)
    gdi32.BitBlt.argtypes = [W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD]
    gdi32.BitBlt.restype = W.BOOL

    class VisibleCapture(BitBltScreencapper):
        def _do_capture(self, context):
            return gdi32.BitBlt(context.mfcDC, 0, 0, context.width, context.height, context.hwndDC,
                                context.src_x, context.src_y, 0x40CC0020)  # SRCCOPY | CAPTUREBLT

    return VisibleCapture


def _capture_visible(window, rect: tuple[int, int, int, int]):
    from one_dragon.base.geometry.rectangle import Rect

    left, top, right, bottom = rect
    # The SDK's independent BitBlt strategy uses the desktop DC and only
    # reads the requested rectangle. No controller observe or activation path.
    capture = _visible_capture_class()(window, right - left, bottom - top)
    return capture.capture(Rect(left, top, right, bottom), independent=True)


class Win32RecoveryCapture:
    """Read-only client pixels from the exact retained game binding."""

    def __init__(self, window, binding, process_id, process_handle):
        self._window, self._binding = window, binding
        self._process_id, self._process_handle = process_id, process_handle
        self._hwnd = getattr(binding, 'hwnd', None)

    def identity(self) -> WindowIdentity:
        from .task_lease import _created

        window, hwnd = self._window, self._hwnd
        user32, kernel32, dwmapi, geometry = _win32()
        if (not hwnd or window.bound is not self._binding or self._binding.hwnd != hwnd
                or window.process_id != self._process_id or window.process_handle != self._process_handle
                or not self._process_handle or not window.identity_valid()
                or kernel32.GetProcessId(self._process_handle) != self._process_id
                or user32.GetAncestor(hwnd, 2) != hwnd):  # GA_ROOT
            raise ObservationRejected('window_changed')
        if user32.GetForegroundWindow() != hwnd:
            raise ObservationRejected('focus_lost')
        if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd) or _cloaked(dwmapi, hwnd):
            raise ObservationRejected('client_not_fully_visible')
        pid = W.DWORD()
        thread_id = user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
        if not thread_id or pid.value != self._process_id:
            raise ObservationRejected('window_changed')
        current = geometry(hwnd)
        rect = current.left, current.top, current.left + current.width, current.top + current.height
        identity = WindowIdentity(hwnd, pid.value, _created(kernel32, self._process_handle), thread_id, rect)
        if not _valid_identity(identity):
            raise ObservationRejected('visibility_unknown')
        _check_visible_client(user32, dwmapi, hwnd, rect)
        if user32.GetForegroundWindow() != hwnd:
            raise ObservationRejected('focus_lost')
        return identity

    def capture(self) -> CapturedFrame:
        started = time.perf_counter()
        identity = self.identity()
        image = _capture_visible(self._window, identity.client_rect)
        if self.identity() != identity:
            raise ObservationRejected('window_changed')
        finished = time.perf_counter()
        left, top, right, bottom = identity.client_rect
        if (image is None or image.shape != (bottom - top, right - left, 3)
                or image.dtype.name != 'uint8' or image[::8, ::8].std() < 2):
            raise ObservationRejected('capture_invalid')
        image.setflags(write=False)
        return CapturedFrame(identity, image, started, finished)

    def __call__(self) -> CapturedFrame:
        return self.capture()


def native_recovery_capture(window) -> Win32RecoveryCapture:
    return Win32RecoveryCapture(window, window.bound, window.process_id, window.process_handle)
