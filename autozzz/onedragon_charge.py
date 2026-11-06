"""为上游体力计划连接 AutoZZZ 持久预算，覆盖首次出战与再来一次。"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from functools import wraps
import re
from threading import Lock
from time import monotonic
from typing import Any, Callable

from .battle_budget import BattleBudget
from .onedragon_charge_recovery import NativeChargeRecovery, SOURCE_VICTORY
from .onedragon_control import ExecutionStopped


@dataclass(frozen=True)
class _ChargeContinuation:
    operation: Any
    battle_node: Any
    application: Any
    plan: Any
    plan_snapshot: dict
    reservation: str
    revision: str
    may_settle: Callable[[], bool]


class ChargeBudgetControl:
    CATEGORIES = {"实战模拟室":20,"区域巡防":60,"专业挑战室":40,"恶名狩猎":60,"合成电池":60}
    REPEAT_BUTTON = "战斗结果-再来一次"
    FINISH_BUTTON = "战斗结果-完成"
    REPEAT_CONFIRM_SECONDS = 60
    RECOVERY_FINISHED = "原战斗续接已结算"

    def __init__(self,ctx:Any,control:Any,budget:BattleBudget,max_per_battle:int,restore_mode='不使用',restore_authorized=False,synthesis_authorized=False,evidence_dir=None,*,track_recovery_graph=False):
        if budget.pending:raise ValueError("该预算有尚未核对的出战，不能重新开始。")
        if type(max_per_battle)is not int or not 20<=max_per_battle<=100:raise ValueError("单次电量上限须为 20—100。")
        self.ctx,self.control,self.budget,self.maximum = ctx,control,budget,max_per_battle
        self.owner,self.cost,self.reservation = None,0,None
        self._repeat_operation = None
        self._repeat_attempt = None
        self._repeat_after_restore = False
        self._continuation_types: tuple[type, ...] = ()
        self._continuation: _ChargeContinuation | None = None
        self._continuation_lock = Lock()
        self._continuation_attempted = False
        self._continuation_settled = False
        self.originals = []
        self.limited = False
        self.restore_mode,self.restore_authorized=restore_mode,restore_authorized
        self.restore_control=None;self.restore_cost=0
        self.synthesis_authorized=synthesis_authorized;self.synthesis_control=None
        self.failures=[]
        self.recovery_graph = None
        if track_recovery_graph:
            from .recovery_graph import RecoveryGraph
            self.recovery_graph = RecoveryGraph()
        from .charge_evidence import ChargeEvidence
        self.evidence=ChargeEvidence(evidence_dir) if evidence_dir is not None else None

    def check_restore_budget(self):
        self._forbid_continuation()
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

    def arm_recovery_continuation(self, operation, plan, reservation: str, revision: str,
                                  *, may_settle: Callable[[], bool]) -> None:
        """Bind one paused SDK battle before the worker opens its input gate."""
        if not callable(may_settle) or not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision):
            raise ValueError("原战斗续接缺少同场核实或有效的预算版本。")
        with self.control.lock, self._continuation_lock:
            if self._continuation is not None:
                raise ValueError("原战斗续接已绑定。")
            if (not self.control.recovery_blocked or self.control.stopped
                    or not self.ctx.run_context.is_context_pause
                    or getattr(self.ctx, 'autozzz_charge_control', None) is not self
                    or type(operation) not in self._continuation_types or self.owner is not operation
                    or operation.ctx is not self.ctx or operation.plan is not plan or not reservation
                    or self.reservation != reservation or self._repeat_operation is not None
                    or self.budget.data['battle_limit'] != 1 or self.restore_mode != '不使用'
                    or self.restore_authorized or self.synthesis_authorized):
                raise ValueError("当前预留不允许原战斗续接。")
            node = operation.current_node.node
            app = self.ctx.run_context.current_application
            if (node is None or node.op_method is not type(operation).auto_battle
                    or app is None or app.app_id != 'charge_plan' or app.current_plan is not plan
                    or plan.is_agent_plan or not plan.plan_id
                    or self.ctx.auto_battle_context.last_check_end_result is not None):
                raise ValueError("原战斗或电量计划已变化。")
            recovery = NativeChargeRecovery(self.budget.path.parent)
            pending = recovery.pending_binding(self.budget, reservation, plan_uid=plan.uid,
                                               expected_revision=revision)
            if pending['cost'] != self.cost or recovery.verified_receipt(self.budget.data, revision) is not None:
                raise ValueError("原待核对出战已变化。")
            self._continuation = _ChargeContinuation(
                operation=operation, battle_node=node, application=app, plan=plan, plan_snapshot=plan.to_dict(),
                reservation=reservation, revision=revision, may_settle=may_settle,
            )

    def _forbid_continuation(self):
        if self._continuation is not None:
            self._block("续接只允许原战斗和原胜利结算。" + self.pending_result_error())

    def _require_continuation_victory(self, continuation):
        try:
            allowed = continuation.may_settle() is True
        except Exception:
            allowed = False
        if not allowed:
            self._block("未核实续接后的首个同场战斗画面，预算保持待核对。")

    def _patch_node(self,cls,name,handler,*,allow_continuation=False):
        original = getattr(cls,name)
        @wraps(original)
        def guarded(operation,*args,**kwargs):
            if not allow_continuation:
                self._forbid_continuation()
            return handler(operation,original,*args,**kwargs)
        node = copy.copy(original.operation_node_annotation)
        node.op_method = guarded  # 注解引用原函数；仅替换类属性不会改变实际节点执行。
        guarded.operation_node_annotation = node
        self.originals.append((cls,name,original))
        setattr(cls,name,guarded)

    def _guard_continuation_nodes(self, *classes):
        installed = {(cls, name) for cls, name, _ in self.originals}
        for cls in classes:
            for name, method in tuple(vars(cls).items()):
                if (cls, name) not in installed and hasattr(method, 'operation_node_annotation'):
                    self._patch_node(cls, name, lambda operation, original: original(operation))

    @staticmethod
    def _require_exact_edge(method, source):
        edges = []
        for edge in method.operation_edge_annotation:
            if edge.node_from_name == source:
                edge = copy.copy(edge)
                edge.ignore_status = False
            edges.append(edge)
        method.operation_edge_annotation = edges

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
        self._forbid_continuation()
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
        if self.recovery_graph is not None:
            self.recovery_graph.bind(owner, self.reservation)
        self.control.checkpoint(require_frame=True)

    def install(self):
        self.ctx.autozzz_charge_control=self
        from zzz_od.operation.compendium.combat_simulation import CombatSimulation
        from zzz_od.operation.compendium.area_patrol import AreaPatrol
        from zzz_od.operation.compendium.expert_challenge import ExpertChallenge
        from zzz_od.operation.compendium.notorious_hunt import NotoriousHunt
        from zzz_od.operation.challenge_mission.check_next_after_battle import ChooseNextOrFinishAfterBattle
        from zzz_od.operation.challenge_mission.restart_in_battle import RestartInBattle
        from zzz_od.application.charge_plan.charge_plan_app import ChargePlanApp
        from zzz_od.operation.deploy import Deploy
        from zzz_od.operation.restore_charge import RestoreCharge
        from zzz_od.operation.exchange_ether_battery import ExchangeEtherBattery

        self._continuation_types = (CombatSimulation, AreaPatrol, ExpertChallenge)
        graph = self.recovery_graph
        if graph is not None:
            from one_dragon.base.operation.operation import Operation
            from one_dragon.base.operation.operation_round_result import OperationRoundResultEnum

            execute = Operation.execute

            @wraps(execute)
            def track_visit(operation):
                if operation.ctx is self.ctx and type(operation) in self._continuation_types:
                    with graph.track(operation, separate_start=type(operation) is CombatSimulation):
                        return execute(operation)
                return execute(operation)

            self.originals.append((Operation, 'execute', execute))
            Operation.execute = track_visit

            def deploy(operation, original):
                owner = self.owner
                attempt = graph.attempt(owner, self.reservation, 'deploy') if operation.ctx is self.ctx else None
                result = original(operation)
                # The pinned Deploy node returns WAIT for an actual click. Its
                # later SUCCESS can only confirm that the button disappeared.
                if result.result is OperationRoundResultEnum.WAIT and result.status == '按钮-出战':
                    graph.accepted(owner, attempt)
                return result

            self._patch_node(Deploy, 'deploy', deploy)

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

        def auto_battle(operation, original):
            continuation = self._continuation
            if continuation is not None and (operation is not continuation.operation
                    or operation.current_node.node is not continuation.battle_node
                    or operation.plan is not continuation.plan or self._continuation_attempted):
                self._block("原战斗节点已变化，预算保持待核对。")
            result = original(operation)
            continuation = self._continuation
            if continuation is not None and result.is_success:
                if result.status != SOURCE_VICTORY:
                    self._block("原战斗已结束且未核实胜利，预算保持待核对。")
                self._require_continuation_victory(continuation)
            return result

        def after_battle(operation,original):
            if isinstance(operation, NotoriousHunt) and not operation.use_charge_power:
                return original(operation)
            self.control.checkpoint(require_frame=True)
            source_result = self.ctx.auto_battle_context.last_check_end_result
            if source_result != SOURCE_VICTORY:
                self._block("未核实胜利结算，预算保持待核对。")
            if self.owner is not operation or self.reservation is None:self._block("缺少本场出战的预算记录。")
            continuation = self._continuation
            if continuation is not None:
                with self._continuation_lock:
                    already_attempted = self._continuation_attempted
                    self._continuation_attempted = True
                if already_attempted:
                    self._block("原战斗已尝试结算。" + self.pending_result_error())
                previous = operation.previous_node
                if (operation is not continuation.operation or operation.plan is not continuation.plan
                        or operation.plan.to_dict() != continuation.plan_snapshot
                        or self.reservation != continuation.reservation or previous.node is not continuation.battle_node
                        or not previous.is_success or previous.status != SOURCE_VICTORY):
                    self._block("原战斗结算节点已变化，预算保持待核对。")
                try:
                    if not operation.round_by_find_area(operation.last_screenshot, "战斗画面", self.FINISH_BUTTON).is_success:
                        raise ValueError("当前画面未核实原胜利结算。")
                    recovery = NativeChargeRecovery(self.budget.path.parent)
                    recovery.pending_binding(self.budget, self.reservation, plan_uid=operation.plan.uid,
                                             expected_revision=continuation.revision)
                    self.control.checkpoint(require_frame=True)
                except Exception as exc:
                    self._block("核实原胜利结算失败，预算保持待核对。" + str(exc))
                self._require_continuation_victory(continuation)
            try:
                result = original(operation)
                if result.is_success:
                    recovery = NativeChargeRecovery(self.budget.path.parent)
                    revision = None if continuation is None else continuation.revision
                    proof = recovery.record_victory(self.budget, self.reservation, source_result=source_result,
                                                    plan_uid=operation.plan.uid, expected_revision=revision)
                    if continuation is not None:
                        recovery.pending_binding(self.budget, self.reservation, plan_uid=operation.plan.uid,
                                                 expected_revision=revision)
                    self.budget.settle(self.reservation, None, verification=proof)
            except Exception as exc:
                # SDK 会重试普通异常。原生节点已可能保存场次，不能再执行一次。
                self._block("保存原生胜利结算失败，预算保持待核对："+str(exc))
            if not result.is_success:
                if continuation is not None:
                    self._block("原生胜利结算未成功，预算保持待核对。")
                return result
            self.reservation = None
            self._repeat_operation = None
            self._repeat_attempt = None
            self._repeat_after_restore = False
            if continuation is not None:
                self._continuation_settled = True
                result.status = self.RECOVERY_FINISHED
            return result

        def repeat_battle_loaded(operation):
            screen = operation.last_screenshot
            return (operation.round_by_find_area(screen, "战斗画面", "按键-普通攻击").is_success
                    or (isinstance(self.owner, NotoriousHunt)
                        and operation.round_by_find_area(screen, "战斗画面", "按键-交互").is_success))

        def wait_for_repeat_frame(operation):
            epoch, started = self._repeat_attempt
            now = monotonic()
            if epoch != self.control.epoch:
                # Resume waiting for evidence without resubmitting a paid click.
                self._repeat_attempt = (self.control.epoch, now)
            elif now - started >= self.REPEAT_CONFIRM_SECONDS:
                self._block("未核实再来一次后的战斗或补电提示，预算保持待核对。")
            return operation.round_wait("等待再来一次提交后的新画面", wait=.5)

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
                if self._repeat_attempt is not None:
                    # A focus change or an ambiguous click response can re-enter this node.
                    # Continue from new evidence without clicking the paid button again.
                    recovery = operation.round_by_find_area(screen, "恢复电量", "标题-恢复电量")
                    if recovery.is_success or repeat_battle_loaded(operation):
                        return operation.round_success(self.REPEAT_BUTTON)
                    return wait_for_repeat_frame(operation)
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
            self._repeat_attempt = (self.control.epoch, monotonic())
            owner = self.owner
            attempt = None if graph is None or operation.ctx is not self.ctx else graph.attempt(owner, self.reservation, 'repeat')
            result = operation.round_by_find_and_click_area(screen, "战斗画面", self.REPEAT_BUTTON,
                                                            success_wait=1, retry_wait=1)
            if attempt is not None and result.is_success and result.status == self.REPEAT_BUTTON:
                graph.accepted(owner, attempt)
            return result

        def after_repeat_submit(operation, original):
            if self._repeat_operation is not operation:
                return original(operation)
            recovery = operation.round_by_find_area(operation.last_screenshot, "恢复电量", "标题-恢复电量")
            if not recovery.is_success:
                if repeat_battle_loaded(operation):
                    return operation.round_success(self.REPEAT_BUTTON)
                return wait_for_repeat_frame(operation)
            if self._repeat_after_restore:
                self._block("恢复后再次提示电量不足，预算保持待核对。")
            result=original(operation)
            if result.is_success and result.status==self.FINISH_BUTTON and operation.try_next:
                # The game explicitly rejected the first start for insufficient energy.
                # A successful authorized restore permits one retry of this same reservation.
                self._repeat_after_restore=True
                self._repeat_attempt = None
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

        def plan_outcome(operation,original):
            previous=operation.previous_node
            continuation = self._continuation
            if continuation is not None:
                if (operation is continuation.application and operation.current_plan is continuation.plan
                        and self._continuation_settled and previous.is_success
                        and previous.status == self.RECOVERY_FINISHED):
                    return operation.round_success(self.RECOVERY_FINISHED)
                self._block("原战斗续接未核实完成。" + self.pending_result_error())
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

        for cls in (*self._continuation_types, NotoriousHunt):
            self._patch_node(cls,"click_next",before_next)
            self._patch_node(cls,"after_battle",after_battle,allow_continuation=cls in self._continuation_types)
        for cls in self._continuation_types:
            self._patch_node(cls,"auto_battle",auto_battle,allow_continuation=True)
        self._patch_node(ChooseNextOrFinishAfterBattle,"check_next",repeat)
        self._patch_node(ChooseNextOrFinishAfterBattle,"restore_charge",after_repeat_submit)
        self._patch_node(ChargePlanApp,"find_next_plan",next_plan)
        self._patch_node(ChargePlanApp,"challenge_complete",plan_outcome,allow_continuation=True)
        self._patch_node(ChargePlanApp,"skip_plan_or_finish",plan_outcome)
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
            self._guard_continuation_nodes(cls)
        self._guard_continuation_nodes(*self._continuation_types, NotoriousHunt, Deploy, RestartInBattle,
                                       ChooseNextOrFinishAfterBattle, ChargePlanApp)
        # The pinned source uses a status-free success edge in the ordinary graph.
        # A continued victory can finish both graphs without another input node.
        for cls in self._continuation_types:
            self._require_exact_edge(cls.check_next, '战斗结束')
        self._require_exact_edge(ChargePlanApp.back_before_open_compendium, '挑战完成')

    def status(self) -> dict:
        return dict(id=self.budget.data["id"],reserved=self.budget.reserved,remaining=self.budget.remaining,
                    rounds_left=self.budget.rounds_left,pending=len(self.budget.pending),limited=self.limited,
                    failures=[dict(item) for item in self.failures],
                    evidence=[] if self.evidence is None else self.evidence.status(),
                    evidence_errors=[] if self.evidence is None else list(self.evidence.errors))

    def close(self):
        if self.recovery_graph is not None:self.recovery_graph.close()
        if self.synthesis_control is not None:self.synthesis_control.close()
        if self.restore_control is not None:self.restore_control.close()
        for cls,name,original in reversed(self.originals):setattr(cls,name,original)
        self.originals.clear()
        if getattr(self.ctx,'autozzz_charge_control',None) is self:delattr(self.ctx,'autozzz_charge_control')
