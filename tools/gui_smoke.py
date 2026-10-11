"""Exercise the desktop controls with a fake backend; never access the game."""
from __future__ import annotations

import json
import sys
import threading
import time
import tkinter as tk
import types
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from autozzz import windows  # noqa: E402,F401 - DPI awareness before Tk
from autozzz.config import GAME_PATHS, Settings  # noqa: E402
from autozzz.gui import AutoZZZWindow  # noqa: E402
from PIL import ImageGrab  # noqa: E402

OUTPUT = PROJECT_ROOT / "runs" / "gui-smoke"
OUTPUT.mkdir(parents=True, exist_ok=True)
CONFIG = OUTPUT / "mock-config.json"
RUN_DIR = OUTPUT / "mock-run"
RUN_DIR.mkdir(exist_ok=True)


def pump(root: tk.Tk, predicate=lambda: True, timeout: float = 4) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for the GUI event.")


def enabled(widget: tk.Widget) -> bool:
    return str(widget.cget("state")) != "disabled"


def destroy(root: tk.Tk) -> None:
    try:
        for callback in root.tk.splitlist(root.tk.call("after", "info")):
            root.after_cancel(callback)
        root.destroy()
    except tk.TclError:
        pass


def screenshot(root: tk.Tk, name: str) -> None:
    root.update_idletasks()
    ImageGrab.grab(window=root.winfo_id()).save(OUTPUT / name)


def layout_check(root: tk.Tk) -> None:
    def walk(widget):
        for child in widget.winfo_children():
            if child.winfo_ismapped():
                x = child.winfo_rootx() - root.winfo_rootx()
                y = child.winfo_rooty() - root.winfo_rooty()
                assert x >= 0 and y >= 0, (str(child), x, y)
                assert x + child.winfo_width() <= root.winfo_width() + 1, (str(child), "width")
                assert y + child.winfo_height() <= root.winfo_height() + 1, (str(child), "height")
                if child.winfo_class() in ("TLabel", "TButton", "TCheckbutton", "TEntry", "TCombobox"):
                    assert child.winfo_height() >= child.winfo_reqheight(), (str(child), "clipped height")
                walk(child)
    walk(root)


