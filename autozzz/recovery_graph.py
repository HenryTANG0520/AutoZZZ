"""Record SDK graph visits for the original paid battle reservation."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock, get_ident
from time import perf_counter
from typing import Any

from .onedragon_charge_recovery import SOURCE_VICTORY


_DEPLOY = '出战'
_REPEAT = '判断下一次'
_REPEAT_RESULT = '战斗结果-再来一次'
_LOADING = '加载自动战斗指令'
_HUD = '等待战斗画面加载'
_BATTLE = '自动战斗'
_END = '战斗结束'


@dataclass(frozen=True, slots=True, eq=False)
class BattleVisit:
    operation: Any
    node: Any
    sequence: int
    reservation: str
    thread: int
    submission: str
    attempted_at: float
    accepted_at: float
    entered_at: float


class RecoveryGraph:
    """SDK-side provenance only. A visit grants no game-side authority."""

    def __init__(self):
        self._lock = Lock()
        self._sequence = 0
        self._closed = False
        self._operation = self._thread = None
        self._clear()

    def _clear(self):
        self._node = self._reservation = self._attempt = self._submission = None
        self._path_index = self._visit = self._terminal = None
        self._used = False

    def _on_thread(self, operation):
        return self._operation is operation and self._thread == get_ident()

    @contextmanager
    def track(self, operation, *, separate_start):
        initialize = getattr(operation, '_init_before_execute', None)
        reset = getattr(operation, '_reset_status_for_new_node', None)
        with self._lock:
            installed = not self._closed and self._operation is None and callable(initialize) and callable(reset)
            if installed:
                self._operation, self._thread = operation, get_ident()
                self._path = (_LOADING, _HUD, '向前移动准备战斗',
                              *(('开始自动战斗',) if separate_start else ()), _BATTLE)
            elif self._operation is not None:
                self._visit = self._terminal = self._path_index = None
                self._used = True
        if not installed:
            yield
            return

        def started():
            initialize()
            self._entered(operation, initial=True)

        def entered():
            self._entered(operation)
            reset()

        hooks = {'_init_before_execute': started, '_reset_status_for_new_node': entered}
        saved = {name: vars(operation)[name] for name in hooks if name in vars(operation)}
        try:
            for name, hook in hooks.items():
                setattr(operation, name, hook)
            yield
        finally:
            for name in hooks:
                if name in saved:
                    setattr(operation, name, saved[name])
                else:
                    vars(operation).pop(name, None)
            with self._lock:
                self._clear()
                self._operation = self._thread = None

    def bind(self, operation, reservation):
        with self._lock:
            if self._on_thread(operation) and self._reservation != reservation:
                self._reservation = reservation
                self._attempt = self._submission = self._path_index = self._visit = self._terminal = None
                self._used = False

    def attempt(self, operation, reservation, kind):
        with self._lock:
            expected = _DEPLOY if kind == 'deploy' else _REPEAT
            if (not self._on_thread(operation) or not reservation or reservation != self._reservation
                    or self._used or self._node is None or self._node.cn != expected
                    or operation.current_node.node is not self._node):
                return None
            self._visit = self._terminal = self._path_index = None
            self._attempt = (kind, reservation, perf_counter())
            return self._attempt

    def accepted(self, operation, attempt):
        with self._lock:
            if attempt is not None and self._on_thread(operation) and self._attempt is attempt:
                self._submission = (*attempt, perf_counter())

    def _entered(self, operation, *, initial=False):
        with self._lock:
            if not self._on_thread(operation):
                return
            previous, node = operation.previous_node, operation.current_node.node
            old_node, self._node = self._node, node
            visit, self._visit = self._visit, None
            self._terminal = None
            self._sequence += 1
            if (initial or node is None or old_node is None or previous.node is not old_node or not previous.is_success):
                self._path_index = self._submission = None
                return
            if (visit is not None and previous.node is visit.node and node.cn == _END
                    and previous.status == SOURCE_VICTORY):
                self._terminal = visit
                return
            if self._submission is None or self._used:
                return
            kind, reservation, attempted_at, accepted_at = self._submission
            if node.cn == _LOADING:
                expected = _DEPLOY if kind == 'deploy' else _REPEAT
                confirmed = (old_node.cn == expected and (kind == 'deploy' or previous.status == _REPEAT_RESULT))
                self._path_index = 0 if confirmed else None
                if not confirmed:
                    self._submission = None
                return
            index = self._path_index
            if (index is None or node.cn != self._path[index + 1]
                    or (old_node.cn == _HUD and previous.status != '按键-普通攻击')):
                self._path_index = self._submission = None
                return
            if node.cn != _BATTLE:
                self._path_index += 1
                return
            self._path_index = None
            self._used = True
            self._visit = BattleVisit(operation, node, self._sequence, reservation, self._thread,
                                      kind, attempted_at, accepted_at, perf_counter())

    def current(self, operation, reservation):
        with self._lock:
            visit = self._visit
            if (visit is not None and visit.operation is operation and visit.reservation == reservation
                    and operation.current_node.node is visit.node):
                return visit
            return None

    def _matches(self, visit, terminal):
        return (visit is not None and self._operation is visit.operation
                and self._reservation == visit.reservation and self._node is visit.operation.current_node.node
                and (self._visit is visit or (terminal and self._terminal is visit)))

    def matches(self, visit, *, terminal=False):
        with self._lock:
            return self._matches(visit, terminal)

    def status(self, visit=None):
        with self._lock:
            matched = self._matches(visit, False)
            return dict(state='original' if matched else ('changed' if visit is not None else 'unverified'),
                        current_sequence=self._sequence, current_node=None if self._node is None else self._node.cn,
                        original_sequence=None if visit is None else visit.sequence,
                        submission=None if visit is None else visit.submission,
                        attempted_submission=None if self._attempt is None else self._attempt[0],
                        attempted_qpc=None if visit is None else visit.attempted_at,
                        accepted_qpc=None if visit is None else visit.accepted_at,
                        entered_qpc=None if visit is None else visit.entered_at)

    def close(self):
        with self._lock:
            self._closed = True
            self._clear()
            self._operation = self._thread = None
