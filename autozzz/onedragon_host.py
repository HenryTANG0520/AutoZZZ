"""上游进程内的受控前台设备与上下文；保留上游应用及识别实现。"""
from __future__ import annotations

import math
import inspect
from functools import cached_property
from pathlib import Path
import threading
from contextvars import ContextVar
from typing import Any

from . import windows as win32_io  # 在上游导入 pyautogui 前设置与 AutoZZZ 一致的 DPI。
from pynput import keyboard
from pygetwindow import Win32Window
from one_dragon.base.controller.pc_button.keyboard_mouse_controller import KeyboardMouseController
from one_dragon.base.controller.pc_game_window import PcGameWindow
from one_dragon.base.controller.pc_screenshot.pc_screenshot_controller import PcScreenshotController
from one_dragon.base.operation.context_event_bus import ContextEventItem
from one_dragon.base.operation.application.application_run_context import ApplicationRunContextStateEventEnum
from zzz_od.controller.zzz_pc_controller import ZPcController
from zzz_od.context.zzz_context import ZContext

from .onedragon_control import ExecutionControl, WindowState,ObservationExpired,ExecutionStopped

_menu_click_target=ContextVar('autozzz_menu_click_target',default=None)


class RelativeMouse:
    """保留鼠标按钮和绝对坐标行为，相机转向发送真正的相对输入。"""
    def __init__(self,raw:Any):self.raw=raw

    def __getattr__(self,name):return getattr(self.raw,name)

    @property
    def position(self):return self.raw.position

    @position.setter
    def position(self,value):self.raw.position=value

    def move(self,dx:float,dy:float) -> None:
        if not all(math.isfinite(v) and -(2**31)<=v<2**31 for v in (dx,dy)):
            raise ValueError("相对鼠标位移无效。")
        event=win32_io.INPUT(type=0,mi=win32_io.MOUSEINPUT(int(dx),int(dy),0,1,0,0))
        if win32_io.user32.SendInput(1,win32_io.C.byref(event),win32_io.C.sizeof(event))!=1:
            raise RuntimeError("相机输入被 Windows 拒绝，请检查助手权限。")


class ControlledDevice:
    """记录本进程按下的键；不允许恢复旧任务的设备调用。"""
    def __init__(self, raw: Any, control: ExecutionControl,foreground_guard=None):
        self.raw, self.control = raw, control
        self.pressed: set[Any] = set()
        self.lock = threading.RLock()
        self.before_press = None
        self.foreground_guard=foreground_guard

    def _input(self,action):
        if self.foreground_guard is not None:self.foreground_guard()
        return self.control.input(action)

    def press(self, key: Any) -> None:
        def send():
            with self.lock:
                if self.before_press is not None:self.before_press(key)
                self.pressed.add(key)
                self.raw.press(key)
        self._input(send)

    def release(self, key: Any) -> None:
        with self.lock:
            if key in self.pressed:
                self.raw.release(key)
                self.pressed.discard(key)

    def tap(self, key: Any) -> None:
        self.press(key)
        try:
            self.control.hold(.03)
        finally:
            self.release(key)

    def type(self, text: str) -> None:
        for char in text:
            self._input(lambda char=char: self.raw.type(char))

    @property
    def position(self):
        return self.raw.position

    @position.setter
    def position(self, value):
        self._input(lambda: setattr(self.raw, "position", value))

    def move(self, dx: float, dy: float) -> None:
        self._input(lambda: self.raw.move(dx, dy))

    def scroll(self, dx: int, dy: int) -> None:
        self._input(lambda: self.raw.scroll(dx, dy))

    def reset(self) -> None:
        for key in list(self.pressed):
            self.release(key)


class ControlledKeyboard(KeyboardMouseController):
    def __init__(self, control: ExecutionControl,foreground_guard=None):
        super().__init__()
        self.control = control
        self.keyboard = ControlledDevice(self.keyboard, control,foreground_guard)
        self.mouse = ControlledDevice(RelativeMouse(self.mouse), control,foreground_guard)

    def tap(self, key: str) -> None:
        self.press(key, .03)

    def press(self, key: str, press_time: float | None = None) -> None:
        if press_time is not None and (not math.isfinite(press_time) or press_time < 0):
            raise ValueError("按键时长必须是有限非负数。")
        super().press(key, None)
        if press_time is not None:
            try:
                self.control.hold(press_time)
            finally:
                self.release(key)

    def reset(self) -> None:
        super().reset()
        self.keyboard.reset()
        self.mouse.reset()


