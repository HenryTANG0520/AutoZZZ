"""上游脚本的预编译与逐指令播放，不改写条件战斗引擎。"""
from __future__ import annotations

import copy
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps,update_wrapper
import math
from types import SimpleNamespace
from typing import Any

from .onedragon_control import ExecutionStopped, ObservationExpired

_playing: ContextVar[Any] = ContextVar("autozzz_script_playback", default=None)


def validate_definition(definition: Any) -> None:
    for name in ("pre_delay", "post_delay", "wait_seconds", "btn_press"):
        value = getattr(definition, name, None if name == "btn_press" else 0)
        if value is None and name == "btn_press":
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("指令时长必须为非负有限数字：" + name)
    repeats = getattr(definition, "btn_repeat_times", 1)
    if type(repeats) is not int or repeats < 1:
        raise ValueError("指令重复次数必须为正整数。")


def validate_atomic(operation: Any) -> None:
    # 旧格式可将持续时间放在 data 中；按实际解析结果再检查一次。
    validate_definition(SimpleNamespace(
        pre_delay=getattr(operation, "pre_delay", 0),
        post_delay=getattr(operation, "post_delay", 0),
        wait_seconds=getattr(operation, "wait_seconds", 0),
        btn_press=getattr(operation, "press_time", None),
        btn_repeat_times=getattr(operation, "repeat_times", 1),
    ))


def compile_key_sim(ctx: Any, name: str, app_id: str) -> list[Any]:
    from .onedragon_service import OneDragonService
    from one_dragon.base.conditional_operation.operation_def import OperationDef
    from zzz_od.operation.key_sim_yaml_config import KeySimYamlConfig
    OneDragonService._name(name)
    plugin = ctx.factory_manager.get_plugin_info(app_id)
    directory = None if plugin is None or not plugin.key_sim_dir or plugin.plugin_dir is None else plugin.plugin_dir / plugin.key_sim_dir
    config = KeySimYamlConfig(name, plugin_dir=directory)
    raw = config.data.get("operations", [])
    if not isinstance(raw, list) or not raw:
        raise ValueError("键鼠脚本不存在或没有指令：" + name)
    operations = []
    for item in raw:
        if not isinstance(item, dict):raise ValueError("脚本指令必须为映射。")
        definition = OperationDef(item)
        validate_definition(definition)
        operation = ctx.auto_battle_context.atomic_op_factory.get_atomic_op(definition)
        validate_atomic(operation)
        operations.append(operation)
    return operations


def compile_operation_template(ctx: Any, name: str) -> list[Any]:
    from .onedragon_service import OneDragonService
    from one_dragon.base.conditional_operation.loader import ConditionalOperatorLoader
    from one_dragon.base.conditional_operation.operation_def import OperationDef
    OneDragonService._name(name)
    data = ConditionalOperatorLoader.load_yaml_config(["auto_battle_operation"], name, False)
    raw = data.get("operations", [])
    if not isinstance(raw, list) or not raw:
        raise ValueError("指令模板不存在或没有指令：" + name)
    loader = ConditionalOperatorLoader(["auto_battle_operation"], name, ["auto_battle_operation"], ["auto_battle_state_handler"], False)
    operations = []
    for item in raw:
        if not isinstance(item, dict):raise ValueError("模板指令必须为映射。")
        for definition in loader.load_template_for_operation(OperationDef(item), set()):
            validate_definition(definition)
            operation = ctx.auto_battle_context.atomic_op_factory.get_atomic_op(definition)
            validate_atomic(operation)
            operations.append(operation)
    if not operations:raise ValueError("展开后的指令模板为空。")
    return operations


@dataclass
class ProgramState:
    index: int = 0
    epoch: int = -1
    held: dict[str, Any] = field(default_factory=dict)


