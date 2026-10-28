"""真实上游 Operation/线程池/RunSlot 的控制回放，输入设备全部替换为记录器。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def wait(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("控制回放未在时限内结束")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "vendor" / "onedragon" / "src"))
    import numpy as np
    from one_dragon.base.operation.operation import Operation
    from one_dragon.base.operation.operation_node import operation_node
    from one_dragon.base.operation.application.application_factory import ApplicationFactory
    from one_dragon.base.conditional_operation.state_recorder import StateRecord
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_execution import OneDragonExecution
    from autozzz.onedragon_control import WindowState, ObservationExpired
    from autozzz.onedragon_host import ControlledKeyboard, ControlledDevice, RelativeMouse
    from autozzz.run_lock import RunLock

    service = OneDragonService(root / "vendor" / "onedragon", args.data)
    ctx = service.ctx
    manager = OneDragonExecution(ctx)
    service._execution = manager
    control = manager.control
    window = [WindowState(True, True, (0, 0, 1920, 1080))]
    events, clicks = [], []
    entered, finish = threading.Event(), threading.Event()
    checks = []

    class Recorder:
        def press(self, key): events.append(("down", str(key)))
        def release(self, key): events.append(("up", str(key)))

    keys = ControlledKeyboard(control)
    keys.keyboard.raw = Recorder()
    keys.mouse.raw = Recorder()

    class Controller:
        is_game_window_ready = True
        keyboard_controller = keys
        image = 0
        def probe(self): return window[0]
        def bind(self, _path): pass
        def init_before_context_run(self): return True
        def screenshot(self):
            def capture():
                self.image += 1
                return time.time(), np.full((1080, 1920, 3), self.image, dtype=np.uint8)
            return control.observe(capture)
        def click(self): control.input(lambda: clicks.append(self.image))

    controller = Controller()
    ctx.controller = controller
    ctx.ready_for_application = True
    control.poll()

    class RetryWithoutOldClick(Operation):
        attempts = 0
        @operation_node(name="识别后失焦", is_start_node=True, node_max_retry_times=0)
        def step(self):
            self.attempts += 1
            if self.attempts == 1:
                window[0] = WindowState(True, False, window[0].geometry)
                control.poll()
                window[0] = WindowState(True, True, window[0].geometry)
                control.poll()
            controller.click()
            return self.round_success()

    class BlockedOperation(Operation):
        @operation_node(name="等待测试放行", is_start_node=True)
        def step(self):
            entered.set()
            finish.wait(5)
            return self.round_wait(wait=.01)

    class Factory(ApplicationFactory):
        def __init__(self):
            super().__init__(SimpleNamespace(APP_ID="autozzz_fixture", APP_NAME="控制回放", DEFAULT_GROUP=False, NEED_NOTIFY=False))
            self.ctx = ctx
        def create_application(self, instance_idx, group_id):
            return BlockedOperation(ctx, op_name="停止回放")

    try:
        assert ctx.run_context.start_running()
        operation = RetryWithoutOldClick(ctx, op_name="焦点回放")
        result = operation.execute()
        assert result.success and operation.attempts == 2 and operation.node_retry_times == 0
        assert len(clicks) == 1 and clicks[0] == controller.image
        ctx.run_context.stop_running()
        checks.append("actual_operation_discards_old_frame_without_spending_retry")

        # 一个识别任务及其 Future 回调都必须保留旧代次。
        control.reset()
        with control.scope():
            controller.screenshot()
        release_inference = threading.Event()
        callback_done = threading.Event()
        recorders = ctx.auto_battle_context.state_record_service
        stale_name = "自定义-AutoZZZ旧识别"
        callback_name = "自定义-AutoZZZ旧回调"
        with ThreadPoolExecutor(max_workers=1) as executor, control.scope():
            def infer():
                release_inference.wait(3)
                recorders.batch_update_states([StateRecord(stale_name, time.time())])
                try:
                    controller.click()
                except ObservationExpired:
                    return "discarded"
                raise AssertionError("旧识别任务发送了输入")
            future = executor.submit(infer)
            def callback(_future):
                recorders.batch_update_states([StateRecord(callback_name, time.time())])
                callback_done.set()
            future.add_done_callback(callback)
            control.set_manual_pause(True)
            control.set_manual_pause(False)
            with control.scope():
                controller.screenshot()
            release_inference.set()
            assert future.result(3) == "discarded" and callback_done.wait(3)
        assert recorders.get_state_recorder(stale_name).last_record_time == -1
        assert recorders.get_state_recorder(callback_name).last_record_time == -1
        checks.append("actual_executor_and_future_callback_discard_stale_state_and_input")

        events.clear()
        control.reset()
        with control.scope(): controller.screenshot()
        held = threading.Event()
        def hold():
            with control.scope():
                held.set()
                keys.press("e", 30)
        thread = threading.Thread(target=hold)
        thread.start()
        assert held.wait(1)
        wait(lambda: any(event[0] == "down" for event in events))
        window[0] = WindowState(True, False, window[0].geometry)
        control.poll()
        thread.join(1)
        assert not thread.is_alive() and [event[0] for event in events] == ["down", "up"], events
        assert not keys.keyboard.pressed and not keys._pressed_keys
        window[0] = WindowState(True, True, window[0].geometry)
        control.poll()
        checks.append("actual_finite_key_hold_releases_immediately_on_focus_loss")

        # 真正的条件执行器和原子操作线程要同时满足新观察与战斗画面判断。
        from one_dragon.base.conditional_operation.atomic_op import AtomicOp
        from one_dragon.base.conditional_operation.operation_executor import OperationExecutor
        class RecordedAttack(AtomicOp):
            def __init__(self, duration=None):
                super().__init__("测试普攻")
                self.duration=duration
            def execute(self):
                if self.duration is None:controller.click()
                else:keys.press("e",self.duration)
        battle_control=manager.battle_control
        control.reset()
        with control.scope():controller.screenshot()
        before=len(clicks)
        with control.scope():assert not OperationExecutor([RecordedAttack()],time.time()).run_async().result(3)
        assert len(clicks)==before
        battle_control.allowed=True
        battle_control.allowed_epoch=control.epoch
        with control.scope():assert OperationExecutor([RecordedAttack()],time.time()).run_async().result(3)
        assert len(clicks)==before+1
        old_epoch=control.epoch
        control.set_manual_pause(True)
        control.set_manual_pause(False)
        with control.scope():controller.screenshot()
        with control.scope():assert not OperationExecutor([RecordedAttack()],time.time()).run_async().result(3)
        assert len(clicks)==before+1, "普通新截图不能代替恢复后的战斗识别"
        battle_control.allowed_epoch=control.epoch
        events.clear()
        with control.scope():future=OperationExecutor([RecordedAttack(30)],time.time()).run_async()
        wait(lambda: any(event[0]=="down" for event in events))
        with control.lock:battle_control.allowed=False
        assert future.result(1)
        assert [event[0] for event in events]==["down","up"],events
        assert not keys.keyboard.pressed and not keys._pressed_keys
        checks.append("actual_conditional_executor_requires_current_battle_classification_and_releases_hold_on_result")

        battle=ctx.auto_battle_context
        battle.last_check_end_result=None
        battle.last_check_in_battle=False
        with control.scope(old_epoch):
            battle.last_check_end_result="普通战斗-完成"
            battle.last_check_in_battle=True
        assert battle.last_check_end_result is None and not battle.last_check_in_battle
        with control.scope():
            battle.last_check_end_result="普通战斗-完成"
            battle.last_check_in_battle=True
        assert battle.last_check_end_result=="普通战斗-完成" and battle.last_check_in_battle
        battle.last_check_end_result=None
        battle.last_check_in_battle=False
        checks.append("raw_async_battle_result_and_hud_state_cannot_cross_pause_epoch")

        # 截获 Win32 结构体，核实相机转向是相对鼠标位移；不发送系统输入。
        from autozzz import windows
        packets=[]
        def record_packet(count,pointer,size):
            event=windows.C.cast(pointer,windows.C.POINTER(windows.INPUT)).contents
            packets.append((event.type,event.mi.dx,event.mi.dy,event.mi.dwFlags))
            assert count==1 and size==windows.C.sizeof(windows.INPUT)
            return 1
        relative=ControlledDevice(RelativeMouse(SimpleNamespace()),control)
        with patch.object(windows.user32,"SendInput",side_effect=record_packet):
            with control.scope():relative.move(17,-23)
            assert packets==[(0,17,-23,1)],packets
            window[0]=WindowState(True,False,window[0].geometry)
            try:
                with control.scope():relative.move(10,0)
                raise AssertionError("失焦时仍提交了相机位移")
            except ObservationExpired:pass
            assert len(packets)==1
            for dx in (float("nan"),float("inf"),2**31):
                try:
                    relative.raw.move(dx,0)
                    raise AssertionError("无效相对位移未被拒绝")
                except ValueError:pass
        window[0]=WindowState(True,True,window[0].geometry)
        control.poll()
        checks.append("relative_camera_packet_and_focus_guard_without_windows_input")

        ctx.run_context.registry_application([Factory()])
        name = "Local\\AutoZZZ.ControlSmoke." + uuid.uuid4().hex
        original_lock = RunLock
        def watcher(stop):
            while not stop.is_set():
                control.poll()
                stop.wait(.01)
        manager._watch = watcher
        manager.prepared_app="autozzz_fixture"
        manager.prepared_hash=manager.configuration_digest()
        manager.resource_confirmation.prepare([], manager.prepared_hash)
        with patch("autozzz.onedragon_execution.RunLock", side_effect=lambda: original_lock(name)):
            started = manager.start_application("autozzz_fixture")
            assert entered.wait(3), manager.status()
            manager.pause(True)
            window[0] = WindowState(True, False, window[0].geometry)
            control.poll()
            window[0] = WindowState(True, True, window[0].geometry)
            control.poll()
            assert ctx.run_context.is_context_pause
            assert manager.status()["control"]["manual_paused"]
            stopped = manager.stop()
            assert stopped["busy"] and stopped["state"] == "stopping" and stopped["job_id"] == started["job_id"]
            try:
                with original_lock(name):
                    raise AssertionError("工作线程仍活动时提前释放了互斥量")
            except RuntimeError:
                pass
            finish.set()
            wait(lambda: not manager.busy)
            assert manager.status()["state"] == "stopped", manager.status()
            with original_lock(name): pass
        checks.append("actual_run_slot_keeps_same_job_and_mutex_until_worker_exits")

        # 上游暂停回调失败时也必须进入 STOP，不能永久卡在 PAUSE。
        from one_dragon.base.operation.application.application_run_context import ApplicationRunContextStateEventEnum
        class BrokenListener:
            def pause(self, _event):
                raise RuntimeError("回放暂停回调失败")
        broken_pause = BrokenListener().pause
        ctx.run_context.event_bus.listen_event(ApplicationRunContextStateEventEnum.PAUSE, broken_pause)
        entered.clear()
        finish.clear()
        try:
            manager.prepared_hash=manager.configuration_digest()
            manager.resource_confirmation.prepare([], manager.prepared_hash)
            with patch("autozzz.onedragon_execution.RunLock", side_effect=lambda: original_lock(name)):
                manager.start_application("autozzz_fixture")
                assert entered.wait(3)
                try:
                    manager.stop()
                    raise AssertionError("必须报告暂停回调失败")
                except RuntimeError:
                    pass
                assert ctx.run_context.is_context_stop
                finish.set()
                wait(lambda: not manager.busy)
                assert manager.status()["state"] == "failed" and "回调失败" in manager.status()["error"], manager.status()
        finally:
            finish.set()
            ctx.run_context.event_bus.unlisten_event(ApplicationRunContextStateEventEnum.PAUSE, broken_pause)
        checks.append("failing_pause_callback_cannot_leave_backend_permanently_paused")

        # 初始化中的停止不会重启或误报 ready，查询保持响应。
        init_entered, init_finish = threading.Event(), threading.Event()
        def initialize():
            init_entered.set()
            init_finish.wait(3)
        fixture_exe = args.data / "ZenlessZoneZero.exe"
        fixture_exe.touch()
        with patch.object(ctx, "init", side_effect=initialize):
            initial = manager.initialize("cn", str(fixture_exe))
            assert init_entered.wait(1)
            stopped = manager.stop()
            assert stopped["busy"] and stopped["job_id"] == initial["job_id"]
            init_finish.set()
            wait(lambda: not manager.busy)
        assert manager.status()["state"] == "stopped" and not manager.status()["ready"]
        checks.append("initialization_stop_remains_queryable_and_never_reports_ready")
        print(json.dumps(dict(checks=checks, game_input_sent=False, simulated_clicks=clicks), ensure_ascii=False))
    finally:
        finish.set()
        manager.close()
        keys.reset()
        ctx.controller = None
        service.close()


if __name__ == "__main__":
    main()