class BoundGameWindow(PcGameWindow):
    def __init__(self, width: int, height: int):
        super().__init__(width, height)
        self.bound: Any = None
        self.process_handle: Any = None
        self.process_id: int | None = None

    def identity_valid(self) -> bool:
        from .windows import user32, kernel32, process_path, W, C
        if self.bound is None or self.process_handle is None:
            return False
        pid = W.DWORD()
        user32.GetWindowThreadProcessId(self.bound.hwnd, C.byref(pid))
        return (kernel32.WaitForSingleObject(self.process_handle, 0) == 258
                and user32.IsWindow(self.bound.hwnd) and pid.value == self.process_id
                and process_path(self.bound.hwnd).casefold() == self.bound.path.casefold())

    def clear_binding(self) -> None:
        from .windows import kernel32
        if self.process_handle is not None:
            kernel32.CloseHandle(self.process_handle)
        self.process_handle = None
        self.process_id = None
        self.bound = None
        self._clear_cached_window()

    def init_win(self) -> None:
        if self.bound is None:
            self._clear_cached_window()
            return
        if not self.identity_valid():
            self._clear_cached_window()
            return
        self._hWnd = self.bound.hwnd
        self._win = Win32Window(self.bound.hwnd)

    def active(self) -> bool:
        return self.is_win_active  # 只查询，不恢复或激活窗口。

    @property
    def win_rect(self):
        if self.bound is None:
            return None
        from .windows import geometry, user32
        if not user32.IsWindow(self.bound.hwnd) or user32.IsIconic(self.bound.hwnd):
            return None
        from one_dragon.base.geometry.rectangle import Rect
        g = geometry(self.bound.hwnd)
        return Rect(g.left, g.top, g.left+g.width, g.top+g.height)


class WindowCapture(PcScreenshotController):
    def __init__(self,owner,*args):
        self.owner=owner;super().__init__(*args)

    def _get_method_priority_list(self,method):
        if self.owner.using_background:
            from one_dragon.envs.env_config import ScreenshotMethodEnum
            return [ScreenshotMethodEnum.PRINT_WINDOW.value.value]
        return super()._get_method_priority_list(method)


