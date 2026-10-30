"""后台输入的前台借用控制；保留显式等待选项与用户新的窗口选择。"""
from __future__ import annotations

import threading
from contextlib import contextmanager,nullcontext
from functools import wraps


def background_mouse_policy(config):
    value=config.get('autozzz_background_mouse','flash')
    if value not in ('wait','flash'):raise ValueError('后台鼠标方式无效。')
    return value


@contextmanager
def physical_input_scope(controller):
    factory=getattr(controller,'physical_input_scope',None)
    with factory() if factory is not None else nullcontext():yield


def physical_input(method):
    @wraps(method)
    def wrapped(controller,*args,**kwargs):
        with physical_input_scope(controller):return method(controller,*args,**kwargs)
    return wrapped


class ForegroundBorrow:
    def __init__(self,control,target,desktop):
        self.control,self.target,self.desktop=control,target,desktop
        self.previous=None;self.previous_identity=None;self.target_identity=None;self.borrowed=False

    def __enter__(self):
        self.control.checkpoint(require_frame=True)
        self.previous=self.desktop.current();self.target_identity=self.desktop.identity(self.target)
        if self.target_identity is None:raise RuntimeError('游戏窗口身份不可用。')
        if self.previous==self.target:return self
        self.previous_identity=self.desktop.identity(self.previous)
        if self.previous_identity is None:raise RuntimeError('无法核实原前台窗口，未切换游戏。')
        def activate():
            if self.desktop.identity(self.target)!=self.target_identity or self.desktop.current()!=self.previous:
                raise RuntimeError('切换前窗口身份或前台已变化。')
            self.borrowed=True;self.desktop.activate(self.target)
        try:
            self.control.input(activate)
            if self.desktop.current()!=self.target:raise RuntimeError('Windows 未允许临时切换前台。')
        except Exception:
            self._restore();raise
        return self

    def _restore(self):
        if not self.borrowed:return
        self.borrowed=False
        # 用户已自行切换窗口时，保留用户新的选择。
        if self.desktop.current()!=self.target:return
        if self.desktop.identity(self.target)!=self.target_identity:return
        if self.desktop.identity(self.previous)!=self.previous_identity:return
        try:self.desktop.activate(self.previous)
        except Exception:
            if self.desktop.current()==self.previous:return
        if self.desktop.current()==self.target:
            message='临时操作结束，但原前台窗口尚未恢复。'
            self.control.stop(message)
            with self.control.lock:self.control.error=self.control.error or message
            raise RuntimeError(message)

    def __exit__(self,kind,value,traceback):
        self._restore();return False


class WindowsDesktop:
    def current(self):
        from .windows import user32
        return user32.GetForegroundWindow()

    def activate(self,hwnd):
        from .windows import user32
        user32.SetForegroundWindow(hwnd)

    def identity(self,hwnd):
        from .windows import user32,kernel32,C,W
        from .task_lease import _created
        if not hwnd or not user32.IsWindow(hwnd):return None
        pid=W.DWORD();user32.GetWindowThreadProcessId(hwnd,C.byref(pid))
        handle=kernel32.OpenProcess(0x101000,False,pid.value)
        if not handle:return None
        try:
            born=_created(kernel32,handle)
            again=W.DWORD();user32.GetWindowThreadProcessId(hwnd,C.byref(again))
            if again.value!=pid.value or kernel32.WaitForSingleObject(handle,0)!=258:return None
            return pid.value,born
        finally:kernel32.CloseHandle(handle)


class PhysicalForeground:
    """一次完整物理输入期间保持前台，释放本次按键后才恢复窗口。"""
    def __init__(self,controller):
        self.controller=controller;self.control=controller.control
        self.acquired=False;self.nested=False;self.borrow=None;self.before=[]

    def __enter__(self):
        controller=self.controller;lock=controller._physical_input_lock
        while not lock.acquire(blocking=False):
            self.control.checkpoint(require_frame=True)
            self.control.changed.wait(.01)
        self.acquired=True
        if controller._physical_input_owner==threading.get_ident():
            self.nested=True
            try:self.control.checkpoint(require_frame=True)
            except BaseException:self._leave();raise
            return self
        self.previous_required=controller._foreground_required
        try:
            self.control.checkpoint(require_frame=True)
            self.before=[(device,set(device.pressed)) for device in
                         (controller.keyboard_controller.keyboard,controller.keyboard_controller.mouse)]
            def claim():
                controller._physical_input_owner=threading.get_ident()
                controller._physical_input_generation=getattr(controller,'_physical_input_generation',0)+1
            self.control.input(claim)
            for name in ('xbox_controller','ds4_controller'):
                device=getattr(controller,name,None)
                if device is not None:device.reset()
            self.borrow=ForegroundBorrow(self.control,controller.game_win.bound.hwnd,controller.desktop)
            self.borrow.__enter__()
            controller._foreground_borrowing=self.borrow.borrowed
            controller._foreground_required=True
            self.control.checkpoint(require_frame=True)
            controller._prepare_physical_mode()
            return self
        except BaseException:
            self._leave();raise

    def _leave(self):
        if not self.acquired:return
        controller=self.controller
        try:
            if not self.nested:
                try:
                    for device,previous in self.before:
                        for key in list(device.pressed-previous):device.release(key)
                except Exception:
                    self.control.stop('临时前台操作的按键未能释放。');raise
                finally:
                    controller._foreground_required=self.previous_required
                    try:
                        if self.borrow is not None:self.borrow.__exit__(None,None,None)
                    finally:
                        controller._foreground_borrowing=False
                        controller._physical_input_owner=None
        finally:
            self.acquired=False;controller._physical_input_lock.release()

    def __exit__(self,kind,value,traceback):
        self._leave();return False


class BackgroundMouse:
    def __init__(self,controller):
        self.controller=controller;self.lock=threading.RLock();self.down=None

    def press(self):
        from .windows import user32
        controller=self.controller
        def send():
            with self.lock:
                if self.down is not None:raise RuntimeError('上一个后台鼠标动作尚未释放。')
                controller._witness_mouse()
                self.down=(controller.game_win.bound.hwnd,controller.game_win.process_id)
                if not user32.PostMessageW(self.down[0],0x201,1,0):raise RuntimeError('后台鼠标按下消息未被接受。')
        controller.control.input(send)

    def reset(self):
        from .windows import user32
        controller=self.controller
        with self.lock:
            if self.down is None:return
            window=controller.game_win
            if not window.identity_valid() or window.bound is None or self.down!=(window.bound.hwnd,window.process_id):
                self.down=None;return
            if not user32.PostMessageW(self.down[0],0x202,0,0):raise RuntimeError('后台鼠标释放消息未被接受。')
            self.down=None
