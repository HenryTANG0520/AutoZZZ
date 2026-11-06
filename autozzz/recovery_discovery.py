"""只为当前 Windows 登录会话发布原工作进程的短期恢复地址。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import threading
import time
from typing import Callable

from .recovery_pipe import RecoveryPeerError, RecoveryPipeServer, connect_recovery_pipe, current_recovery_identity


_BUSINESS_PREFIX = r'\\.\pipe\AutoZZZ.Recovery.'
_DISCOVERY_PREFIX = _BUSINESS_PREFIX + 'Discovery.'
_SCOPE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}')
_HEX_ID = re.compile(r'[0-9a-f]{32}')
_FIELDS = frozenset({'version', 'pid', 'created', 'pipe_name', 'generation', 'deadline'})
_MAX_DESCRIPTOR_BYTES = 512
_MAX_SECONDS = 30.0
_ACCEPT_SECONDS = .1
_SEND_SECONDS = .25


@dataclass(frozen=True, slots=True)
class RecoveryDescriptor:
    pid: int
    created: int
    pipe_name: str
    generation: str
    deadline: float


def validate_recovery_scope(scope: str) -> str:
    if type(scope) is not str or _SCOPE.fullmatch(scope) is None:
        raise ValueError('恢复发现作用域无效。')
    return scope


def _discovery_name(scope: str, identity) -> str:
    parts = [validate_recovery_scope(scope), identity.user_sid, identity.logon_sid,
             identity.authentication_id, identity.session_id]
    encoded = json.dumps(parts, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return _DISCOVERY_PREFIX + hashlib.sha256(encoded).hexdigest()


def _validate_descriptor(descriptor: RecoveryDescriptor, now: float) -> None:
    if type(descriptor) is not RecoveryDescriptor:
        raise ValueError('恢复发现描述符格式无效。')
    if (type(descriptor.pid) is not int or not 0 < descriptor.pid < 2**32
            or type(descriptor.created) is not int or not 0 < descriptor.created < 2**64
            or type(descriptor.pipe_name) is not str or not descriptor.pipe_name.startswith(_BUSINESS_PREFIX)
            or _HEX_ID.fullmatch(descriptor.pipe_name[len(_BUSINESS_PREFIX):]) is None
            or type(descriptor.generation) is not str or _HEX_ID.fullmatch(descriptor.generation) is None
            or type(descriptor.deadline) is not float or not now < descriptor.deadline <= now + _MAX_SECONDS):
        raise ValueError('恢复发现描述符格式无效或已过期。')


def _unique_fields(pairs: list[tuple]) -> dict:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError('恢复发现描述符有重复字段。')
    return fields


def _decode(payload: bytes) -> RecoveryDescriptor:
    if type(payload) is not bytes or not 0 < len(payload) <= _MAX_DESCRIPTOR_BYTES:
        raise ValueError('恢复发现描述符尺寸无效。')
    fields = json.loads(payload.decode('ascii'), object_pairs_hook=_unique_fields)
    if type(fields) is not dict or set(fields) != _FIELDS:
        raise ValueError('恢复发现描述符字段无效。')
    version = fields.pop('version')
    if type(version) is not int or version != 1:
        raise ValueError('恢复发现描述符版本不符。')
    descriptor = RecoveryDescriptor(**fields)
    _validate_descriptor(descriptor, time.monotonic())
    return descriptor


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('恢复发现等待已到期。')
    return remaining


class RecoveryPublication:
    def __init__(self, server: RecoveryPipeServer, payload: bytes, deadline: float,
                 available: Callable[[], bool] | None):
        self._server = server
        self._payload = payload
        self._deadline = deadline
        self._available = available
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._serve, name='AutoZZZ-Recovery-Discovery', daemon=True)
        self._thread.start()

    def _eligible(self) -> bool:
        if self._closed.is_set() or time.monotonic() >= self._deadline:
            return False
        if self._available is not None:
            try:
                if self._available() is not True:
                    return False
            except Exception:
                return False
        return not self._closed.is_set() and time.monotonic() < self._deadline

    def _serve(self) -> None:
        try:
            while self._eligible():
                try:
                    with self._server.accept(min(_ACCEPT_SECONDS, _remaining(self._deadline))) as connection:
                        if not self._eligible():
                            return
                        connection.assert_peer_alive()
                        connection.send(self._payload, min(_SEND_SECONDS, _remaining(self._deadline)))
                except TimeoutError:
                    continue
                except OSError:
                    # 客户端可在读取前退出。保持原实例，并给撤销和到期检查留机会。
                    self._closed.wait(.01)
        finally:
            self._closed.set()
            self._server.close()

    def close(self) -> None:
        self._closed.set()
        self._server.close()
        if threading.current_thread() is not self._thread:
            self._thread.join()

    def __enter__(self) -> RecoveryPublication:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def publish_recovery(scope: str, descriptor: RecoveryDescriptor, *,
                     available: Callable[[], bool] | None = None) -> RecoveryPublication:
    """在原进程冻结成功后发布。available 只做快速的只读状态检查。"""
    validate_recovery_scope(scope)
    _validate_descriptor(descriptor, time.monotonic())
    if available is not None and (not callable(available) or available() is not True):
        raise ValueError('原工作进程当前不可发布恢复地址。')
    identity = current_recovery_identity()
    if (descriptor.pid, descriptor.created) != (identity.pid, identity.created):
        raise ValueError('恢复发现只允许发布当前进程。')
    payload = json.dumps(dict(version=1, **asdict(descriptor)), separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(payload) > _MAX_DESCRIPTOR_BYTES:
        raise ValueError('恢复发现描述符过大。')
    server = RecoveryPipeServer(_discovery_name(scope, identity))
    try:
        return RecoveryPublication(server, payload, descriptor.deadline, available)
    except BaseException:
        server.close()
        raise


def discover_recovery(scope: str, *, timeout: float,
                      expected_pid: int | None = None, expected_created: int | None = None) -> RecoveryDescriptor | None:
    """用独立进程身份验证发现端。未指定身份时仅提供本机定位信息。"""
    validate_recovery_scope(scope)
    if type(timeout) not in (int, float) or not 0 < timeout <= _MAX_SECONDS:
        raise ValueError('恢复发现等待时限无效。')
    deadline = time.monotonic() + timeout
    try:
        name = _discovery_name(scope, current_recovery_identity())
        with connect_recovery_pipe(name, expected_pid=expected_pid, expected_created=expected_created,
                                   timeout=_remaining(deadline)) as connection:
            descriptor = _decode(connection.receive(_remaining(deadline)))
            if (descriptor.pid, descriptor.created) != (connection.peer.pid, connection.peer.created):
                return None
            connection.assert_peer_alive()
            _remaining(min(deadline, descriptor.deadline))
            return descriptor
    except RecoveryPeerError:
        if expected_pid is not None:
            raise
        return None
    except (OSError, ValueError, RecursionError):
        return None
