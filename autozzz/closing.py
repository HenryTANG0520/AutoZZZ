"""Normal game exit after compound daily proof; never terminates a process."""
from __future__ import annotations

import time

from .models import AutomationError, FocusLost, ScreenshotNotReady, TaskResult, normalize
from .policy import guard_scene

DIALOG = (.20, .20, .82, .82)
EXIT_PROMPTS = ("是否退出游戏", "确定退出游戏吗", "是否确认退出游戏", "确认退出游戏", "确定要退出游戏吗")


def exit_confirmation(scene):
    """A generic confirmation alone never authorizes an exit click."""
    guard_scene(scene)
    if not scene.has(EXIT_PROMPTS, DIALOG, exact=True) or not scene.has("取消", DIALOG, exact=True):
        return None
    candidates = [t for t in scene.tokens if t.within(DIALOG)
                  and normalize(t.text) in ("确认", "确定", "退出游戏")]
    return candidates[0] if len(candidates) == 1 else None


def close_verified_game(context, timeout=15):
    """Issue one WM_CLOSE to this run's verified window, then wait for its PID."""
    runner = context.runner
    runner._control_checkpoint()
    window = context.window
    watch = window.request_close(runner._control_checkpoint)
    deadline = time.monotonic() + timeout
    confirmed = False
    try:
        while True:
            if watch.exited():
                return TaskResult("close_game", "ok", "已正常关闭本次绝区零窗口，并核实对应游戏进程退出。")
            runner._control_checkpoint()
            if time.monotonic() >= deadline:
                break
            # Most clients exit directly. Read only if they remain open.
            time.sleep(.15)
            if watch.exited():
                continue
            if confirmed:
                continue
            try:
                scene = context.snapshot()
            except (AutomationError, FocusLost, ScreenshotNotReady):
                # The window may vanish before its process finishes cleanup.
                continue
            confirm = exit_confirmation(scene)
            if confirm is not None:
                context.evidence(scene, "auto_exit_confirmation")
                context.click(scene, confirm)
                confirmed = True
        if context.last_scene is not None:
            context.evidence(context.last_scene, "auto_exit_unconfirmed")
        return TaskResult("close_game", "partial", "正常关闭请求已发送，但未核实游戏进程退出；未强制结束。")
    finally:
        watch.close()
