"""One locally authorized power action, bound to a completed run report."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from .onedragon_patrol import PATROL_INCOMPLETE_ERROR, patrol_incomplete
from .run_lock import RunLock

POWER_ACTIONS = ('shutdown', 'hibernate', 'sleep')
FINISH_DELAY_SECONDS = 60
RECEIPT_NAME = 'after-done.json'
_MAX_REPORT_SIZE = 32 * 1024**2
_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class FinishStatus:
    state: str
    remaining_seconds: int = 0
    detail: str = ''


def _file_bytes(path: Path, limit: int) -> bytes:
    metadata = path.lstat()
    if path.is_symlink() or getattr(metadata, 'st_file_attributes', 0) & _REPARSE_POINT:
        raise ValueError('收尾报告或记录路径被重定向。')
    if not path.is_file() or metadata.st_size > limit:
        raise ValueError('收尾报告或记录不是有效文件。')
    with path.open('rb') as stream:
        result = stream.read(limit + 1)
    if len(result) > limit:
        raise ValueError('收尾报告或记录超过大小限制。')
    return result


class FinishAction:
    """Start from a per-run choice and drive the countdown with the UI's poll loop."""

    def __init__(self, mode=None, *, clock=time.monotonic, dispatch=None,
                 lock_factory=RunLock, guard=None):
        if mode is not None and mode not in POWER_ACTIONS:
            raise ValueError('电源动作无效。')
        self._mode = mode
        self._clock = clock
        self._dispatch = _dispatch_windows if dispatch is None else dispatch
        self._lock_factory = lock_factory
        self._guard = guard
        self._mutex = threading.RLock()
        self._status = FinishStatus('idle' if mode else 'disabled')
        self._report = self._receipt = None
        self._snapshot = self._record = None
        self._deadline = None

    def _read_report(self, report: Path, job_id: str, app_id: str) -> str:
        if report.name != 'report.json' or report != report.resolve():
            raise ValueError('收尾动作没有绑定到原运行报告。')
        raw = _file_bytes(report, _MAX_REPORT_SIZE)
        data = json.loads(raw)
        if (not isinstance(data, dict) or data.get('status') != 'success'
                or not isinstance(data.get('finished_at'), str) or not data['finished_at']):
            raise ValueError('原运行报告没有记录成功结束。')
        if app_id == '_rotation':
            matches = (data.get('operation') == 'account_rotation' and data.get('job_id') == job_id
                       and data.get('finish_action') == self._mode)
            stages = data.get('stages')
            if not isinstance(stages, list):
                stages = []
            executions = (stage.get('execution') for stage in stages
                          if isinstance(stage, dict) and stage.get('method') == 'start_application')
        else:
            request, execution = data.get('request'), data.get('execution')
            matches = (data.get('operation') == 'onedragon' and data.get('app_id') == app_id
                       and isinstance(request, dict) and request.get('source') in (None, 'gui')
                       and request.get('finish_action') == self._mode
                       and isinstance(execution, dict) and execution.get('job_id') == job_id
                       and execution.get('state') == 'success' and execution.get('busy') is False)
            executions = (execution,)
        if not matches:
            raise ValueError('收尾动作与原任务报告不符。')
        if any(isinstance(execution, dict) and patrol_incomplete(execution.get('world_patrol'))
               for execution in executions):
            raise ValueError(PATROL_INCOMPLETE_ERROR)
        return hashlib.sha256(raw).hexdigest()

    def _write_record(self, state: str, detail: str = '') -> None:
        path = self._receipt
        if path != path.resolve():
            raise ValueError('收尾记录路径被重定向。')
        if self._snapshot is not None and _file_bytes(path, 65536) != self._snapshot:
            raise ValueError('收尾记录已变化。')
        payload = dict(self._record, state=state, detail=detail,
                       updated_at=datetime.now(timezone.utc).isoformat())
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
        temporary = path.with_name(f'.{path.name}-{uuid.uuid4().hex}.tmp')
        created = False
        try:
            with temporary.open('xb') as stream:
                created = True
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if self._snapshot is None:
                # Claim this report without replacing any previous attempt.
                os.link(temporary, path)
            else:
                os.replace(temporary, path)
        finally:
            if created:
                temporary.unlink(missing_ok=True)
        self._snapshot = raw

    def arm(self, report_path, job_id: str, app_id: str, *, succeeded: bool) -> FinishStatus:
        with self._mutex:
            if self._status.state != 'idle':
                return self._status
            if succeeded is not True or type(job_id) is not str or not job_id or not app_id:
                self._status = FinishStatus('refused', detail='原任务未确认成功。')
                return self._status
            try:
                report = Path(report_path).absolute()
                checksum = self._read_report(report, job_id, app_id)
                if self._guard is not None:
                    self._guard()
                self._report = report
                self._receipt = report.with_name(RECEIPT_NAME)
                self._record = dict(format=1, action=self._mode, app_id=app_id, job_id=job_id,
                                    report=report.name, report_sha256=checksum,
                                    delay_seconds=FINISH_DELAY_SECONDS)
                self._write_record('pending')
            except (OSError, ValueError, RuntimeError, TypeError) as exc:
                self._status = FinishStatus('refused', detail=str(exc))
                return self._status
            self._deadline = self._clock() + FINISH_DELAY_SECONDS
            self._status = FinishStatus('pending', FINISH_DELAY_SECONDS)
            return self._status

    def _terminal(self, state: str, detail: str = '') -> FinishStatus:
        try:
            self._write_record(state, detail)
        except (OSError, ValueError, RuntimeError) as exc:
            detail = '；'.join(filter(None, (detail, f'收尾记录写入失败：{exc}')))
        self._status = FinishStatus(state, detail=detail)
        return self._status

    def cancel(self) -> bool:
        with self._mutex:
            if self._status.state == 'idle':
                self._status = FinishStatus('cancelled')
            elif self._status.state == 'pending':
                self._terminal('cancelled')
            else:
                return False
            return True

    def _check_original_report(self) -> None:
        checksum = self._read_report(self._report, self._record['job_id'], self._record['app_id'])
        if checksum != self._record['report_sha256']:
            raise ValueError('倒计时期间原运行报告已变化。')

    def poll(self) -> FinishStatus:
        with self._mutex:
            if self._status.state != 'pending':
                return self._status
            remaining = math.ceil(self._deadline - self._clock())
            if remaining > 0:
                self._status = FinishStatus('pending', remaining)
                return self._status
            lock = None
            acquired = False
            try:
                lock = self._lock_factory()
                lock.acquire()
                acquired = True
                self._check_original_report()
                if self._guard is not None:
                    self._guard()
                self._write_record('dispatching')
                self._status = FinishStatus('dispatching')
                threading.Thread(target=self._dispatch_worker, args=(lock,),
                                 name='AutoZZZ-Finish-Action', daemon=True).start()
            except (OSError, ValueError, RuntimeError) as exc:
                detail = str(exc)
                if acquired:
                    try:
                        lock.close()
                    except RuntimeError as cleanup:
                        detail = f'{detail}；{cleanup}'
                return self._terminal('failed', detail)
            return self._status

    def _dispatch_worker(self, lock) -> None:
        state, detail = 'dispatched', ''
        try:
            self._check_original_report()
            if self._receipt != self._receipt.resolve() or _file_bytes(self._receipt, 65536) != self._snapshot:
                raise ValueError('收尾记录已变化。')
            if self._guard is not None:
                self._guard()
            self._dispatch(self._mode)
        except Exception as exc:
            state, detail = 'failed', str(exc)
        finally:
            try:
                lock.close()
            except RuntimeError as exc:
                detail = '；'.join(filter(None, (detail, str(exc))))
            with self._mutex:
                self._terminal(state, detail)


