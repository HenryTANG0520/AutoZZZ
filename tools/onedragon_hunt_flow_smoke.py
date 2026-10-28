"""真实恶名狩猎节点、免费计划与深度计划回放，使用合成数字画面和记录设备。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import sys
import time
import uuid
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def wait(predicate, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate(): return
        time.sleep(.01)
    raise AssertionError("恶名狩猎回放超时")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    marker = args.data / ".autozzz-hunt-flow-test"
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file():
        raise SystemExit("请使用空目录或本工具已有的测试目录。")
    args.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("isolated-test-profile", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    from autozzz.onedragon_runtime import RuntimePaths
    from autozzz.config import GAME_PATHS
    defaults = RuntimePaths.discover(root)
    RuntimePaths(defaults.source, defaults.python, args.data.resolve()).prepare()
    cache = root / "runs/onedragon-20261029/hunt-preparation/assets/models"
    if cache.is_dir(): shutil.copytree(cache, args.data / "assets/models", dirs_exist_ok=True)
    sys.path.insert(0, str(defaults.source / "src"))
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_control import WindowState
    from autozzz.onedragon_charge import ChargeBudgetControl
    from autozzz.onedragon_hunt import NotoriousHuntControl
    from autozzz.battle_budget import BattleBudget
    from one_dragon.base.operation.operation_base import OperationResult
    from one_dragon.base.operation.operation_round_result import OperationRoundResultEnum
    from one_dragon.base.matcher.match_result import MatchResult, MatchResultList
    from zzz_od.application.charge_plan.charge_plan_config import ChargePlanItem
    from zzz_od.application.charge_plan.charge_plan_app import ChargePlanApp
    from zzz_od.operation.compendium.notorious_hunt import NotoriousHunt as Hunt
    from zzz_od.application.notorious_hunt.notorious_hunt_app import NotoriousHuntApp
    from zzz_od.operation.challenge_mission.check_next_after_battle import ChooseNextOrFinishAfterBattle
    from zzz_od.operation.compendium.tp_by_compendium import TransportByCompendium
    from zzz_od.operation.compendium.notorious_hunt_move import NotoriousHuntMove
    from zzz_od.operation.back_to_normal_world import BackToNormalWorld

    service = OneDragonService(defaults.source, args.data.resolve())
    service.initialize_execution("cn", GAME_PATHS["cn"])
    manager = service._execution
    wait(lambda: not manager.busy)
    assert manager.state == "ready", manager.status()
    ctx, control = service.ctx, manager.control
    controller = ctx.controller
    env = dict(scene="stage", quota=3, cost=60, mode="weekly", reward=False, fail=False, loaded=False, pause_next=False, paused=False)
    window = [WindowState(True, True, (0, 0, 1920, 1080))]
    events, checks = [], []
    budget_root = args.data / "budgets" / uuid.uuid4().hex
    controller.bind = lambda path: None
    controller.probe = lambda: window[0]
    controller.init_before_context_run = lambda: True
    controller.game_win.game2win_pos = lambda point: point

    class Recorder:
        position = (0, 0)
        def press(self, key): events.append(("down", str(key)))
        def release(self, key): events.append(("up", str(key)))
        def type(self, text): events.append(("type", text))

    controller.keyboard_controller.keyboard.raw = Recorder()
    controller.keyboard_controller.mouse.raw = Recorder()
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 24)
    number_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 21)
    def frame():
        image = Image.new("RGB", (1920, 1080), (25, 25, 25))
        draw = ImageDraw.Draw(image)
        button = ctx.screen_loader.get_area("实战模拟室", "下一步").rect
        draw.text((button.x1 - 200, button.y1 + 3), "320 / " + str(env["cost"]), font=font, fill="white")
        remaining = ctx.screen_loader.get_area("恶名狩猎", "剩余次数").rect
        draw.text((remaining.x1, remaining.y1 - 4), str(env["quota"]), font=number_font, fill="white")
        return time.time(), np.asarray(image)
    controller.screenshot = lambda independent=False: control.observe(frame)
    def watcher(stopped):
        while not stopped.wait(.01): control.poll()
    manager._watch = watcher

    def area_found(operation, screen=None, screen_name=None, area_name=None, **kwargs):
        if screen_name == "实战模拟室":
            matched = (area_name == "下一步" and env["scene"] == "stage") or (area_name == "出战" and env["scene"] == "team")
        elif screen_name == "恶名狩猎":
            matched = (area_name == "按钮-街区" and env["scene"] == "stage") or (area_name in ("按钮-深度追猎-ON", "深度追猎-信息") and env["mode"] == "deep")
        elif screen_name == "战斗画面":
            matched = ((area_name in ("按键-普通攻击", "按键-交互") and env["scene"] == "battle")
                       or (area_name == "战斗结果-再来一次" and env["scene"] == "result"))
        else: matched = False
        return operation.round_success(area_name) if matched else operation.round_fail(area_name)

    def click(operation, screen=None, screen_name=None, area_name=None, **kwargs):
        if area_name == "按钮-深度追猎-确认": return operation.round_fail(area_name)
        if area_name == "战斗结果-倒带" and env["fail"]:return operation.round_fail(area_name)
        if area_name == "下一步" and env["pause_next"] and not env["paused"]:
            env["paused"] = True
            window[0] = WindowState(True, False, window[0].geometry)
            control.poll()
        control.input(lambda: events.append((screen_name, area_name)))
        if area_name == "下一步": env["scene"] = "team"
        if area_name in ("出战", "战斗结果-再来一次", "重新开始-确认"):
            env.update(scene="battle", loaded=False)
        if area_name == "战斗结果-完成": env["scene"] = "stage"
        return operation.round_success(area_name)

    def click_area(operation, screen_name, area_name, **kwargs):
        return click(operation, screen_name=screen_name, area_name=area_name)

    def battle_check(*args, **kwargs):
        if not env["loaded"]:
            env["loaded"] = True
            if env["mode"] == "weekly" and not env["fail"]: env["quota"] -= 1
        env["scene"] = "result"
        ctx.auto_battle_context.last_check_end_result = "普通战斗-撤退" if env["fail"] else "普通战斗-完成"

    def travel(operation):
        env["scene"] = "stage"
        if ctx.run_context.current_app_id in ("charge_plan", "notorious_hunt"):
            env["mode"] = "deep" if ctx.run_context.current_app_id == "charge_plan" else "weekly"
        return OperationResult(True)

    def save(app, **values):
        description = service.describe_configuration(app)
        return service.save_configuration(app, values, description["revision"])

    def prepare(app):
        manager.prepare_application(app)
        wait(lambda: not manager.busy)
        assert manager.state == "prepared", manager.status()

    def run(app, budget=None):
        manager.start_application(app, fixed_entry=True, budget=budget, timeout_seconds=30)
        if env["pause_next"] and not env["paused"]:
            wait(lambda: env["paused"] or not manager.busy)
            assert env["paused"], manager.status()
            assert manager.status()["budget"]["pending"] == 1
            control.set_manual_pause(True)
            window[0] = WindowState(True, True, window[0].geometry)
            control.poll()
            assert control.status()["state"] == "paused"
            control.set_manual_pause(False)
        wait(lambda: not manager.busy)
        return manager.status()

    def node(operation, name):
        operation._current_node = operation._node_map[name]
        operation.round_start_time = time.time()
        return operation._execute_one_round()

    try:
        options = service.training_options()
        categories = options.get("training", options)
        types = categories["恶名狩猎"]["types"]
        mission_type = next(name for name in types if name != "代理人方案培养")
        missions = types[mission_type]["missions"]
        mission = missions[0]["value"] if missions else None
        plan = dict(tab_name="训练", category_name="恶名狩猎", mission_type_name=mission_type,
                    mission_name=mission, level="默认等级", auto_battle_config="全配队通用", predefined_team_idx=-1,
                    run_times=0, plan_times=3, card_num="默认数量", notorious_hunt_buff_num=1, plan_id=None)
        matches = MatchResultList(only_best=True)
        matches.append(MatchResult(.99, 1, 1, 10, 10))
        with ExitStack() as stack:
            stack.enter_context(patch.object(type(controller), "is_game_window_ready", new_callable=PropertyMock, return_value=True))
            stack.enter_context(patch.object(BackToNormalWorld, "execute", lambda op: OperationResult(True)))
            stack.enter_context(patch.object(TransportByCompendium, "execute", travel))
            stack.enter_context(patch.object(NotoriousHuntMove, "execute", lambda op: OperationResult(True)))
            stack.enter_context(patch.object(ctx.ocr, "crop_and_run_ocr", return_value={mission_type: matches}))
            stack.enter_context(patch.object(ctx.auto_battle_context, "start_auto_battle", ctx.auto_battle_context.init_battle_context))
            stack.enter_context(patch.object(ctx.auto_battle_context, "check_battle_state", battle_check))
            stack.enter_context(patch.object(ChargePlanApp, "round_by_goto_screen", lambda op, **kw: op.round_success()))
            power_matches = MatchResultList(only_best=True)
            power_matches.append(MatchResult(.99, 163, 0, 20, 20))
            stack.enter_context(patch.object(ctx.cv_service, "run_pipeline", return_value=SimpleNamespace(ocr_result={"320/240":power_matches})))
            for cls in (Hunt, NotoriousHuntApp, ChooseNextOrFinishAfterBattle):
                stack.enter_context(patch.object(cls, "round_by_find_area", area_found))
                stack.enter_context(patch.object(cls, "round_by_find_and_click_area", click))
                stack.enter_context(patch.object(cls, "round_by_click_area", click_area))

            save("notorious_hunt", plan_list=[plan], loop=True, weekly_challenge_start_weekday=1)
            prepare("notorious_hunt")
            state = run("notorious_hunt")
            assert state["state"] == "success" and state["hunt"]["free_completed"] == 3, state
            assert "budget" not in state and env["quota"] == 0, state
            assert not any("深度追猎" in str(event) for event in events), events
            checks.append("actual_weekly_app_and_run_slot_complete_three_free_battles_without_energy_ledger_or_deep_switch")

            control.reset()
            env.update(scene="stage", mode="weekly", quota=3)
            with control.scope(): controller.screenshot()
            hunt = Hunt(ctx, ChargePlanItem(**plan), use_charge_power=False)
            hunt._init_before_execute()
            guard = NotoriousHuntControl(ctx, control)
            guard.install()
            try:
                hunt._init_before_execute()
                hunt.run_record.left_times = 3
                hunt.node_retry_times = 10
                with patch.object(ctx.ocr, "run_ocr_single_line", return_value="?"):
                    result = node(hunt, "抉择恶名狩猎")
                assert result.result == OperationRoundResultEnum.RETRY and hunt.run_record.left_times == 3, (result.result, result.status, hunt.run_record.left_times)
                env["mode"] = "deep"
                result = node(hunt, "抉择恶名狩猎")
                assert result.status == Hunt.STATUS_NO_LEFT_TIMES and hunt.run_record.left_times == 0
            finally: guard.close()
            checks.append("unreadable_current_quota_never_falls_back_to_cached_count_and_deep_screen_means_no_free_battles")

            env.update(scene="stage", mode="deep", quota=0, cost=60, pause_next=True, paused=False)
            events.clear()
            save("charge_plan", plan_list=[plan], loop=True, double_reward=False, restore_charge="不使用")
            prepare("charge_plan")
            budget = dict(id="paid-two", allowance=120, battle_limit=2, max_per_battle=60)
            with patch("autozzz.battle_budget.budget_directory", return_value=budget_root):
                state = run("charge_plan", budget)
            assert state["state"] == "success" and state["hunt"]["paid_completed"] == 2, state
            assert state["budget"]["reserved"] == 120 and state["budget"]["pending"] == 0 and state["budget"]["limited"], state
            checks.append("actual_deep_plan_reads_synthetic_ocr_cost_reserves_first_and_repeat_then_stops_at_shared_budget")
            checks.append("deep_reservation_survives_focus_pause_without_duplicate_entry_and_focus_return_preserves_manual_pause")

            control.reset()
            env.update(scene="stage", mode="weekly", quota=3, pause_next=False, paused=False)
            with control.scope(): controller.screenshot()
            ledger = BattleBudget(args.data / "manual-budgets", "weekly-after-paid", "cn", 120, 2)
            charge = ChargeBudgetControl(ctx, control, ledger, 60)
            charge.install()
            guard = NotoriousHuntControl(ctx, control)
            guard.install()
            try:
                assert ctx.run_context.start_running()
                paid = Hunt(ctx, ChargePlanItem(**plan), use_charge_power=True)
                paid._init_before_execute()
                before = len(events)
                result = node(paid, "下一步")
                assert result.result == OperationRoundResultEnum.RETRY and not ledger.pending and len(events) == before
                env["scene"] = "unknown"
                result = node(paid, "下一步")
                assert result.result == OperationRoundResultEnum.RETRY and not ledger.pending and len(events) == before
                env["scene"] = "team"
                result = node(paid, "下一步")
                assert result.is_fail and "出战预算" in control.error and not ledger.pending and len(events) == before
                control.reset()
                env["scene"] = "stage"
                assert ctx.run_context.start_running()
                env.update(mode="deep", cost=40)
                result = node(paid, "下一步")
                assert result.result == OperationRoundResultEnum.RETRY and not ledger.pending and len(events) == before
                env["cost"] = 60
                charge.maximum = 20
                result = node(paid, "下一步")
                assert result.is_fail and "上限" in control.error and not ledger.pending and len(events) == before
                charge.maximum = 60
                control.reset()
                env.update(mode="weekly", quota=3)
                assert ctx.run_context.start_running()
                free = Hunt(ctx, ChargePlanItem(**plan), use_charge_power=False)
                free._init_before_execute()
                node(free, "抉择恶名狩猎")
                env["scene"] = "result"
                ctx.auto_battle_context.last_check_end_result = "普通战斗-完成"
                node(free, "战斗结束")
                node(free, "战斗结束")
                assert guard.status()["free_completed"] == 1, guard.status()
                charge.owner, charge.cost = paid, 60
                result = node(free, "判断下一次")
                assert result.is_success and result.status == "战斗结果-再来一次", result.status
                assert not ledger.data["entries"], ledger.data
                env["scene"] = "battle"
                node(free, "等待战斗画面加载")
                ctx.auto_battle_context.last_check_end_result = "普通战斗-撤退"
                count = free.run_record.left_times
                result = node(free, "战斗结束")
                assert result.is_fail and free.run_record.left_times == count and guard.status()["free_completed"] == 1
            finally:
                ctx.run_context.stop_running()
                guard.close()
                charge.close()
            checks.append("paid_next_requires_enabled_deep_mode_before_reserving_and_free_repeat_does_not_charge_previous_paid_owner")
            checks.append("actual_free_victory_bookkeeping_is_idempotent_for_same_settlement_node")
            checks.append("wrong_ocr_cost_and_per_battle_limit_do_not_reserve_or_click_and_unverified_result_does_not_update_free_count")
            checks.append("unknown_stage_does_not_delegate_click_and_deployment_without_reservation_is_rejected")

            env.update(scene="world", mode="weekly", quota=3)
            events.clear()
            save("notorious_hunt", plan_list=[plan], loop=False)
            config = ctx.run_context.get_config("notorious_hunt", ctx.current_instance_idx, "one_dragon")
            for item in config.plan_list: item.run_times = item.plan_times
            config.save()
            prepare("notorious_hunt")
            state = run("notorious_hunt")
            assert state["state"] == "success" and env["scene"] == "stage", state
            assert ("恶名狩猎", "奖励入口") in events
            checks.append("completed_plan_transports_to_hunt_board_before_clicking_fixed_reward_entry")

            env.update(scene="world", quota=3, mode="weekly", cost=60, pause_next=False, paused=False)
            save("notorious_hunt", plan_list=[plan], loop=True)
            save("charge_plan", plan_list=[plan], loop=True, double_reward=False, restore_charge="不使用")
            ctx.run_context.get_run_record("notorious_hunt", ctx.current_instance_idx).reset_record()
            description = service.describe_configuration("_group")
            rows = next(field["value"] for field in description["fields"] if field["name"] == "app_list")
            for row in rows: row["enabled"] = row["app_id"] in {"notorious_hunt", "charge_plan"}
            rows.sort(key=lambda row: 0 if row["app_id"]=="notorious_hunt" else 1 if row["app_id"]=="charge_plan" else 2)
            save("_group", app_list=rows)
            prepare("one_dragon")
            budget["id"] = "group-free-and-paid"
            with patch("autozzz.battle_budget.budget_directory", return_value=budget_root):
                state = run("one_dragon", budget)
            assert state["state"] == "success", state
            assert state["hunt"]["free_completed"] == 3 and state["hunt"]["paid_completed"] == 2, state
            assert state["budget"]["reserved"] == 120 and state["budget"]["pending"] == 0, state
            assert all(item["state"] == "success" for item in state["applications"]), state
            checks.append("actual_one_dragon_group_combines_three_free_and_two_paid_battles_with_only_paid_entries_in_shared_budget")
            assert "after_operation_done" not in vars(NotoriousHuntApp) and "after_operation_done" not in vars(ChargePlanApp)
            checks.append("inherited_application_callbacks_are_restored_without_retaining_previous_group_instance")

            env.update(scene="stage", quota=3, mode="weekly", fail=True)
            save("notorious_hunt", plan_list=[plan], loop=False)
            prepare("notorious_hunt")
            state = run("notorious_hunt")
            assert state["state"] == "failed" and "计划未完成" in state["error"], state
            assert state["hunt"]["free_completed"] == 0 and len(state["hunt"]["failures"]) == 1, state
            checks.append("failed_hunt_child_is_not_reported_as_success_when_parent_skips_plan_and_finishes_reward_flow")
        print(json.dumps(dict(checks=checks, synthetic_images=True, game_started=False, hardware_input_sent=False,
                              budget_directory=str(budget_root)), ensure_ascii=False), flush=True)
    finally:
        service.close()


if __name__ == "__main__": main()
