"""Opt-in timing for the existing native battle round and its triggered executor."""
from __future__ import annotations

from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from threading import get_ident
from time import perf_counter
from weakref import WeakKeyDictionary, ref


_BANS = {'按键禁用-普通攻击', '按键禁用-终结技'}
_LEAVES = {'薇斯珀-首领交互-切人-第一套', '薇斯珀-首领交互-切人-第二套', '薇斯珀-首领交互-闪避',
           '基塔布鲁-首领交互-切人', '基塔布鲁-首领交互-闪避'}
_BUTTONS = {'闪避': 'dodge', '切换角色-下一个': 'switch_next', '连携技-左': 'chain_left',
            '连携技-右': 'chain_right', '连携技-取消': 'chain_cancel'}


@dataclass
class _Round:
    operation: ref
    thread: int
    visit: int
    origin: tuple | None = None


@dataclass(frozen=True)
class _Task:
    executor: object
    number: int
    origin: tuple


class BossTimingTrace:
    def __init__(self, runtime, guard):
        from one_dragon.base.conditional_operation.operation_executor import OperationExecutor
        from one_dragon.base.debug.debug_trace_bus import TimelineTraceItem
        from zzz_od.auto_battle.atomic_op.btn_common import AtomicBtnCommon
        from zzz_od.auto_battle.atomic_op.state_set import AtomicSetState
        from zzz_od.auto_battle.atomic_op.wait import AtomicWait
        from zzz_od.operation.compendium.area_patrol import AreaPatrol
        from zzz_od.operation.compendium.notorious_hunt import NotoriousHunt

        self.runtime, self.guard, self.control = runtime, guard, guard.control
        self.item = TimelineTraceItem
        self.executor = OperationExecutor
        self.routes = {route: route.auto_battle for route in (AreaPatrol, NotoriousHunt)}
        self.atom_kinds = {AtomicWait: 'wait', AtomicBtnCommon: 'button', AtomicSetState: 'set_state'}
        self.round = ContextVar('autozzz_boss_timing_round', default=None)
        self.task = ContextVar('autozzz_boss_timing_task', default=None)
        self.atom = ContextVar('autozzz_boss_timing_atom', default=None)
        self.visits = WeakKeyDictionary()
        self.visit_count = self.task_count = 0
        self.hooks = []

    def _latest(self):
        receipt = self.control.capture_receipt
        if receipt is None or receipt.epoch != self.control.epoch or receipt.epoch != self.control.task_epoch:
            return None
        return dict(sequence=receipt.sequence, epoch=receipt.epoch,
                    started_qpc=receipt.started, finished_qpc=receipt.finished)

    def _record(self, event, origin=None, *, frame=None, task=None, **fields):
        # Native submission may already hold the operator lock. A diagnostic must
        # never wait for the control lock in the opposite order.
        try:
            qpc = perf_counter()
            lock = self.control.lock
            if not lock.acquire(blocking=False):
                return None
            try:
                runtime = self.runtime
                with runtime.lock:
                    if runtime.boss_timing is not self or not runtime.enabled or runtime.closed:
                        return None
                    runtime._sync(self.control)
                    latest = self._latest()
                    if frame is not None:
                        receipt = self.control.capture_receipt
                        screen, stamp, scope = frame
                        if latest is None or receipt.screen is not screen or receipt.wall_time != stamp:
                            return None
                        origin = (runtime.generation, scope.visit, latest)
                    generation, visit, receipt = origin
                    if generation != runtime.generation or receipt['epoch'] != self.control.task_epoch:
                        return None
                    if event == 'executor_queued':
                        if latest != receipt:
                            return None
                        self.task_count += 1
                        task = self.task_count
                    meta = dict(qpc=qpc, visit=visit, origin=receipt, latest=latest, **fields)
                    if task is not None:
                        meta['task'] = task
                    runtime.bus.add_timeline(self.item('boss_timing', event, meta=meta))
                    return task if event == 'executor_queued' else origin
            finally:
                lock.release()
        except Exception:
            # Diagnostics cannot change a round, an atomic result or input.
            return None

    def _round(self, native, operation):
        scope = None
        with suppress(Exception):
            route = type(operation)
            node = operation._current_node
            if (operation.ctx is self.runtime.ctx and route in self.routes and node is not None
                    and node.op_method is self.routes[route]):
                with self.runtime.lock:
                    start = operation._current_node_start_time
                    visit = self.visits.get(operation)
                    if visit is None or visit[:2] != (node, start):
                        self.visit_count += 1
                        visit = self.visits[operation] = (node, start, self.visit_count)
                scope = _Round(ref(operation), get_ident(), visit[2])
        token = self.round.set(scope)
        try:
            return native(operation)
        finally:
            self.round.reset(token)

    def _hud(self, native, battle, screen, stamp):
        hud = native(battle, screen, stamp)
        with suppress(Exception):
            scope = self.round.get()
            if scope is not None and scope.thread == get_ident() and scope.origin is None:
                operation = scope.operation()
                if operation is not None and operation.last_screenshot is screen and operation.last_screenshot_time == stamp:
                    scope.origin = self._record('frame', frame=(screen, stamp, scope), route=type(operation).__name__,
                                                normal=hud.normal_available, normal_x=hud.normal_disabled,
                                                ultimate_x=hud.ultimate_disabled, blocks_ordinary=hud.blocks_ordinary)
        return hud

    def _queued_task(self, executor):
        scope = self.round.get()
        if scope is None or scope.origin is None:
            return None
        operator = self.runtime.ctx.auto_battle_context.auto_op
        info = getattr(operator, 'current_execution_info', None)
        if getattr(operator, 'running_executor', None) is not executor or info is None:
            return None
        operations, trigger = executor.op_list, info.trigger
        count = len(operations)
        if info.op_list is not operations or not trigger or not 0 < count <= 128:
            return None
        leaf = 'other_trigger'
        if trigger in _BANS:
            states = info.state_list
            leaf = states[0] if states else None
            if leaf not in _LEAVES:
                return None
        number = self._record('executor_queued', scope.origin, leaf=leaf, atom_count=count)
        return _Task(executor, number, scope.origin) if number is not None else None

    def _submit(self, native, executor):
        task = None
        with suppress(Exception):
            task = self._queued_task(executor)
        token = self.task.set(task)
        try:
            return native(executor)
        finally:
            self.task.reset(token)

    def _execute(self, native, executor):
        task = self.task.get()
        if task is None or task.executor is not executor:
            return native(executor)
        self._record('executor_enter', task.origin, task=task.number)
        try:
            return native(executor)
        finally:
            self._record('executor_leave', task.origin, task=task.number)

    def _atomic_details(self, operation):
        task = self.task.get()
        kind = self.atom_kinds.get(type(operation))
        if task is None or kind is None or operation is not task.executor._current_op:
            return None
        indexes = [index for index, atom in enumerate(task.executor.op_list) if atom is operation]
        if len(indexes) != 1:
            return None
        index = indexes[0]
        fields = dict(index=index, atom=kind)
        if kind == 'wait':
            fields['wait_seconds'] = operation.wait_seconds
        elif kind == 'button':
            fields['button'] = _BUTTONS.get(operation.btn_name, 'other')
        return task, index, fields

    def _atomic(self, native, operation):
        details = None
        with suppress(Exception):
            details = self._atomic_details(operation)
        token = self.atom.set(None if details is None else details[:2])
        try:
            if details is not None:
                task, index, fields = details
                self._record('atom_enter', task.origin, task=task.number, **fields)
            return native(operation)
        finally:
            if details is not None:
                self._record('atom_leave', task.origin, task=task.number, index=index)
            self.atom.reset(token)

    def _input(self, native, action):
        atomic = self.atom.get()
        if atomic is None:
            return native(action)
        task, index = atomic

        def observed():
            self._record('input_enter', task.origin, task=task.number, index=index)
            result = action()
            self._record('input_return', task.origin, task=task.number, index=index)
            return result

        return native(observed)

    def _hook(self, owner, name, observe):
        native = getattr(owner, name)

        @wraps(native)
        def wrapper(*args, **kwargs):
            return observe(native, *args, **kwargs)

        self.hooks.append((owner, name, name in vars(owner), native, wrapper))
        setattr(owner, name, wrapper)

    def install(self):
        try:
            for route in self.routes:
                self._hook(route, '_execute_one_round', self._round)
            self._hook(self.guard, '_observe_hud', self._hud)
            self._hook(self.executor, 'run_async', self._submit)
            self._hook(self.executor, '_run', self._execute)
            for atom in self.atom_kinds:
                self._hook(atom, 'execute', self._atomic)
            self._hook(self.control, 'input', self._input)
        except Exception:
            self.close()
            raise

    def close(self):
        for owner, name, owned, native, wrapper in reversed(self.hooks):
            if getattr(owner, name) is wrapper:
                if owned:
                    setattr(owner, name, native)
                else:
                    delattr(owner, name)
        self.hooks.clear()
