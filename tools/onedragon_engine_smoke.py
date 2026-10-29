"""用真实 AutoBattleOperator 验证状态、打断和停止；原子输入由记录器替代。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "vendor" / "onedragon"
    sys.path.insert(0, str(source / "src"))
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_runtime import RuntimePaths
    from one_dragon.base.conditional_operation.atomic_op import AtomicOp
    from one_dragon.base.conditional_operation.state_recorder import StateRecord
    from zzz_od.auto_battle.auto_battle_operator import AutoBattleOperator

    data=args.data.resolve();defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1])
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    RuntimePaths(source,defaults.python,data).prepare()
    import platform
    platform.uname()
    scope=ExitStack()
    def forbidden(*_,**__):raise AssertionError('条件引擎回放不得访问游戏、网络或输入设备。')
    for target in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                   'autozzz.windows.find_game','autozzz.windows.user32.SendInput','pynput.keyboard.Controller.press','pynput.mouse.Controller.press'):
        scope.enter_context(patch(target,side_effect=forbidden))
    service = OneDragonService(source, data)
    service.trace_configure(True)
    events: list[str] = []
    attack = threading.Event()
    dodge = threading.Event()

    class RecordingOp(AtomicOp):
        def __init__(self, name: str):
            super().__init__(name)
            self.cancelled = threading.Event()

        def execute(self) -> None:
            self.cancelled.clear()
            events.append(self.op_name)
            if self.op_name == "按键-普通攻击":
                attack.set()
                if not self.cancelled.wait(2):
                    events.append("普攻自然结束")
            else:
                dodge.set()
                self.cancelled.wait(1)

        def stop(self) -> None:
            events.append(self.op_name + "停止")
            self.cancelled.set()

    class RecordingFactory:
        def get_atomic_op(self, definition):
            return RecordingOp(definition.op_name)

    content = '''author: AutoZZZ test
auto_lock_interval: -1
auto_turn_interval: -1
scenes:
  - priority: 1
    interval: 0.02
    handlers:
      - states: "[自定义-可攻击,0,60]"
        operations:
          - op_name: 按键-普通攻击
  - triggers: [自定义-闪避]
    priority: 99
    interval: 0
    handlers:
      - states: "[自定义-闪避,0,60]"
        operations:
          - op_name: 按键-闪避
'''
    name = "AutoZZZ条件引擎验证"
    service.policy_save(name, content)
    context = service.ctx.auto_battle_context
    context.atomic_op_factory = RecordingFactory()
    operator = AutoBattleOperator(context, "auto_battle", name, False)
    context.auto_op=operator
    try:
        ok, message = operator.init_before_running()
        assert ok, message
        assert operator.start_running_async()
        assert not attack.wait(.1), "没有状态时不能普攻"
        context.state_record_service.update_state(StateRecord("自定义-可攻击", time.time()))
        assert attack.wait(2), "更新状态后应执行普攻"
        from autozzz.onedragon_battle_state import battle_snapshot
        attack_state=battle_snapshot(service.ctx)
        assert attack_state['running'] and attack_state['task']['priority']=='1'
        assert attack_state['task']['conditions']=='[自定义-可攻击,0,60]'
        assert any(item['name']=='自定义-可攻击' for item in attack_state['states'])
        context.state_record_service.update_state(StateRecord("自定义-闪避", time.time()))
        assert dodge.wait(1), "高优先级场景应打断持续普攻"
        dodge_state=battle_snapshot(service.ctx)
        assert dodge_state['running'] and dodge_state['task']['priority']=='99'
        assert dodge_state['task']['trigger']=='自定义-闪避'
        assert "普攻自然结束" not in events
        assert events.index("按键-普通攻击停止") < events.index("按键-闪避")
        trace=service.trace_snapshot()
        assert any(row['trigger']=='自定义-闪避' and row['expression']=='[自定义-闪避,0,60]' for row in trace['items']['decision']),trace
        assert any(row['category']=='decision' for row in trace['items']['timeline']),trace
        (data/'trace.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
        operator.stop_running()
        time.sleep(.1)
        count = len(events)
        context.state_record_service.update_state(StateRecord("自定义-闪避", time.time()))
        time.sleep(.1)
        assert len(events) == count and not operator.is_running, "停止后不能继续发操作"
        assert not battle_snapshot(service.ctx)['running'] and not battle_snapshot(service.ctx)['states']
        assert service.ctx.controller is None and not service.ctx._btn_listener_started
        print(json.dumps(dict(engine=type(operator).__module__ + "." + type(operator).__name__,
                              checks=["no_action_without_state", "state_starts_attack",
                                      "high_priority_interrupts", "stop_rejects_new_state","actual_executor_telemetry","stopped_telemetry_cleared","actual_conditional_decision_and_timeline_trace"],
                              events=events, game_input_sent=False), ensure_ascii=False))
    finally:
        operator.dispose()
        service.close()
        scope.close()


if __name__ == "__main__":
    main()
