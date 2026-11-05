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
_requires_battle_hud: ContextVar[bool] = ContextVar('autozzz_requires_battle_hud', default=False)
_permit_scope: ContextVar[int | str | None] = ContextVar('autozzz_battle_permit', default=None)
_chain_frame: ContextVar[tuple | None] = ContextVar('autozzz_chain_frame', default=None)
_UNOBSERVED = object()


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
        self.permit_version = 0
        self._permit_key = None
        self.cancellations=deque(maxlen=16)
        self.cancellation_count=0
        self._diagnostic_lock=threading.Lock()
        self._diagnostic_keys=deque(maxlen=16)
        self._diagnostic_emitted=0
        self._stateful_atomic_types=()
        self._atomic_wait_status=None

    def _current_permit(self):
        scope = _permit_scope.get()
        return type(scope) is not int or scope == self.permit_version

    def _frame_permit(self, reason, *, chain_key=None, release=True):
        control = self.control
        with control.lock:
            epoch = control.task_epoch
            previous = self._permit_key
            key = ((epoch, reason, chain_key) if reason in ('battle_visible', 'chain_visible') else None)
            if control.stopped or control.manual_paused or control.focus_waiting or epoch != control.epoch:
                key = None
            changed = key != previous or (key is None and self.allowed)
            release_old = changed and self.allowed and release
            if changed:
                self.permit_version += 1
                self._permit_key = key
                self.allowed = False
            self.allowed_epoch = epoch
            self.permit_reason = reason
            version = self.permit_version
        if release_old:
            from .onedragon_gamepad import release_controller_inputs
            release_controller_inputs(self.ctx.controller)
        if changed and previous is not None and previous[1] == 'chain_visible':
            battle = getattr(self.ctx, 'auto_battle_context', None)
            if battle is not None:
                from one_dragon.base.conditional_operation.state_recorder import StateRecord

                token = _permit_scope.set(version)
                try:
                    battle.state_record_service.batch_update_states([
                        StateRecord('连携技-准备', is_clear=True),
                        StateRecord('按键可用-连携技', is_clear=True),
                    ])
                finally:_permit_scope.reset(token)
        if key is not None and reason == 'battle_visible':
            with control.lock:
                if version == self.permit_version and epoch == control.epoch and not control.stopped:
                    self.allowed = True
        return version

    def revoke_frame(self, reason='battle_not_verified'):
        self._frame_permit(reason, release=False)

    def observe_chain_frame(self, battle, screen, screenshot_time):
        from .onedragon_chain import observe_chain_prompt

        # An ordinary action must not continue while looking for a chain or result.
        if self.permit_reason != 'chain_visible':
            self._frame_permit('battle_not_verified')
        evidence = observe_chain_prompt(battle, screen, screenshot_time)
        if evidence is None:
            self._frame_permit('battle_not_verified')
        return evidence

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
                'battle_permit_changed'if not self._current_permit() else
                'battle_permit_epoch_changed'if self.allowed_epoch!=epoch else
                self.permit_reason if not self.allowed else
                'periodic_action_outside_normal_battle'if _permit_scope.get()=='periodic'and self.permit_reason!='battle_visible'else
                'action_requires_battle_hud' if _requires_battle_hud.get() and self.permit_reason == 'chain_visible' else
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
            # The source main loop can select a chain too. Restrict buttons,
            # while preserving chain choices, releases, waits and custom states.
            name = getattr(operation, 'op_name', '')
            requires_hud = name.startswith('按键-') and not (
                name.startswith('按键-连携技-') or name.endswith('松开') or getattr(operation, 'is_release', False))
            hud_scope = _requires_battle_hud.set(requires_hud)
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
            finally:
                _battle_input.reset(token)
                _requires_battle_hud.reset(hud_scope)
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
        if _battle_input.get() and (not self.allowed or self.allowed_epoch!=self.control.epoch or not self._current_permit()
                or (_requires_battle_hud.get() and self.permit_reason == 'chain_visible')
                or (_permit_scope.get()=='periodic' and self.permit_reason!='battle_visible')):
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
        from one_dragon.base.conditional_operation.operator import ConditionalOperator
        from one_dragon.base.conditional_operation.state_record_service import StateRecordService
        from one_dragon.utils import cv2_utils
        from zzz_od.auto_battle.auto_battle_operator import AutoBattleOperator
        from zzz_od.auto_battle.auto_battle_context import AutoBattleContext
        from zzz_od.auto_battle.atomic_op.btn_common import AtomicBtnCommon,BtnRunStatus
        from zzz_od.auto_battle.atomic_op.btn_switch_agent import AtomicBtnSwitchAgent
        from zzz_od.auto_battle.atomic_op.btn_quick_assist import AtomicBtnQuickAssist
        execute = OperationExecutor._run
        submit = OperationExecutor.run_async
        init_operator = ConditionalOperator.__init__
        trigger = ConditionalOperator._trigger_scene
        periodic = AutoBattleOperator.operate_periodically
        check = AutoBattleContext.check_battle_state
        chain_check = AutoBattleContext.check_chain_attack
        chain_recognition = AutoBattleContext._check_chain_attack_in_parallel
        chain_match = AutoBattleContext._match_chain_agent_in
        switch = AutoBattleContext.switch_by_name
        publish = StateRecordService.batch_update_states
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

        @wraps(init_operator)
        def guarded_operator_init(operator, *args, **kwargs):
            init_operator(operator, *args, **kwargs)
            # The source registers its completion callback under this lock.
            # An already-completed future invokes that callback inline.
            operator._task_lock = threading.RLock()

        @wraps(execute)
        def guarded_execute(executor):
            if not owner.allowed or owner.allowed_epoch!=owner.control.epoch or not owner._current_permit():
                return owner._retire_cancelled(executor,owner._cancellation_evidence('entry'))
            token = _battle_input.set(True)
            try:return owner._run_sequence(executor,executor_source)
            finally:_battle_input.reset(token)

        @wraps(submit)
        def guarded_submit(executor):
            scope = _permit_scope.get()
            token = _permit_scope.set(owner.permit_version if type(scope) is not int else scope)
            try:return submit(executor)
            finally:_permit_scope.reset(token)

        @wraps(trigger)
        def guarded_trigger(operator, state_name):
            if not owner._current_permit():
                return None
            if _chain_frame.get() is not None:
                # A trigger-only source policy may retain its finished executor.
                # It no longer owns priority over the next chain choice.
                with operator._task_lock:
                    previous = operator.running_executor
                    if previous is not None and not previous.running:
                        operator.running_executor = None
            return trigger(operator, state_name) if owner._current_permit() else None

        @wraps(periodic)
        def guarded_periodic(operator, generation):
            token = _battle_input.set(True)
            scope = _permit_scope.set('periodic')
            try:
                while operator.is_running and operator._periodic_generation == generation:
                    try:return periodic(operator, generation)
                    except (ObservationExpired, ExecutionStopped) as exc:
                        evidence = owner._cancellation_evidence('periodic', exc)
                        if evidence is None:raise
                        if owner.control.task_epoch != owner.control.epoch or operator._stop_event.wait(.05):
                            return None
            finally:
                _permit_scope.reset(scope)
                _battle_input.reset(token)

        def guarded_assign(battle,name,value):
            if name in ("last_check_end_result","last_check_in_battle"):
                with owner.control.lock:
                    if owner.control.task_epoch != owner.control.epoch or not owner._current_permit():return
                    return assign(battle,name,value)
            return assign(battle,name,value)

        @wraps(publish)
        def guarded_publish(service, records):
            if type(_permit_scope.get()) is not int:
                return publish(service, records)
            with owner.control.lock:
                if owner._current_permit():
                    return publish(service, records)
            return None

        @wraps(chain_check)
        def guarded_chain_check(battle, screen, screenshot_time):
            frame = _chain_frame.get()
            if frame is None or frame[0] is not battle or frame[1] is not screen or frame[2] != screenshot_time:
                return
            control = owner.control
            with control.lock:
                if (not owner._current_permit() or owner.permit_reason != 'chain_visible'
                        or control.task_epoch != control.epoch or control.observed_epoch != control.epoch
                        or control.stopped or control.manual_paused or control.focus_waiting):
                    return
                owner.allowed = True
            token = _battle_input.set(True)
            try:return chain_check(battle, screen, screenshot_time)
            finally:_battle_input.reset(token)

        @wraps(chain_recognition)
        def guarded_chain_recognition(battle, screen, screenshot_time):
            try:
                owner.control.checkpoint(require_frame=True)
                owner.check_input()
                return chain_recognition(battle, screen, screenshot_time)
            except (ObservationExpired, ExecutionStopped) as exc:
                evidence = owner._cancellation_evidence('chain', exc)
                if evidence is None:raise
                owner._record_cancellation(evidence)

        @wraps(chain_match)
        def guarded_chain_match(battle, image, possible_agents):
            frame = _chain_frame.get()
            if frame is None or frame[0] is not battle:
                return chain_match(battle, image, possible_agents)

            # The fixed recognizer passes views of the two portrait regions.
            # Supply the verified agent for that view so a stale costume hint
            # cannot change the portrait before the source publishes its states.
            areas = (battle.area_chain_1, battle.area_chain_2)
            for portrait in frame[3].portraits:
                verified = cv2_utils.crop_image_only(frame[1], areas[portrait.side - 1].rect)
                if image.shape == verified.shape and image.ctypes.data == verified.ctypes.data:
                    return portrait.agent
            return None

        @wraps(switch)
        def guarded_switch(battle, agent_name):
            if _chain_frame.get() is None:
                return switch(battle, agent_name)
            owner.control.checkpoint(require_frame=True)
            owner.check_input()
            try:
                return switch(battle, agent_name)
            except (ObservationExpired, ExecutionStopped):
                # The source predicts the new team order before sending the key.
                # If a transition wins that race, refresh the prediction from HUD.
                battle.agent_context.team_info.request_check_all_agents()
                battle.agent_context._last_check_agent_time = 0
                raise

        @wraps(check)
        def guarded_check(battle,screen,screenshot_time,check_battle_end_normal_result=False,
                          check_battle_end_hollow_result=False,check_battle_end_defense_result=False,
                          check_distance=False,sync=False,*,chain_prompt=_UNOBSERVED):
            owner.control.checkpoint(require_frame=True)
            in_battle = battle.is_normal_attack_btn_available(screen)
            chain = None
            if not in_battle:
                chain = (owner.observe_chain_frame(battle, screen, screenshot_time)
                         if chain_prompt is _UNOBSERVED else chain_prompt)
                # 结束结果同步确认，避免异步检查窗口中仍然发普攻。
                battle._check_battle_end(screen,screenshot_time,check_battle_end_normal_result,
                                        check_battle_end_hollow_result,check_battle_end_defense_result)
            ended = battle.last_check_end_result is not None
            owner.control.checkpoint(require_frame=True)
            if ended:
                owner._frame_permit('battle_end_confirmed')
                if battle.auto_op is not None:battle.auto_op.stop_running()
                return False
            if in_battle:
                version = owner._frame_permit('battle_visible')
            elif chain is not None:
                chain_key = tuple((portrait.side, portrait.agent.agent_name) for portrait in chain.portraits)
                version = owner._frame_permit('chain_visible', chain_key=chain_key)
            else:
                version = owner._frame_permit('battle_not_verified')
            scope = _permit_scope.set(version)
            frame = _chain_frame.set((battle, screen, screenshot_time, chain) if chain is not None else None)
            try:
                return check(battle,screen,screenshot_time,False,False,False,check_distance,sync)
            finally:
                _chain_frame.reset(frame)
                _permit_scope.reset(scope)

        self._patch(OperationExecutor,"_run",guarded_execute)
        self._patch(OperationExecutor,'run_async',guarded_submit)
        self._patch(ConditionalOperator,'__init__',guarded_operator_init)
        self._patch(ConditionalOperator,'_trigger_scene',guarded_trigger)
        self._patch(AutoBattleOperator,"operate_periodically",guarded_periodic)
        self._patch(AutoBattleContext,"check_battle_state",guarded_check)
        self._patch(AutoBattleContext,'check_chain_attack',guarded_chain_check)
        self._patch(AutoBattleContext,'_check_chain_attack_in_parallel',guarded_chain_recognition)
        self._patch(AutoBattleContext,'_match_chain_agent_in',guarded_chain_match)
        self._patch(AutoBattleContext,'switch_by_name',guarded_switch)
        self._patch(AutoBattleContext,"__setattr__",guarded_assign)
        self._patch(StateRecordService,'batch_update_states',guarded_publish)
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
