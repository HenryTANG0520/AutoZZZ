"""真实上游工具、指令和 RunSlot 回放；键鼠设备全部替换为记录器。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def wait(predicate, timeout: float = 6) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("工具回放超时")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    marker = args.data / ".autozzz-tools-flow-test"
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file():
        raise SystemExit("请使用空目录或本工具已有的测试目录。")
    args.data.mkdir(parents=True, exist_ok=True)
    marker.write_text("isolated-test-profile", encoding="utf-8")
    from autozzz.onedragon_runtime import RuntimePaths
    defaults = RuntimePaths.discover(root)
    RuntimePaths(defaults.source, defaults.python, args.data.resolve()).prepare()
    sys.path.insert(0, str(defaults.source / "src"))
    import numpy as np
    import yaml
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_execution import OneDragonExecution
    from autozzz.onedragon_control import ObservationExpired, WindowState
    from autozzz.onedragon_host import ManagedController
    from autozzz.onedragon_scripts import compile_key_sim, compile_operation_template
    from zzz_od.application.commission_assistant.commission_assistant_app import CommissionAssistantApp
    from zzz_od.application.redemption_code.redemption_code_app import RedemptionCodeApp
    from one_dragon.base.operation.operation_base import OperationResult
    from one_dragon.base.operation.operation_round_result import OperationRoundResultEnum
    from zzz_od.operation.key_sim_runner import KeySimRunner

    service = OneDragonService(defaults.source, args.data.resolve())
    ctx = service.ctx
    manager = OneDragonExecution(ctx)
    service._execution = manager
    control = manager.control
    controller = ManagedController(ctx, control)
    ctx.controller = controller
    ctx.ready_for_application = True
    window = [WindowState(True, True, (0, 0, 1920, 1080))]
    events, loads, checks = [], [], []

    class Recorder:
        position = (0, 0)
        def press(self, key): events.append(("down", str(key)))
        def release(self, key): events.append(("up", str(key)))
        def type(self, text): events.append(("text", text))
        def move(self, dx, dy): events.append(("move", dx, dy))
        def scroll(self, dx, dy): events.append(("scroll", dx, dy))

    controller.keyboard_controller.keyboard.raw = Recorder()
    controller.keyboard_controller.mouse.raw = Recorder()
    controller.bind = lambda _: None
    controller.probe = lambda: window[0]
    controller.init_before_context_run = lambda: True
    controller.active_window = lambda: None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    controller.screenshot = lambda independent=False: control.observe(lambda: (time.time(), image))
    manager._watch = lambda stopped: stopped.wait()

    def fresh():
        control.poll()
        with control.scope(): controller.screenshot()

    def start(app_id):
        manager.script_control.reset()
        manager._submit("running", lambda: manager._run_bound_app(app_id))
        wait(lambda: manager.busy or manager.state == "failed")

    def stop():
        manager.stop()
        wait(lambda: not manager.busy)
        assert not controller.keyboard_controller.keyboard.pressed
        assert not controller.keyboard_controller.mouse.pressed

    name = "AutoZZZ-工具指令回放"
    template = args.data / "config/auto_battle_operation" / (name + ".yml")
    config = ctx.run_context.get_config("operation_debug", ctx.current_instance_idx, "one_dragon")

    def script(operations, repeat=False):
        template.write_text(yaml.safe_dump(dict(operations=operations), allow_unicode=True), encoding="utf-8")
        config.operation_template = name
        config.repeat_enabled = repeat

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(type(controller), "is_game_window_ready", new_callable=PropertyMock, return_value=True))
            stack.enter_context(patch.object(type(controller.game_win), "is_win_active", new_callable=PropertyMock, side_effect=lambda: window[0].focused))
            script([
                dict(op_name="按键-普通攻击"),
                dict(op_name="按键-移动-前-按下"),
                dict(op_name="等待秒数", seconds=.6),
                dict(op_name="按键-移动-前-松开"),
                dict(op_name="按键-特殊攻击", way="按下", press=.05),
            ])
            start("operation_debug")
            wait(lambda: manager.script_control.progress.get("completed") == 2)
            window[0] = WindowState(True, False, window[0].geometry)
            control.poll()
            wait(lambda: control.status()["state"] == "focus_wait")
            assert not controller.keyboard_controller.keyboard.pressed, events
            assert manager.script_control.progress["completed"] == 2
            control.set_manual_pause(True)
            window[0] = WindowState(True, True, window[0].geometry)
            control.poll()
            assert control.status()["state"] == "paused"
            unchanged = list(events)
            time.sleep(.08)
            assert events == unchanged
            control.set_manual_pause(False)
            wait(lambda: not manager.busy)
            assert manager.state == "success", manager.status()
            assert sum(kind == "down" and "Button.left" in key for kind, key, *rest in events) == 1, events
            assert sum(event == ("down", "'w'") for event in events) == 2, events
            assert manager.script_control.progress["completed"] == 5
            assert not controller.keyboard_controller.keyboard.pressed
            checks.append("actual_debug_run_slot_resumes_current_step_and_restores_legacy_hold_without_replaying_attack")

            for operations in ([dict(op_name="等待秒数", seconds=10)],
                               [dict(op_name="按键-普通攻击", pre_delay=10)],
                               [dict(op_name="按键-普通攻击", post_delay=10)]):
                script(operations)
                start("operation_debug")
                wait(lambda: ctx.run_context.is_context_running)
                time.sleep(.12)
                before = time.monotonic()
                stop()
                assert time.monotonic() - before < 1.5, manager.status()
                assert manager.state == "stopped", manager.status()
            checks.append("actual_wait_and_pre_post_delays_stop_without_waiting_for_ten_second_timeout")

            script([dict(op_name="按键-普通攻击", post_delay=.03)], repeat=True)
            events.clear()
            start("operation_debug")
            wait(lambda: sum(event[0] == "down" for event in events) >= 3)
            stop()
            checks.append("actual_debug_repeat_runs_until_stopped_and_releases_owned_keys")

            ops = compile_key_sim(ctx, "真拿命验收", "life_on_line")
            assert len(ops) == 14
            for op in ops: op.dispose()
            script([dict(operation_template="安比-3A特殊攻击")])
            ops = compile_operation_template(ctx, name)
            assert len(ops) == 12
            for op in ops: op.dispose()
            script([dict(op_name="等待秒数", seconds=-1)])
            try: compile_operation_template(ctx, name)
            except ValueError: pass
            else: raise AssertionError("非法指令时间未被拒绝")
            script([dict(op_name="按键-移动-前-按下", data=["-1"])])
            try: compile_operation_template(ctx, name)
            except ValueError: pass
            else: raise AssertionError("旧指令 data 中的非法时间未被拒绝")
            checks.append("actual_template_expansion_life_script_and_invalid_duration_validation")

            keys_name = "AutoZZZ-工具按键回放"
            keys_file = args.data / "config/key_sim" / (keys_name + ".yml")
            keys_file.write_text(yaml.safe_dump(dict(operations=[
                dict(op_name="按键-普通攻击"), dict(op_name="等待秒数", seconds=.01),
                dict(op_name="按键-特殊攻击", way="按下", press=.02),
            ]), allow_unicode=True), encoding="utf-8")
            control.reset()
            fresh()
            assert ctx.run_context.start_running()
            result = KeySimRunner(ctx, keys_name).execute()
            assert result.success and manager.script_control.progress == dict(name=keys_name, completed=3, total=3)
            ctx.run_context.stop_running()
            checks.append("actual_key_sim_operation_graph_loads_and_plays_user_yaml")

            control.reset()
            fresh()
            assert ctx.run_context.start_running()
            assistant = CommissionAssistantApp(ctx)
            assistant.handle_init()
            # 保留真实模式切换/节点方法；识别和设备操作用记录器替代。
            stack.enter_context(patch.object(ctx.auto_battle_context, "init_auto_op", side_effect=lambda **kw: loads.append(kw)))
            stack.enter_context(patch.object(ctx.auto_battle_context, "start_auto_battle"))
            stack.enter_context(patch.object(ctx.auto_battle_context, "check_battle_state"))
            mode = manager.assistant_control
            mode.set_mode(1)
            fresh()
            with control.scope(): assistant.auto_mode()
            mode.set_mode(2)
            fresh()
            with control.scope(): assistant.auto_mode()
            assert [item["sub_dir"] for item in loads] == ["dodge", "auto_battle"], loads
            checks.append("actual_assistant_direct_dodge_to_battle_reloads_matching_policy")

            mode.set_mode(0)
            fresh()
            mode.poll_keys(lambda vk: 0)
            mode.poll_keys(lambda vk: 0x8000 if vk == ord("5") else 0)
            assert assistant.run_mode == 1
            fresh()
            mode.poll_keys(lambda vk: 0x8000 if vk == ord("5") else 0)
            assert assistant.run_mode == 1
            mode.poll_keys(lambda vk: 0)
            fresh()
            mode.poll_keys(lambda vk: 0x8000 if vk == ord("5") else 0)
            assert assistant.run_mode == 0
            control.set_manual_pause(True)
            mode.poll_keys(lambda vk: 0x8000 if vk == ord("6") else 0)
            mode.set_mode(2)
            assert control.status()["state"] == "paused" and assistant.run_mode == 2
            control.set_manual_pause(False)
            fresh()
            mode.poll_keys(lambda vk: 0x8000 if vk == ord("6") else 0)
            assert assistant.run_mode == 2
            checks.append("actual_assistant_hotkey_edges_and_ui_mode_change_preserve_manual_pause")
            assistant.after_operation_done(OperationResult(True))
            assert mode.status()["mode"] is None
            ctx.run_context.stop_running()
            try: manager.set_assistant_mode(0, "obsolete-job")
            except RuntimeError: pass
            else: raise AssertionError("旧任务模式请求未拒绝")
            checks.append("assistant_completion_clears_mode_and_obsolete_job_is_rejected")

            control.reset()
            fresh()
            redemption = RedemptionCodeApp(ctx)
            redemption.handle_init()
            redemption.unused_code_list = ["AUTOZZZ-OFFLINE-TEST"]
            clicks = []
            redemption.round_by_find_area = lambda *a, **kw: redemption.round_success("兑换码兑换")
            redemption.round_by_click_area = lambda *a, **kw: redemption.round_success()
            def submit(*args, **kwargs):
                control.input(lambda: clicks.append("submit"))
                return redemption.round_success("兑换码兑换")
            redemption.round_by_find_and_click_area = submit
            with control.scope(): assert redemption.input_code().result == OperationRoundResultEnum.WAIT
            raw = controller.keyboard_controller.keyboard.raw
            original_type = raw.type
            def interrupted_type(text):
                original_type(text)
                window[0] = WindowState(True, False, window[0].geometry)
                control.poll()
            raw.type = interrupted_type
            try:
                with control.scope(): redemption.input_code()
            except ObservationExpired: pass
            else: raise AssertionError("失焦后仍完成输入")
            raw.type = original_type
            window[0] = WindowState(True, True, window[0].geometry)
            control.poll()
            fresh()
            events.clear()
            with control.scope(): redemption.input_code()
            fresh()
            with control.scope(): redemption.input_code()
            assert "".join(event[1] for event in events if event[0] == "text") == "AUTOZZZ-OFFLINE-TEST", events
            assert ("down", "Key.ctrl") in events and ("down", "'a'") in events, events
            fresh()
            with control.scope(): redemption.input_code()
            assert clicks == ["submit"]
            control.set_manual_pause(True)
            control.set_manual_pause(False)
            fresh()
            with control.scope(): redemption.input_code()
            assert clicks == ["submit"]
            assert manager.redemption_control.attempts == [dict(code="AUTOZZZ-OFFLINE-TEST", status="submitted_unverified", reward_verified=False)]
            checks.append("redemption_partial_input_is_replaced_and_submitted_code_is_not_replayed_after_focus_change")
            checks.append("redemption_without_reward_evidence_remains_submitted_unverified")
        print(json.dumps(dict(checks=checks, game_started=False, hardware_input_sent=False), ensure_ascii=False), flush=True)
    finally:
        service.close()


if __name__ == "__main__":
    main()
