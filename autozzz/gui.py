"""Chinese desktop controls for the non-combat daily workflow."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .config import CONFIG_PATH, GAME_PATHS, ROOT, Settings

DAILY_TASK_NAMES = {"scratch": "每日签到（刮卡/盲盒）", "coffee": "咖啡店喝咖啡", "video": "录像店营业", "daily": "验证活跃度并领取奖励"}
FINISH_TASK_NAMES = {"weekly": "丽都周纪奖励", "city_fund": "丽都城募奖励"}
TASK_NAMES = {**DAILY_TASK_NAMES, **FINISH_TASK_NAMES}
SERVER_NAMES = {"cn": "国服", "global": "国际服"}
STATUS_NAMES = {
    "pending": "等待", "running": "进行中", "ok": "完成", "success": "全部完成",
    "partial": "部分完成", "failed": "失败", "stopped": "已停止", "skipped": "跳过",
}


class AutoZZZWindow:
    def __init__(self, root: tk.Tk, config_path: Path = CONFIG_PATH, *, server: str | None = None,
                 game_path: str | None = None):
        self.root, self.config_path = root, Path(config_path)
        load_error = None
        try:
            self.settings = Settings.load(self.config_path)
        except (ValueError, TypeError, OSError) as exc:
            self.settings, load_error = Settings(), str(exc)
        if server:
            self.settings.select_server(server)
        if game_path:
            self.settings.game_path = game_path
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.runner = None
        self.stop_requested = threading.Event()
        self.busy = self.running = self.paused = self.stopping = self.closing = False
        self.focus_waiting = False
        self.worker_done = False
        self._daily_goal_complete = False
        self.operation = "daily"
        self.overlay_game_handle = None
        self.started_at: float | None = None
        self.last_output_dir: Path | None = None
        self.last_report_file: Path | None = None
        self.last_screenshot: Path | None = None
        self._f9_pressed = False
        self.server = tk.StringVar(root, SERVER_NAMES[self.settings.server])
        self.game_path = tk.StringVar(root, self.settings.game_path)
        self.auto_launch = tk.BooleanVar(root, self.settings.auto_launch)
        self.overlay_logs = tk.BooleanVar(root, self.settings.overlay_logs)
        self.tasks = {name: tk.BooleanVar(root, getattr(self.settings, name)) for name in TASK_NAMES}
        self.action_delay = tk.StringVar(root, str(self.settings.action_delay))
        self.transition_timeout = tk.StringVar(root, str(self.settings.transition_timeout))
        self.ocr_confidence = tk.StringVar(root, str(self.settings.ocr_confidence))
        self.promoter = tk.StringVar(root, self.settings.promoter)
        self.promoter_hint = tk.StringVar(root, "留空选择第一位；账号未拥有时回退第一位。")
        self.status = tk.StringVar(root, "准备就绪")
        self.summary = tk.StringVar(root, "点击启动游戏进入主界面，或直接开始每日。")
        self.elapsed = tk.StringVar(root, "00:00")
        self.engagement = tk.StringVar(root, "未验证")
        self.saved = tk.StringVar(root, "配置按“保存设置”或开始任务时保存。")
        self.setting_controls: list[ttk.Widget] = []
        self._build()
        from .battle_ui import BattlePanel
        self.combat_panel = BattlePanel(self)
        from .overlay import GameLogOverlay
        self.overlay = GameLogOverlay(root, lambda: self.overlay_game_handle, self._log)
        self.overlay.set_enabled(self.overlay_logs.get())
        self.overlay_logs.trace_add("write", self._overlay_enabled)
        self.status.trace_add("write", self._overlay_status)
        self._saved_form = self._form_signature()
        for variable in (self.server, self.game_path, self.auto_launch, self.overlay_logs, self.action_delay,
                         self.transition_timeout, self.ocr_confidence, self.promoter, *self.tasks.values()):
            variable.trace_add("write", self._form_changed)
        self.promoter.trace_add("write", self._promoter_changed)
        self._promoter_changed()
        self._discover_report()
        self._refresh_controls()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<KeyPress-F9>", self._f9_press)
        self.root.bind("<KeyRelease-F9>", self._f9_release)
        self.root.bind("<F10>", lambda _: self._stop())
        self.root.after(80, self._drain)
        self._log("每日不消耗电量；自动战斗在独立页面启动。F9 暂停 / 继续，F10 紧急停止。")
        if load_error:
            self.saved.set("原配置无法读取；已临时使用默认值。")
            self._log(f"配置加载失败：{load_error}", "error")
            self.root.after(150, lambda: messagebox.showerror("配置无法读取", load_error, parent=root))

    def _build(self) -> None:
        from .interface import build_window
        build_window(self, TASK_NAMES, SERVER_NAMES)

    def _show_page(self, name: str) -> None:
        for key, page in self.pages.items():
            if key == name:
                page.grid()
            else:
                page.grid_remove()
            self.nav_buttons[key].configure(style="Selected.Nav.TButton" if key == name else "Nav.TButton")

    def _overlay_status(self, *_args: Any) -> None:
        if getattr(self, "overlay", None):
            self.overlay.set_status(self.status.get())

    def _overlay_enabled(self, *_args: Any) -> None:
        self.overlay.set_enabled(self.overlay_logs.get())

    def _finish_overlay(self) -> None:
        if getattr(self, "overlay", None):
            self.overlay.finish(self.status.get())

    def _text_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.output_tabs)
        self.output_tabs.add(frame, text=title)
        text = tk.Text(frame, height=7, wrap="word", font=("Microsoft YaHei UI", 10), bg="white", fg="#263e49",
                       borderwidth=0, padx=10, pady=8, state="disabled")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        return text

    def _select_server(self, _event: Any = None) -> None:
        server = next(key for key, label in SERVER_NAMES.items() if label == self.server.get())
        self.game_path.set(GAME_PATHS[server])
        self.saved.set("区服与游戏路径已修改，开始任务或保存设置后生效。")

    def _form_signature(self) -> tuple:
        return (self.server.get(), self.game_path.get(), self.auto_launch.get(), self.overlay_logs.get(), self.action_delay.get(),
                self.transition_timeout.get(), self.ocr_confidence.get(), self.promoter.get(),
                *(variable.get() for variable in self.tasks.values()))

    def _promoter_changed(self, *_args: Any) -> None:
        from .characters import canonical_promoter_name
        try:
            name = canonical_promoter_name(self.promoter.get())
            text = f"优先选择 {name}；未拥有时选择第一位。" if name else "留空选择第一位；账号未拥有时回退第一位。"
            self.promoter_hint.set(text)
            self.promoter_hint_label.configure(foreground="#58707a")
        except (TypeError, ValueError) as exc:
            detail = str(exc)
            self.promoter_hint.set(detail if len(detail) <= 45 else "角色名不在内置名单，请检查输入后再保存。")
            self.promoter_hint_label.configure(foreground="#b33c40")

    def _form_changed(self, *_args: Any) -> None:
        if self._form_signature() != self._saved_form:
            self.saved.set("设置已修改，点击保存设置或开始任务后生效。")
        else:
            self.saved.set("设置与当前保存值一致。")

    def _choose_game(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="选择 ZenlessZoneZero.exe",
                                           initialdir=str(Path(self.game_path.get()).parent),
                                           filetypes=[("绝区零程序", "ZenlessZoneZero.exe"), ("应用程序", "*.exe")])
        if path:
            self.game_path.set(path)
            self.saved.set("游戏路径已修改。")

    def _read_settings(self) -> Settings | None:
        try:
            server = next(key for key, label in SERVER_NAMES.items() if label == self.server.get())
            settings = replace(self.settings, server=server, game_path=self.game_path.get().strip(),
                               auto_launch=self.auto_launch.get(), overlay_logs=self.overlay_logs.get(), action_delay=float(self.action_delay.get()),
                               transition_timeout=int(self.transition_timeout.get()), ocr_confidence=float(self.ocr_confidence.get()),
                               promoter=self.promoter.get(),
                               **{name: variable.get() for name, variable in self.tasks.items()})
            settings.validate()
            settings.save(self.config_path)
            self.settings = settings
            self.promoter.set(settings.promoter)
            self._saved_form = self._form_signature()
            self.saved.set(f"配置已保存：{self.config_path}")
            return settings
        except (ValueError, TypeError, OSError, StopIteration) as exc:
            self.saved.set("设置未保存，请检查输入。")
            self._log(f"设置未保存：{exc}", "error")
            messagebox.showerror("设置未保存", str(exc), parent=self.root)
            return None

    def _save_settings(self) -> None:
        if not self.busy and self._read_settings() is not None:
            self._log("配置已保存。")

    def emit(self, event: dict[str, Any]) -> None:
        self.events.put(event)

    def _refresh_controls(self) -> None:
        for control in self.setting_controls:
            control.configure(state="disabled" if self.busy else ("readonly" if control is self.server_combo else "normal"))
        for button in (self.start_button, self.check_button, self.launch_button):
            button.configure(state="disabled" if self.busy else "normal")
        self.pause_button.configure(text="继续" if self.paused else "暂停",
                                    state="normal" if self.running and self.runner is not None and not self.stopping else "disabled")
        self.stop_button.configure(state="normal" if self.running and not self.stopping else "disabled")
        for button, path in ((self.screenshot_button, self.last_screenshot), (self.report_button, self.last_report_file),
                             (self.directory_button, self.last_output_dir)):
            button.configure(state="normal" if path and (not self.busy or self.paused) else "disabled")

    def _check(self) -> None:
        self._operation("check")

    def _launch(self) -> None:
        self._start("enter_game")

    def _operation(self, action: str) -> None:
        if self.busy or self.closing:
            return
        settings = self._read_settings()
        if settings is None:
            return
        self.busy, self.worker_done = True, False
        self.status.set("正在检测窗口")
        self._refresh_controls()
        if action == "check":
            self.root.iconify()

        def work() -> None:
            try:
                from .app import inspect_game
                self.emit({"type": "checked", "data": inspect_game(settings)})
            except Exception as exc:
                self.emit({"type": "error", "message": str(exc)})
            finally:
                self.emit({"type": "worker_done"})

        self.worker = threading.Thread(target=work, name=f"AutoZZZ-{action}", daemon=False)
        self.worker.start()

    def _start(self, operation: str = "daily") -> None:
        if operation in ("battle", "battle_prepare", "battle_catalog"):
            from .battle_ui import start
            start(self, operation)
            return
        if self.busy or self.closing:
            return
        settings = self._read_settings()
        if settings is None:
            return
        self.runner = None
        self.operation = operation
        self.overlay_game_handle = None
        self._daily_goal_complete = False
        self.stop_requested.clear()
        self.busy = self.running = True
        self.paused = self.stopping = self.worker_done = False
        self.focus_waiting = False
        self.started_at = time.monotonic()
        entering = operation == "enter_game"
        self.status.set("正在进入游戏" if entering else "正在运行")
        self.summary.set("正在登录并进入已保存账号；F9 暂停 / 继续，F10 停止。" if entering else
                         "F9 暂停 / 继续 · F10 紧急停止 · 本次执行不消耗体力")
        if not entering:
            self.engagement.set("活跃度未验证")
            for name, label in TASK_NAMES.items():
                enabled = getattr(settings, name)
                self.table.item(name, values=(label, "等待" if enabled else "跳过", "" if enabled else "未勾选"),
                                tags=() if enabled else ("skipped",))
        self.output_tabs.select(0)
        self._show_page("home")
        self._refresh_controls()
        self.overlay.begin(self.status.get())
        action = "进入游戏" if entering else "每日任务"
        self._log(f"开始{SERVER_NAMES[settings.server]}{action}；助手将最小化。")
        self.root.iconify()

        def work() -> None:
            finished = threading.Event()

            def send(event: dict[str, Any]) -> None:
                if event.get("type") == "finished":
                    finished.set()
                self.emit(event)

            try:
                from .app import Runner
                runner = Runner(settings, send)
                stop_applied = self.stop_requested.is_set()
                if stop_applied:
                    runner.stop()
                self.emit({"type": "runner_ready", "runner": runner, "stop_applied": stop_applied})
                report = runner.enter_game() if entering else runner.run()
                if not finished.is_set():
                    self.emit({"type": "finished", "report": report.to_dict() if hasattr(report, "to_dict") else report})
            except Exception as exc:
                self.emit({"type": "error", "message": str(exc)})
            finally:
                self.emit({"type": "worker_done"})

        self.worker = threading.Thread(target=work, name=f"AutoZZZ-{operation}", daemon=False)
        self.worker.start()

    def _pause(self) -> None:
        if self.runner is None or not self.running or self.stopping:
            return
        if self.paused:
            self.root.iconify()
        self._pause_state(bool(self.runner.toggle_pause()))

    def _pause_state(self, paused: bool) -> None:
        if not self.running or self.stopping:
            return
        changed = self.paused != paused
        self.paused = paused
        self._runtime_status()
        self._refresh_controls()
        if changed:
            if paused:
                self._restore()
            elif not self.closing:
                self.root.iconify()

    def _runtime_status(self) -> None:
        if self.paused:
            self.status.set("已暂停")
            self.summary.set("任务已暂停；点击继续或按 F9 恢复。")
        elif self.focus_waiting:
            self.status.set("等待游戏回到前台")
            self.summary.set("游戏窗口失去焦点，操作已暂停；切回绝区零后自动继续，F10 可停止。")
        else:
            self.status.set("正在进入游戏" if getattr(self, "operation", "daily") == "enter_game" else "正在运行")
            self.summary.set("F9 暂停 / 继续 · F10 紧急停止 · 按独立预算运行，结算时释放按键" if getattr(self,"operation","") in ("battle","battle_prepare") else "F9 暂停 / 继续 · F10 紧急停止 · 本次执行不消耗体力")

    def _focus_wait_state(self, waiting: bool) -> None:
        if not self.running or self.stopping or self.closing:
            return
        self.focus_waiting = waiting
        self._runtime_status()
        # Background focus changes must never activate or minimize the helper.
        self._refresh_controls()

    def _f9_press(self, _event: Any) -> str:
        self._f9_pressed = True
        return "break"

    def _f9_release(self, _event: Any) -> str:
        pressed, self._f9_pressed = self._f9_pressed, False
        if pressed:
            self._pause()
        return "break"

    def _stop(self) -> None:
        if not self.running or self.stopping:
            return
        self.stopping, self.paused = True, False
        self.focus_waiting = False
        self.stop_requested.set()
        if self.runner is not None:
            self.runner.stop()
        self.status.set("正在停止")
        self.summary.set("正在结束当前操作并保存运行报告。")
        self._log("已请求停止，等待当前识别结束。", "warning")
        self._refresh_controls()

    def _task(self, event: dict[str, Any]) -> None:
        name = str(event.get("task", ""))
        name = next((key for key, label in TASK_NAMES.items() if name == label), name)
        status, detail = str(event.get("status", "pending")), str(event.get("detail", ""))
        if name in TASK_NAMES:
            self.table.item(name, values=(TASK_NAMES[name], STATUS_NAMES.get(status, status), detail), tags=(status,))
        if status == "running" and not self.paused and not self.focus_waiting:
            self.status.set(f"正在执行：{TASK_NAMES.get(name, name)}")

    def _task_detail(self, _event: Any) -> None:
        if self.table.selection() and (not self.busy or self.paused):
            task, status, detail = self.table.item(self.table.selection()[0], "values")
            self.summary.set(f"{task} · {status}：{detail or '暂无详细说明'}")

    def _log(self, message: Any, level: str = "info") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n", level)
        if int(self.log.index("end-1c").split(".")[0]) > 2500:
            self.log.delete("1.0", "501.0")
        self.log.see("end")
        self.log.configure(state="disabled")
        if getattr(self, "overlay", None):
            self.overlay.append(str(message), level)

    @staticmethod
    def _replace_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value)
        widget.configure(state="disabled")

    def _finished(self, report: Any) -> None:
        if hasattr(report, "to_dict"):
            report = report.to_dict()
        report = report if isinstance(report, dict) else {}
        if report.get("operation") in ("battle", "battle_prepare", "battle_catalog"):
            from .battle_ui import finished
            finished(self,report)
            return
        if report.get("operation") == "enter_game" or getattr(self, "operation", "daily") == "enter_game":
            self._entered(report)
            return
        self.running, self.paused = False, False
        self.focus_waiting = False
        self._freeze_clock()
        for result in report.get("results", []):
            if isinstance(result, dict):
                self._task(result)
        status = str(report.get("status", "partial"))
        engagement = report.get("engagement")
        verified = report.get("daily_verified") is True and type(engagement) is int and engagement == 400
        self._daily_goal_complete = verified
        if verified:
            status = "success"
            for name in DAILY_TASK_NAMES:
                if self.tasks[name].get():
                    self._task({"task": name, "status": "ok", "detail": "每日任务已完成：400 活跃度已满且四档奖励已领取。"})
        for name in TASK_NAMES:
            task, previous, detail = self.table.item(name, "values")
            if previous in ("等待", "进行中"):
                result_status = "stopped" if status == "stopped" else ("failed" if previous == "进行中" else "skipped")
                self._task({"task": name, "status": result_status, "detail": "未确认完成；请查看日志与截图。"})
        self.engagement.set((f"活跃度 {engagement} · " if engagement is not None else "") + ("每日已验证" if verified else "每日未验证"))
        completion = "每日任务已完成" if verified else STATUS_NAMES.get(status, status)
        unfinished_cleanup = [name for name in FINISH_TASK_NAMES if self.tasks[name].get()
                              and self.table.item(name, "values")[1] != "完成"]
        if verified and unfinished_cleanup:
            completion += " · 收尾奖励有未完成项"
        self.status.set(completion)
        self.summary.set(f"{completion} · {self.engagement.get()} · 体力消耗 0")
        self._replace_text(self.report_text, json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if report.get("run_dir"):
            self.last_output_dir = Path(report["run_dir"])
            self.last_report_file = self.last_output_dir / "report.json"
            self._find_screenshot(self.last_output_dir)
        self._log(self.summary.get(), "info" if status == "success" else "warning")
        self._finish_overlay()
        self._restore()

    def _entered(self, report: dict) -> None:
        self.running = self.paused = self.focus_waiting = False
        self._freeze_clock()
        success = report.get("status") == "success" and any(
            isinstance(result, dict) and result.get("task") == "launch" and result.get("status") == "ok"
            for result in report.get("results", []))
        self.status.set("已进入游戏" if success else ("已停止" if report.get("status") == "stopped" else "进入游戏未完成"))
        self.summary.set("已进入游戏主界面，可以开始每日。" if success else
                         "进入游戏未完成；请查看日志和截图，处理登录或更新提示后重试。")
        self._replace_text(self.report_text, json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if report.get("run_dir"):
            self.last_output_dir = Path(report["run_dir"])
            self.last_report_file = self.last_output_dir / "report.json"
            self._find_screenshot(self.last_output_dir)
        self._log(self.summary.get(), "info" if success else "warning")
        self._finish_overlay()
        if not success:
            self._restore()

    def _checked(self, data: dict[str, Any]) -> None:
        if data.get("screenshot"):
            self.last_screenshot = Path(data["screenshot"])
            self.last_output_dir = self.last_screenshot.parent
        self._replace_text(self.inspection_text,
            f"游戏窗口：{data.get('title', '未知')}\n客户区：{data.get('width', '?')} × {data.get('height', '?')}\n"
            f"助手管理员权限：{'是' if data.get('administrator') else '否'}\n截图：{self.last_screenshot or '未生成'}\n"
            "权限说明：游戏以管理员运行时，助手也需要管理员权限；请使用 Start-AutoZZZ-Admin.cmd。\n\n"
            f"OCR 识别文字\n{data.get('ocr_text') or '未识别到文字'}")
        self.status.set("检测完成")
        self._log(f"窗口检测完成：{data.get('title', '未知')}；详见“窗口检测”。")
        if not data.get("administrator"):
            self._log("助手当前没有管理员权限；游戏以管理员运行时，请使用管理员启动器重开助手。", "warning")
        self._show_page("logs")
        self.output_tabs.select(1)
        self._restore()

    def _find_screenshot(self, directory: Path) -> None:
        try:
            pictures = list(directory.glob("*.png"))
            self.last_screenshot = max(pictures, key=lambda path: path.stat().st_mtime) if pictures else None
        except OSError:
            self.last_screenshot = None

    def _discover_report(self) -> None:
        try:
            reports = list((ROOT / "runs").glob("*/report.json"))
            if reports:
                self.last_report_file = max(reports, key=lambda path: path.stat().st_mtime)
                self.last_output_dir = self.last_report_file.parent
                self._find_screenshot(self.last_output_dir)
                self._replace_text(self.report_text, self.last_report_file.read_text(encoding="utf-8"))
        except OSError:
            pass

    def _open_path(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            if not path.exists():
                raise OSError("文件或目录已不存在。")
            os.startfile(str(path.resolve()))
        except OSError as exc:
            self._log(f"无法打开：{exc}", "error")
            messagebox.showerror("无法打开", str(exc), parent=self.root)

    def _restore(self) -> None:
        if not self.closing:
            self.root.deiconify()
            self.root.lift()

    def _clock(self) -> None:
        if self.started_at is not None:
            seconds = int(time.monotonic() - self.started_at)
            minutes, seconds = divmod(seconds, 60)
            self.elapsed.set(f"{minutes:02}:{seconds:02}")

    def _freeze_clock(self) -> None:
        self._clock()
        self.started_at = None

    def _drain(self) -> None:
        try:
            for _ in range(100):
                event = self.events.get_nowait()
                kind = event.get("type")
                if kind == "log":
                    self._log(event.get("message", ""), str(event.get("level", "info")))
                elif kind == "task":
                    self._task(event)
                elif kind == "paused":
                    self._pause_state(bool(event.get("paused")))
                elif kind == "focus_wait":
                    self._focus_wait_state(bool(event.get("waiting")))
                elif kind == "runner_ready":
                    self.runner = event["runner"]
                    if self.stop_requested.is_set() and not event.get("stop_applied"):
                        self.runner.stop()
                    self._refresh_controls()
                elif kind == "finished":
                    self._finished(event.get("report"))
                elif kind == "checked":
                    self._checked(event.get("data", {}))
                elif kind == "error":
                    if self._daily_goal_complete:
                        self._log(f"每日任务已完成；后续通知错误不影响完成结果：{event.get('message', '')}", "warning")
                        continue
                    self.running, self.paused = False, False
                    self.focus_waiting = False
                    self._freeze_clock()
                    for name in (() if getattr(self, "operation", "daily") == "enter_game" else TASK_NAMES):
                        if self.table.item(name, "values")[1] in ("等待", "进行中"):
                            self._task({"task": name, "status": "failed", "detail": "流程中断，未确认完成。"})
                    self.status.set("执行失败")
                    self.summary.set(str(event.get("message", "未知错误")))
                    self._log(self.summary.get(), "error")
                    self._finish_overlay()
                    self._restore()
                elif kind == "worker_done":
                    self.worker_done = True
        except queue.Empty:
            pass
        if self.runner is not None:
            self.overlay_game_handle = getattr(getattr(self.runner, "_window", None), "hwnd", None)
        self._clock()
        worker_alive = self.worker is not None and self.worker.is_alive()
        if self.worker_done and not worker_alive:
            self.busy = self.running = self.paused = self.stopping = False
            self.focus_waiting = False
            self.runner = None
            self._refresh_controls()
        if self.closing and not worker_alive:
            self.root.destroy()
            return
        self.root.after(80, self._drain)

    def _close(self) -> None:
        if self.closing:
            return
        self.closing = True
        if getattr(self, "overlay", None):
            self.overlay.close()
        if self.running:
            self._stop()
        self.root.withdraw()
        # Keep Tk and the worker alive until cooperative cancellation and report writing finish.
        if self.worker is None or not self.worker.is_alive():
            self.root.destroy()


def main(config_path: Path = CONFIG_PATH, *, server: str | None = None, game_path: str | None = None) -> None:
    from . import windows  # noqa: F401 - establish DPI awareness before creating Tk

    root = tk.Tk()
    AutoZZZWindow(root, config_path, server=server, game_path=game_path)
    root.mainloop()
