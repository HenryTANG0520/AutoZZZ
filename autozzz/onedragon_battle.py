"""上游战斗的输入范围与画面状态；不改动固定子模块。"""
from __future__ import annotations

from contextvars import ContextVar
from collections import deque
from dataclasses import dataclass
from functools import wraps
import json
import threading
from typing import Any

from .onedragon_control import ObservationExpired,ExecutionStopped

_battle_input: ContextVar[bool] = ContextVar("autozzz_battle_input",default=False)


@dataclass(frozen=True)
class _CancelledAtomic:
    evidence: dict


class _BattleInputExpired(ObservationExpired):
    def __init__(self,message,evidence):
        super().__init__(message)
        self.evidence=evidence


class BattleInputControl:
    def __init__(self,ctx:Any,control:Any):
        self.ctx,self.control = ctx,control
        self.allowed = False
        self.allowed_epoch = -1
        self.originals = []
        self.previous_validator=None;self.input_guard=None
        self.permit_reason='not_observed'
        self.cancellations=deque(maxlen=16)
        self.cancellation_count=0
        self._diagnostic_lock=threading.Lock()
        self._diagnostic_keys=deque(maxlen=16)
        self._diagnostic_emitted=0
        self._stateful_atomic_types=()
        self._atomic_wait_status=None

    def _reset_cancelled_atomic(self,operation):
        # These three fixed-source atoms keep a WAIT/RUNNING/STOP state. An
        # input exception skips their normal WAIT epilogue. Only an expected
        # cancellation may reset them, after execute has unwound.
        if isinstance(operation,self._stateful_atomic_types):
            operation._autozzz_cancelled_owner=self
            with operation._update_lock:operation._status=self._atomic_wait_status

    def _cancellation_evidence(self,stage,message=''):
        """Only classify known controller invalidation, never arbitrary errors."""
        control=self.control;epoch=control.epoch
        task_epoch=getattr(control,'task_epoch',epoch)
        reason=('stopped'if getattr(control,'stopped',False)else
                'manual_pause'if getattr(control,'manual_paused',False)else
                'focus_wait'if getattr(control,'focus_waiting',False)else
                'observation_epoch_changed'if task_epoch!=epoch else
                'battle_permit_epoch_changed'if self.allowed_epoch!=epoch else
                self.permit_reason if not self.allowed else
                'fresh_frame_required'if hasattr(control,'observed_epoch')and control.observed_epoch!=epoch else None)
        if reason is None:return None
        return dict(reason=reason,stage=stage,message=str(message)[:160],allowed=self.allowed,
                    allowed_epoch=self.allowed_epoch,current_epoch=epoch,task_epoch=task_epoch,
                    permit_reason=self.permit_reason)

    def _record_cancellation(self,evidence):
        if evidence is None:return
        from one_dragon.utils.log_utils import log
        key=tuple(evidence.get(name)for name in ('reason','stage','allowed_epoch','current_epoch'))
        with self._diagnostic_lock:
            self.cancellation_count+=1
            self.cancellations.append(dict(evidence))
            if key in self._diagnostic_keys or self._diagnostic_emitted>=16:return
            self._diagnostic_keys.append(key);self._diagnostic_emitted+=1
        # Bounded INFO diagnostics; no exception filtering outside this sequence.
        log.info('AutoZZZ battle sequence cancelled: %s',json.dumps(evidence,ensure_ascii=False))

    def _retire_cancelled(self,executor,evidence,*,record=True):
        """_op_lock arbitrates cancellation accounting with original stop()."""
        if record:self._record_cancellation(evidence)
        with executor._op_lock:
            if not executor.running:return False  # original stop owns the slot
            executor.running=False
            current=executor._current_op
            if current is not None:current.stop()
            for operation in executor._async_ops:
                if operation is not current:operation.stop()
            executor._current_op=None;executor._async_ops.clear()
            # True retires the worker slot in ConditionalOperator._on_task_done;
            # it does not publish battle completion or settle a resource claim.
            return True

    def _run_sequence(self,executor,source):
        """Fixed upstream loop with one local typed cancellation result.

        Keep the source thread pool, callbacks, stop lock and ordinary ERROR
        behavior. A cancelled atomic must not leak an exception to its generic
        callback or allow the remainder of the stale sequence to run.
        """
        from concurrent.futures import TimeoutError
        from one_dragon.utils import thread_utils
        from one_dragon.utils.log_utils import log
        owner=self
        def atomic(operation):
            token=_battle_input.set(True)
            try:
                checkpoint=getattr(owner.control,'checkpoint',None)
                if checkpoint is not None:checkpoint(require_frame=True)
                owner.check_input()
                if isinstance(operation,owner._stateful_atomic_types):operation._autozzz_cancelled_owner=None
                return operation.execute()
            except (ObservationExpired,ExecutionStopped)as exc:
                evidence=(exc.evidence if isinstance(exc,_BattleInputExpired)
                          else owner._cancellation_evidence('atomic',exc))
                if evidence is None:raise  # a genuine/unclassified fault
                owner._reset_cancelled_atomic(operation)
                owner._record_cancellation(evidence)
                return _CancelledAtomic(evidence)
            finally:_battle_input.reset(token)
        for index,operation in enumerate(executor.op_list):
            with executor._op_lock:
                if not executor.running:return False
                executor._current_op=operation
                if operation.async_op:executor._async_ops.append(operation)
                future=source._od_op_task_executor.submit(atomic,operation)
                future.add_done_callback(thread_utils.handle_future_result)
            result=None
            while True:
                if not executor.running:return False
                try:
                    result=future.result(timeout=.05)
                    break
                except TimeoutError:continue
                except Exception:
                    log.error('指令执行出错',exc_info=True)
                    break
            if isinstance(result,_CancelledAtomic):return self._retire_cancelled(executor,result.evidence,record=False)
            with executor._op_lock:
                if not executor.running:return False
                executor._current_op=None
                if index==len(executor.op_list)-1:
                    executor.running=False
                    return True
        return False

    def check_input(self) -> None:
        if _battle_input.get() and (not self.allowed or self.allowed_epoch!=self.control.epoch):
            message="当前画面不允许战斗输入。"
            # Capture at rejection: a newer HUD frame may restore permission
            # before the atomic Future handles this cancellation.
            raise _BattleInputExpired(message,self._cancellation_evidence('atomic',message))

    def _patch(self,cls,name,method):
        self.originals.append((cls,name,getattr(cls,name)))
        setattr(cls,name,method)

    def install(self) -> None:
        from one_dragon.base.conditional_operation.operation_executor import OperationExecutor
        from one_dragon.base.conditional_operation import operation_executor as executor_source
        from one_dragon.base.screen import screen_utils
        from one_dragon.base.screen.screen_utils import FindAreaResultEnum
        from zzz_od.auto_battle.auto_battle_operator import AutoBattleOperator
        from zzz_od.auto_battle.auto_battle_context import AutoBattleContext
        from zzz_od.auto_battle.atomic_op.btn_common import AtomicBtnCommon,BtnRunStatus
        from zzz_od.auto_battle.atomic_op.btn_switch_agent import AtomicBtnSwitchAgent
        from zzz_od.auto_battle.atomic_op.btn_quick_assist import AtomicBtnQuickAssist
        execute = OperationExecutor._run
        periodic = AutoBattleOperator.operate_periodically
        check = AutoBattleContext.check_battle_state
        assign = AutoBattleContext.__setattr__
        owner = self
        self._stateful_atomic_types=(AtomicBtnCommon,AtomicBtnSwitchAgent,AtomicBtnQuickAssist)
        self._atomic_wait_status=BtnRunStatus.WAIT
        # stop() may race after the cancelled execute() unwound. Restore WAIT
        # after its release/STOP semantics too, without touching normal atoms.
        for atomic_type in self._stateful_atomic_types:
            original_stop=atomic_type.stop
            @wraps(original_stop)
            def guarded_stop(operation,_original=original_stop):
                try:return _original(operation)
                finally:
                    if getattr(operation,'_autozzz_cancelled_owner',None)is owner:
                        owner._reset_cancelled_atomic(operation)
            self._patch(atomic_type,'stop',guarded_stop)

        @wraps(execute)
        def guarded_execute(executor):
            if not owner.allowed or owner.allowed_epoch!=owner.control.epoch:
                return owner._retire_cancelled(executor,owner._cancellation_evidence('entry'))
            token = _battle_input.set(True)
            try:return owner._run_sequence(executor,executor_source)
            finally:_battle_input.reset(token)

        @wraps(periodic)
        def guarded_periodic(operator,*args,**kwargs):
            token = _battle_input.set(True)
            try:return periodic(operator,*args,**kwargs)
            finally:_battle_input.reset(token)

        def guarded_assign(battle,name,value):
            if name in ("last_check_end_result","last_check_in_battle"):
                with owner.control.lock:
                    if owner.control.task_epoch != owner.control.epoch:return
                    return assign(battle,name,value)
            return assign(battle,name,value)

        @wraps(check)
        def guarded_check(battle,screen,screenshot_time,check_battle_end_normal_result=False,
                          check_battle_end_hollow_result=False,check_battle_end_defense_result=False,
                          check_distance=False,sync=False):
            owner.control.checkpoint(require_frame=True)
            in_battle = battle.is_normal_attack_btn_available(screen)
            if not in_battle:
                # 结束结果同步确认，避免异步检查窗口中仍然发普攻。
                battle._check_battle_end(screen,screenshot_time,check_battle_end_normal_result,
                                        check_battle_end_hollow_result,check_battle_end_defense_result)
            ended = battle.last_check_end_result is not None
            chain = False
            if not in_battle and not ended:
                chain = any(screen_utils.find_area(owner.ctx,screen,"战斗画面",area)==FindAreaResultEnum.TRUE
                            for area in ("连携技-1","连携技-2"))
            owner.control.checkpoint(require_frame=True)
            with owner.control.lock:
                owner.allowed = (not ended and (in_battle or chain) and not owner.control.manual_paused
                                 and not owner.control.focus_waiting and owner.control.task_epoch==owner.control.epoch)
                owner.allowed_epoch=owner.control.task_epoch
                owner.permit_reason=('battle_end_confirmed'if ended else
                                     'battle_visible'if in_battle else
                                     'chain_visible'if chain else'battle_not_verified')
            if not owner.allowed:
                from .onedragon_gamepad import release_controller_inputs
                release_controller_inputs(owner.ctx.controller)
            if ended:
                if battle.auto_op is not None:battle.auto_op.stop_running()
                return False
            # 结束结果已经同步检查，其余角色、闪光、连携等仍走真实上游识别。
            return check(battle,screen,screenshot_time,False,False,False,check_distance,sync)

        self._patch(OperationExecutor,"_run",guarded_execute)
        self._patch(AutoBattleOperator,"operate_periodically",guarded_periodic)
        self._patch(AutoBattleContext,"check_battle_state",guarded_check)
        self._patch(AutoBattleContext,"__setattr__",guarded_assign)
        self.previous_validator=self.control.input_validator
        def validate():
            if self.previous_validator is not None:self.previous_validator()
            self.check_input()
        self.input_guard=validate;self.control.input_validator=validate

    def close(self) -> None:
        self.allowed = False
        if self.control.input_validator is self.input_guard:self.control.input_validator=self.previous_validator
        for cls,name,original in reversed(self.originals):setattr(cls,name,original)
        self.originals.clear()
