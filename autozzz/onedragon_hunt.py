"""恶名狩猎的免费次数与战斗结果核实；深度追猎继续使用持久电量预算。"""
from __future__ import annotations

import copy
from contextvars import ContextVar
from functools import wraps
import re
from typing import Any

from .onedragon_control import ExecutionStopped

_current_hunt: ContextVar[Any] = ContextVar("autozzz_current_hunt", default=None)


def current_hunt() -> Any:
    return _current_hunt.get()


def parse_free_times(text: str) -> int:
    if not isinstance(text, str) or not re.fullmatch(r"\s*[0-3]\s*", text):
        raise ValueError("未能核实当前每周剩余免费次数。")
    return int(text.strip())


class NotoriousHuntControl:
    def __init__(self, ctx: Any, control: Any):
        self.ctx, self.control = ctx, control
        self.originals: list[tuple[Any, str, Any, bool]] = []
        self.results: list[dict] = []
        self.failures: list[dict] = []
        self.skipped: list[dict] = []

    def patch_node(self, cls: Any, name: str, handler: Any) -> None:
        original = getattr(cls, name)
        @wraps(original)
        def replacement(operation, *args, **kwargs):
            return handler(operation, original, *args, **kwargs)
        node = copy.copy(original.operation_node_annotation)
        node.op_method = replacement
        replacement.operation_node_annotation = node
        self.originals.append((cls, name, original, name in vars(cls)))
        setattr(cls, name, replacement)

    def stop(self, message: str) -> None:
        self.control.stop(message)
        raise ExecutionStopped(message)

    def deep_mode(self, operation: Any) -> bool:
        return operation.round_by_find_area(operation.last_screenshot, "恶名狩猎", "按钮-深度追猎-ON").is_success

    def read_free_times(self, operation: Any) -> int:
        from one_dragon.utils import cv2_utils
        self.control.checkpoint(require_frame=True)
        if self.deep_mode(operation) or operation.round_by_find_area(operation.last_screenshot, "恶名狩猎", "深度追猎-信息").is_success:
            return 0
        area = self.ctx.screen_loader.get_area("恶名狩猎", "剩余次数")
        part = cv2_utils.crop_image_only(operation.last_screenshot, area.rect)
        text = self.ctx.ocr.run_ocr_single_line(part, threshold=.7)
        self.control.checkpoint(require_frame=True)
        return parse_free_times(text)

    def install(self) -> None:
        from zzz_od.operation.compendium.notorious_hunt import NotoriousHunt
        from zzz_od.application.notorious_hunt.notorious_hunt_app import NotoriousHuntApp
        from zzz_od.application.charge_plan.charge_plan_app import ChargePlanApp
        from zzz_od.operation.challenge_mission.check_next_after_battle import ChooseNextOrFinishAfterBattle
        from zzz_od.operation.compendium.tp_by_compendium import TransportByCompendium
        from zzz_od.operation.restore_charge import RestoreCharge
        from zzz_od.operation.exchange_ether_battery import ExchangeEtherBattery
        owner = self

        def decide(operation, original):
            if operation.use_charge_power:
                return original(operation)
            try:
                left = owner.read_free_times(operation)
            except ValueError as exc:
                return operation.round_retry(str(exc), wait=.5)
            operation.run_record.left_times = left
            operation.can_run_times = min(left, max(0, operation.plan.plan_times - operation.plan.run_times))
            if left == 0:
                return operation.round_success(NotoriousHunt.STATUS_NO_LEFT_TIMES)
            return operation.round_success(NotoriousHunt.STATUS_WITH_LEFT_TIMES)

        def before_next(operation, original):
            deploying = operation.round_by_find_area(operation.last_screenshot, "实战模拟室", "出战").is_success
            next_button = operation.round_by_find_area(operation.last_screenshot, "实战模拟室", "下一步").is_success
            if not deploying and not next_button:
                return operation.round_retry("等待恶名狩猎副本选择画面", wait=.5)
            if next_button and not deploying:
                if operation.use_charge_power:
                    if not owner.deep_mode(operation):
                        return operation.round_retry("尚未核实深度追猎已开启", wait=.5)
                else:
                    try:
                        left = owner.read_free_times(operation)
                    except ValueError as exc:
                        return operation.round_retry(str(exc), wait=.5)
                    operation.run_record.left_times = left
                    operation.can_run_times = min(left, max(0, operation.plan.plan_times - operation.plan.run_times))
                    if operation.can_run_times <= 0:
                        return operation.round_success(NotoriousHunt.STATUS_NO_LEFT_TIMES)
            return original(operation)

        def battle_loaded(operation, original):
            result = original(operation)
            if result.is_success:
                operation._autozzz_hunt_settled = False
            return result

        def after_battle(operation, original):
            owner.control.checkpoint(require_frame=True)
            if getattr(operation, "_autozzz_hunt_settled", False):
                return operation.round_success()
            if owner.ctx.auto_battle_context.last_check_end_result != "普通战斗-完成":
                owner.stop("未核实恶名狩猎胜利结算，未更新完成次数。")
            result = original(operation)
            if result.is_success:
                operation._autozzz_hunt_settled = True
                owner.results.append(dict(plan=operation.plan.uid, mode="deep" if operation.use_charge_power else "weekly",
                                          result="victory", free_left=None if operation.use_charge_power else operation.run_record.left_times))
            return result

        def choose_next(operation, original):
            token = _current_hunt.set(operation)
            try:
                return original(operation)
            finally:
                _current_hunt.reset(token)

        def free_repeat(operation, original):
            hunt = current_hunt()
            if hunt is not None and not hunt.use_charge_power:
                owner.control.checkpoint(require_frame=True)
                if hunt.can_run_times <= 0 or hunt.run_record.left_times <= 0:
                    operation.try_next = False
            return original(operation)

        def reward_entry(operation, original):
            on_board = any(operation.round_by_find_area(operation.last_screenshot, "恶名狩猎", area).is_success
                           for area in ("当期剩余奖励次数", "按钮-街区"))
            if not on_board:
                plan = operation.config.plan_list[0]
                travel = TransportByCompendium(owner.ctx, plan.tab_name, plan.category_name, plan.mission_type_name)
                result = travel.execute()
                if not result.success:
                    return operation.round_by_op_result(result)
                return operation.round_wait("等待恶名狩猎奖励入口", wait=1)
            return original(operation)

        self.patch_node(NotoriousHunt, "decide_notorious_hunt", decide)
        self.patch_node(NotoriousHunt, "click_next", before_next)
        self.patch_node(NotoriousHunt, "wait_battle_screen", battle_loaded)
        self.patch_node(NotoriousHunt, "after_battle", after_battle)
        self.patch_node(NotoriousHunt, "check_next", choose_next)
        self.patch_node(ChooseNextOrFinishAfterBattle, "check_next", free_repeat)
        self.patch_node(NotoriousHuntApp, "click_reward_entry", reward_entry)

        finish = NotoriousHunt.after_operation_done
        @wraps(finish)
        def finish_hunt(operation, result):
            item = dict(app_id=owner.ctx.run_context.current_app_id, plan=operation.plan.uid,
                        mode="deep" if operation.use_charge_power else "weekly", status=result.status or "")
            if not result.success:
                owner.failures.append(item)
            elif result.status in (NotoriousHunt.STATUS_NO_LEFT_TIMES, NotoriousHunt.STATUS_BLOCKED_BY_LEFT_TIMES,
                                   ChooseNextOrFinishAfterBattle.STATUS_AGENT_PLAN_FINISHED):
                owner.skipped.append(item)
            return finish(operation, result)
        self.originals.append((NotoriousHunt, "after_operation_done", finish, "after_operation_done" in vars(NotoriousHunt)))
        NotoriousHunt.after_operation_done = finish_hunt
        for cls in (NotoriousHuntApp, ChargePlanApp):
            original = cls.after_operation_done
            @wraps(original)
            def finish_app(operation, result, original=original):
                failed = [item for item in owner.failures if item["app_id"] == operation.app_id]
                if result.success and failed:
                    result.success = False
                    result.status = "恶名狩猎计划未完成：" + "；".join(item["plan"] + " " + item["status"] for item in failed)
                return original(operation, result)
            self.originals.append((cls, "after_operation_done", original, "after_operation_done" in vars(cls)))
            cls.after_operation_done = finish_app
        for cls in (RestoreCharge, ExchangeEtherBattery):
            original = cls.execute
            @wraps(original)
            def guarded(operation, original=original):
                hunt = current_hunt()
                if hunt is not None and not hunt.use_charge_power:
                    owner.stop("每周免费挑战不能补充电量或合成电池。")
                return original(operation)
            self.originals.append((cls, "execute", original, "execute" in vars(cls)))
            cls.execute = guarded

    def status(self) -> dict:
        return dict(free_completed=sum(item["mode"] == "weekly" for item in self.results),
                    paid_completed=sum(item["mode"] == "deep" for item in self.results),
                    results=[dict(item) for item in self.results], failures=[dict(item) for item in self.failures],
                    skipped=[dict(item) for item in self.skipped])

    def close(self) -> None:
        for cls, name, original, was_local in reversed(self.originals):
            if was_local:setattr(cls, name, original)
            else:delattr(cls, name)
        self.originals.clear()
