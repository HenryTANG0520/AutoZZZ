"""Local Win32 recovery transport with kernel-verified peers and bounded frames."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
from functools import lru_cache
import math
import os
import re
import struct
import threading
import time
from uuid import uuid4

from .task_lease import _created

if os.name == 'nt':
    import _winapi


PIPE_PREFIX = r'\\.\pipe\AutoZZZ.Recovery.'
DISCOVERY_PIPE_PREFIX = PIPE_PREFIX + 'Discovery.'
MAX_FRAME_BYTES = 64 * 1024
_LENGTH = struct.Struct('<I')
_FRAME_ACK = b'\xa5'
_PIPE_ACCESS = 0x100083  # SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_READ_DATA | FILE_WRITE_DATA
_PROCESS_ACCESS = 0x101000  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
_TOKEN_USER = 1
_TOKEN_STATISTICS = 10
_TOKEN_SESSION_ID = 12
_TOKEN_LOGON_SID = 28
_PIPE_REJECT_REMOTE_CLIENTS = 0x8
_IDENTIFICATION_SQOS = 0x110000  # SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION
_INVALID_HANDLE = C.c_void_p(-1).value


class RecoveryPipeError(OSError):
    pass


class RecoveryPeerError(PermissionError):
    pass


@dataclass(frozen=True)
class RecoveryProcessIdentity:
    pid: int
    created: int
    user_sid: str
    logon_sid: str
    authentication_id: int
    session_id: int


class _SidAndAttributes(C.Structure):
    _fields_ = [('sid', W.LPVOID), ('attributes', W.DWORD)]


class _TokenGroups(C.Structure):
    _fields_ = [('count', W.DWORD), ('groups', _SidAndAttributes * 1)]


class _Luid(C.Structure):
    _fields_ = [('low', W.DWORD), ('high', W.LONG)]


class _TokenStatistics(C.Structure):
    _fields_ = [('token_id', _Luid), ('authentication_id', _Luid), ('expires', C.c_longlong),
                ('token_type', C.c_int), ('impersonation_level', C.c_int), ('dynamic_charged', W.DWORD),
                ('dynamic_available', W.DWORD), ('group_count', W.DWORD), ('privilege_count', W.DWORD),
                ('modified_id', _Luid)]


class _SecurityAttributes(C.Structure):
    _fields_ = [('length', W.DWORD), ('descriptor', W.LPVOID), ('inherit', W.BOOL)]


@lru_cache(maxsize=1)
def _apis():
    if os.name != 'nt':
        raise RecoveryPipeError('恢复命名管道只支持 Windows。')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.GetProcessId.argtypes = [W.HANDLE]
    kernel.GetProcessId.restype = W.DWORD
    kernel.ProcessIdToSessionId.argtypes = [W.DWORD, C.POINTER(W.DWORD)]
    kernel.ProcessIdToSessionId.restype = W.BOOL
    kernel.LocalFree.argtypes = [W.HLOCAL]
    kernel.LocalFree.restype = W.HLOCAL
    kernel.CreateEventW.argtypes = [W.LPVOID, W.BOOL, W.BOOL, W.LPCWSTR]
    kernel.CreateEventW.restype = W.HANDLE
    kernel.SetEvent.argtypes = [W.HANDLE]
    kernel.SetEvent.restype = W.BOOL
    for query in (kernel.GetNamedPipeClientProcessId, kernel.GetNamedPipeClientSessionId,
                  kernel.GetNamedPipeServerProcessId, kernel.GetNamedPipeServerSessionId):
        query.argtypes = [W.HANDLE, C.POINTER(W.ULONG)]
        query.restype = W.BOOL
    kernel.CreateNamedPipeW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD, W.DWORD,
                                       W.DWORD, W.DWORD, C.POINTER(_SecurityAttributes)]
    kernel.CreateNamedPipeW.restype = W.HANDLE
    kernel.DisconnectNamedPipe.argtypes = [W.HANDLE]
    kernel.DisconnectNamedPipe.restype = W.BOOL
    advapi = C.WinDLL('advapi32', use_last_error=True)
    advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    advapi.OpenProcessToken.restype = W.BOOL
    advapi.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)]
    advapi.GetTokenInformation.restype = W.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [W.LPVOID, C.POINTER(W.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = W.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        W.LPCWSTR, W.DWORD, C.POINTER(W.LPVOID), C.POINTER(W.ULONG)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = W.BOOL
    return kernel, advapi


def new_recovery_pipe_name() -> str:
    return PIPE_PREFIX + uuid4().hex


def _name_kind(name: str) -> str:
    if isinstance(name, str) and name.startswith(PIPE_PREFIX):
        suffix = name[len(PIPE_PREFIX):]
        if re.fullmatch(r'[a-f0-9]{32}', suffix):
            return 'recovery'
        if re.fullmatch(r'Discovery\.[a-f0-9]{64}', suffix):
            return 'discovery'
    raise ValueError('恢复管道名称须为本机约定名称。')


def _expected(expected_pid, expected_created, *, required: bool) -> None:
    if expected_pid is None and expected_created is None and not required:
        return
    if (type(expected_pid) is not int or not 0 < expected_pid < 2**32
            or type(expected_created) is not int or not 0 < expected_created < 2**64):
        raise ValueError('须提供有效的原进程 PID 和 Win32 创建时间。')


def _assert_alive(handle) -> None:
    if _winapi.WaitForSingleObject(handle, 0) != _winapi.WAIT_TIMEOUT:
        raise RecoveryPeerError('恢复管道的原对端进程已经退出。')


def _token_info(api, token, kind: int):
    size = W.DWORD()
    api.GetTokenInformation(token, kind, None, 0, C.byref(size))
    if not 0 < size.value <= 65536:
        raise RecoveryPeerError('无法核实恢复进程的令牌信息长度。')
    data = C.create_string_buffer(size.value)
    if not api.GetTokenInformation(token, kind, data, size.value, C.byref(size)):
        raise C.WinError(C.get_last_error())
    return data


def _sid_string(kernel, advapi, sid) -> str:
    result = W.LPWSTR()
    if not sid or not advapi.ConvertSidToStringSidW(sid, C.byref(result)):
        raise RecoveryPeerError('无法核实恢复进程的安全标识符。')
    try:
        return result.value
    finally:
        kernel.LocalFree(C.cast(result, W.HLOCAL))


def _read_identity(process, pid: int) -> RecoveryProcessIdentity:
    kernel, advapi = _apis()
    if kernel.GetProcessId(process) != pid:
        raise RecoveryPeerError('恢复进程句柄与 PID 不一致。')
    _assert_alive(process)
    created = _created(kernel, process)
    token = W.HANDLE()
    if not advapi.OpenProcessToken(process, 8, C.byref(token)):  # TOKEN_QUERY
        raise C.WinError(C.get_last_error())
    try:
        user = _token_info(advapi, token, _TOKEN_USER)
        logon = _token_info(advapi, token, _TOKEN_LOGON_SID)
        session = _token_info(advapi, token, _TOKEN_SESSION_ID)
        statistics = _token_info(advapi, token, _TOKEN_STATISTICS)
        group = _TokenGroups.from_buffer(logon)
        if group.count != 1:
            raise RecoveryPeerError('恢复进程缺少唯一的 Windows 登录 SID。')
        user_sid = _sid_string(kernel, advapi, _SidAndAttributes.from_buffer(user).sid)
        logon_sid = _sid_string(kernel, advapi, group.groups[0].sid)
        if re.fullmatch(r'S-1-5-5-[0-9]+-[0-9]+', logon_sid) is None:
            raise RecoveryPeerError('恢复进程的 Windows 登录 SID 无效。')
        session_id = W.DWORD.from_buffer(session).value
        authentication = _TokenStatistics.from_buffer(statistics).authentication_id
        authentication_id = (authentication.high & 0xffffffff) << 32 | authentication.low
    finally:
        _winapi.CloseHandle(token.value)
    process_session = W.DWORD()
    if (session_id == 0 or not kernel.ProcessIdToSessionId(pid, C.byref(process_session))
            or process_session.value != session_id):
        raise RecoveryPeerError('无法核实恢复进程的 Windows 交互会话。')
    _assert_alive(process)
    return RecoveryProcessIdentity(pid, created, user_sid, logon_sid, authentication_id, session_id)


def current_recovery_identity() -> RecoveryProcessIdentity:
    kernel, _ = _apis()
    return _read_identity(kernel.GetCurrentProcess(), os.getpid())


def _principal(identity: RecoveryProcessIdentity) -> tuple[str, str, int, int]:
    return identity.user_sid, identity.logon_sid, identity.authentication_id, identity.session_id


def verify_recovery_owner(handle, pid: int) -> RecoveryProcessIdentity:
    """Verify an already opened process handle; the caller retains ownership."""
    owner = _read_identity(handle, pid)
    if _principal(owner) != _principal(current_recovery_identity()):
        raise RecoveryPeerError('原宿主与当前 worker 的 Windows 用户或登录会话不一致。')
    return owner


class RecoveryPeer:
    """An independently held handle to the originally authenticated process."""

    def __init__(self, identity: RecoveryProcessIdentity, handle):
        self.identity = identity
        self._handle = handle
        self._lock = threading.Lock()

    def _live_handle(self):
        if self._handle is None:
            raise RecoveryPeerError('恢复进程引用已关闭。')
        _assert_alive(self._handle)
        return self._handle

    def assert_alive(self) -> None:
        with self._lock:
            self._live_handle()

    def _retain(self) -> RecoveryPeer:
        with self._lock:
            process = _winapi.GetCurrentProcess()
            handle = _winapi.DuplicateHandle(process, self._live_handle(), process, 0, False,
                                             _winapi.DUPLICATE_SAME_ACCESS)
            return RecoveryPeer(self.identity, handle)

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                handle, self._handle = self._handle, None
                _winapi.CloseHandle(handle)

    def __enter__(self) -> RecoveryPeer:
        self.assert_alive()
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def _pipe_peer_ids(pipe, server: bool) -> tuple[int, int]:
    kernel, _ = _apis()
    pid, session = W.ULONG(), W.ULONG()
    process_query = kernel.GetNamedPipeClientProcessId if server else kernel.GetNamedPipeServerProcessId
    session_query = kernel.GetNamedPipeClientSessionId if server else kernel.GetNamedPipeServerSessionId
    if not process_query(pipe, C.byref(pid)) or not session_query(pipe, C.byref(session)) or not pid.value:
        raise RecoveryPeerError('无法从 Windows 管道核实实际对端进程。')
    return pid.value, session.value


def _authenticate(pipe, server: bool, local: RecoveryProcessIdentity, expected_pid, expected_created) -> RecoveryPeer:
    pid, session = _pipe_peer_ids(pipe, server)
    if expected_pid is not None and pid != expected_pid:
        raise RecoveryPeerError('恢复管道的实际对端 PID 与预期不一致。')
    process = _winapi.OpenProcess(_PROCESS_ACCESS, False, pid)
    try:
        peer = _read_identity(process, pid)
        if expected_created is not None and peer.created != expected_created:
            raise RecoveryPeerError('恢复管道的实际对端创建时间与预期不一致。')
        if (session != peer.session_id or _principal(peer) != _principal(local)
                or _pipe_peer_ids(pipe, server) != (pid, session)):
            raise RecoveryPeerError('恢复管道只接受同一 Windows 用户和登录会话。')
        _assert_alive(process)
        return RecoveryPeer(peer, process)
    except BaseException:
        _winapi.CloseHandle(process)
        raise


def _deadline(timeout: float) -> float:
    if type(timeout) not in (int, float) or not 0 < timeout <= 60 or not math.isfinite(timeout):
        raise ValueError('恢复管道等待时间须大于 0 且不超过 60 秒。')
    return time.monotonic() + timeout


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('等待恢复管道超时。')
    return remaining


@contextmanager
def _locked_until(lock, deadline: float):
    if not lock.acquire(timeout=_remaining(deadline)):
        raise TimeoutError('等待恢复管道操作结束超时。')
    try:
        yield
    finally:
        lock.release()


def _cancel_pending(overlapped) -> None:
    try:
        overlapped.cancel()
    finally:
        try:
            overlapped.GetOverlappedResult(True)
        except OSError:
            pass


def _complete(overlapped, cancel, deadline: float, peer_handle=None, *, preserve_on_timeout=False) -> int:
    handles = [cancel, overlapped.event] if peer_handle is None else [cancel, peer_handle, overlapped.event]
    try:
        status = _winapi.WaitForMultipleObjects(handles, False, math.ceil(1000 * _remaining(deadline)))
        if status == _winapi.WAIT_TIMEOUT:
            raise TimeoutError('等待恢复管道超时。')
        if status == _winapi.WAIT_OBJECT_0:
            raise RecoveryPipeError('恢复管道已关闭。')
        if peer_handle is not None and status == _winapi.WAIT_OBJECT_0 + 1:
            raise RecoveryPeerError('恢复管道的原对端进程已经退出。')
        if status != _winapi.WAIT_OBJECT_0 + len(handles) - 1:
            raise RecoveryPipeError('无法核实恢复管道 I/O 状态。')
    except TimeoutError:
        if not preserve_on_timeout:
            _cancel_pending(overlapped)
        raise
    except BaseException:
        _cancel_pending(overlapped)
        raise
    transferred, error = overlapped.GetOverlappedResult(True)
    if error:
        raise C.WinError(error)
    return transferred


def _create_listener(name: str, local: RecoveryProcessIdentity):
    kernel, advapi = _apis()
    descriptor = W.LPVOID()
    # OW suppresses the object's owner's implicit WRITE_DAC and READ_CONTROL.
    sddl = f'D:P(D;;WDWO;;;OW)(A;;0x{_PIPE_ACCESS:08x};;;{local.logon_sid})'
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    if not convert(sddl, 1, C.byref(descriptor), None):
        raise C.WinError(C.get_last_error())
    try:
        security = _SecurityAttributes(C.sizeof(_SecurityAttributes), descriptor, False)
        flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED | _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
        pipe = kernel.CreateNamedPipeW(name, flags, _PIPE_REJECT_REMOTE_CLIENTS, 1, 4096, 4096, 100, C.byref(security))
        if not pipe or pipe == _INVALID_HANDLE:
            raise C.WinError(C.get_last_error())
        return pipe
    finally:
        kernel.LocalFree(descriptor)


def _disconnect(pipe) -> None:
    kernel, _ = _apis()
    if not kernel.DisconnectNamedPipe(pipe):
        error = C.get_last_error()
        if error not in (232, 233):  # ERROR_NO_DATA | ERROR_PIPE_NOT_CONNECTED
            raise C.WinError(error)


def _cancel_event():
    kernel, _ = _apis()
    handle = kernel.CreateEventW(None, True, False, None)
    if not handle:
        raise C.WinError(C.get_last_error())
    return handle


def _signal_cancel(handle) -> None:
    kernel, _ = _apis()
    if not kernel.SetEvent(handle):
        raise C.WinError(C.get_last_error())


class RecoveryPipeConnection:
    """One exchange, with up to two server frames and one client frame."""

    def __init__(self, pipe, peer: RecoveryPeer, server: RecoveryPipeServer | None):
        self.peer = peer.identity
        self._peer = peer
        self._server = server
        self._pipe = pipe
        self._io_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._cancel = _cancel_event()
        self._send_left = 2 if server is not None else 1
        self._receive_left = 1 if server is not None else 2
        self._failed = False

    def assert_peer_alive(self) -> None:
        self._peer.assert_alive()

    def retain_peer(self) -> RecoveryPeer:
        return self._peer._retain()

    def _check_open(self) -> None:
        if self._pipe is None or self._failed or _winapi.WaitForSingleObject(self._cancel, 0) != _winapi.WAIT_TIMEOUT:
            raise RecoveryPipeError('恢复管道连接已关闭或已中断。')
        self.assert_peer_alive()

    def _read(self, size: int, deadline: float) -> bytes:
        data = bytearray(size)
        offset = 0
        while offset < size:
            _remaining(deadline)
            self._check_open()
            operation, _ = _winapi.ReadFile(self._pipe, size - offset, overlapped=True)
            count = _complete(operation, self._cancel, deadline, self._peer._handle)
            if not count:
                raise RecoveryPipeError('恢复管道提前结束。')
            data[offset:offset + count] = operation.getbuffer()
            offset += count
        return bytes(data)

    def _write(self, data: bytes, deadline: float) -> None:
        view = memoryview(data)
        while view:
            _remaining(deadline)
            self._check_open()
            operation, _ = _winapi.WriteFile(self._pipe, view, overlapped=True)
            count = _complete(operation, self._cancel, deadline, self._peer._handle)
            if not count:
                raise RecoveryPipeError('恢复管道未完成写入。')
            view = view[count:]

    def send(self, payload: bytes, timeout: float) -> None:
        if type(payload) is not bytes or len(payload) > MAX_FRAME_BYTES:
            raise ValueError('恢复管道帧须为不超过 64 KiB 的字节串。')
        deadline = _deadline(timeout)
        with _locked_until(self._io_lock, deadline):
            self._check_open()
            if self._send_left == 0:
                raise RecoveryPipeError('该恢复连接的发送帧数已用完。')
            try:
                self._write(_LENGTH.pack(len(payload)) + payload, deadline)
                if self._read(1, deadline) != _FRAME_ACK:
                    raise RecoveryPipeError('恢复管道收妥确认无效。')
                self.assert_peer_alive()
            except BaseException:
                self._failed = True
                raise
            self._send_left -= 1

    def receive(self, timeout: float) -> bytes:
        deadline = _deadline(timeout)
        with _locked_until(self._io_lock, deadline):
            self._check_open()
            if self._receive_left == 0:
                raise RecoveryPipeError('该恢复连接的接收帧数已用完。')
            try:
                size, = _LENGTH.unpack(self._read(_LENGTH.size, deadline))
                if size > MAX_FRAME_BYTES:
                    raise RecoveryPipeError('恢复管道收到过大的帧。')
                payload = self._read(size, deadline)
                self._write(_FRAME_ACK, deadline)
                self.assert_peer_alive()
            except BaseException:
                self._failed = True
                raise
            self._receive_left -= 1
            return payload

    def close(self) -> None:
        with self._close_lock:
            if self._pipe is None:
                return
            _signal_cancel(self._cancel)
            with self._io_lock:
                pipe, self._pipe = self._pipe, None
                try:
                    if self._server is None:
                        _winapi.CloseHandle(pipe)
                    else:
                        self._server._release(self)
                finally:
                    try:
                        self._peer.close()
                    finally:
                        _winapi.CloseHandle(self._cancel)

    def __enter__(self) -> RecoveryPipeConnection:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class RecoveryPipeServer:
    """One exclusive local named-pipe instance, reused between connections."""

    def __init__(self, name: str):
        _name_kind(name)
        self._local = current_recovery_identity()
        self._lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closing = threading.Event()
        self._active: RecoveryPipeConnection | None = None
        self._pending_accept = None
        self._cancel = _cancel_event()
        try:
            self._pipe = _create_listener(name, self._local)
        except BaseException:
            _winapi.CloseHandle(self._cancel)
            raise

    def accept(self, timeout: float, *, expected_pid=None, expected_created=None) -> RecoveryPipeConnection:
        _expected(expected_pid, expected_created, required=False)
        deadline = _deadline(timeout)
        with _locked_until(self._lock, deadline):
            if self._closing.is_set() or self._active is not None:
                raise RecoveryPipeError('恢复监听已关闭或仍有一个原连接。')
            peer = None
            try:
                if self._pending_accept is None:
                    self._pending_accept = _winapi.ConnectNamedPipe(self._pipe, overlapped=True)
                # Keep the same native accept when the caller only ends this poll.
                _complete(self._pending_accept, self._cancel, deadline, preserve_on_timeout=True)
                self._pending_accept = None
                peer = _authenticate(self._pipe, True, self._local, expected_pid, expected_created)
                if self._closing.is_set():
                    raise RecoveryPipeError('恢复监听已关闭。')
                self._active = RecoveryPipeConnection(self._pipe, peer, self)
                return self._active
            except TimeoutError:
                raise
            except BaseException:
                self._pending_accept = None
                try:
                    if peer is not None:
                        peer.close()
                finally:
                    _disconnect(self._pipe)
                raise

    def _release(self, connection: RecoveryPipeConnection) -> None:
        with self._lock:
            if self._active is connection:
                try:
                    _disconnect(self._pipe)
                finally:
                    self._active = None

    def close(self) -> None:
        with self._close_lock:
            if self._closing.is_set():
                return
            self._closing.set()
            _signal_cancel(self._cancel)
            with self._lock:
                active = self._active
                pending, self._pending_accept = self._pending_accept, None
            try:
                if pending is not None:
                    _cancel_pending(pending)
                if active is not None:
                    active.close()
            finally:
                try:
                    _winapi.CloseHandle(self._pipe)
                finally:
                    _winapi.CloseHandle(self._cancel)

    def __enter__(self) -> RecoveryPipeServer:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def connect_recovery_pipe(name: str, *, expected_pid=None, expected_created=None,
                          timeout: float) -> RecoveryPipeConnection:
    kind = _name_kind(name)
    _expected(expected_pid, expected_created, required=kind == 'recovery')
    deadline = _deadline(timeout)
    local = current_recovery_identity()
    flags = _winapi.FILE_FLAG_OVERLAPPED | _IDENTIFICATION_SQOS
    while True:
        _remaining(deadline)
        try:
            pipe = _winapi.CreateFile(name, _PIPE_ACCESS, 0, 0, _winapi.OPEN_EXISTING, flags, 0)
            break
        except OSError as exc:
            if exc.winerror not in (2, _winapi.ERROR_PIPE_BUSY):
                raise
            time.sleep(min(.02, _remaining(deadline)))
    peer = None
    try:
        peer = _authenticate(pipe, False, local, expected_pid, expected_created)
        _remaining(deadline)
        return RecoveryPipeConnection(pipe, peer, None)
    except BaseException:
        try:
            if peer is not None:
                peer.close()
        finally:
            _winapi.CloseHandle(pipe)
        raise
