"""A passive Windows keyboard-event source; never reserves or consumes a key."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import ctypes
from ctypes import wintypes as W
import threading
import time

MODIFIER_CODES=(160,161,162,163,164,165,91,92)
MODIFIER_GROUPS={16:(160,161),17:(162,163),18:(164,165),91:(91,92)}
F10=0x79
_MOUSE_BUTTON_MESSAGES = {
    0x0201: (1, True), 0x0202: (1, False),    # left
    0x0204: (2, True), 0x0205: (2, False),    # right
    0x0207: (4, True), 0x0208: (4, False),    # middle
    0x020B: (0, True), 0x020C: (0, False),    # X1 or X2, identified in mouseData
}


@dataclass(frozen=True)
class KeyEdge:
    kind:str
    generation:int
    created:float
    foreground:int
    code:int
    injected:bool
    lower_integrity_injected:bool
    cancel_epoch:int
    os_time:int=0


class KeyboardEdgeQueue:
    """Pure reducer: retain modifiers and the selected key, never arbitrary text."""
    def __init__(self,limit=32):
        self.lock=threading.RLock();self.limit=limit;self.events=deque()
        self.combo=();self.generation=0;self.pressed=set();self.closed=False
        self.cancel_epoch=0;self.dropped=0;self.presses=0;self.releases=0;self.ignored_repeats=0

    def configure(self,combo,generation,pressed=()):
        combo=tuple(combo)
        if (type(generation)is not int or generation<0 or combo and
            (len(combo)>5 or any(type(key)is not int or key not in MODIFIER_GROUPS for key in combo[:-1])
             or type(combo[-1])is not int or not 1<=combo[-1]<=254 or combo[-1]in (*MODIFIER_GROUPS,*MODIFIER_CODES,0x78,0x79))):
            raise ValueError('全局截图事件配置无效。')
        with self.lock:
            self.combo=combo;self.generation=generation;self.events.clear()
            relevant={*MODIFIER_CODES,F10,*combo[-1:]}
            self.pressed=set(pressed)&relevant

    @staticmethod
    def normalized(code,scan=0,flags=0):
        if code==16:return 161 if scan==0x36 else 160
        if code==17:return 163 if flags&1 else 162
        if code==18:return 165 if flags&1 else 164
        return code

    def feed(self,code,down,*,foreground,created=None,flags=0,scan=0,os_time=0):
        code=self.normalized(code,scan,flags)
        with self.lock:
            if self.closed or code not in {*MODIFIER_CODES,F10,*self.combo[-1:]}:return
            previous=code in self.pressed
            if down:self.pressed.add(code)
            else:self.pressed.discard(code)
            if code==F10 and down and not previous:
                self.cancel_epoch+=1;self.events.clear()
                self._append(KeyEdge('cancel',self.generation,time.monotonic() if created is None else created,
                    int(foreground or 0),code,bool(flags&0x10),bool(flags&2),self.cancel_epoch,os_time))
            if not self.combo or code!=self.combo[-1]:return
            if down:
                self.presses+=1
                if previous:self.ignored_repeats+=1;return
            else:
                self.releases+=1;return
            modifiers={group for group,keys in MODIFIER_GROUPS.items() if any(key in self.pressed for key in keys)}
            if F10 in self.pressed or modifiers!=set(self.combo[:-1]):return
            self._append(KeyEdge('capture',self.generation,time.monotonic() if created is None else created,
                int(foreground or 0),code,bool(flags&0x10),bool(flags&2),self.cancel_epoch,os_time))

    def _append(self,event):
        if len(self.events)>=self.limit:
            self.events.popleft();self.dropped+=1
        self.events.append(event)

    def drain(self):
        with self.lock:
            result=list(self.events);self.events.clear();return result

    def status(self):
        with self.lock:
            return dict(generation=self.generation,cancel_epoch=self.cancel_epoch,queue_size=len(self.events),
                queue_dropped=self.dropped,key_down_events=self.presses,key_up_events=self.releases,ignored_repeats=self.ignored_repeats)

    def close(self):
        with self.lock:self.closed=True;self.events.clear()


class ReadOnlyKeyboardEvents:
    """Passive Win32 hooks on one message thread; all callbacks pass through.

    The default reducer retains only the screenshot key, F10 and modifiers.
    A run-key reducer can request mouse buttons. Movement and wheel events are ignored.
    Startup/installation failure is observable; there is no silent reliable flag.
    """
    def __init__(self, queue=None, *, mouse_enabled=None):
        self.queue=KeyboardEdgeQueue() if queue is None else queue
        self._stop=threading.Event();self.ready=threading.Event();self._close_lock=threading.Lock()
        self._state='starting';self._error='';self._thread_id=None;self._user32=None;self._hook=None;self._callback=None
        self._mouse_enabled = mouse_enabled
        self._mouse_state = 'disabled'
        self._mouse_error = ''
        self.worker=threading.Thread(target=self._run,name='AutoZZZ-ReadOnly-Keyboard',daemon=False)
        self.worker.start()

    def configure(self,combo,generation):
        # Seed outside the hook callback. Windows async key state is not updated
        # yet inside LowLevelKeyboardProc, so callbacks use the reducer instead.
        pressed=[]
        try:
            user32=ctypes.WinDLL('user32',use_last_error=True)
            user32.GetAsyncKeyState.argtypes=[ctypes.c_int];user32.GetAsyncKeyState.restype=ctypes.c_short
            for code in (*MODIFIER_CODES,F10,*tuple(combo)[-1:]):
                if user32.GetAsyncKeyState(code)&0x8000:pressed.append(code)
        except (AttributeError,OSError):pass
        self.queue.configure(combo,generation,pressed)

    def drain(self):return self.queue.drain()

    def status(self):
        result=self.queue.status()|dict(state=self._state,error=self._error,thread_alive=self.worker.is_alive(),
            installed=self._state=='listening')
        if self._mouse_enabled is not None:
            result.update(mouse_state=self._mouse_state, mouse_error=self._mouse_error)
        return result

    def refresh_mouse(self):
        """Wake the message thread after the selected mouse buttons change."""
        with self._close_lock:
            if not self._stop.is_set() and self._user32 is not None and self._thread_id is not None:
                self._user32.PostThreadMessageW(self._thread_id, 0x8000, 0, 0)

    def close(self):
        with self._close_lock:
            if self._stop.is_set():return
            self._stop.set();self.queue.close()
            # Thread IDs can be reused. Finally clears ours under this lock
            # before exit, and repeated close never posts to an old thread ID.
            # A failed post is harmless: the pump checks stop every 100 ms.
            if self._user32 is not None and self._thread_id is not None:
                self._user32.PostThreadMessageW(self._thread_id,0x12,0,0)

    def join(self,timeout=None):
        if threading.current_thread() is not self.worker:self.worker.join(timeout)

    def _run(self):
        hook=mouse_hook=None;user32=None
        try:
            user32=ctypes.WinDLL('user32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            self._user32=user32
            result_type=ctypes.c_ssize_t
            callback_type=ctypes.WINFUNCTYPE(result_type,ctypes.c_int,W.WPARAM,W.LPARAM)
            class KeyboardData(ctypes.Structure):
                _fields_=[('vkCode',W.DWORD),('scanCode',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('dwExtraInfo',ctypes.c_size_t)]
            user32.SetWindowsHookExW.argtypes=[ctypes.c_int,callback_type,W.HINSTANCE,W.DWORD];user32.SetWindowsHookExW.restype=W.HHOOK
            user32.CallNextHookEx.argtypes=[W.HHOOK,ctypes.c_int,W.WPARAM,W.LPARAM];user32.CallNextHookEx.restype=result_type
            user32.UnhookWindowsHookEx.argtypes=[W.HHOOK];user32.UnhookWindowsHookEx.restype=W.BOOL
            user32.GetForegroundWindow.restype=W.HWND
            user32.PeekMessageW.argtypes=[ctypes.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT];user32.PeekMessageW.restype=W.BOOL
            user32.TranslateMessage.argtypes=[ctypes.POINTER(W.MSG)]
            user32.DispatchMessageW.argtypes=[ctypes.POINTER(W.MSG)];user32.DispatchMessageW.restype=result_type
            user32.PostThreadMessageW.argtypes=[W.DWORD,W.UINT,W.WPARAM,W.LPARAM];user32.PostThreadMessageW.restype=W.BOOL
            user32.MsgWaitForMultipleObjects.argtypes=[W.DWORD,ctypes.c_void_p,W.BOOL,W.DWORD,W.DWORD];user32.MsgWaitForMultipleObjects.restype=W.DWORD
            kernel.GetCurrentThreadId.restype=W.DWORD;kernel.GetModuleHandleW.argtypes=[W.LPCWSTR];kernel.GetModuleHandleW.restype=W.HMODULE
            self._thread_id=kernel.GetCurrentThreadId();message=W.MSG()
            user32.PeekMessageW(ctypes.byref(message),None,0,0,0)  # Create this thread's message queue.
            @callback_type
            def callback(code,message_id,data_pointer):
                try:
                    if code>=0 and message_id in (0x100,0x101,0x104,0x105):
                        value=ctypes.cast(data_pointer,ctypes.POINTER(KeyboardData)).contents
                        received=time.monotonic()
                        self.queue.feed(value.vkCode,message_id in (0x100,0x104),foreground=user32.GetForegroundWindow(),
                            created=received,flags=value.flags,scan=value.scanCode,os_time=value.time)
                except BaseException as exc:
                    # The hook must never turn a Python callback error into input suppression.
                    self._error='键盘事件处理失败：'+str(exc)
                return user32.CallNextHookEx(hook,code,message_id,data_pointer)
            self._callback=callback
            if self._stop.is_set():return
            module=kernel.GetModuleHandleW(None)
            hook=user32.SetWindowsHookExW(13,callback,module,0)
            if not hook:raise OSError(ctypes.get_last_error(),'无法安装只读键盘事件监听')
            self._hook=hook

            sync_mouse = None
            if self._mouse_enabled is not None:
                class MouseData(ctypes.Structure):
                    _fields_ = [('point', W.POINT), ('button_data', W.DWORD), ('flags', W.DWORD),
                                ('time', W.DWORD), ('extra', ctypes.c_size_t)]

                @callback_type
                def mouse_callback(code, message_id, data_pointer):
                    try:
                        button = _MOUSE_BUTTON_MESSAGES.get(message_id) if code >= 0 and self._mouse_state == 'listening' else None
                        if button is not None:
                            value = ctypes.cast(data_pointer, ctypes.POINTER(MouseData)).contents
                            key, down = button
                            if key == 0:
                                key = {1: 5, 2: 6}.get(value.button_data >> 16)
                            if key is not None:
                                # Align LLMHF flags with the existing LLKHF input reducer.
                                flags = ((value.flags & 1) << 4) | (value.flags & 2)
                                received = time.monotonic()
                                self.queue.feed(key, down, foreground=user32.GetForegroundWindow(),
                                                created=received, flags=flags, os_time=value.time)
                    except BaseException as exc:
                        self._mouse_error = '鼠标事件处理失败：' + str(exc)
                    return user32.CallNextHookEx(mouse_hook, code, message_id, data_pointer)

                mouse_requested = False

                def sync_mouse():
                    nonlocal mouse_hook, mouse_requested
                    requested = self._mouse_enabled()
                    if requested == mouse_requested:
                        return
                    mouse_requested = requested
                    if requested:
                        if mouse_hook is not None:
                            return  # Previous removal was uncertain. Never create a duplicate hook.
                        mouse_hook = user32.SetWindowsHookExW(14, mouse_callback, module, 0) or None
                        if mouse_hook is None:
                            self._mouse_state = 'failed'
                            self._mouse_error = '无法安装只读鼠标事件监听：' + str(ctypes.get_last_error())
                        else:
                            self._mouse_state = 'listening'
                            self._mouse_error = ''
                    elif mouse_hook is None or user32.UnhookWindowsHookEx(mouse_hook):
                        mouse_hook = None
                        self._mouse_state = 'disabled'
                        self._mouse_error = ''
                    else:
                        self._mouse_state = 'failed'
                        self._mouse_error = '无法核实鼠标事件监听卸载：' + str(ctypes.get_last_error())

                sync_mouse()
            self._state='listening';self.ready.set()
            while not self._stop.is_set():
                result=user32.MsgWaitForMultipleObjects(0,None,False,100,0x04FF)
                if result==0xFFFFFFFF:raise OSError(ctypes.get_last_error(),'键盘事件消息等待失败')
                while user32.PeekMessageW(ctypes.byref(message),None,0,0,1):
                    if message.message==0x12:return
                    user32.TranslateMessage(ctypes.byref(message));user32.DispatchMessageW(ctypes.byref(message))
                if sync_mouse is not None and not self._stop.is_set():
                    sync_mouse()
        except BaseException as exc:
            self._state='failed';self._error=str(exc)
        finally:
            if mouse_hook is not None and user32 is not None:
                if not user32.UnhookWindowsHookEx(mouse_hook):
                    self._mouse_error = '无法核实鼠标事件监听卸载：' + str(ctypes.get_last_error())
            if self._mouse_enabled is not None:
                self._mouse_state = 'closed'
            if hook and user32 is not None:
                if not user32.UnhookWindowsHookEx(hook):
                    self._error='无法核实键盘事件监听卸载：'+str(ctypes.get_last_error())
            self._hook=None
            with self._close_lock:self._thread_id=None
            if self._state!='failed':self._state='closed'
            self.ready.set()
