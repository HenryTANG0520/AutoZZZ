"""用真实 AutoBattleOperator 验证状态、打断和停止；原子输入由记录器替代。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "vendor" / "onedragon"
    sys.path.insert(0, str(source / "src"))
    from autozzz.onedragon_service import OneDragonService
    from one_dragon.base.conditional_operation.atomic_op import AtomicOp
    from one_dragon.base.conditional_operation.state_recorder import StateRecord
    from zzz_od.auto_battle.auto_battle_operator import AutoBattleOperator

    service = OneDragonService(source, args.data)
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
    try:
        ok, message = operator.init_before_running()
        assert ok, message
        assert operator.start_running_async()
        assert not attack.wait(.1), "没有状态时不能普攻"
        context.state_record_service.update_state(StateRecord("自定义-可攻击", time.time()))
        assert attack.wait(2), "更新状态后应执行普攻"
        context.state_record_service.update_state(StateRecord("自定义-闪避", time.time()))
        assert dodge.wait(1), "高优先级场景应打断持续普攻"
        assert "普攻自然结束" not in events
        assert events.index("按键-普通攻击停止") < events.index("按键-闪避")
        operator.stop_running()
        time.sleep(.1)
        count = len(events)
        context.state_record_service.update_state(StateRecord("自定义-闪避", time.time()))
        time.sleep(.1)
        assert len(events) == count and not operator.is_running, "停止后不能继续发操作"
        assert service.ctx.controller is None and not service.ctx._btn_listener_started
        print(json.dumps(dict(engine=type(operator).__module__ + "." + type(operator).__name__,
                              checks=["no_action_without_state", "state_starts_attack",
                                      "high_priority_interrupts", "stop_rejects_new_state"],
                              events=events, game_input_sent=False), ensure_ascii=False))
    finally:
        operator.dispose()
        service.close()


if __name__ == "__main__":
    main()