class ManagedController(ZPcController):
    def __init__(self, ctx: Any, control: ExecutionControl):
        super().__init__(ctx.game_config, ctx.env_config.screenshot_method,
                         ctx.project_config.screen_standard_width, ctx.project_config.screen_standard_height)
        self.ctx, self.control = ctx, control
        self._background_requested=self._foreground_required=self._task_policy_active=False
        from .onedragon_background import BackgroundMouse,WindowsDesktop
        self.background_mouse=BackgroundMouse(self);self.desktop=WindowsDesktop()
        self.keyboard_controller = ControlledKeyboard(control,self._physical_checkpoint)
        self.btn_controller = self.keyboard_controller
        self.game_win = BoundGameWindow(self.standard_width, self.standard_height)
        # 上游构造器已把原 game_win 传入截图器及其每个策略；换窗口须同步重建。
        self.screenshot_controller.cleanup()
        self.screenshot_controller = WindowCapture(self,self.game_win, self.standard_width, self.standard_height)

    def bind(self, game_path: str) -> None:
        from .windows import find_game, user32, kernel32, W, C
        self.game_win.clear_binding()
        bound = find_game(game_path)
        pid = W.DWORD()
        user32.GetWindowThreadProcessId(bound.hwnd, C.byref(pid))
        handle = kernel32.OpenProcess(0x101000, False, pid.value)
        if not handle:
            raise RuntimeError("无法保留游戏进程身份，请使用与游戏相同的管理员权限运行 AutoZZZ。")
        self.game_win.bound = bound
        self.game_win.process_id, self.game_win.process_handle = pid.value, handle
        if not self.game_win.identity_valid():
            self.game_win.clear_binding()
            raise RuntimeError("绑定期间游戏进程发生变化，请重新检测。")
        self.game_win.init_win()

    def cleanup_after_app_shutdown(self) -> None:
        try:
            super().cleanup_after_app_shutdown()
        finally:
            from .onedragon_gamepad import release_controller_inputs
            try:release_controller_inputs(self)
            finally:self.game_win.clear_binding()

    def probe(self) -> WindowState:
        from .windows import user32
        bound = self.game_win.bound
        if not self.game_win.identity_valid():
            return WindowState(False, False, reason="所选绝区零窗口已关闭或进程身份发生变化。")
        if user32.IsIconic(bound.hwnd):
            return WindowState(True, False)
        if self.using_background and not self._foreground_required:
            from .windows import geometry
            g=geometry(bound.hwnd)
            return WindowState(True,True,(g.left,g.top,g.width,g.height))
        if user32.GetForegroundWindow()!=bound.hwnd:return WindowState(True,False)
        from .models import FocusLost, AutomationError
        try:
            g = bound.guard(refresh=True)
        except FocusLost:
            return WindowState(True, False)
        except AutomationError as exc:
            return WindowState(False, True, reason=str(exc))
        return WindowState(True, True, (g.left, g.top, g.width, g.height))

    def init_before_context_run(self) -> bool:
        while True:
            try:
                with self.control.scope():
                    self.control.checkpoint()
                    if self._background_requested:self.enable_background_mode(self.game_config.background_gamepad_type)
                    else:self.enable_foreground_mode()
                    self.control.checkpoint()
                    return self.init_game_win()
            except ObservationExpired:self.control.changed.wait(.02)

    def sync_game_config(self, config: Any) -> None:
        self.game_config = config
        if not self._task_policy_active:self.enable_foreground_mode()

    def active_window(self) -> None:
        self.game_win.init_win()

    def enable_background_mode(self, gamepad_type: str = "xbox") -> None:
        if gamepad_type not in ('xbox','ds4'):raise ValueError('后台手柄类型无效。')
        getattr(self,'enable_'+gamepad_type)()
        self.background_mode=True;self._background_requested=True

    @property
    def using_background(self):
        return self.background_mode or getattr(self,'_background_requested',False)

    def begin_input_policy(self,app_id):
        requested=self.ctx.battle_assistant_config.background_mode if app_id in ('auto_battle','dodge_assistant') else self.game_config.background_mode
        if type(requested)is not bool:raise ValueError('后台开关须为布尔值。')
        if self.game_config.get('autozzz_background_mouse','wait') not in ('wait','flash'):raise ValueError('后台鼠标方式无效。')
        self._task_policy_active=True;self._foreground_required=False;self._background_requested=requested

    def end_input_policy(self):
        self._background_requested=self._task_policy_active=self._foreground_required=False
        self.background_mode=False

    def enable_foreground_mode(self):
        self._foreground_required=False;self._background_requested=False
        super().enable_foreground_mode()

    def _physical_checkpoint(self):
        if self.using_background:
            self._foreground_required=True
            self.control.checkpoint(require_frame=True)
            self._game_input_mode='keyboard_mouse'

    def _gamepad_checkpoint(self,key=None):
        self._foreground_required=False
        if key is not None and key==_menu_click_target.get():self._witness_mouse()

    def _witness_mouse(self):
        from pynput.mouse import Button
        before=self.keyboard_controller.mouse.before_press
        if before is not None:before(Button.left)

    def _ensure_gamepad_mode(self):
        if self._game_input_mode=='gamepad':return
        self.btn_controller.tap(self._get_switch_gamepad_key())
        if not self.control.hold(.1):self.control.checkpoint(require_frame=True)
        self._game_input_mode='gamepad'

    def _send_activate(self):
        from .windows import user32,C
        def send():
            result=C.c_size_t()
            if not user32.SendMessageTimeoutW(self.game_win.bound.hwnd,6,1,0,2,100,C.byref(result)):
                raise RuntimeError('游戏未响应后台激活消息。')
        self.control.input(send);return True

    def _gamepad_turn(self,dx,dy):
        speed=self.game_config.gamepad_turn_speed
        if not all(type(value)in (int,float) and math.isfinite(value) for value in (dx,dy,speed)):raise ValueError('手柄镜头参数必须为有限数。')
        if speed<=0 or dx==dy==0:return
        self._send_activate();self._ensure_gamepad_mode()
        self.btn_controller.turn(dx,dy,speed)

    def _gamepad_click(self,gamepad_key):
        keys=self.gamepad_action_keys.get(gamepad_key,[])
        token=_menu_click_target.set(keys[-1] if keys else None)
        try:return super()._gamepad_click(gamepad_key)
        finally:_menu_click_target.reset(token)

    def _ensure_mouse_mode(self):
        if not self.using_background:return True
        if self.game_config.get('autozzz_background_mouse','wait')=='wait':
            previous=self._game_input_mode;self._physical_checkpoint()
            if previous!='keyboard_mouse':
                self.keyboard_controller.mouse.move(1,0)
                if not self.control.hold(self.game_config.mouse_flash_duration):self.control.checkpoint(require_frame=True)
            return True
        if self._game_input_mode=='keyboard_mouse':return True
        from .onedragon_background import ForegroundBorrow
        try:
            with ForegroundBorrow(self.control,self.game_win.bound.hwnd,self.desktop):
                if not self.control.hold(self.game_config.mouse_flash_duration):self.control.checkpoint(require_frame=True)
                def move():
                    if self.desktop.current()!=self.game_win.bound.hwnd:raise ObservationExpired('临时前台已变化，未发送鼠标输入。')
                    self.keyboard_controller.mouse.raw.move(1,0)
                self.control.input(move)
                if not self.control.hold(self.game_config.mouse_flash_duration):self.control.checkpoint(require_frame=True)
        except Exception:
            self._foreground_required=True
            self.control.checkpoint(require_frame=True)
            raise
        self._game_input_mode='keyboard_mouse';return True

    def _background_cursor(self,point):
        position=self.game_win.game2win_pos(point)
        if position is None:raise ValueError('后台鼠标目标超出游戏客户区。')
        self.control.input(lambda:setattr(self.keyboard_controller.mouse.raw,'position',(position.x,position.y)))

    def _background_click(self,pos,press_time=0):
        if type(press_time)not in (int,float) or not math.isfinite(press_time) or press_time<0:raise ValueError('鼠标时长必须为有限非负数。')
        self.control.checkpoint(require_frame=True);self._ensure_mouse_mode()
        if pos is not None:self._background_cursor(pos)
        if not self.control.hold(.01):self.control.checkpoint(require_frame=True)
        self._send_activate()
        try:
            self.background_mouse.press()
            if not self.control.hold(max(.02,press_time)):self.control.checkpoint(require_frame=True)
            self._foreground_required=False;return True
        finally:self.background_mouse.reset()

    def _background_drag(self,start,end,duration=.5,*,timing=None):
        if type(duration)not in (int,float) or not math.isfinite(duration) or duration<0:raise ValueError('拖动时长必须为有限非负数。')
        self.control.checkpoint(require_frame=True);self._ensure_mouse_mode();self._background_cursor(start)
        press_wait=.02 if timing is None else timing.press_wait
        release_wait=.2 if timing is None else timing.release_wait
        moving=max(.1,duration-release_wait if timing is None else timing.move_duration)
        self._send_activate()
        try:
            self.background_mouse.press()
            if not self.control.hold(press_wait):self.control.checkpoint(require_frame=True)
            steps=max(5,round(moving/.02))
            from one_dragon.base.geometry.point import Point
            for index in range(1,steps+1):
                self._background_cursor(Point(start.x+(end.x-start.x)*index/steps,start.y+(end.y-start.y)*index/steps))
                if not self.control.hold(moving/steps):self.control.checkpoint(require_frame=True)
            if not self.control.hold(release_wait):self.control.checkpoint(require_frame=True)
            self._foreground_required=False
        finally:self.background_mouse.reset()

    def enable_xbox(self) -> None:
        self._enable_gamepad('xbox')

    def enable_ds4(self) -> None:
        self._enable_gamepad('ds4')

    def _enable_gamepad(self,kind):
        from one_dragon.base.controller.pc_button import pc_button_utils
        if not pc_button_utils.is_vgamepad_installed():raise ValueError('未安装虚拟手柄依赖 vgamepad / ViGEmBus。')
        self.control.checkpoint()
        from .onedragon_gamepad import ControlledGamepad,release_controller_inputs
        release_controller_inputs(self)
        name=kind+'_controller';device=getattr(self,name,None)
        if device is None:
            if kind=='xbox':
                from one_dragon.base.controller.pc_button.xbox_button_controller import XboxButtonController
                raw=XboxButtonController()
            else:
                from one_dragon.base.controller.pc_button.ds4_button_controller import Ds4ButtonController
                raw=Ds4ButtonController()
            device=ControlledGamepad(raw,self.control);device.before_input=self._gamepad_checkpoint;setattr(self,name,device)
        device.set_key_press_time(getattr(self.game_config,kind+'_key_press_time'))
        self.btn_controller=device;self._gamepad_type=kind
        self.action_keys=self.game_config.get_action_keys(kind)
        self.gamepad_action_keys=self.game_config.get_gamepad_action_keys(kind)

    def enable_keyboard(self):
        from .onedragon_gamepad import release_controller_inputs
        release_controller_inputs(self)
        super().enable_keyboard()

    def _get_switch_gamepad_key(self):
        return 'ds4_rs_up' if getattr(self,'_gamepad_type',None)=='ds4' else 'xbox_rs_up'

    def screenshot(self, independent: bool = False):
        def capture():
            try:
                result = super(ManagedController, self).screenshot(independent)
                if result[1] is None or result[1].size == 0 or result[1].std() < 2:
                    raise ValueError("游戏截图为空或尚未加载完成。")
                return result
            except (ObservationExpired,ExecutionStopped):raise
            except Exception:
                self.control.discard_frame();raise
        return self.control.observe(capture)

    def _position(self, point: Any) -> None:
        self.control.checkpoint(require_frame=True)
        if point is not None:
            position = self.game_win.game2win_pos(point)
            if position is None:
                raise ValueError("目标坐标超出游戏客户区。")
            self.keyboard_controller.mouse.position = (position.x, position.y)

    def _foreground_click(self, pos: Any, press_time: float = .1, pc_alt: bool = False) -> bool:
        if self.using_background:self._physical_checkpoint()
        self.control.checkpoint(require_frame=True)
        try:
            if pc_alt:
                self.keyboard_controller.keyboard.press(keyboard.Key.alt)
                if not self.control.hold(.2):
                    return False
            self._position(pos)
            self.keyboard_controller.press("mouse_left", max(.03, press_time))
            return True
        finally:
            if pc_alt:
                self.keyboard_controller.keyboard.release(keyboard.Key.alt)

    def _foreground_drag(self, start: Any, end: Any, duration: float = .5, *, timing: Any = None) -> None:
        if self.using_background:self._physical_checkpoint()
        self._position(start)
        press_wait = .02 if timing is None else timing.press_wait
        release_wait = .2 if timing is None else timing.release_wait
        move_duration = max(.1, duration-release_wait if timing is None else timing.move_duration)
        self.keyboard_controller.press("mouse_left")
        try:
            if not self.control.hold(press_wait):
                return
            steps = max(5, round(move_duration/.02))
            for i in range(1, steps+1):
                self.control.checkpoint(require_frame=True)
                from one_dragon.base.geometry.point import Point
                self._position(Point(start.x+(end.x-start.x)*i/steps, start.y+(end.y-start.y)*i/steps))
                if not self.control.hold(move_duration/steps):
                    return
            self.control.hold(release_wait)
        finally:
            self.keyboard_controller.release("mouse_left")

    def scroll(self, down: int, pos: Any = None) -> None:
        if self.using_background:self._physical_checkpoint()
        self._position(pos)
        self.keyboard_controller.mouse.scroll(0, -down)

    def mouse_move(self, game_pos: Any) -> None:
        self._position(game_pos)

    def move_mouse_relative(self, dx: float, dy: float) -> None:
        if self.background_mode:self._gamepad_turn(dx,dy)
        else:self.keyboard_controller.mouse.move(dx, dy)

    def input_str(self, to_input: str, interval: float = .1) -> None:
        for char in to_input:
            self.keyboard_controller.keyboard.type(char)
            if not self.control.hold(interval):
                return

    def close_game(self) -> None:
        self.control.checkpoint(require_frame=True)
        bound = self.game_win.bound
        if bound is None:
            raise ValueError("尚未绑定游戏窗口。")
        watch = bound.request_close(lambda: self.control.checkpoint(require_frame=True),submit=self.control.input)
        watch.close()


