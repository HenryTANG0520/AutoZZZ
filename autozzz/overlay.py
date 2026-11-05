"""Game-aligned, non-activating log display; never sends game input."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass,field
import ctypes as C
from ctypes import wintypes as W
import time
import tkinter as tk


@dataclass(frozen=True)
class LogLine:
    text: str
    level: str = "info"
    created: float = field(default_factory=time.monotonic)


def overlay_bounds(left, top, width, height):
    if width < 960 or height < 540:
        return None
    return (left + round(width * 20/1920), top + round(height * .73),
            round(width * .30), round(height * .21))


class NativePresentation:
    """Own-window styles: click through, no activation and capture exclusion."""
    def __init__(self, *, protect_capture=True):
        self.user32 = C.WinDLL("user32", use_last_error=True)
        self.protect_capture = protect_capture
        self.user32.GetAncestor.argtypes = [W.HWND, W.UINT]
        self.user32.GetAncestor.restype = W.HWND
        self.user32.GetForegroundWindow.restype = W.HWND
        self.user32.IsWindow.argtypes = [W.HWND]
        self.user32.IsIconic.argtypes = [W.HWND]
        self.user32.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
        self.user32.ClientToScreen.argtypes = [W.HWND, C.POINTER(W.POINT)]
        self.user32.SetWindowDisplayAffinity.argtypes = [W.HWND, W.DWORD]
        self.user32.SetWindowDisplayAffinity.restype = W.BOOL
        self.user32.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
        self.user32.SetWindowPos.restype = W.BOOL
        self.get_style = self.user32.GetWindowLongPtrW
        self.get_style.argtypes = [W.HWND, C.c_int]
        self.get_style.restype = C.c_ssize_t
        self.set_style = self.user32.SetWindowLongPtrW
        self.set_style.argtypes = [W.HWND, C.c_int, C.c_ssize_t]
        self.set_style.restype = C.c_ssize_t

    def prepare(self, window):
        handle = self.user32.GetAncestor(window.winfo_id(), 2)
        style = self.get_style(handle, -20)
        self.set_style(handle, -20, style | 0x20 | 0x80 | 0x80000 | 0x8000000)
        if self.get_style(handle, -20) & 0x80800a0 != 0x80800a0:
            raise OSError("覆盖日志无法启用鼠标穿透与禁止激活")
        # Never put OCR-obscuring logs over the game if Windows cannot exclude them.
        if self.protect_capture and not self.user32.SetWindowDisplayAffinity(handle, 0x11):
            raise OSError(C.get_last_error(), "覆盖日志无法从截图中排除")
        return handle

    def game_bounds(self, handle, *, require_foreground=True):
        if (not isinstance(handle, int) or not self.user32.IsWindow(handle)
                or self.user32.IsIconic(handle) or (require_foreground and self.user32.GetForegroundWindow() != handle)):
            return None
        rect, point = W.RECT(), W.POINT()
        if not self.user32.GetClientRect(handle, C.byref(rect)) or not self.user32.ClientToScreen(handle, C.byref(point)):
            return None
        return point.x, point.y, rect.right-rect.left, rect.bottom-rect.top

    def show(self, handle, bounds):
        if not self.user32.SetWindowPos(handle, W.HWND(-1), *bounds, 0x0010 | 0x0040):
            raise OSError(C.get_last_error(), "覆盖日志定位失败")


class GameLogOverlay:
    def __init__(self, root, game_handle, warn, *, presentation=None,preferences_path=None):
        self.root, self.game_handle, self.warn = root, game_handle, warn
        self.lines = deque(maxlen=40)
        self.enabled = True
        self.active = False
        self.visible = False
        self.closed = False
        self.linger_until = 0.0
        self.status = "准备就绪"
        self._bounds = None
        self._dirty = True
        self._timer = None
        self.log_panel_enabled=True;self.log_fade_seconds=0;self.preference_next=0
        from .trace_preferences import TracePreferences,DEFAULTS
        from copy import deepcopy
        self.preference_store=TracePreferences(preferences_path);self.preferences=deepcopy(DEFAULTS)
        self.desktop_panel=None;self.scale=float(root.tk.call('tk','scaling'))*72/96
        self.window = tk.Toplevel(root, takefocus=False)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg="#010101")
        self.window.attributes("-transparentcolor", "#010101")
        self.canvas = tk.Canvas(self.window, bg="#010101", highlightthickness=0, takefocus=False)
        self.canvas.pack(fill="both", expand=True)
        self.window.update_idletasks()
        self.presentation = presentation or NativePresentation()
        try:
            self.handle = self.presentation.prepare(self.window)
        except OSError as exc:
            self.handle = None
            self.warn(f"覆盖日志已隐藏，以免影响游戏识别：{exc}", "warning")
        self._tick()

    def begin(self, status):
        self.lines.clear()
        self.active, self.linger_until = True, 0.0
        self.set_status(status)

    def append(self, message, level="info"):
        self.lines.append(LogLine(f"{time.strftime('%H:%M:%S')}  {message}", level))
        self._dirty = True

    def set_status(self, value):
        if self.status != value:
            self.status, self._dirty = value, True

    def set_enabled(self, value):
        self.enabled = bool(value)
        if not self.enabled:
            self._hide()

    def finish(self, status):
        self.active = False
        self.linger_until = time.monotonic() + 6
        self.set_status(status)

    def _hide(self):
        if self.visible:
            self.window.withdraw()
            self.visible = False
        panel=getattr(self,'desktop_panel',None)
        if panel is not None:panel.hide()

    def save_panel(self,key,changes):
        # Load the latest revision before editing only log presentation; other windows' fields survive.
        value=self.preference_store.load();value['panels']['log'].update(changes)
        self.preference_store.save(value);self.preferences=value;self._dirty=True

    def _desktop_render(self,canvas,bounds,config,color):
        self._render_text(canvas,bounds,max(8,round(config['font_size']*96/72*self.scale)),color)

    def _render(self, bounds, game_width):
        config=self.preferences['panels']['log']
        self._render_text(self.canvas,bounds,max(12,round(config['font_size']*96/72*game_width/1920)),self.preferences['text_color'])
        self._dirty=False

    def _render_text(self,canvas,bounds,size,color):
        width, height = bounds[2:]
        canvas.delete("all")
        font = ("Microsoft YaHei UI", -size)
        gap = max(3, round(size*.25))
        title = f"AutoZZZ  ·  {self.status}  |  F9 暂停  F10 停止"
        canvas.create_text(2, 2, anchor="nw", text=title, width=width-4, fill="#000000", font=font)
        heading = canvas.create_text(0, 0, anchor="nw", text=title, width=width-4, fill="#9ce1ff", font=font)
        cutoff = canvas.bbox(heading)[3] + gap*2
        y = height-gap
        colors = {"warning": "#ffda82", "error": "#ff9393", "info": color}
        for line in reversed(self.lines):
            shadow = canvas.create_text(2, y+2, anchor="sw", text=line.text, width=width-6, fill="black", font=font)
            item = canvas.create_text(0, y, anchor="sw", text=line.text, width=width-6,
                                           fill=colors.get(line.level, colors["info"]), font=font)
            top = canvas.bbox(item)[1]
            if top < cutoff:
                canvas.delete(item, shadow)
                break
            y = top-gap

    def _tick(self):
        if self.closed:
            return
        try:
            now=time.monotonic()
            if now>=self.preference_next:
                self.preference_next=now+1
                try:
                    from .trace_preferences import TracePreferences
                    preferences=(self.preference_store if hasattr(self,'preference_store') else TracePreferences()).load()
                    if preferences!=getattr(self,'preferences',None):self._dirty=True
                    self.preferences=preferences
                    self.log_panel_enabled=preferences['log_panel_enabled']
                    self.log_fade_seconds=preferences['log_fade_seconds']
                    if self.lines.maxlen!=preferences['log_max_lines']:
                        self.lines=deque(self.lines,maxlen=preferences['log_max_lines']);self._dirty=True
                except (OSError,ValueError):pass
            while self.log_fade_seconds and self.lines and now-self.lines[0].created>self.log_fade_seconds:
                self.lines.popleft();self._dirty=True
            config=self.preferences['panels']['log'];desktop=config['mode']=='desktop'
            game = (self.presentation.game_bounds(self.game_handle(),require_foreground=False) if desktop
                    else self.presentation.game_bounds(self.game_handle())) if self.handle else None
            from .trace_preferences import panel_bounds
            bounds=None
            if game and game[2]>=960 and game[3]>=540:
                x,y,w,h=panel_bounds(config,game[2:]);bounds=(game[0]+x,game[1]+y,w,h)
            if (not self.enabled or not self.log_panel_enabled or not config['visible'] or not self.handle or not bounds
                    or not (self.active or time.monotonic() < self.linger_until)):
                self._hide()
            elif desktop:
                if self.visible:self.window.withdraw();self.visible=False
                if self.desktop_panel is None:
                    from .trace_panels_ui import TracePanel
                    self.desktop_panel=TracePanel(self.root,'log',self.scale,self.save_panel,
                        presentation=self.presentation,renderer=self._desktop_render)
                self.desktop_panel.show([],config,(config['desktop_x'],config['desktop_y'],
                    round(config['w']*self.scale),round(config['h']*self.scale)),self.preferences['text_color'])
            else:
                if self.desktop_panel is not None:self.desktop_panel.hide()
                self.window.attributes('-alpha',config['opacity']/100)
                if bounds != self._bounds:
                    self.window.geometry(f"{bounds[2]}x{bounds[3]}")
                    self._bounds, self._dirty = bounds, True
                if self._dirty:
                    self._render(bounds, game[2])
                if not self.visible:
                    self.window.deiconify()
                    self.visible = True
                self.presentation.show(self.handle, bounds)
                self.handle=self.presentation.prepare(self.window)
        except (OSError, tk.TclError):
            self._hide()
        self._timer = self.root.after(getattr(self,'preferences',{}).get('follow_interval_ms',200), self._tick)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._timer is not None:
            self.root.after_cancel(self._timer)
        if self.desktop_panel is not None:self.desktop_panel.close()
        self.window.destroy()
