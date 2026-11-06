"""One opt-in source GUI handoff, using private inherited Windows pipes."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

import _winapi

from .onedragon_runtime import UPSTREAM_REVISION
from .recovery_channel import RecoveryRejected
from .recovery_discovery import validate_recovery_scope
from .recovery_pipe import current_recovery_identity
from .trusted_handoff import (
    HandoffDenied, ProcessPin, TrustedLauncher, _ancestor, _decode, _identity, _key, _startup, _write,
    connect_as_target, register_worker_handle,
)


_HANDOFF_ERRORS = (HandoffDenied, OSError, RecoveryRejected, ValueError)


class OriginalHandoff:
    def __init__(self, root, scope, startup, broker, reader, writer):
        self.root, self.scope, self.startup = root, scope, startup
        self._broker = broker
        self._reader, self._writer = reader, writer
        self._lock = threading.Lock()

    def _matches(self, paths):
        source = self.root / 'vendor/onedragon'
        stable = paths.selection is None or paths.selection.kind == 'stable'
        return (not paths.management_only and stable and paths.revision == UPSTREAM_REVISION
                and paths.product_root is not None and paths.product_root.resolve() == self.root
                and paths.bridge_root.resolve() == self.root and paths.source.resolve() == source.resolve()
                and paths.python.resolve() == (source / '.venv/Scripts/python.exe').resolve())

    @contextmanager
    def worker_options(self, paths):
        reader = None
        options = {}
        with self._lock:
            if self._reader is not None and self._matches(paths):
                reader, self._reader = self._reader, None
                try:
                    with ProcessPin(*self._broker):
                        _winapi.PeekNamedPipe(reader, 0)
                except _HANDOFF_ERRORS:
                    pass
                else:
                    options = dict(retain_on_host_loss=True, recovery_scope=self.scope, trusted_handoff=self.startup)
        try:
            yield options
        finally:
            if reader is not None:
                _winapi.CloseHandle(reader)

    def register(self, client, job):
        with self._lock:
            if self._writer is None:
                return
            writer, self._writer = self._writer, None
        try:
            register_worker_handle(writer, client, job)
        finally:
            _winapi.CloseHandle(writer)

    def close(self):
        with self._lock:
            for name in ('_reader', '_writer'):
                handle = getattr(self, name)
                if handle is not None:
                    setattr(self, name, None)
                    _winapi.CloseHandle(handle)


@dataclass(frozen=True)
class RecoveredCharge:
    client: object
    worker: ProcessPin
    job: str

    def stop(self):
        return self.client.stop_current()


def _route(raw):
    route = _decode(raw)
    if set(route) != {'version', 'root', 'scope', 'broker_pid', 'broker_created', 'report'}:
        raise HandoffDenied('可信 GUI 启动记录无效。')
    broker = _identity(route['broker_pid'], route['broker_created'])
    if type(route['root']) is not str or type(route['report']) is not int or not 0 < route['report'] <= sys.maxsize:
        raise HandoffDenied('可信 GUI 目录或登记管道无效。')
    root = Path(route['root']).resolve()
    scope = validate_recovery_scope(route['scope'])
    report = route['report']
    with ProcessPin(*broker) as owner:
        if not _ancestor(_key(current_recovery_identity()), owner.key):
            raise HandoffDenied('GUI 不属于可信启动方的启动链。')
        if _winapi.GetFileType(report) != 3 or not os.get_handle_inheritable(report):
            raise HandoffDenied('GUI 登记写端无效。')
        os.set_handle_inheritable(report, False)
    return root, scope, broker, report


@contextmanager
def gui_context(root, *, original=None, startup=None, replacement=None):
    """Return optional GUI state only after validating a private launch role."""
    resources = ExitStack()
    options = {}
    try:
        if original is not None and startup is not None and replacement is None:
            expected_root, scope, expected_broker, writer = _route(original)
            resources.callback(_winapi.CloseHandle, writer)
            broker, reader = _startup(startup, check_open=False)
            if reader == writer or not os.get_handle_inheritable(reader):
                raise HandoffDenied('原 GUI 继承读端无效。')
            resources.callback(_winapi.CloseHandle, reader)
            if broker != expected_broker or expected_root != root or Path.cwd().resolve() != root:
                raise HandoffDenied('原 GUI 目录或启动身份不符。')
            os.set_handle_inheritable(reader, False)
            handoff = OriginalHandoff(root, scope, startup, broker, reader, writer)
            resources.pop_all()  # The original GUI now owns the two endpoints.
            resources.callback(handoff.close)
            options['source_handoff'] = handoff
        elif replacement is not None and original is None and startup is None:
            expected_root, _scope, _broker, writer = _route(replacement)
            try:
                if expected_root != root or Path.cwd().resolve() != root:
                    raise HandoffDenied('新 GUI 与原源码目录不符。')
                pid, created = _key(current_recovery_identity())
                _write(writer, dict(pid=pid, created=created))
            finally:
                _winapi.CloseHandle(writer)
            client = resources.enter_context(connect_as_target(sys.stdin, timeout=8))
            descriptor = client.descriptor
            worker = resources.enter_context(ProcessPin(descriptor.pid, descriptor.created))
            claimed = client.claim()
            options['recovered'] = RecoveredCharge(client, worker, claimed['job_id'])
    except _HANDOFF_ERRORS:
        resources.close()
    try:
        yield options
    finally:
        resources.close()


def _launch_reporter(launch, root, scope, option, gui_args):
    reader, writer = _winapi.CreatePipe(None, 4096)
    try:
        pid, created = _key(current_recovery_identity())
        route = json.dumps(dict(version=1, root=str(root), scope=scope, broker_pid=pid, broker_created=created,
                                report=writer), ensure_ascii=True, separators=(',', ':'))
        command = [sys.executable, '-B', '-m', 'autozzz', *gui_args, option, route]
        process = launch(command, cwd=root, stdout=None, inherited_handles=(writer,))
        return process, reader
    except BaseException:
        _winapi.CloseHandle(reader)
        raise
    finally:
        _winapi.CloseHandle(writer)


def supervise(root, gui_args):
    """Return None when the ordinary source GUI should handle startup."""
    try:
        launcher = TrustedLauncher('source-handoff-' + uuid4().hex)
    except _HANDOFF_ERRORS:
        return None
    with launcher:
        try:
            original, report = _launch_reporter(launcher.launch_original, root, launcher.scope,
                                                '--_source-original', gui_args)
        except _HANDOFF_ERRORS:
            return None
        try:
            while original.poll() is None:
                if report is not None:
                    try:
                        accepted = launcher.poll_worker_registration(report)
                    except _HANDOFF_ERRORS:
                        accepted = False
                    if accepted is not None:
                        _winapi.CloseHandle(report)
                        report = None
                time.sleep(.02)
        finally:
            original.stdin.close()
            if report is not None:
                _winapi.CloseHandle(report)
        if original.returncode == 0 or launcher.worker is None:
            return original.returncode
        try:
            if launcher.awaiting_worker(timeout=2) is None:
                return original.returncode
            target, report = _launch_reporter(launcher.launch_target, root, launcher.scope,
                                              '--_source-replacement', gui_args)
        except _HANDOFF_ERRORS:
            return original.returncode
        try:
            launcher.register_target(report)
            launcher.authorize_target(timeout=2)
        except _HANDOFF_ERRORS:
            # EOF makes an already launched replacement use its ordinary GUI.
            target.stdin.close()
        finally:
            _winapi.CloseHandle(report)
        return target.wait()
