"""Read, claim, continue once and cooperatively stop the one retained worker."""
from __future__ import annotations

import json
import math
import secrets
import threading
import time

from .recovery_discovery import RecoveryDescriptor, publish_recovery
from .recovery_pipe import (
    RecoveryPipeServer,
    connect_recovery_pipe,
    current_recovery_identity,
    new_recovery_pipe_name,
)

_VERSION = 1
_MAX_FRAME = 4096
_ACCEPT_SECONDS = .08
_EXCHANGE_SECONDS = .5
_METHODS = frozenset({'status', 'claim', 'resume_current', 'stop_current'})
_REQUEST_KEYS = frozenset({'version', 'method', 'generation', 'challenge', 'pid', 'created'})


class RecoveryRejected(RuntimeError):
    """The original worker could not authorize or complete the operation."""


def _remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError('原任务恢复期限已到。')
    return seconds


def _timeout(value):
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= 5:
        raise ValueError('恢复请求等待时间无效。')
    return float(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('恢复帧有重复字段。')
        result[key] = value
    return result


def _read(connection, deadline):
    raw = connection.receive(_remaining(deadline))
    if not 0 < len(raw) <= _MAX_FRAME:
        raise ValueError('恢复帧长度无效。')
    try:
        message = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    except RecursionError as exc:
        raise ValueError('恢复帧结构无效。') from exc
    if type(message) is not dict:
        raise ValueError('恢复帧结构无效。')
    return message


def _write(connection, message, deadline):
    raw = json.dumps(message, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > _MAX_FRAME:
        raise ValueError('恢复帧长度无效。')
    connection.send(raw, _remaining(deadline))


def _process(identity):
    return dict(pid=identity.pid, created=identity.created)


class RecoveryClient:
    """A client tied to the process that opened it and to the discovered worker."""

    def __init__(self, descriptor: RecoveryDescriptor, *, timeout: float = 1.0):
        if not isinstance(descriptor, RecoveryDescriptor):
            raise ValueError('恢复发现描述符无效。')
        self.descriptor = descriptor
        self._timeout = _timeout(timeout)
        self._identity = current_recovery_identity()

    def status(self) -> dict:
        return self._call('status')

    def claim(self) -> dict:
        return self._call('claim')

    def stop_current(self) -> dict:
        return self._call('stop_current')

    def resume_current(self) -> dict:
        return self._call('resume_current')

    def _call(self, method):
        if current_recovery_identity() != self._identity:
            raise RecoveryRejected('request_rejected')
        descriptor = self.descriptor
        deadline = min(time.monotonic() + self._timeout, descriptor.deadline)
        try:
            connection = connect_recovery_pipe(
                descriptor.pipe_name, expected_pid=descriptor.pid, expected_created=descriptor.created,
                timeout=_remaining(deadline),
            )
            try:
                hello = _read(connection, deadline)
                challenge = hello.get('challenge')
                if (set(hello) != {'version', 'generation', 'challenge'} or type(hello['version']) is not int
                        or hello['version'] != _VERSION or hello['generation'] != descriptor.generation
                        or type(challenge) is not str or len(challenge) != 32):
                    raise RecoveryRejected('request_rejected')
                connection.assert_peer_alive()
                _write(connection, dict(version=_VERSION, method=method, generation=descriptor.generation,
                                        challenge=challenge, **_process(self._identity)), deadline)
                response = _read(connection, deadline)
            finally:
                connection.close()
        except (OSError, ValueError) as exc:
            raise RecoveryRejected('recovery_unavailable') from exc
        if set(response) == {'ok', 'error'} and response['ok'] is False and type(response['error']) is str:
            raise RecoveryRejected(response['error'])
        if (set(response) != {'ok', 'result'} or response['ok'] is not True
                or type(response['result']) is not dict or response['result'].get('worker') != _process(descriptor)):
            raise RecoveryRejected('request_rejected')
        return response['result']


class RecoveryServer:
    """Ephemeral endpoint. The monitor owns its lifetime and the original execution."""

    def __init__(self, scope, deadline, *, available, snapshot, handoff, resume=None):
        self.stop_requested = threading.Event()
        self._closing = threading.Event()
        self._revoke_lock = threading.Lock()
        self._available = available
        self._snapshot = snapshot
        self._handoff = handoff
        self._claimant = None
        self._resume = resume
        self._resume_used = self._resume_started = self._stop_accepted = False
        identity = current_recovery_identity()
        self._descriptor = RecoveryDescriptor(
            pid=identity.pid, created=identity.created, pipe_name=new_recovery_pipe_name(),
            generation=secrets.token_hex(16), deadline=deadline,
        )
        if not self._is_open():
            raise RecoveryRejected('recovery_unavailable')
        self._listener = RecoveryPipeServer(self._descriptor.pipe_name)
        try:
            self._publication = publish_recovery(scope, self._descriptor, available=self._is_open)
            self._thread = threading.Thread(target=self._serve, name='AutoZZZ-RecoveryChannel', daemon=True)
            try:
                self._thread.start()
            except BaseException:
                self._publication.close()
                raise
        except BaseException:
            self._listener.close()
            raise

    def _is_open(self):
        return (not self._closing.is_set() and not self.stop_requested.is_set()
                and time.monotonic() < self._descriptor.deadline and self._handoff.alive() and self._available())

    def _continuation_authority(self, claimant, generation):
        if (self._stop_accepted or claimant is not self._claimant or generation != self._descriptor.generation
                or not self._is_open()):
            return False
        try:
            claimant.assert_alive()
        except OSError:
            return False
        return True

    def _serve(self):
        try:
            while self._is_open():
                if self._claimant is not None:
                    try:
                        self._claimant.assert_alive()
                    except OSError:
                        self.stop_requested.set()
                        return
                try:
                    timeout = min(_ACCEPT_SECONDS, _remaining(self._descriptor.deadline))
                    connection = self._listener.accept(timeout)
                except TimeoutError:
                    continue
                except OSError:
                    self._closing.wait(.01)
                    continue
                try:
                    self._handle(connection)
                except (OSError, ValueError):
                    continue
                finally:
                    connection.close()
        finally:
            try:
                self._revoke()
            finally:
                if self._claimant is not None:
                    self._claimant.close()

    def _handle(self, connection):
        if not self._is_open():
            return
        descriptor = self._descriptor
        deadline = min(descriptor.deadline, time.monotonic() + _EXCHANGE_SECONDS)
        challenge = secrets.token_hex(16)
        _write(connection, dict(version=_VERSION, generation=descriptor.generation, challenge=challenge), deadline)
        request = _read(connection, deadline)
        method = request.get('method')
        peer = connection.peer
        stop = False
        try:
            if (set(request) != _REQUEST_KEYS or type(request['version']) is not int or request['version'] != _VERSION
                    or type(method) is not str or method not in _METHODS or request['generation'] != descriptor.generation
                    or request['challenge'] != challenge or type(request['pid']) is not int or request['pid'] != peer.pid
                    or type(request['created']) is not int or request['created'] != peer.created):
                raise RecoveryRejected('request_rejected')
            connection.assert_peer_alive()
            if not self._is_open():
                raise RecoveryRejected('recovery_unavailable')
            broker_status = method == 'status' and self._handoff.is_broker(peer)
            permit = None if broker_status else self._handoff.grant_for(peer, descriptor)
            if not broker_status and permit is None:
                raise RecoveryRejected('handoff_not_authorized')
            if method == 'claim' and self._claimant is not None:
                raise RecoveryRejected('claim_used')
            if method in ('stop_current', 'resume_current') and (self._claimant is None or self._claimant.identity != peer):
                raise RecoveryRejected('not_claim_owner')
            if method == 'resume_current' and self._resume_used:
                raise RecoveryRejected('resume_used')
            try:
                snapshot = self._snapshot()
            except Exception:
                snapshot = None
            if snapshot is None:
                stop = True
                raise RecoveryRejected('recovery_unavailable')
            if permit is not None and snapshot.get('job_id') != permit['job']:
                stop = True
                raise RecoveryRejected('handoff_task_changed')
            with self._revoke_lock:
                connection.assert_peer_alive()
                _remaining(deadline)
                if not self._is_open():
                    raise RecoveryRejected('recovery_unavailable')
                if method == 'claim':
                    self._claimant = connection.retain_peer()
                if method == 'stop_current':
                    self._claimant.assert_alive()
                    self._stop_accepted = True
                    stop = True
                    state = 'stop_requested'
                elif method == 'resume_current':
                    claimant = self._claimant
                    claimant.assert_alive()
                    self._resume_used = True
                    state = 'resume_started'
                elif self._resume_started:
                    state = 'resumed'
                else:
                    state = 'awaiting_claim' if self._claimant is None else 'claimed'
            if method == 'resume_current':
                graph_visit = snapshot.get('graph_visit')
                if type(graph_visit) is not dict or graph_visit.get('state') != 'original':
                    raise RecoveryRejected('continuity_unverified')

                def authority():
                    return self._continuation_authority(claimant, descriptor.generation)

                try:
                    resumed = None if self._resume is None else self._resume(authority, deadline)
                except Exception:
                    stop = True
                    raise RecoveryRejected('recovery_unavailable') from None
                if resumed is None:
                    raise RecoveryRejected('continuity_unverified')
                self._resume_started = True
                snapshot = resumed
            result = dict(snapshot, state=state, worker=_process(descriptor),
                          claim_owner=None if self._claimant is None else _process(self._claimant.identity))
            _write(connection, dict(ok=True, result=result), deadline)
        except RecoveryRejected as exc:
            _write(connection, dict(ok=False, error=str(exc)), deadline)
        finally:
            if stop:
                self.stop_requested.set()

    def _revoke(self):
        with self._revoke_lock:
            if self._closing.is_set():
                return
            self._closing.set()
        try:
            self._publication.close()
        finally:
            self._listener.close()

    def close(self):
        self._revoke()
        self._thread.join(_EXCHANGE_SECONDS)