class FakeRunner:
    def __init__(self, settings, emit):
        self.settings, self.emit = settings, emit
        self.paused = False
        self.stop_calls = 0
        self.release = threading.Event()

    def run(self):
        self.emit({"type": "task", "task": "scratch", "status": "running", "detail": "模拟刮刮卡"})
        self.release.wait(8)
        stopped = bool(self.stop_calls)
        report = {
            "status": "stopped" if stopped else "success", "engagement": None if stopped else 400,
            "stamina_spent": 0, "daily_verified": not stopped, "run_dir": str(RUN_DIR),
            "results": [{"task": name, "status": "stopped" if stopped else "ok", "detail": "模拟结果"}
                        for name in ("scratch", "coffee", "video", "daily") if getattr(self.settings, name)],
        }
        (RUN_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        self.emit({"type": "finished", "report": report})
        return report

    def stop(self):
        self.stop_calls += 1
        self.release.set()

    def toggle_pause(self):
        self.paused = not self.paused
        self.emit({"type": "paused", "paused": self.paused})
        return self.paused


def main() -> int:
    fake = types.ModuleType("autozzz.app")
    fake.Runner = FakeRunner
    fake.inspect_game = lambda _: {
        "title": "绝区零（模拟检测）", "width": 1920, "height": 1080, "administrator": True,
        "ocr_text": "模拟识别文字\n实训\n活跃度 400\n今日已领取", "screenshot": str(OUTPUT / "initial.png"),
    }
    fake.launch_game = lambda _: "模拟启动请求，未访问游戏。"
    Settings().save(CONFIG)

    with patch.dict(sys.modules, {"autozzz.app": fake}), \
            patch("autozzz.gui.messagebox.showerror") as errors, \
            patch("autozzz.gui.os.startfile") as opened:
        root = tk.Tk()
        ui = AutoZZZWindow(root, CONFIG)
        try:
            root.geometry(f"{round(1060 * ui.ui_scale)}x{round(830 * ui.ui_scale)}")
            pump(root)
            layout_check(root)
            screenshot(root, "initial.png")
            root.geometry(f"{round(960 * ui.ui_scale)}x{round(780 * ui.ui_scale)}")
            pump(root)
            layout_check(root)
            screenshot(root, "minimum.png")
            root.geometry(f"{round(1060 * ui.ui_scale)}x{round(830 * ui.ui_scale)}")
            pump(root)

            ui.server.set("国际服")
            ui._select_server()
            assert ui.game_path.get() == GAME_PATHS["global"] and not ui.busy
            assert Settings.load(CONFIG).server == "cn", "Changing the selection must not start or save."
            ui._save_settings()
            assert Settings.load(CONFIG).server == "global"
            assert Settings.load(CONFIG).promoter == ""
            ui.promoter.set("  安比  ")
            assert "安比" in ui.promoter_hint.get()
            ui._save_settings()
            assert Settings.load(CONFIG).promoter == "安比" and ui.promoter.get() == "安比"
            ui.promoter.set("不在角色名单的名字")
            assert ui._read_settings() is None and errors.call_count == 1
            assert Settings.load(CONFIG).promoter == "安比", "Invalid names must not overwrite saved settings."
            ui.promoter.set("")
            ui._save_settings()
            assert Settings.load(CONFIG).promoter == ""
            ui.server.set("国服")
            ui._select_server()
            ui.tasks["video"].set(False)
            ui._save_settings()
            assert Settings.load(CONFIG).video is False
            ui.tasks["video"].set(True)
            ui.action_delay.set("wrong")
            assert ui._read_settings() is None and errors.call_count == 2
            ui.action_delay.set("1.2")
            ui._save_settings()
            ui._check()
            assert ui.busy
            pump(root, lambda: not ui.busy)
            assert "1920" in ui.inspection_text.get("1.0", "end")
            assert "模拟识别文字" not in ui.log.get("1.0", "end"), "Full OCR belongs in the inspection tab."
            assert enabled(ui.screenshot_button)
            ui.screenshot_button.invoke()
            assert opened.call_count == 1
            screenshot(root, "inspection.png")

            ui._launch()
            pump(root, lambda: not ui.busy)
            assert ui.status.get() == "启动请求已发送"
            ui._start()
            pump(root, lambda: ui.runner is not None)
            assert ui.running and all(not enabled(control) for control in ui.setting_controls)
            runner = ui.runner
            with patch.object(ui, "_restore") as restored, patch.object(root, "iconify") as minimized:
                ui.emit({"type": "focus_wait", "waiting": True})
                pump(root, lambda: ui.focus_waiting)
                assert ui.status.get() == "等待游戏回到前台" and ui.running and ui.busy
                assert not ui.paused and ui.pause_button.cget("text") == "暂停"
                assert runner.stop_calls == 0
                ui.emit({"type": "focus_wait", "waiting": False})
                pump(root, lambda: not ui.focus_waiting)
                assert ui.status.get() == "正在运行"
                restored.assert_not_called()
                minimized.assert_not_called()
            ui._restore()
            root.focus_force()
            root.event_generate("<KeyPress-F9>")
            root.event_generate("<KeyRelease-F9>")
            pump(root, lambda: ui.paused)
            assert ui.pause_button.cget("text") == "继续"
            root.event_generate("<KeyRelease-F9>")
            pump(root)
            assert ui.paused, "An unmatched key release must not resume."
            ui.emit({"type": "focus_wait", "waiting": True})
            pump(root, lambda: ui.focus_waiting)
            assert ui.status.get() == "已暂停"
            ui.emit({"type": "focus_wait", "waiting": False})
            pump(root, lambda: not ui.focus_waiting)
            assert ui.paused and ui.status.get() == "已暂停", "Focus return must preserve manual pause."
            screenshot(root, "paused.png")
            ui._pause()
            pump(root, lambda: not ui.paused)
            ui._pause()
            pump(root, lambda: ui.paused)
            ui._stop()
            ui._stop()
            ui._pause_state(True)
            assert ui.stopping and not ui.paused and not enabled(ui.pause_button)
            pump(root, lambda: not ui.busy)
            assert runner.stop_calls == 1 and ui.status.get() == "已停止"
            assert enabled(ui.report_button)
            ui.report_button.invoke()
            ui.directory_button.invoke()
            assert opened.call_count == 3
            screenshot(root, "stopped.png")

            ui._start()
            pump(root, lambda: ui.runner is not None)
            ui.runner.release.set()
            pump(root, lambda: not ui.busy)
            assert "400" in ui.engagement.get() and "每日已验证" in ui.summary.get()
            assert ui.status.get() == "每日任务已完成"
            assert all(ui.table.item(name, "values")[1] == "完成" for name in ui.tasks)
            screenshot(root, "finished.png")
            # Once authoritative completion is recorded, a later incidental error
            # cannot turn the displayed daily goal back into a failure.
            ui._finished({"status": "failed", "engagement": 400, "daily_verified": True,
                          "results": [{"task": "video", "status": "failed", "detail": "late focus error"}]})
            assert ui.status.get() == "每日任务已完成"
            assert all(ui.table.item(name, "values")[1] == "完成" for name in ui.tasks)
            ui.emit({"type": "error", "message": "late notification error"})
            pump(root)
            assert ui.status.get() == "每日任务已完成", "Later UI notification errors must preserve authoritative completion."
            ui._finished({"status": "partial", "engagement": 400, "daily_verified": False, "results": []})
            assert ui.status.get() == "部分完成" and "每日未验证" in ui.summary.get(), "400 without claimed evidence must not complete."
        finally:
            if ui.runner is not None:
                ui.runner.stop()
            if ui.worker is not None:
                ui.worker.join(timeout=3)
            destroy(root)

        # Closing during worker startup must also deliver the stop request.
        root = tk.Tk()
        ui = AutoZZZWindow(root, CONFIG)
        ui._start()
        ui._close()
        assert ui.closing and ui.stop_requested.is_set()
        pump(root, lambda: not ui.worker.is_alive())
        ui.worker.join(timeout=3)
        assert not ui.worker.is_alive(), "Close must stop and await the worker."
        destroy(root)

    print("GUI smoke passed: layout/server/path/tasks/save/validation/check/launch/focus-wait/F9/pause/stop/report/close.")
    print(f"Screenshots: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
