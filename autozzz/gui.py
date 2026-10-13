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
        self.started_at: float | None = None
        self.last_output_dir: Path | None = None
        self.last_report_file: Path | None = None
        self.last_screenshot: Path | None = None
        self._f9_pressed = False
        self.server = tk.StringVar(root, SERVER_NAMES[self.settings.server])
        self.game_path = tk.StringVar(root, self.settings.game_path)
        self.auto_launch = tk.BooleanVar(root, self.settings.auto_launch)
        self.tasks = {name: tk.BooleanVar(root, getattr(self.settings, name)) for name in TASK_NAMES}
        self.action_delay = tk.StringVar(root, str(self.settings.action_delay))
        self.transition_timeout = tk.StringVar(root, str(self.settings.transition_timeout))
        self.ocr_confidence = tk.StringVar(root, str(self.settings.ocr_confidence))
        self.promoter = tk.StringVar(root, self.settings.promoter)
        self.promoter_hint = tk.StringVar(root, "留空选择第一位；账号未拥有时回退第一位。")
        self.status = tk.StringVar(root, "准备就绪")
        self.summary = tk.StringVar(root, "先进入游戏自由行动界面，再检测窗口或开始每日。")
        self.elapsed = tk.StringVar(root, "00:00")
        self.engagement = tk.StringVar(root, "未验证")
        self.saved = tk.StringVar(root, "配置按“保存设置”或开始任务时保存。")
        self.setting_controls: list[ttk.Widget] = []
        self._build()
        self._saved_form = self._form_signature()
        for variable in (self.server, self.game_path, self.auto_launch, self.action_delay,
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
        self._log("当前版本执行非战斗每日，体力消耗为 0。F9 暂停 / 继续，F10 紧急停止。")
        if load_error:
            self.saved.set("原配置无法读取；已临时使用默认值。")
            self._log(f"配置加载失败：{load_error}", "error")
            self.root.after(150, lambda: messagebox.showerror("配置无法读取", load_error, parent=root))

    def _build(self) -> None:
        self.root.title("AutoZZZ · 绝区零每日助手")
        # Tk fonts follow Windows DPI. Scale the initial client area as well so
        # 150% / 200% displays do not clip the notebook or footer controls.
        self.ui_scale = max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        self.root.geometry(f"{round(1060 * self.ui_scale)}x{round(920 * self.ui_scale)}")
        self.root.minsize(round(960 * self.ui_scale), round(870 * self.ui_scale))
        self.root.configure(bg="#eef2f3")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background="#eef2f3", foreground="#20313b")
        style.configure("TButton", padding=(12, 7))
        style.configure("TCheckbutton", padding=(0, 3))
        style.configure("TLabelframe", background="#eef2f3")
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 23, "bold"), foreground="#122e38")
        style.configure("Hint.TLabel", foreground="#58707a")
        style.configure("Accent.TButton", background="#13796b", foreground="white", padding=(22, 9))
        style.map("Accent.TButton", background=[("active", "#096254"), ("disabled", "#cbd5d6")])
        style.configure("Treeview", rowheight=29, background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))

        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(header, text="AutoZZZ", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="绝区零每日助手  ·  非战斗每日", style="Hint.TLabel").pack(side="left", padx=18, pady=(8, 0))
        ttk.Label(header, textvariable=self.status, foreground="#13796b", font=("Microsoft YaHei UI", 11, "bold")).pack(side="right")

        game = ttk.LabelFrame(outer, text="游戏与区服", padding=12)
        game.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        game.columnconfigure(3, weight=1)
        ttk.Label(game, text="区服").grid(row=0, column=0, padx=(0, 8))
        self.server_combo = ttk.Combobox(game, textvariable=self.server, values=list(SERVER_NAMES.values()), state="readonly", width=8)
        self.server_combo.grid(row=0, column=1, padx=(0, 18))
        self.server_combo.bind("<<ComboboxSelected>>", self._select_server)
        ttk.Label(game, text="游戏路径").grid(row=0, column=2, padx=(0, 8))
        path_entry = ttk.Entry(game, textvariable=self.game_path)
        path_entry.grid(row=0, column=3, sticky="ew")
        browse = ttk.Button(game, text="浏览…", command=self._choose_game)
        browse.grid(row=0, column=4, padx=(8, 0))
        launch_setting = ttk.Checkbutton(game, text="开始时自动启动游戏", variable=self.auto_launch)
        launch_setting.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(game, text="首次登录、更新和验证码请手动完成。国际服请使用简体中文界面。", style="Hint.TLabel").grid(row=1, column=3, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(game, text="游戏以管理员运行时，请用 Start-AutoZZZ-Admin.cmd 启动助手，并同意 Windows 管理员提示。",
                  style="Hint.TLabel", wraplength=870).grid(row=2, column=0, columnspan=5, sticky="w", pady=(5, 0))
        self.setting_controls.extend((self.server_combo, path_entry, browse, launch_setting))

        settings = ttk.LabelFrame(outer, text="每日任务与收尾奖励", padding=12)
        settings.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        for index, (name, label) in enumerate(TASK_NAMES.items()):
            control = ttk.Checkbutton(settings, text=label, variable=self.tasks[name])
            control.grid(row=index // 4, column=index % 4, sticky="w", padx=(0, 20))
            self.setting_controls.append(control)
        promoter_row = ttk.Frame(settings)
        promoter_row.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(7, 0))
        ttk.Label(promoter_row, text="宣传员（选填）").pack(side="left", padx=(0, 8))
        self.promoter_entry = ttk.Entry(promoter_row, textvariable=self.promoter, width=24)
        self.promoter_entry.pack(side="left", padx=(0, 12))
        self.promoter_hint_label = ttk.Label(promoter_row, textvariable=self.promoter_hint, style="Hint.TLabel", wraplength=480)
        self.promoter_hint_label.pack(side="left")
        self.setting_controls.append(self.promoter_entry)
        timing = ttk.Frame(settings)
        timing.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        for text, variable, width in (("操作间隔（秒）", self.action_delay, 5), ("界面超时（秒）", self.transition_timeout, 5),
                                      ("OCR 阈值", self.ocr_confidence, 5)):
            ttk.Label(timing, text=text, style="Hint.TLabel").pack(side="left", padx=(0, 6))
            control = ttk.Entry(timing, textvariable=variable, width=width)
            control.pack(side="left", padx=(0, 18))
            self.setting_controls.append(control)
        save = ttk.Button(timing, text="保存设置", command=self._save_settings)
        save.pack(side="right")
        self.setting_controls.append(save)

        actions = ttk.Frame(outer)
        actions.grid(row=3, column=0, sticky="ew", pady=(2, 10))
        self.start_button = ttk.Button(actions, text="开始每日", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.check_button = ttk.Button(actions, text="检测窗口", command=self._check)
        self.check_button.pack(side="left", padx=8)
        self.launch_button = ttk.Button(actions, text="启动游戏", command=self._launch)
        self.launch_button.pack(side="left")
        self.pause_button = ttk.Button(actions, text="暂停", command=self._pause)
        self.pause_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(actions, text="停止", command=self._stop)
        self.stop_button.pack(side="left")
        ttk.Label(actions, text="体力消耗  0", style="Hint.TLabel").pack(side="right")
        ttk.Label(actions, textvariable=self.elapsed, style="Hint.TLabel").pack(side="right", padx=18)
        ttk.Label(actions, textvariable=self.engagement, foreground="#13796b").pack(side="right")

        results = ttk.Frame(outer)
        results.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        self.table = ttk.Treeview(results, columns=("task", "status", "detail"), show="headings", height=6, selectmode="browse")
        for name, label, width in (("task", "任务", 235), ("status", "状态", 90), ("detail", "结果说明", 550)):
            self.table.heading(name, text=label)
            self.table.column(name, width=width, minwidth=60, stretch=name == "detail")
        self.table.pack(fill="x")
        for name, label in TASK_NAMES.items():
            self.table.insert("", "end", iid=name, values=(label, "等待", ""))
        for tag, color in (("ok", "#14735d"), ("failed", "#b33c40"), ("partial", "#a5701c"), ("skipped", "#6a7b83")):
            self.table.tag_configure(tag, foreground=color)
        self.table.bind("<<TreeviewSelect>>", self._task_detail)

        self.output_tabs = ttk.Notebook(outer)
        self.output_tabs.grid(row=5, column=0, sticky="nsew")
        self.log = self._text_tab("运行日志")
        self.inspection_text = self._text_tab("窗口检测")
        self.report_text = self._text_tab("运行报告")
        self.log.tag_configure("error", foreground="#b33c40")
        self.log.tag_configure("warning", foreground="#986219")
        self.log.tag_configure("info", foreground="#263e49")
        footer = ttk.Frame(outer)
        footer.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.summary, style="Hint.TLabel", wraplength=625).grid(row=0, column=0, sticky="w")
        buttons = ttk.Frame(footer)
        buttons.grid(row=0, column=1, sticky="e")
        self.screenshot_button = ttk.Button(buttons, text="打开截图", command=lambda: self._open_path(self.last_screenshot))
        self.screenshot_button.pack(side="left")
        self.report_button = ttk.Button(buttons, text="打开报告", command=lambda: self._open_path(self.last_report_file))
        self.report_button.pack(side="left", padx=6)
        self.directory_button = ttk.Button(buttons, text="运行目录", command=lambda: self._open_path(self.last_output_dir))
        self.directory_button.pack(side="left")
        ttk.Label(outer, textvariable=self.saved, style="Hint.TLabel").grid(row=7, column=0, sticky="w", pady=(7, 0))

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
        return (self.server.get(), self.game_path.get(), self.auto_launch.get(), self.action_delay.get(),
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
                               auto_launch=self.auto_launch.get(), action_delay=float(self.action_delay.get()),
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
        self._operation("launch")

    def _operation(self, action: str) -> None:
        if self.busy or self.closing:
            return
        settings = self._read_settings()
        if settings is None:
            return
        self.busy, self.worker_done = True, False
        self.status.set("正在检测窗口" if action == "check" else "正在启动游戏")
        self._refresh_controls()
        if action == "check":
            self.root.iconify()

        def work() -> None:
            try:
                from .app import inspect_game, launch_game
                if action == "check":
                    self.emit({"type": "checked", "data": inspect_game(settings)})
                else:
                    self.emit({"type": "launched", "message": launch_game(settings)})
            except Exception as exc:
                self.emit({"type": "error", "message": str(exc)})
            finally:
                self.emit({"type": "worker_done"})

        self.worker = threading.Thread(target=work, name=f"AutoZZZ-{action}", daemon=False)
        self.worker.start()

    def _start(self) -> None:
        if self.busy or self.closing:
            return
        settings = self._read_settings()
        if settings is None:
            return
        self.runner = None
        self._daily_goal_complete = False
        self.stop_requested.clear()
        self.busy = self.running = True
        self.paused = self.stopping = self.worker_done = False
        self.focus_waiting = False
        self.started_at = time.monotonic()
        self.engagement.set("活跃度未验证")
        self.status.set("正在运行")
        self.summary.set("F9 暂停 / 继续 · F10 紧急停止 · 本次执行不消耗体力")
        for name, label in TASK_NAMES.items():
            enabled = getattr(settings, name)
            self.table.item(name, values=(label, "等待" if enabled else "跳过", "" if enabled else "未勾选"),
                            tags=() if enabled else ("skipped",))
        self.output_tabs.select(0)
        self._refresh_controls()
        self._log(f"开始{SERVER_NAMES[settings.server]}每日任务；助手将最小化。")
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
                report = runner.run()
                if not finished.is_set():
                    self.emit({"type": "finished", "report": report.to_dict() if hasattr(report, "to_dict") else report})
            except Exception as exc:
                self.emit({"type": "error", "message": str(exc)})
            finally:
                self.emit({"type": "worker_done"})

        self.worker = threading.Thread(target=work, name="AutoZZZ-daily", daemon=False)
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
            self.status.set("正在运行")
            self.summary.set("F9 暂停 / 继续 · F10 紧急停止 · 本次执行不消耗体力")

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
                elif kind == "launched":
                    self.status.set("启动请求已发送")
                    self._log(event.get("message") or "请完成登录并进入游戏。")
                elif kind == "error":
                    if self._daily_goal_complete:
                        self._log(f"每日任务已完成；后续通知错误不影响完成结果：{event.get('message', '')}", "warning")
                        continue
                    self.running, self.paused = False, False
                    self.focus_waiting = False
                    self._freeze_clock()
                    for name in TASK_NAMES:
                        if self.table.item(name, "values")[1] in ("等待", "进行中"):
                            self._task({"task": name, "status": "failed", "detail": "流程中断，未确认完成。"})
                    self.status.set("执行失败")
                    self.summary.set(str(event.get("message", "未知错误")))
                    self._log(self.summary.get(), "error")
                    self._restore()
                elif kind == "worker_done":
                    self.worker_done = True
        except queue.Empty:
            pass
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
