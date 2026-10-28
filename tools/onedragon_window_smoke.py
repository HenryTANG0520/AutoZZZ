"""用隔离子进程的隐藏测试窗口验证绑定与退出，不访问游戏。"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "onedragon" / "src"))


def main():
    import numpy as np
    from autozzz.onedragon_host import ManagedController
    from autozzz.onedragon_control import ExecutionControl, WindowState
    from autozzz.windows import GameWindow, kernel32, W, C, user32
    code = '''import ctypes as C,json,sys,os
from ctypes import wintypes as W
u=C.WinDLL('user32',use_last_error=True)
u.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p]
u.CreateWindowExW.restype=W.HWND
u.DestroyWindow.argtypes=[W.HWND]
hwnd=u.CreateWindowExW(0,'STATIC','AutoZZZ isolated identity test',0,0,0,960,540,None,None,None,None)
if not hwnd: raise RuntimeError(C.get_last_error())
print(json.dumps({'hwnd':hwnd,'pid':os.getpid()}),flush=True)
for command in sys.stdin:
 if command.strip()=='destroy':
  u.DestroyWindow(hwnd)
  print('destroyed',flush=True)
 else: break
'''
    process = subprocess.Popen([sys.executable, "-u", "-c", code], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    no_op = lambda: None
    control = ExecutionControl(lambda: WindowState(False, False), no_op, no_op, no_op, no_op)
    ctx = SimpleNamespace(game_config=SimpleNamespace(get_action_keys=lambda _: {}, get_gamepad_action_keys=lambda: {}),
                          env_config=SimpleNamespace(screenshot_method="auto"),
                          project_config=SimpleNamespace(screen_standard_width=1920, screen_standard_height=1080))
    controller = ManagedController(ctx, control)
    window = controller.game_win
    try:
        details = json.loads(process.stdout.readline())
        window.bound = GameWindow(details["hwnd"])
        window.process_id = details["pid"]
        window.process_handle = kernel32.OpenProcess(0x101000, False, details["pid"])
        assert window.process_handle
        assert window.identity_valid()
        window.init_win()
        assert window.get_hwnd() == details["hwnd"]
        captured = []
        for strategy in controller.screenshot_controller.strategies.values():
            def record(rect, independent=False, strategy=strategy):
                assert strategy.game_win.get_hwnd() == details["hwnd"]
                captured.append((rect.width, rect.height))
                return np.full((rect.height, rect.width, 3), 50, dtype=np.uint8)
            strategy.capture = record
        expected = window.win_rect
        frame = controller.get_screenshot(independent=True)
        assert frame is not None and frame.shape == (1080, 1920, 3)
        assert captured == [(expected.width, expected.height)]
        foreground = user32.GetForegroundWindow()
        assert not window.active() and user32.GetForegroundWindow() == foreground
        window.process_id += 1
        assert not window.identity_valid(), "同一路径也不能忽略 PID 变化"
        window.process_id = details["pid"]
        process.stdin.write("destroy\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "destroyed"
        assert process.poll() is None, "销毁窗口时保留测试进程，单独验证窗口身份"
        assert not window.identity_valid()
        window.init_win()
        assert window._hWnd is None and window._win is None
        process.stdin.close()
        assert process.wait(timeout=5) == 0, process.stderr.read()
        assert kernel32.WaitForSingleObject(window.process_handle, 0) == 0
        assert not window.identity_valid()
        print(json.dumps(dict(checks=["exact_window_pid_and_process_handle", "query_does_not_activate",
                                      "destroyed_window_not_replaced", "terminated_process_stays_invalid",
                                      "actual_screenshot_pipeline_uses_retained_window"],
                              game_accessed=False, game_input_sent=False)))
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        window.clear_binding()
        controller.screenshot_controller.cleanup()


if __name__ == "__main__":
    main()
