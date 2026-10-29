"""上游战斗的输入范围与画面状态；不改动固定子模块。"""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
from typing import Any

from .onedragon_control import ObservationExpired

_battle_input: ContextVar[bool] = ContextVar("autozzz_battle_input",default=False)


class BattleInputControl:
    def __init__(self,ctx:Any,control:Any):
        self.ctx,self.control = ctx,control
        self.allowed = False
        self.allowed_epoch = -1
        self.originals = []
        self.previous_validator=None;self.input_guard=None

    def check_input(self) -> None:
        if _battle_input.get() and (not self.allowed or self.allowed_epoch!=self.control.epoch):
            raise ObservationExpired("当前画面不允许战斗输入。")

    def _patch(self,cls,name,method):
        self.originals.append((cls,name,getattr(cls,name)))
        setattr(cls,name,method)

    def install(self) -> None:
        from one_dragon.base.conditional_operation.operation_executor import OperationExecutor
        from one_dragon.base.screen import screen_utils
        from one_dragon.base.screen.screen_utils import FindAreaResultEnum
        from zzz_od.auto_battle.auto_battle_operator import AutoBattleOperator
        from zzz_od.auto_battle.auto_battle_context import AutoBattleContext
        execute = OperationExecutor._run
        periodic = AutoBattleOperator.operate_periodically
        check = AutoBattleContext.check_battle_state
        assign = AutoBattleContext.__setattr__
        owner = self

        @wraps(execute)
        def guarded_execute(executor):
            if not owner.allowed or owner.allowed_epoch!=owner.control.epoch:
                executor.running=False
                return False
            token = _battle_input.set(True)
            try:return execute(executor)
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
