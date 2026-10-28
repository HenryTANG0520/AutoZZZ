"""委托助手模式与快捷键适配；模式请求不直接发送游戏按键。"""
from __future__ import annotations

import copy
from functools import wraps
import re
import threading
from typing import Any, Callable

from .onedragon_control import ObservationExpired, ExecutionStopped


def virtual_key(name: str) -> int:
    if not isinstance(name, str):raise ValueError("模式快捷键须为单个键盘或鼠标按键。")
    key = name.lower()
    if len(key) == 1 and key.isascii() and key.isalnum():return ord(key.upper())
    if match := re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", key):return 111 + int(match[1])
    if match := re.fullmatch(r"numpad_([0-9])", key):return 96 + int(match[1])
    aliases = {"space":32,"enter":13,"tab":9,"esc":27,"backspace":8,"delete":46,"insert":45,
               "home":36,"end":35,"page_up":33,"page_down":34,"left":37,"up":38,"right":39,"down":40,
               "shift":16,"shift_l":160,"shift_r":161,"ctrl":17,"ctrl_l":162,"ctrl_r":163,
               "alt":18,"alt_l":164,"alt_r":165,"alt_gr":165,"cmd":91,"cmd_l":91,"cmd_r":92,
               "mouse_left":1,"mouse_right":2,"mouse_middle":4,"mouse_x1":5,"mouse_x2":6}
    if key in aliases:return aliases[key]
    if len(key) == 1:
        import ctypes
        scan = ctypes.windll.user32.VkKeyScanW
        scan.argtypes, scan.restype = [ctypes.c_wchar], ctypes.c_short
        result = scan(key)
        if result != -1:return result & 0xff
    raise ValueError("不支持的模式快捷键：" + name)


def validate_shortcuts(config: Any) -> dict[str, int]:
    keys = {config.dodge_switch:virtual_key(config.dodge_switch), config.auto_battle_switch:virtual_key(config.auto_battle_switch)}
    if len(keys) != 2 or len(set(keys.values())) != 2:raise ValueError("闪避和战斗模式请使用不同快捷键。")
    if set(keys.values()) & {0x78,0x79}:raise ValueError("F9/F10 用于暂停和停止，请另选模式快捷键。")
    return keys


class AssistantModeControl:
    def __init__(self, ctx: Any, control: Any):
        self.ctx, self.control = ctx, control
        self.app: Any = None
        self.keys: dict[str, int] = {}
        self.previous: dict[str, bool] | None = None
        self.lock = threading.RLock()
        self.originals: list[tuple[Any, str, Any]] = []

    def patch(self, cls: Any, name: str, replacement: Any) -> None:
        original = getattr(cls, name)
        if hasattr(original, "operation_node_annotation"):
            node = copy.copy(original.operation_node_annotation)
            node.op_method = replacement
            replacement.operation_node_annotation = node
        self.originals.append((cls,name,original))
        setattr(cls,name,replacement)

    def set_mode(self, mode: int) -> dict:
        if type(mode) is not int or mode not in (0,1,2):raise ValueError("助手模式须为对话、闪避或战斗。")
        with self.lock:
            if self.app is None:raise RuntimeError("委托助手尚未开始。")
            if self.app.run_mode != mode:
                with self.control.lock:self.control._invalidate()
                self.control.release()
                self.ctx.auto_battle_context.stop_auto_battle()
                self.app.run_mode = mode
            return self.status()

    def poll_keys(self, reader: Callable[[int], int]) -> None:
        with self.lock:
            if self.app is None:return
            pressed = {key:bool(reader(code) & 0x8000) for key,code in self.keys.items()}
            previous, self.previous = self.previous, pressed
            if previous is None:return  # 启动或恢复监听时已按住的按键不触发模式切换。
            if self.control.status()["state"] != "running":return
            for key,value in pressed.items():
                if value and not previous.get(key,False):
                    try:self.control.checkpoint(require_frame=True)
                    except (ObservationExpired,ExecutionStopped):return
                    from one_dragon.base.operation.context_event_bus import ContextEventItem
                    old = self.app.run_mode
                    self.app._on_key_press(ContextEventItem("按键",key))
                    selected, self.app.run_mode = self.app.run_mode, old
                    self.set_mode(selected)

    def status(self) -> dict:
        with self.lock:
            return dict(mode=None if self.app is None else self.app.run_mode,
                        keys={} if self.app is None else dict(dodge=self.app.config.dodge_switch,battle=self.app.config.auto_battle_switch))

    def install(self) -> None:
        from zzz_od.application.commission_assistant.commission_assistant_app import CommissionAssistantApp
        owner = self
        initialize = CommissionAssistantApp.handle_init
        @wraps(initialize)
        def init(operation):
            initialize(operation)
            with owner.lock:
                owner.app, owner.keys, owner.previous = operation, validate_shortcuts(operation.config), None
                operation._autozzz_loaded_mode = None
        self.patch(CommissionAssistantApp,"handle_init",init)
        load = CommissionAssistantApp._load_auto_op
        @wraps(load)
        def load_mode(operation):
            with owner.lock:
                selected = operation.run_mode
                load(operation)
                operation._autozzz_loaded_mode = selected
        self.patch(CommissionAssistantApp,"_load_auto_op",load_mode)
        auto = CommissionAssistantApp.auto_mode
        @wraps(auto)
        def auto_mode(operation):
            if operation.run_mode and operation.run_mode != getattr(operation,"_autozzz_loaded_mode",None):
                owner.ctx.auto_battle_context.stop_auto_battle()
                operation._load_auto_op()
            return auto(operation)
        self.patch(CommissionAssistantApp,"auto_mode",auto_mode)
        finish = CommissionAssistantApp.after_operation_done
        @wraps(finish)
        def done(operation,result):
            try:return finish(operation,result)
            finally:
                with owner.lock:
                    if owner.app is operation:owner.app,owner.keys,owner.previous = None,{},None
        self.patch(CommissionAssistantApp,"after_operation_done",done)

    def close(self) -> None:
        for target,name,original in reversed(self.originals):setattr(target,name,original)
        self.originals.clear()
