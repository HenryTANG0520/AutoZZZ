"""Game-aligned, non-activating log display; never sends game input."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import ctypes as C
from ctypes import wintypes as W
import time
import tkinter as tk


@dataclass(frozen=True)
class LogLine:
    text: str
    level: str = "info"


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

    def game_bounds(self, handle):
        if (not isinstance(handle, int) or not self.user32.IsWindow(handle)
                or self.user32.IsIconic(handle) or self.user32.GetForegroundWindow() != handle):
            return None
        rect, point = W.RECT(), W.POINT()
        if not self.user32.GetClientRect(handle, C.byref(rect)) or not self.user32.ClientToScreen(handle, C.byref(point)):
            return None
        return point.x, point.y, rect.right-rect.left, rect.bottom-rect.top

    def show(self, handle, bounds):
        if not self.user32.SetWindowPos(handle, W.HWND(-1), *bounds, 0x0010 | 0x0040):
            raise OSError(C.get_last_error(), "覆盖日志定位失败")


class GameLogOverlay:
    def __init__(self, root, game_handle, warn, *, presentation=None):
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

    def _render(self, bounds, game_width):
        width, height = bounds[2:]
        self.canvas.delete("all")
        size = max(12, round(15 * game_width/1920))
        font = ("Microsoft YaHei UI", -size)
        gap = max(3, round(size*.25))
        title = f"AutoZZZ  ·  {self.status}  |  F9 暂停  F10 停止"
        self.canvas.create_text(2, 2, anchor="nw", text=title, width=width-4, fill="#000000", font=font)
        heading = self.canvas.create_text(0, 0, anchor="nw", text=title, width=width-4, fill="#9ce1ff", font=font)
        cutoff = self.canvas.bbox(heading)[3] + gap*2
        y = height-gap
        colors = {"warning": "#ffda82", "error": "#ff9393", "info": "#f0f1f5"}
        for line in reversed(self.lines):
            shadow = self.canvas.create_text(2, y+2, anchor="sw", text=line.text, width=width-6, fill="black", font=font)
            item = self.canvas.create_text(0, y, anchor="sw", text=line.text, width=width-6,
                                           fill=colors.get(line.level, colors["info"]), font=font)
            top = self.canvas.bbox(item)[1]
            if top < cutoff:
                self.canvas.delete(item, shadow)
                break
            y = top-gap
        self._dirty = False

    def _tick(self):
        if self.closed:
            return
        try:
            game = self.presentation.game_bounds(self.game_handle()) if self.handle else None
            bounds = overlay_bounds(*game) if game else None
            if (not self.enabled or not self.handle or not bounds
                    or not (self.active or time.monotonic() < self.linger_until)):
                self._hide()
            else:
                if bounds != self._bounds:
                    self.window.geometry(f"{bounds[2]}x{bounds[3]}")
                    self._bounds, self._dirty = bounds, True
                if self._dirty:
                    self._render(bounds, game[2])
                if not self.visible:
                    self.window.deiconify()
                    self.visible = True
                self.presentation.show(self.handle, bounds)
        except (OSError, tk.TclError):
            self._hide()
        self._timer = self.root.after(200, self._tick)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._timer is not None:
            self.root.after_cancel(self._timer)
        self.window.destroy()
