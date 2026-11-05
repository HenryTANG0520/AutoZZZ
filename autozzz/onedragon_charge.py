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
    REPEAT_BUTTON = "战斗结果-再来一次"
    FINISH_BUTTON = "战斗结果-完成"

    def __init__(self,ctx:Any,control:Any,budget:BattleBudget,max_per_battle:int,restore_mode='不使用',restore_authorized=False,synthesis_authorized=False,evidence_dir=None):
        if budget.pending:raise ValueError("该预算有尚未核对的出战，不能重新开始。")
        if type(max_per_battle)is not int or not 20<=max_per_battle<=100:raise ValueError("单次电量上限须为 20—100。")
        self.ctx,self.control,self.budget,self.maximum = ctx,control,budget,max_per_battle
        self.owner,self.cost,self.reservation = None,0,None
        self._repeat_operation = None
        self._repeat_attempted = False
        self._repeat_after_restore = False
        self.originals = []
        self.limited = False
        self.restore_mode,self.restore_authorized=restore_mode,restore_authorized
        self.restore_control=None;self.restore_cost=0
        self.synthesis_authorized=synthesis_authorized;self.synthesis_control=None
        self.failures=[]
        from .charge_evidence import ChargeEvidence
        self.evidence=ChargeEvidence(evidence_dir) if evidence_dir is not None else None

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

    def pending_result_error(self) -> str:
        if self.budget.pending:
            return "本轮仍有待核对的电量消耗。请在预算记录中核对。"
        return ""

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
        # 费用右侧会延伸到下一步按钮边缘内。旧 x2=button.x1 把 100
        # 裁为 10，且 OCR 仍给出高置信度。保留两侧与上下的完整字形。
        rect=Rect(max(0,button.x1-230),max(0,button.y1-10),button.x1+50,button.y2+10)
        part=cv2_utils.crop_image_only(operation.last_screenshot,rect)
        raw=self.ctx.ocr.run_ocr(part)
        try:return self._validate_cost(operation,raw)
        except ValueError as exc:
            evidence=getattr(self,'evidence',None)
            if evidence is not None:evidence.record(operation.last_screenshot,part,rect,raw,str(exc),operation.plan)
            raise

    def _validate_cost(self,operation,raw) -> tuple[int,int]:
        results=[]
        for text,matches in raw.items():
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
        if cost not in allowed:raise ValueError(f"当前副本消耗与所选计划不一致：画面 {available}/{cost}，计划消耗 {sorted(allowed)}。")
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
        self.ctx.autozzz_charge_control=self
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
                self._repeat_operation=None
                self._repeat_attempted=False
                self._repeat_after_restore=False
            return result

        def repeat(operation,original):
            from .onedragon_hunt import current_hunt
            hunt = current_hunt()
            if hunt is not None and not hunt.use_charge_power:
                return original(operation)
            if not operation.try_next:
                return original(operation)
            screen = operation.last_screenshot
            if self.reservation is not None:
                if self._repeat_operation is not operation:
                    self._block("上一场出战未结算，不能开始新的出战。")
                if self._repeat_attempted:
                    # A focus change or an ambiguous click response can re-enter this node.
                    # Continue from new evidence without clicking the paid button again.
                    recovery = operation.round_by_find_area(screen, "恢复电量", "标题-恢复电量")
                    battle = operation.round_by_find_area(screen, "战斗画面", "按键-普通攻击")
                    if recovery.is_success or battle.is_success:
                        return operation.round_success(self.REPEAT_BUTTON)
                    return operation.round_retry("等待再来一次提交后的新画面", wait=.5)
            else:
                if operation.is_agent_plan and operation.round_by_find_area(screen,"战斗画面","战斗结果-已达成").is_success:
                    result=operation.round_by_find_and_click_area(screen,"战斗画面",self.FINISH_BUTTON,
                                                                  success_wait=5,retry_wait=1)
                    if result.is_success:
                        return operation.round_success(ChooseNextOrFinishAfterBattle.STATUS_AGENT_PLAN_FINISHED)
                    return result
                if self.owner is None:self._block("未找到上一场副本，不能重复出战。")
                if self.budget.remaining<self.cost or self.budget.rounds_left<=0:
                    self.limited=True
                    operation.try_next=False
                    return original(operation)
            if not operation.round_by_find_area(screen, "战斗画面", self.REPEAT_BUTTON).is_success:
                if self.reservation is not None:
                    return operation.round_retry("等待恢复电量后重新核实再来一次按钮", wait=.5)
                return operation.round_by_find_and_click_area(screen, "战斗画面", self.FINISH_BUTTON,
                                                              success_wait=5, retry_wait=1)
            self._repeat_operation=operation
            self._reserve(self.owner,self.cost)
            self._repeat_attempted=True
            return operation.round_by_find_and_click_area(screen, "战斗画面", self.REPEAT_BUTTON,
                                                          success_wait=1, retry_wait=1)

        def after_repeat_submit(operation, original):
            if self._repeat_operation is not operation:
                return original(operation)
            recovery = operation.round_by_find_area(operation.last_screenshot, "恢复电量", "标题-恢复电量")
            if recovery.is_success and self._repeat_after_restore:
                self._block("恢复后再次提示电量不足，预算保持待核对。")
            result=original(operation)
            if recovery.is_success and result.is_success and result.status==self.FINISH_BUTTON and operation.try_next:
                # The game explicitly rejected the first start for insufficient energy.
                # A successful authorized restore permits one retry of this same reservation.
                self._repeat_after_restore=True
                self._repeat_attempted=False
            return result

        def open_restore(operation, original):
            if operation.is_after_battle_retry:
                recovery = operation.round_by_find_area(operation.last_screenshot, "恢复电量", "标题-恢复电量")
                if not recovery.is_success:
                    return operation.round_retry("等待已提交的恢复电量提示", wait=.5)
            return original(operation)

        def next_plan(operation,original):
            if self.budget.remaining<20 or self.budget.rounds_left<=0:
                self.limited=True
                return operation.round_success(ChargePlanApp.STATUS_ROUND_FINISHED)
            return original(operation)

        def challenge_complete(operation,original):
            previous=operation.previous_node
            if not previous.is_success:
                self.failures.append(dict(plan_id=operation.current_plan.uid,
                    detail=str(previous.status or "副本执行失败")))
            pending_error=self.pending_result_error()
            if pending_error:
                self._block(pending_error)
            return original(operation)

        def back_to_world(operation,original):
            result=original(operation)
            if result.is_success and self.failures:
                return operation.round_fail("刷本未完成："+"；".join(item['detail'] for item in self.failures))
            return result

        for cls in (CombatSimulation,AreaPatrol,ExpertChallenge,NotoriousHunt):
            self._patch_node(cls,"click_next",before_next)
            self._patch_node(cls,"after_battle",after_battle)
        self._patch_node(ChooseNextOrFinishAfterBattle,"check_next",repeat)
        self._patch_node(ChooseNextOrFinishAfterBattle,"restore_charge",after_repeat_submit)
        self._patch_node(ChargePlanApp,"find_next_plan",next_plan)
        self._patch_node(ChargePlanApp,"challenge_complete",challenge_complete)
        self._patch_node(ChargePlanApp,"back_to_world",back_to_world)
        if self.restore_mode!='不使用':
            from .onedragon_restore import RestoreChargeControl
            self.restore_control=RestoreChargeControl(self.ctx,self.control,self.restore_mode,self);self.restore_control.install()
            self._patch_node(RestoreCharge,"click_charge_text",open_restore)
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
                    rounds_left=self.budget.rounds_left,pending=len(self.budget.pending),limited=self.limited,
                    failures=[dict(item) for item in self.failures],
                    evidence=[] if self.evidence is None else self.evidence.status(),
                    evidence_errors=[] if self.evidence is None else list(self.evidence.errors))

    def close(self):
        if self.synthesis_control is not None:self.synthesis_control.close()
        if self.restore_control is not None:self.restore_control.close()
        for cls,name,original in reversed(self.originals):setattr(cls,name,original)
        self.originals.clear()
        if getattr(self.ctx,'autozzz_charge_control',None) is self:delattr(self.ctx,'autozzz_charge_control')