def _dispatch_windows(mode: str) -> None:
    if sys.platform != 'win32' or mode not in POWER_ACTIONS:
        raise OSError('当前系统不支持该电源动作。')
    if mode in ('sleep', 'hibernate'):
        _suspend_windows(hibernate=mode == 'hibernate')
        return
    system_directory = ctypes.WinDLL('kernel32', use_last_error=True).GetSystemDirectoryW
    system_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    system_directory.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    size = system_directory(buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    # A nonzero OS delay would imply forced closure. The app handled the delay.
    command = [str(Path(buffer.value) / 'shutdown.exe'), '/s', '/t', '0']
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)


def _suspend_windows(*, hibernate: bool) -> None:
    class Luid(ctypes.Structure):
        _fields_ = [('low', wintypes.DWORD), ('high', wintypes.LONG)]

    class TokenPrivileges(ctypes.Structure):
        _fields_ = [('count', wintypes.DWORD), ('luid', Luid), ('attributes', wintypes.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    open_token = advapi.OpenProcessToken
    open_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    open_token.restype = wintypes.BOOL
    lookup = advapi.LookupPrivilegeValueW
    lookup.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(Luid)]
    lookup.restype = wintypes.BOOL
    adjust = advapi.AdjustTokenPrivileges
    adjust.argtypes = [wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(TokenPrivileges), wintypes.DWORD,
                       ctypes.POINTER(TokenPrivileges), ctypes.POINTER(wintypes.DWORD)]
    adjust.restype = wintypes.BOOL
    suspend = ctypes.WinDLL('powrprof', use_last_error=True).SetSuspendState
    suspend.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte]
    suspend.restype = ctypes.c_ubyte
    token = wintypes.HANDLE()
    token_adjust_privileges, token_query = 0x20, 0x8
    if not open_token(kernel.GetCurrentProcess(), token_adjust_privileges | token_query, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        luid = Luid()
        if not lookup(None, 'SeShutdownPrivilege', ctypes.byref(luid)):
            raise ctypes.WinError(ctypes.get_last_error())
        privilege_enabled = 0x2
        change = TokenPrivileges(1, luid, privilege_enabled)
        previous = TokenPrivileges()
        returned = wintypes.DWORD()
        ctypes.set_last_error(0)
        changed = adjust(token, False, ctypes.byref(change), ctypes.sizeof(previous),
                         ctypes.byref(previous), ctypes.byref(returned))
        error = ctypes.get_last_error()
        if not changed or error:
            raise ctypes.WinError(error)
        try:
            if not suspend(hibernate, False, False):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            adjust(token, False, ctypes.byref(previous), 0, None, None)
    finally:
        kernel.CloseHandle(token)