class ManagedZContext(ZContext):
    def __init__(self):
        # 固定版本的旧式“下一个角色-按下/松开”类缺少运行时导入。
        # 新式 way 写法未走该分支；补全符号，仍保留实际上游原子类与动作。
        from typing import Optional
        from zzz_od.auto_battle.atomic_op import btn_switch_next
        from zzz_od.auto_battle.auto_battle_state import BattleStateEnum
        if not hasattr(btn_switch_next,'BattleStateEnum'):btn_switch_next.BattleStateEnum=BattleStateEnum
        if not hasattr(btn_switch_next,'Optional'):btn_switch_next.Optional=Optional
        super().__init__()
        from .onedragon_trace import TraceRuntime
        self.debug_trace_runtime=TraceRuntime(self)
        from .onedragon_push import ManagedPushService
        self.push_service.after_app_shutdown()
        self.push_service = ManagedPushService(self)
        self.execution_control: ExecutionControl | None = None
        self.download_cancel = threading.Event()
        bus = self.run_context.event_bus
        original_dispatch = bus.dispatch_event
        state_events = {ApplicationRunContextStateEventEnum.PAUSE, ApplicationRunContextStateEventEnum.RESUME,
                        ApplicationRunContextStateEventEnum.STOP}
        def dispatch(event_id, event_obj=None):
            if event_id not in state_events:
                return original_dispatch(event_id, event_obj)
            failures = []
            for callback in list(bus.callbacks.get(event_id, [])):
                try:
                    callback(ContextEventItem(event_id, event_obj))
                except Exception as exc:
                    failures.append(exc)
            if failures:
                raise RuntimeError(f"运行状态回调失败：{failures[0]}")
        bus.dispatch_event = dispatch

    @cached_property
    def notify_config(self):
        from one_dragon.base.config.notify_config import NotifyConfig
        config = NotifyConfig(self.current_instance_idx, self.run_context.notify_app_map)
        if "enable_notify" not in config.data: config.enable_notify = False
        if "title" not in config.data: config.title = "AutoZZZ 运行通知"
        return config

    @cached_property
    def world_patrol_service(self):
        from .onedragon_patrol import managed_patrol_service
        return managed_patrol_service(self)

    @cached_property
    def application_plugin_dirs(self):
        from one_dragon.base.operation.application.plugin_info import PluginSource
        from one_dragon.utils import os_utils
        # 上游默认按实际子类文件定位；本适配器位于 AutoZZZ，需明确内置应用来源。
        builtin = Path(inspect.getfile(ZContext)).parent.parent / "application"
        result = [(builtin, PluginSource.BUILTIN)]
        plugins = Path(os_utils.get_work_dir()) / "plugins"
        if plugins.is_dir():
            result.append((plugins, PluginSource.THIRD_PARTY))
        return result

    def register_application_factory(self) -> None:
        # 初始化和显式刷新都可能再次扫描；上游注册会追加入口列表，须先清空旧注册。
        self.run_context.clear_applications()
        self.app_group_manager.clear_config_cache()
        super().register_application_factory()
        factory = self.run_context._application_factory_map.get("commission_assistant")
        if factory is not None:
            # 上游构造函数误用了截图助手的 APP_ID；仅覆盖工厂创建，避免两个应用共用文件。
            from zzz_od.application.commission_assistant.commission_assistant_config import CommissionAssistantConfig
            from one_dragon.base.operation.application.application_config import ApplicationConfig
            class ManagedCommissionConfig(CommissionAssistantConfig):
                def __init__(self, instance_idx: int, group_id: str):
                    ApplicationConfig.__init__(self, "commission_assistant", instance_idx, group_id)
            factory.create_config = lambda instance_idx, group_id: ManagedCommissionConfig(instance_idx, group_id)

    def init_controller(self) -> None:
        if self.execution_control is None:
            raise ValueError("未安装 AutoZZZ 执行控制器。")
        if self.controller is not None:
            self.controller.cleanup_after_app_shutdown()
        self.controller = ManagedController(self, self.execution_control)
        self.controller.set_window_title(self._get_win_title())

    def _start_btn_listener(self) -> None:
        pass  # 由 AutoZZZ 统一处理 F9/F10，避免上游热键启动第二个应用。

    def init_others(self) -> None:
        pass  # 此初始化不启动上游可选遥测服务。

    def _confirm_resource_download(self, title: str, note: str) -> bool:
        return not self.download_cancel.is_set()
