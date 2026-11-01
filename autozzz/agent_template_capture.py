"""Standalone read-only source capture: real task lock, identity and geometry checks."""
from __future__ import annotations
import hashlib
from pathlib import Path
from types import SimpleNamespace
import uuid

from .run_lock import RunLock
from .trace_capture import capture_directory,atomic_bytes


def capture(root,game_path):
    import cv2
    from .windows import find_game,user32,kernel32,W,C
    from .onedragon_host import BoundGameWindow,WindowCapture
    game=Path(game_path)
    if not game.is_file() or game.name.casefold()!='zenlesszonezero.exe':raise ValueError('请选择有效的绝区零程序。')
    with RunLock():
        bound=find_game(str(game.resolve()));window=BoundGameWindow(1920,1080);window.bound=bound
        pid=W.DWORD();user32.GetWindowThreadProcessId(bound.hwnd,C.byref(pid));window.process_id=pid.value
        window.process_handle=kernel32.OpenProcess(0x101000,False,pid.value);source=None
        try:
            window.init_win()
            def geometry():
                rect=window.win_rect
                return None if rect is None else (rect.x1,rect.y1,rect.x2,rect.y2)
            initial=geometry()
            def valid():
                return (window.identity_valid() and initial is not None and geometry()==initial
                    and user32.GetForegroundWindow()==bound.hwnd and not user32.IsIconic(bound.hwnd)
                    and not user32.GetAsyncKeyState(0x79)&0x8000)
            if not valid():raise ValueError('等待绝区零回到前台后截图。')
            source=WindowCapture(SimpleNamespace(using_background=False),window,1920,1080)
            image=source.get_screenshot(independent=True,resize=False)
            if not valid() or image is None:raise ValueError('窗口身份或尺寸已变化，请重新截图。')
            if image.ndim!=3 or image.shape[2]!=3 or image.shape[0]*image.shape[1]>36_000_000:raise ValueError('截图尺寸无效。')
            ok,png=cv2.imencode('.png',cv2.cvtColor(image,cv2.COLOR_RGB2BGR))
            if not ok or not valid():raise ValueError('截图未完成。')
            data=png.tobytes()
            if len(data)>80_000_000:raise ValueError('截图超过80MB。')
            target=capture_directory(root)/(uuid.uuid4().hex+'.png');atomic_bytes(target,data)
            if not valid():target.unlink(missing_ok=True);raise ValueError('截图后窗口发生变化。')
            return dict(path=str(target),sha256=hashlib.sha256(data).hexdigest(),width=int(image.shape[1]),height=int(image.shape[0]))
        finally:
            if source is not None:source.cleanup()
            window.clear_binding()
