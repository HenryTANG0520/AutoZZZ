"""上游脚本的预编译与逐指令播放，不改写条件战斗引擎。"""
from __future__ import annotations

import copy
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps,update_wrapper
import hashlib
import math
from pathlib import Path
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


def _operation_template_root() -> Path:
    from one_dragon.utils import os_utils

    root = Path(os_utils.get_work_dir()).resolve() / "config" / "auto_battle_operation"
    if root.resolve() != root:
        raise ValueError("指令模板目录不能重定向到其他位置。")
    return root


def _operation_template_path(root: Path, name: str) -> Path:
    from .onedragon_service import OneDragonService
    from one_dragon.base.conditional_operation.loader import ConditionalOperatorLoader

    for part in name.split("/") if isinstance(name, str) else (name,):
        OneDragonService._name(part)
    path = Path(ConditionalOperatorLoader.get_yaml_file_path([root.name], name, False))
    if not path.resolve().is_relative_to(root):
        raise ValueError("指令模板路径超出模板目录。")
    return path


def read_operation_template_file(root: Path, path: Path) -> tuple[Path, bytes]:
    """Read the same Win32 handle whose final pathname was checked."""
    import _winapi
    import ctypes as C
    from ctypes import wintypes as W
    import msvcrt
    import os

    from .onedragon_bundle import filesystem_path

    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("指令模板路径超出模板目录。")
    native = filesystem_path(resolved)
    # FILE_SHARE_READ excludes in-place writers and replacement while the bytes are read.
    handle = _winapi.CreateFile(str(native), _winapi.GENERIC_READ, 1, 0, _winapi.OPEN_EXISTING, 0, 0)
    try:
        final_path = C.windll.kernel32.GetFinalPathNameByHandleW
        final_path.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
        final_path.restype = W.DWORD
        buffer = C.create_unicode_buffer(32768)
        size = final_path(handle, buffer, len(buffer), 0)
        if not 0 < size < len(buffer) or Path(buffer.value) != native:
            raise ValueError("无法核实打开的指令模板路径，可能已被重定向。")
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = None  # Ownership transferred to the descriptor.
        with os.fdopen(fd, "rb") as stream:
            return resolved, stream.read()
    finally:
        if handle is not None:
            _winapi.CloseHandle(handle)


def operation_template_choices() -> list[dict]:
    from zzz_od.application.battle_assistant.operation_template_config import get_operation_template_config_list

    root = _operation_template_root()
    options = []
    for item in get_operation_template_config_list():
        try:
            _operation_template_path(root, item.value)
        except (ValueError, OSError, RuntimeError):
            continue
        options.append(dict(label=item.label, value=item.value))
    return options


def compile_operation_template(ctx: Any, name: str) -> list[Any]:
    return capture_operation_template(ctx, name)[0]


def capture_operation_template(ctx: Any, name: str) -> tuple[list[Any], str]:
    from one_dragon.base.conditional_operation.operation_def import OperationDef
    from one_dragon.utils import yaml_utils

    root = _operation_template_root()
    digest = hashlib.sha256()
    templates = {}
    file_bytes = {}

    def definitions(template, active):
        # 固定 SDK 的递归加载没有路径回调。每个引用复用本次捕获的文件版本。
        if not isinstance(template, str):
            raise ValueError("指令模板名称必须为文本。")
        if template not in templates:
            chosen = _operation_template_path(root, template)
            resolved, content = read_operation_template_file(root, chosen)
            if resolved in file_bytes and file_bytes[resolved] != content:
                raise ValueError("读取引用期间指令模板发生变化，请重新准备。")
            file_bytes[resolved] = content
            for value in (template.encode("utf-8"), chosen.relative_to(root).as_posix().encode("utf-8"),
                          resolved.relative_to(root).as_posix().encode("utf-8"), content):
                digest.update(len(value).to_bytes(8, "big"))
                digest.update(value)
            data = yaml_utils.safe_load(content.decode("utf-8"))
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ValueError("指令模板 YAML 顶层必须是映射：" + template)
            templates[template] = (resolved, data.get("operations", []))
        path, raw = templates[template]
        if path in active:
            raise ValueError("指令模板循环引用 " + template)
        if not isinstance(raw, list) or (not raw and not active):
            raise ValueError("指令模板不存在或没有指令：" + template)
        active.add(path)
        try:
            for item in raw:
                if not isinstance(item, dict):
                    raise ValueError("模板指令必须为映射。")
                definition = OperationDef(item)
                if definition.operation_template is None:
                    yield definition
                else:
                    yield from definitions(definition.operation_template, active)
        finally:
            active.remove(path)

    operations = []
    for definition in definitions(name, set()):
        validate_definition(definition)
        operation = ctx.auto_battle_context.atomic_op_factory.get_atomic_op(definition)
        validate_atomic(operation)
        operations.append(operation)
    if not operations:
        raise ValueError("展开后的指令模板为空。")
    return operations, digest.hexdigest()


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
        self.debug_template: tuple[str, bool, str] | None = None

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
        self.debug_template = None

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
        from one_dragon.utils.log_utils import log
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
        original_load_key_sim = KeySimRunner.load_config

        def load_key_sim(operation):
            result = original_load_key_sim(operation)
            if result.is_success:
                operation._autozzz_program = ProgramState()
            return result

        def load_debug(operation):
            if owner.debug_template is None:
                return operation.round_fail("指令模板未准备，请重新准备应用。")
            name, repeat, revision = owner.debug_template
            try:
                config = operation._get_config()
                if config.operation_template != name or config.repeat_enabled is not repeat:
                    return operation.round_fail("指令调试配置已变化，请重新准备应用。")
                ops, current = capture_operation_template(operation.ctx, name)
                if current != revision:
                    return operation.round_fail("指令模板已变化，请重新准备应用。")
            except Exception:
                log.error("指令模板加载失败", exc_info=True)
                return operation.round_fail("无法核实已准备的指令模板，请重新准备应用。")
            operation.ops = ops
            operation.op_idx = 0
            operation._autozzz_repeat = repeat
            operation._autozzz_program = ProgramState()
            return operation.round_success()

        self.patch(KeySimRunner, "load_config", load_key_sim)
        self.patch(OperationDebugApp, "load_op", load_debug)
        def key_step(operation):return owner.execute(operation, False)
        def debug_step(operation):return owner.execute(operation, operation._autozzz_repeat)
        self.patch(KeySimRunner, "run_key_sim", key_step)
        self.patch(OperationDebugApp, "run_operations", debug_step)

    def close(self) -> None:
        self.debug_template = None
        for target, name, original in reversed(self.originals):setattr(target, name, original)
        self.originals.clear()
