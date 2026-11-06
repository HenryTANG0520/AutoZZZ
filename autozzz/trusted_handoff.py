"""Private startup and one-shot claimant authorization for retained Windows workers."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import ctypes as C
import json
import math
import msvcrt
import os
import re
import subprocess
import threading
import time

import _winapi

from .recovery_discovery import discover_recovery, validate_recovery_scope
from .recovery_pipe import RecoveryPeerError, current_recovery_identity, verify_recovery_owner

_LIMIT = 1024
_PROCESS_ACCESS = 0x101000  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
_LAUNCH_LOCK = threading.Lock()


class HandoffDenied(RuntimeError):
    pass


def _key(identity):
    return identity.pid, identity.created


def _identity(pid, created):
    if (type(pid) is not int or not 0 < pid < 2**32
            or type(created) is not int or not 0 < created < 2**64):
        raise HandoffDenied('交接进程身份无效。')
    return pid, created


def _job(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value) is None:
        raise HandoffDenied('原任务编号无效。')
    return value


def _decode(raw):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError('duplicate field')
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise HandoffDenied('私有交接消息无效。') from exc
    if type(value) is not dict or type(value.get('version')) is not int or value['version'] != 1:
        raise HandoffDenied('私有交接协议版本无效。')
    return value


def _pipe_handle(stream):
    return msvcrt.get_osfhandle(stream.fileno())


def _write(handle, value):
    data = json.dumps(dict(version=1, **value), ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    if len(data) > _LIMIT:
        raise HandoffDenied('私有交接消息超限。')
    count, error = _winapi.WriteFile(handle, data)
    if error or count != len(data):
        raise HandoffDenied('私有交接消息未完整送达。')


def _read_available(handle):
    data, available, _ = _winapi.PeekNamedPipe(handle, _LIMIT + 1)
    if available > _LIMIT:
        raise HandoffDenied('私有交接消息超限。')
    if b'\n' not in data:
        return None
    if data.count(b'\n') != 1 or not data.endswith(b'\n'):
        raise HandoffDenied('只允许一条私有交接消息。')
    packet, error = _winapi.ReadFile(handle, len(data))
    if error or packet != data:
        raise HandoffDenied('私有交接消息发生变化。')
    return _decode(packet)


def _read_wait(handle, timeout):
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise ValueError('私有交接等待时间无效。')
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            value = _read_available(handle)
        except OSError as exc:
            raise HandoffDenied('私有交接读端已失效。') from exc
        if value is not None:
            return value
        time.sleep(.01)
    raise HandoffDenied('等待私有交接消息超时。')


class ProcessPin:
    """A live Windows process opened independently of named-pipe discovery."""

    def __init__(self, pid, created=None):
        if type(pid) is not int or not 0 < pid < 2**32:
            raise HandoffDenied('交接 PID 无效。')
        self.handle = _winapi.OpenProcess(_PROCESS_ACCESS, False, pid)
        try:
            self.identity = verify_recovery_owner(self.handle, pid)
            if created is not None and _identity(pid, created) != self.key:
                raise HandoffDenied('交接进程创建时间不符。')
        except BaseException:
            self.close()
            raise

    @property
    def key(self):
        return _key(self.identity)

    def alive(self):
        return self.handle is not None and _winapi.WaitForSingleObject(self.handle, 0) == _winapi.WAIT_TIMEOUT

    def close(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            _winapi.CloseHandle(handle)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _ancestor(child, parent):
    from .worker_host import _ancestor_of, _kernel

    return _ancestor_of(_kernel(), *parent, *child)


def _pin_popen(process):
    from .task_lease import _created
    from .worker_host import _kernel

    if process.poll() is not None:
        raise HandoffDenied('启动方实际创建的进程已退出。')
    return ProcessPin(process.pid, _created(_kernel(), int(process._handle)))


def _startup(raw, *, check_open=True):
    if type(raw) is not str or len(raw) > _LIMIT:
        raise HandoffDenied('私有交接启动参数无效。')
    data = _decode(raw)
    if set(data) != {'version', 'broker_pid', 'broker_created', 'reader'}:
        raise HandoffDenied('私有交接启动字段无效。')
    broker = _identity(data['broker_pid'], data['broker_created'])
    handle = data['reader']
    if type(handle) is not int or not 0 < handle < 2 ** (C.sizeof(C.c_void_p) * 8):
        raise HandoffDenied('私有交接读端无效。')
    if _winapi.GetFileType(handle) != 3:  # FILE_TYPE_PIPE
        raise HandoffDenied('私有交接读端须为管道。')
    if check_open:
        _winapi.PeekNamedPipe(handle, 0)
    return broker, handle


@contextmanager
def _inherit_handles(*handles):
    startup = subprocess.STARTUPINFO()
    startup.lpAttributeList = {'handle_list': list(handles)}
    with _LAUNCH_LOCK:
        try:
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            yield startup
        finally:
            for handle in handles:
                os.set_handle_inheritable(handle, False)


@contextmanager
def forward_to_worker(raw):
    """Pass the borrowed read endpoint through the existing SDK Popen."""
    broker, handle = _startup(raw)
    with ProcessPin(*broker) as owner:
        if not _ancestor(_key(current_recovery_identity()), owner.key):
            raise HandoffDenied('可信启动方不在原宿主的启动链。')
        with _inherit_handles(handle) as startup:
            yield startup


class WorkerHandoff:
    """The worker can read one permit from the broker-only anonymous pipe."""

    def __init__(self, raw):
        broker, self._reader = _startup(raw)
        self._lock = threading.RLock()
        self._broker = None
        self._permit = None
        try:
            os.set_handle_inheritable(self._reader, False)
            self._broker = ProcessPin(*broker)
            if not _ancestor(_key(current_recovery_identity()), self._broker.key):
                raise HandoffDenied('可信启动方不在 SDK worker 的启动链。')
        except BaseException:
            self.close()
            raise

    def alive(self):
        with self._lock:
            return self._broker is not None and self._broker.alive()

    def is_broker(self, peer):
        with self._lock:
            return self.alive() and _key(peer) == self._broker.key

    def grant_for(self, peer, descriptor):
        with self._lock:
            if not self.alive():
                return None
            if self._reader is not None:
                try:
                    permit = _read_available(self._reader)
                    if permit is None:
                        return None
                    self._permit = _validate_permit(permit)
                except (OSError, HandoffDenied):
                    self._permit = None
                self._close_reader()
            permit = self._permit
            if (permit is None or _key(peer) != (permit['target_pid'], permit['target_created'])
                    or (descriptor.pid, descriptor.created) != (permit['worker_pid'], permit['worker_created'])
                    or permit['generation'] != descriptor.generation or permit['deadline'] != descriptor.deadline
                    or time.monotonic() >= permit['deadline']):
                return None
            return permit

    def _close_reader(self):
        if self._reader is not None:
            handle, self._reader = self._reader, None
            _winapi.CloseHandle(handle)

    def close(self):
        with self._lock:
            self._close_reader()
            if self._broker is not None:
                self._broker.close()
                self._broker = None


_PERMIT_KEYS = {'version', 'worker_pid', 'worker_created', 'target_pid', 'target_created', 'generation', 'deadline', 'job'}


def _validate_permit(permit):
    if set(permit) != _PERMIT_KEYS:
        raise HandoffDenied('认领授权字段无效。')
    _identity(permit['worker_pid'], permit['worker_created'])
    _identity(permit['target_pid'], permit['target_created'])
    _job(permit['job'])
    generation, deadline = permit['generation'], permit['deadline']
    if (type(generation) is not str or re.fullmatch(r'[a-f0-9]{32}', generation) is None
            or type(deadline) not in (float, int) or not math.isfinite(deadline)
            or not 0 < deadline - time.monotonic() <= 30):
        raise HandoffDenied('认领代次或截止时间无效。')
    return permit


def register_original_worker(stream, client, job):
    """Called after the existing client has started the original charge task."""
    register_worker_handle(_pipe_handle(stream), client, job)


def register_worker_handle(handle, client, job):
    owner = _key(current_recovery_identity())
    worker = client.handoff_worker_identity()
    _write(handle, dict(owner_pid=owner[0], owner_created=owner[1],
                        worker_pid=worker[0], worker_created=worker[1], job=_job(job)))


def _pin_launched_identity(process, pid, created):
    owner = ProcessPin(*_identity(pid, created))
    try:
        with _pin_popen(process) as started:
            if not started.alive() or (owner.key != started.key and not _ancestor(owner.key, started.key)):
                raise HandoffDenied('报告进程不属于可信启动方创建的进程。')
        return owner
    except BaseException:
        owner.close()
        raise


def discover_pinned_worker(scope, pin, *, generation=None, deadline=None, timeout=1.0):
    from .recovery_channel import RecoveryClient

    if not pin.alive():
        raise HandoffDenied('原 worker 进程已经退出。')
    pid, created = pin.key
    try:
        descriptor = discover_recovery(scope, expected_pid=pid, expected_created=created, timeout=timeout)
    except RecoveryPeerError as exc:
        raise HandoffDenied('发现管道对端与保留的原 worker 不符。') from exc
    if descriptor is None:
        return None
    if (not pin.alive() or (descriptor.pid, descriptor.created) != pin.key
            or generation not in (None, descriptor.generation) or deadline not in (None, descriptor.deadline)):
        raise HandoffDenied('发现结果与独立核实的原 worker 不符。')
    return RecoveryClient(descriptor, timeout=timeout)


class TrustedLauncher:
    """An explicit surviving launcher for one original host and one designated successor."""

    def __init__(self, scope):
        self.scope = validate_recovery_scope(scope)
        self._broker = _key(current_recovery_identity())
        self._reader, self._writer = _winapi.CreatePipe(None, 4096)
        self.original = self.target = None
        self._owner = self.worker = self._job = None
        self._target_pin = None
        self._issued = False
        self._authorization_lock = threading.Lock()

    def launch_original(self, command, *, cwd=None, env=None, stderr=None, stdout=subprocess.PIPE, inherited_handles=()):
        if self.original is not None or self._reader is None:
            raise HandoffDenied('原宿主启动资格已使用。')
        payload = json.dumps(dict(version=1, broker_pid=self._broker[0],
                                  broker_created=self._broker[1], reader=self._reader), separators=(',', ':'))
        try:
            with _inherit_handles(self._reader, *inherited_handles) as startup:
                self.original = subprocess.Popen([*command, '--trusted-handoff', payload], stdin=subprocess.PIPE,
                                                 stdout=stdout, stderr=stderr, cwd=cwd, env=env,
                                                 startupinfo=startup, close_fds=True,
                                                 creationflags=subprocess.CREATE_NO_WINDOW)
            return self.original
        finally:
            handle, self._reader = self._reader, None
            _winapi.CloseHandle(handle)

    def register_worker(self, *, timeout=10):
        if self.original is None or self.original.stdout is None:
            raise HandoffDenied('原 worker 登记管道无效。')
        return self._accept_worker_report(_read_wait(_pipe_handle(self.original.stdout), timeout))

    def poll_worker_registration(self, report_handle):
        report = _read_available(report_handle)
        return None if report is None else self._accept_worker_report(report)

    def _accept_worker_report(self, report):
        if self.original is None or self.worker is not None:
            raise HandoffDenied('原 worker 登记时序无效。')
        if set(report) != {'version', 'owner_pid', 'owner_created', 'worker_pid', 'worker_created', 'job'}:
            raise HandoffDenied('原 worker 登记字段无效。')
        owner = _pin_launched_identity(self.original, report['owner_pid'], report['owner_created'])
        worker = None
        try:
            worker = ProcessPin(*_identity(report['worker_pid'], report['worker_created']))
            if owner.key == worker.key or not _ancestor(worker.key, owner.key):
                raise HandoffDenied('原 worker 不属于已核实的原宿主。')
            self._job = _job(report['job'])
        except BaseException:
            if worker is not None:
                worker.close()
            owner.close()
            raise
        self._owner, self.worker = owner, worker
        return worker.key

    def launch_target(self, command, *, cwd=None, env=None, stderr=None, stdout=None, inherited_handles=()):
        with self._authorization_lock:
            if self.target is not None or self.worker is None or not self.worker.alive() or self._owner.alive():
                raise HandoffDenied('只在原宿主退出后指定一个新宿主。')
            inheritance = _inherit_handles(*inherited_handles) if inherited_handles else nullcontext(None)
            with inheritance as startup:
                self.target = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                               cwd=cwd, env=env, startupinfo=startup, close_fds=True,
                                               creationflags=subprocess.CREATE_NO_WINDOW)
            if not inherited_handles:
                self._target_pin = _pin_popen(self.target)
            return self.target

    def register_target(self, report_handle, *, timeout=5):
        with self._authorization_lock:
            if self.target is None or self._target_pin is not None or self._issued:
                raise HandoffDenied('新宿主登记时序无效。')
            report = _read_wait(report_handle, timeout)
            if set(report) != {'version', 'pid', 'created'}:
                raise HandoffDenied('新宿主登记字段无效。')
            self._target_pin = _pin_launched_identity(self.target, report['pid'], report['created'])

    def awaiting_worker(self, *, timeout=1.0):
        if self.worker is None or not self.worker.alive() or self._owner.alive():
            return None
        client = discover_pinned_worker(self.scope, self.worker, timeout=timeout)
        if client is None:
            return None
        status = client.status()
        if status.get('state') == 'awaiting_claim' and status.get('job_id') == self._job:
            return client.descriptor
        return None

    def authorize_target(self, *, timeout=1.0):
        with self._authorization_lock:
            target = self._target_pin
            if target is None or self._issued or self._writer is None or self._owner.alive():
                raise HandoffDenied('当前不能发放认领授权。')
            descriptor = self.awaiting_worker(timeout=timeout)
            if descriptor is None:
                raise HandoffDenied('当前保留任务与启动记录不符。')
            if not target.alive() or not self.worker.alive() or time.monotonic() >= descriptor.deadline:
                raise HandoffDenied('授权进程已退出或原任务已到期。')
            permit = dict(worker_pid=descriptor.pid, worker_created=descriptor.created,
                          target_pid=target.key[0], target_created=target.key[1],
                          generation=descriptor.generation, deadline=descriptor.deadline, job=self._job)
            self._issued = True
            try:
                _write(self._writer, permit)
            finally:
                writer, self._writer = self._writer, None
                _winapi.CloseHandle(writer)
            with self.target.stdin as stream:
                _write(_pipe_handle(stream), dict(
                    permit=permit, scope=self.scope, broker_pid=self._broker[0], broker_created=self._broker[1],
                ))

    def close(self):
        with self._authorization_lock:
            for name in ('_reader', '_writer'):
                handle = getattr(self, name)
                if handle is not None:
                    setattr(self, name, None)
                    _winapi.CloseHandle(handle)
            for pin in (self._owner, self.worker, self._target_pin):
                if pin is not None:
                    pin.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


@contextmanager
def connect_as_target(stream, *, timeout=6):
    """Read the launcher's private pin, then independently authenticate discovery."""
    ticket = _read_wait(_pipe_handle(stream), timeout)
    if (set(ticket) != {'version', 'permit', 'scope', 'broker_pid', 'broker_created'}
            or type(ticket['permit']) is not dict or 'version' in ticket['permit']):
        raise HandoffDenied('新宿主私有启动记录无效。')
    permit = _validate_permit(dict(version=1, **ticket['permit']))
    me = _key(current_recovery_identity())
    if me != (permit['target_pid'], permit['target_created']):
        raise HandoffDenied('当前实际进程不是指定的新宿主。')
    with ProcessPin(*_identity(ticket['broker_pid'], ticket['broker_created'])) as broker:
        if not _ancestor(me, broker.key):
            raise HandoffDenied('新宿主不在可信启动方的启动链。')
        with ProcessPin(permit['worker_pid'], permit['worker_created']) as worker:
            client = discover_pinned_worker(ticket['scope'], worker, generation=permit['generation'],
                                            deadline=permit['deadline'], timeout=min(timeout, 2.0))
            if client is None or not broker.alive():
                raise HandoffDenied('未发现同一原 worker 或可信启动方已退出。')
            yield client