class ScriptPlaybackControl:
    def __init__(self, control: Any):
        self.control = control
        self.originals: list[tuple[Any, str, Any]] = []
        self.progress: dict = {}

    def patch(self, target: Any, name: str, replacement: Any) -> None:
        original = getattr(target, name)
        if callable(original) and callable(replacement):update_wrapper(replacement,original)
        if hasattr(original, "operation_node_annotation"):
            node = copy.copy(original.operation_node_annotation)
            node.op_method = replacement
            replacement.operation_node_annotation = node
        self.originals.append((target, name, original))
        setattr(target, name, replacement)

    def reset(self) -> None:
        self.progress = {}

    def wait(self, seconds: float) -> None:
        if not self.control.hold(seconds):self.control.checkpoint(require_frame=True)

    def execute_atomic(self, operation: Any) -> None:
        from zzz_od.auto_battle.atomic_op.btn_common import AtomicBtnCommon, BtnRunStatus
        try:
            operation.execute()
            self.control.checkpoint(require_frame=True)
        except (ObservationExpired, ExecutionStopped):
            try:operation.stop()
            except (ObservationExpired, ExecutionStopped):pass
            finally:
                if isinstance(operation, AtomicBtnCommon):
                    with operation._update_lock:operation._status = BtnRunStatus.WAIT
            raise

    def execute(self, operation: Any, repeat: bool) -> Any:
        from zzz_od.auto_battle.atomic_op.btn_common import AtomicBtnCommon
        state = getattr(operation, "_autozzz_program", None)
        if state is None:
            state = operation._autozzz_program = ProgramState()
        if not operation.ops:return operation.round_fail("没有可执行的指令。")
        if state.index>=len(operation.ops):return operation.round_success()
        operation.screenshot()
        self.control.checkpoint(require_frame=True)
        token = _playing.set(self)
        current = operation.ops[state.index]
        try:
            if state.epoch != self.control.epoch:
                for held in state.held.values():self.execute_atomic(held)
                state.epoch = self.control.epoch
            self.execute_atomic(current)
            if isinstance(current, AtomicBtnCommon):
                key, pressed = current.btn_name, current.is_press
            elif (type(current).__module__.startswith("zzz_od.auto_battle.atomic_op.btn_")
                  and type(getattr(current, "press", None)) is bool):
                # 上游旧格式的“-按下/-松开”使用独立原子类，也要恢复持续按键。
                key = current.op_name.removeprefix("按键-").removesuffix("按下").removesuffix("松开").rstrip("-")
                pressed = current.press
            else:
                key, pressed = None, False
            if key is not None:
                if pressed and current.press_time is None:state.held[key] = current
                else:state.held.pop(key, None)
            state.index += 1
            self.progress = dict(name=getattr(operation, "config_name", "指令调试"), completed=state.index, total=len(operation.ops))
            if hasattr(operation, "op_idx"):operation.op_idx = state.index
            if state.index == len(operation.ops):
                if not repeat:return operation.round_success()
                state.index = 0
                if hasattr(operation, "op_idx"):operation.op_idx = 0
            return operation.round_wait()
        finally:_playing.reset(token)

    def install(self) -> None:
        from zzz_od.auto_battle.atomic_op import btn_common
        from zzz_od.auto_battle.atomic_op.wait import AtomicWait
        from zzz_od.operation.key_sim_runner import KeySimRunner
        from zzz_od.application.devtools.operation_debug.operation_debug_app import OperationDebugApp
        owner = self
        original_time = btn_common.time
        class ScopedTime:
            def __getattr__(self, name):return getattr(original_time, name)
            def sleep(self, seconds):
                if _playing.get() is owner:owner.wait(seconds)
                else:original_time.sleep(seconds)
        self.patch(btn_common, "time", ScopedTime())
        original_wait = AtomicWait.execute
        @wraps(original_wait)
        def wait(operation):
            if _playing.get() is owner:return owner.wait(operation.wait_seconds)
            return original_wait(operation)
        self.patch(AtomicWait, "execute", wait)
        for cls, name in ((KeySimRunner, "load_config"), (OperationDebugApp, "load_op")):
            original = getattr(cls, name)
            def loader(operation, original=original):
                result = original(operation)
                if result.is_success:operation._autozzz_program = ProgramState()
                return result
            self.patch(cls, name, loader)
        def key_step(operation):return owner.execute(operation, False)
        def debug_step(operation):return owner.execute(operation, operation._get_config().repeat_enabled)
        self.patch(KeySimRunner, "run_key_sim", key_step)
        self.patch(OperationDebugApp, "run_operations", debug_step)

    def close(self) -> None:
        for target, name, original in reversed(self.originals):setattr(target, name, original)
        self.originals.clear()
