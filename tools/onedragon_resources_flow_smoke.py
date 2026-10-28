"""真实经营/拆解节点与 RunSlot 回放；识别及设备输入均由记录器替代。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def wait(predicate, timeout: float = 15) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate(): return
        time.sleep(.01)
    raise AssertionError("资源流程回放超时")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    marker = args.data / ".autozzz-resources-flow-test"
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file():
        raise SystemExit("请使用空目录或本工具已有的测试目录。")
    args.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("isolated-test-profile", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    from autozzz.onedragon_runtime import RuntimePaths
    from autozzz.config import GAME_PATHS
    defaults = RuntimePaths.discover(root)
    RuntimePaths(defaults.source, defaults.python, args.data.resolve()).prepare()
    cache = root / "runs/onedragon-20261029/resource-preparation/assets/models"
    if cache.is_dir(): shutil.copytree(cache, args.data / "assets/models", dirs_exist_ok=True)
    sys.path.insert(0, str(defaults.source / "src"))
    import numpy as np
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_control import WindowState
    from one_dragon.base.operation.operation_base import OperationResult
    from zzz_od.application.drive_disc_dismantle.drive_disc_dismantle_app import DriveDiscDismantleApp as Disc
    from zzz_od.application.suibian_temple import suibian_temple_app as temple
    from zzz_od.operation.back_to_normal_world import BackToNormalWorld

    service = OneDragonService(defaults.source, args.data.resolve())
    service.initialize_execution("cn", GAME_PATHS["cn"])
    manager = service._execution
    wait(lambda: not manager.busy)
    assert manager.state == "ready", manager.status()
    ctx, control = service.ctx, manager.control
    controller = ctx.controller
    window = [WindowState(True, True, (0, 0, 1920, 1080))]
    events, children, checks = [], [], []
    scenario = dict(fail=None, focus=None, focused=False, reward=True, changed=False)

    class Recorder:
        position = (0, 0)
        def press(self, key): events.append(("down", str(key)))
        def release(self, key): events.append(("up", str(key)))
        def type(self, text): events.append(("type", text))

    controller.keyboard_controller.keyboard.raw = Recorder()
    controller.keyboard_controller.mouse.raw = Recorder()
    bindings = []
    controller.bind = lambda path: bindings.append(path)
    controller.probe = lambda: window[0]
    controller.init_before_context_run = lambda: True
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    controller.screenshot = lambda independent=False: control.observe(lambda: (time.time(), image))

    def watch(stopped):
        while not stopped.wait(.01): control.poll()
    manager._watch = watch

    def save(app, **values):
        description = service.describe_configuration(app)
        return service.save_configuration(app, values, description["revision"])

    def prepare(app):
        manager.prepare_application(app)
        wait(lambda: not manager.busy)
        assert manager.state == "prepared", manager.status()
        return manager.resource_confirmation.token or None

    def reset():
        events.clear()
        children.clear()
        scenario.update(fail=None, focus=None, focused=False, reward=True, changed=False)

    def run(app, token):
        manager.start_application(app, fixed_entry=True, resource_confirmation=token, timeout_seconds=30)
        if scenario["focus"]:
            wait(lambda: scenario["focused"] or not manager.busy)
            assert scenario["focused"], manager.status()
            control.set_manual_pause(True)
            window[0] = WindowState(True, True, window[0].geometry)
            control.poll()
            assert control.status()["state"] == "paused"
            control.set_manual_pause(False)
        wait(lambda: not manager.busy)
        return manager.status()

    def click(operation, screen, screen_name, area_name, **kwargs):
        if area_name == scenario["fail"] or (screen_name == "道具处理-获得" and not scenario["reward"]):
            return operation.round_fail("模拟未识别：" + area_name)
        control.input(lambda: events.append((screen_name, area_name)))
        if area_name == scenario["focus"] and not scenario["focused"]:
            scenario["focused"] = True
            window[0] = WindowState(True, False, window[0].geometry)
            control.poll()
        return operation.round_success(area_name)

    def reset_filter(operation, screen, text, **kwargs):
        assert text == "重置"
        rect = kwargs["area"].rect
        assert (rect.x1, rect.y1, rect.x2, rect.y2) == (642, 466, 1270, 802), rect
        if scenario["fail"] == "重置": return operation.round_fail("模拟未识别重置")
        control.input(lambda: events.append(("filter", text)))
        return operation.round_success(text)

    def child(operation):
        children.append(type(operation).__name__)
        return OperationResult(True)

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(type(controller), "is_game_window_ready", new_callable=PropertyMock, return_value=True))
            stack.enter_context(patch.object(BackToNormalWorld, "execute", return_value=OperationResult(True)))
            stack.enter_context(patch.object(Disc, "round_by_goto_screen", lambda op, **kw: op.round_success()))
            stack.enter_context(patch.object(Disc, "round_by_find_area", lambda op, *a, **kw: op.round_success()))
            stack.enter_context(patch.object(Disc, "round_by_find_and_click_area", click))
            stack.enter_context(patch.object(Disc, "round_by_ocr_and_click", reset_filter))
            stack.enter_context(patch.object(temple.SuibianTempleApp, "check_screen_with_can_go", lambda op, *a, **kw: ("随便观-入口", None)))
            for cls in (temple.SuibianTempleAutoManage, temple.SuibianTempleAdventureSquad, temple.SuibianTempleYumChaSin,
                        temple.SuibianTempleCraft, temple.SuibianTempleSalesStall, temple.SuibianTempleGoodGoods, temple.SuibianTempleBooBox):
                stack.enter_context(patch.object(cls, "execute", child))

            save("drive_disc_dismantle", dismantle_level="A及以下", dismantle_abandon=True)
            token = prepare("drive_disc_dismantle")
            try: manager.start_application("drive_disc_dismantle", resource_confirmation=None)
            except ValueError: pass
            else: raise AssertionError("未确认就启动拆解")
            assert not bindings
            checks.append("actual_execution_rejects_missing_confirmation_before_window_binding")

            reset()
            state = run("drive_disc_dismantle", token)
            assert state["state"] == "success", state
            assert state["resources"]["confirmed"] == ["drive_disc_dismantle:dismantle"], state
            assert state["resource_results"] == [dict(app_id="drive_disc_dismantle", state="reward_verified", reward_verified=True)], state
            assert events.index(("filter", "重置")) < events.index(("仓库-驱动仓库-驱动盘拆解", "按钮-A及以下"))
            try: manager.start_application("drive_disc_dismantle", resource_confirmation=token)
            except ValueError: pass
            else: raise AssertionError("运行后旧确认仍可复用")
            checks.append("actual_disc_nodes_reset_filter_apply_scope_verify_reward_and_consume_confirmation_once")

            for missing in ("重置", "按钮-A及以下", "按钮-全选已弃置"):
                reset()
                scenario["fail"] = missing
                state = run("drive_disc_dismantle", prepare("drive_disc_dismantle"))
                assert state["state"] == "failed", state
                assert not any(area in ("按钮-快速选择-确认", "按钮-拆解", "按钮-拆解-确认") for screen, area in events), events
            checks.append("actual_disc_graph_has_no_failure_fallback_from_quality_or_discard_selection_to_dismantle")

            for target in ("按钮-A及以下", "按钮-全选已弃置"):
                reset()
                scenario["focus"] = target
                state = run("drive_disc_dismantle", prepare("drive_disc_dismantle"))
                assert state["state"] == "success", state
                assert events.count(("filter", "重置")) == 2, events
                assert events.count(("仓库-驱动仓库-驱动盘拆解", "按钮-拆解-确认")) == 1, events
            checks.append("actual_disc_focus_during_selection_restarts_from_reset_instead_of_reusing_partial_filter")

            for target in ("按钮-快速选择-确认", "按钮-拆解-确认"):
                reset()
                scenario["focus"] = target
                state = run("drive_disc_dismantle", prepare("drive_disc_dismantle"))
                assert state["state"] == "success", state
                assert events.count(("仓库-驱动仓库-驱动盘拆解", target)) == 1, events
            checks.append("actual_disc_filter_and_irreversible_confirmation_are_not_clicked_twice_after_focus_change")

            reset()
            scenario["reward"] = False
            state = run("drive_disc_dismantle", prepare("drive_disc_dismantle"))
            assert state["state"] == "success" and state["resource_results"][0]["state"] == "submitted_unverified", state
            checks.append("disc_flow_completion_without_obtained_evidence_does_not_claim_reward_verified")

            reset()
            save("suibian_temple", auto_manage_enabled=True, good_goods_purchase_enabled=False, boo_box_purchase_enabled=False)
            token = prepare("suibian_temple")
            assert token is None
            state = run("suibian_temple", token)
            assert state["state"] == "success" and children == ["SuibianTempleAutoManage"], (state, children)
            checks.append("actual_temple_hosting_branch_skips_manual_consumption_and_disabled_purchases")

            reset()
            save("suibian_temple", auto_manage_enabled=False, yum_cha_sin=True, good_goods_purchase_enabled=True, boo_box_purchase_enabled=True)
            state = run("suibian_temple", prepare("suibian_temple"))
            assert state["state"] == "success", state
            assert children == ["SuibianTempleAdventureSquad", "SuibianTempleYumChaSin", "SuibianTempleAdventureSquad",
                                "SuibianTempleCraft", "SuibianTempleSalesStall", "SuibianTempleGoodGoods", "SuibianTempleBooBox"], children
            assert len(state["resources"]["confirmed"]) == 4, state
            checks.append("actual_temple_manual_branch_delegates_all_enabled_operations_in_source_order")

            reset()
            token = prepare("suibian_temple")
            config = ctx.run_context.get_config("suibian_temple", ctx.current_instance_idx, "one_dragon")
            def changed_screen(operation, *args, **kwargs):
                config.good_goods_purchase_enabled = False
                return "随便观-入口", None
            with patch.object(temple.SuibianTempleApp, "check_screen_with_can_go", changed_screen):
                state = run("suibian_temple", token)
            assert state["state"] == "failed" and not children, (state, children)
            checks.append("resource_nodes_reject_runtime_settings_changes_before_delegating_consumption")

            reset()
            save("suibian_temple", auto_manage_enabled=False, good_goods_purchase_enabled=True)
            token = prepare("suibian_temple")
            config = ctx.run_context.get_config("suibian_temple", ctx.current_instance_idx, "one_dragon")
            def changed_inside_child(operation):
                config.good_goods_purchase_enabled = False
                control.input(lambda: events.append(("unconfirmed", "consumer-input")))
                return OperationResult(True)
            with patch.object(temple.SuibianTempleCraft, "execute", changed_inside_child):
                state = run("suibian_temple", token)
            assert state["state"] == "failed" and ("unconfirmed", "consumer-input") not in events, (state, events)
            assert control.input_validator == manager.battle_control.check_input
            checks.append("nested_consumer_input_rechecks_resource_file_and_restores_previous_battle_validator_after_exit")

            reset()
            token = prepare("drive_disc_dismantle")
            previous = len(bindings)
            save("drive_disc_dismantle", dismantle_level="S及以下")
            state = run("drive_disc_dismantle", token)
            assert state["state"] == "failed" and len(bindings) == previous and not events, state
            checks.append("changed_prepared_file_is_rejected_before_binding_or_any_game_action")

            reset()
            save("suibian_temple", auto_manage_enabled=False, good_goods_purchase_enabled=True, boo_box_purchase_enabled=True)
            description = service.describe_configuration("_group")
            rows = next(field["value"] for field in description["fields"] if field["name"] == "app_list")
            for row in rows: row["enabled"] = row["app_id"] in {"suibian_temple", "drive_disc_dismantle"}
            save("_group", app_list=rows)
            for app in ("suibian_temple", "drive_disc_dismantle"):
                ctx.run_context.get_run_record(app, ctx.current_instance_idx).reset_record()
            state = run("one_dragon", prepare("one_dragon"))
            assert state["state"] == "success", state
            assert len(state["resources"]["confirmed"]) == 5, state
            assert all(item["state"] == "success" for item in state["applications"]), state
            assert state["resource_results"][0]["reward_verified"], state
            checks.append("actual_one_dragon_group_runs_both_resource_applications_under_one_confirmed_snapshot")
        print(json.dumps(dict(checks=checks, game_started=False, hardware_input_sent=False, resources_consumed=False), ensure_ascii=False), flush=True)
    finally:
        service.close()


if __name__ == "__main__": main()
