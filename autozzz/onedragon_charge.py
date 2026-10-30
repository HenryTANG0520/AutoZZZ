"""为上游体力计划连接 AutoZZZ 持久预算，覆盖首次出战与再来一次。"""
from __future__ import annotations

import copy
from functools import wraps
import re
from typing import Any

from .battle_budget import BattleBudget
from .onedragon_control import ExecutionStopped


class ChargeBudgetControl:
    CATEGORIES = {"实战模拟室":20,"区域巡防":60,"专业挑战室":40,"恶名狩猎":60,"合成电池":60}

    def __init__(self,ctx:Any,control:Any,budget:BattleBudget,max_per_battle:int,restore_mode='不使用',restore_authorized=False,synthesis_authorized=False):
        if budget.pending:raise ValueError("该预算有尚未核对的出战，不能重新开始。")
        if type(max_per_battle)is not int or not 20<=max_per_battle<=100:raise ValueError("单次电量上限须为 20—100。")
        self.ctx,self.control,self.budget,self.maximum = ctx,control,budget,max_per_battle
        self.owner,self.cost,self.reservation = None,0,None
        self.originals = []
        self.limited = False
        self.restore_mode,self.restore_authorized=restore_mode,restore_authorized
        self.restore_control=None;self.restore_cost=0
        self.synthesis_authorized=synthesis_authorized;self.synthesis_control=None

    def check_restore_budget(self):
        if self.restore_mode=='不使用' or not self.restore_authorized:self._block('本轮未确认使用储蓄电量或以太电池。')
        if self.reservation is not None:return
        cost=self.restore_cost
        if not cost or cost>self.maximum:self._block('尚未核实可执行的副本消耗，未恢复电量。')
        if self.budget.remaining<cost or self.budget.rounds_left<=0:
            self.limited=True;self.control.stop();raise ExecutionStopped('达到本轮电量或场次预算，未恢复电量。')

    def _block(self,message:str):
        self.control.stop(message)
        raise ExecutionStopped(message)

    def _patch_node(self,cls,name,handler):
        original = getattr(cls,name)
        @wraps(original)
        def guarded(operation,*args,**kwargs):return handler(operation,original,*args,**kwargs)
        node = copy.copy(original.operation_node_annotation)
        node.op_method = guarded  # 注解引用原函数；仅替换类属性不会改变实际节点执行。
        guarded.operation_node_annotation = node
        self.originals.append((cls,name,original))
        setattr(cls,name,guarded)

    def _read_cost(self,operation) -> tuple[int,int]:
        from one_dragon.utils import cv2_utils
        from one_dragon.base.geometry.rectangle import Rect
        button=self.ctx.screen_loader.get_area("实战模拟室","下一步").rect
        part=cv2_utils.crop_image_only(operation.last_screenshot,Rect(max(0,button.x1-210),button.y1,button.x1,button.y2))
        results=[]
        for text,matches in self.ctx.ocr.run_ocr(part).items():
            parsed=re.fullmatch(r"\s*(\d+)\s*/\s*(\d{1,3})\s*",text)
            if parsed and matches.max is not None and matches.max.confidence>=.7:
                results.append(tuple(map(int,parsed.groups())))
        if len(results)!=1:raise ValueError("未能唯一核实画面中的“剩余电量 / 需要电量”。")
        available,cost=results[0]
        category = {"CombatSimulation":"实战模拟室", "AreaPatrol":"区域巡防", "ExpertChallenge":"专业挑战室",
                    "NotoriousHunt":"恶名狩猎"}.get(type(operation).__name__, operation.plan.category_name)
        if category not in self.CATEGORIES:raise ValueError("该副本的预算适配尚未完成。")
        if category=="实战模拟室":
            allowed = {20,40,60,80,100}
            if operation.plan.card_num!="默认数量":allowed={20*int(operation.plan.card_num)}
        else:allowed={self.CATEGORIES[category]}
        if cost not in allowed:raise ValueError("当前副本消耗与所选计划不一致。")
        return available,cost

    def _reserve(self,owner,cost:int):
        self.control.checkpoint(require_frame=True)
        if self.reservation is not None:
            if self.owner is not owner or self.cost!=cost:self._block("未结算的出战与当前副本不一致。")
            return
        if cost>self.maximum:self._block("实际单次消耗超过电量上限。")
        if self.budget.remaining<cost or self.budget.rounds_left<=0:
            self.limited=True
            self.control.stop()
            raise ExecutionStopped("达到本轮电量或场次预算。")
        self.owner,self.cost = owner,cost
        try:self.reservation = self.budget.reserve(cost,owner.plan.uid)
        except Exception as exc:self._block("无法登记出战预算："+str(exc))
        self.control.checkpoint(require_frame=True)

    def install(self):
        from zzz_od.operation.compendium.combat_simulation import CombatSimulation
        from zzz_od.operation.compendium.area_patrol import AreaPatrol
        from zzz_od.operation.compendium.expert_challenge import ExpertChallenge
        from zzz_od.operation.compendium.notorious_hunt import NotoriousHunt
        from zzz_od.operation.challenge_mission.check_next_after_battle import ChooseNextOrFinishAfterBattle
        from zzz_od.application.charge_plan.charge_plan_app import ChargePlanApp
        from zzz_od.operation.restore_charge import RestoreCharge
        from zzz_od.operation.exchange_ether_battery import ExchangeEtherBattery

        def before_next(operation,original):
            if isinstance(operation, NotoriousHunt) and not operation.use_charge_power:
                return original(operation)
            screen=operation.last_screenshot
            if operation.round_by_find_area(screen,'恢复电量','标题-恢复电量').is_success:return original(operation)
            deploying=operation.round_by_find_area(screen,"实战模拟室","出战").is_success
            next_button=operation.round_by_find_area(screen,"实战模拟室","下一步").is_success
            if deploying:
                if self.owner is not operation or self.reservation is None:
                    self._block("缺少本场出战预算，请从副本选择画面重新开始。")
            elif not next_button:
                return operation.round_retry("等待可核实消耗的副本选择画面",wait=.5)
            if next_button and not deploying:
                try:available,cost=self._read_cost(operation)
                except ValueError as exc:return operation.round_retry(str(exc),wait=.5)
                self.restore_cost=cost
                if available<cost:return original(operation)
                self._reserve(operation,cost)
            return original(operation)

        def after_battle(operation,original):
            if isinstance(operation, NotoriousHunt) and not operation.use_charge_power:
                return original(operation)
            self.control.checkpoint(require_frame=True)
            if self.ctx.auto_battle_context.last_check_end_result!="普通战斗-完成":
                self._block("未核实胜利结算，预算保持待核对。")
            if self.owner is not operation or self.reservation is None:self._block("缺少本场出战的预算记录。")
            result=original(operation)
            if result.is_success:
                self.budget.settle(self.reservation,None)
                self.reservation=None
            return result

        def repeat(operation,original):
            from .onedragon_hunt import current_hunt
            hunt = current_hunt()
            if hunt is not None and not hunt.use_charge_power:
                return original(operation)
            if operation.try_next:
                if operation.is_agent_plan and operation.round_by_find_area(operation.last_screenshot,"战斗画面","战斗结果-已达成").is_success:
                    return original(operation)
                if self.owner is None:self._block("未找到上一场副本，不能重复出战。")
                if self.budget.remaining<self.cost or self.budget.rounds_left<=0:
                    self.limited=True
                    operation.try_next=False
                elif operation.round_by_find_area(operation.last_screenshot,"战斗画面","战斗结果-再来一次").is_success:
                    self._reserve(self.owner,self.cost)
            return original(operation)

        def next_plan(operation,original):
            if self.budget.remaining<20 or self.budget.rounds_left<=0:
                self.limited=True
                return operation.round_success(ChargePlanApp.STATUS_ROUND_FINISHED)
            return original(operation)

        for cls in (CombatSimulation,AreaPatrol,ExpertChallenge,NotoriousHunt):
            self._patch_node(cls,"click_next",before_next)
            self._patch_node(cls,"after_battle",after_battle)
        self._patch_node(ChooseNextOrFinishAfterBattle,"check_next",repeat)
        self._patch_node(ChargePlanApp,"find_next_plan",next_plan)
        if self.restore_mode!='不使用':
            from .onedragon_restore import RestoreChargeControl
            self.restore_control=RestoreChargeControl(self.ctx,self.control,self.restore_mode,self);self.restore_control.install()
        if self.synthesis_authorized:
            from .onedragon_synthesis import SynthesisControl
            self.synthesis_control=SynthesisControl(self.ctx,self.control,self);self.synthesis_control.install()
        for cls in tuple(cls for cls,enabled in ((RestoreCharge,self.restore_control),(ExchangeEtherBattery,self.synthesis_control)) if enabled is None):
            original=cls.execute
            self.originals.append((cls,"execute",original))
            def forbidden(_operation):self._block("本轮未确认此项补电或合成操作。")
            cls.execute=forbidden

    def status(self) -> dict:
        return dict(id=self.budget.data["id"],reserved=self.budget.reserved,remaining=self.budget.remaining,
                    rounds_left=self.budget.rounds_left,pending=len(self.budget.pending),limited=self.limited)

    def close(self):
        if self.synthesis_control is not None:self.synthesis_control.close()
        if self.restore_control is not None:self.restore_control.close()
        for cls,name,original in reversed(self.originals):setattr(cls,name,original)
        self.originals.clear()
