"""Light desktop layout with sidebar navigation and separate settings pages."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def build_window(ui, task_names, server_names):
    root = ui.root
    ui.ui_scale = max(1., float(root.tk.call("tk", "scaling")) / (96/72))
    d = lambda n: round(n*ui.ui_scale)
    width, height = min(d(1120), root.winfo_screenwidth()-40), min(d(820), root.winfo_screenheight()-70)
    root.title("AutoZZZ · 绝区零每日助手")
    root.geometry(f"{width}x{height}")
    root.minsize(min(d(1000), width), min(d(760), height))
    root.configure(bg="#f5f5f5")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Microsoft YaHei UI", 10), background="#f5f5f5", foreground="#202020")
    style.configure("TButton", padding=(d(13), d(7)), borderwidth=1, relief="flat", background="white")
    style.map("TButton", background=[("active", "#ebebeb"), ("disabled", "#f0f0f0")])
    style.configure("Accent.TButton", background="#0067c0", foreground="white", borderwidth=0)
    style.map("Accent.TButton", background=[("active", "#005aab"), ("disabled", "#c8d7e5")])
    style.configure("Sidebar.TFrame", background="#eeeeee")
    style.configure("Sidebar.TLabel", background="#eeeeee", foreground="#606060")
    style.configure("Brand.TLabel", background="#eeeeee", font=("Segoe UI", 18, "bold"), foreground="#202020")
    style.configure("Nav.TButton", background="#eeeeee", borderwidth=0, anchor="w", padding=(d(14), d(12)))
    style.configure("Selected.Nav.TButton", background="#dceafb", foreground="#005a9e")
    style.map("Nav.TButton", background=[("active", "#e3e3e3")])
    style.map("Selected.Nav.TButton", background=[("active", "#d0e4fa")])
    style.configure("Card.TFrame", background="white", borderwidth=1, relief="solid", bordercolor="#e4e4e4")
    style.configure("Card.TLabel", background="white")
    style.configure("CardTitle.TLabel", background="white", font=("Microsoft YaHei UI", 11, "bold"))
    style.configure("CardHint.TLabel", background="white", foreground="#686868", font=("Microsoft YaHei UI", 9))
    style.configure("Card.TCheckbutton", background="white", padding=(0, d(5)))
    style.map("Card.TCheckbutton", background=[("active", "white")])
    style.configure("PageTitle.TLabel", font=("Microsoft YaHei UI", 24, "bold"))
    style.configure("Hint.TLabel", foreground="#686868", font=("Microsoft YaHei UI", 9))
    style.configure("Treeview", rowheight=d(29), background="white", fieldbackground="white", borderwidth=0)
    style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), padding=d(6), background="#f8f8f8")
    style.configure("TNotebook", borderwidth=0)
    style.configure("TNotebook.Tab", padding=(d(16), d(8)))
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    shell = ttk.Frame(root)
    shell.grid(row=0, column=0, sticky="nsew")
    shell.columnconfigure(1, weight=1)
    shell.rowconfigure(0, weight=1)
    sidebar = ttk.Frame(shell, style="Sidebar.TFrame", width=d(185), padding=(d(12), d(22)))
    sidebar.grid(row=0, column=0, sticky="nsew")
    sidebar.grid_propagate(False)
    sidebar.columnconfigure(0, weight=1)
    sidebar.rowconfigure(8, weight=1)
    ttk.Label(sidebar, text="AutoZZZ", style="Brand.TLabel").grid(row=0, column=0, sticky="w", padx=d(10))
    ttk.Label(sidebar, text="绝区零每日助手", style="Sidebar.TLabel").grid(row=1, column=0, sticky="w", padx=d(10), pady=(d(5), d(28)))
    ui.nav_buttons = {}
    for row, (name, label) in enumerate((("home", "启动"), ("tasks", "每日任务"), ("logs", "运行日志"), ("settings", "设置")), 2):
        button = ttk.Button(sidebar, text=label, style="Nav.TButton", command=lambda key=name: ui._show_page(key))
        button.grid(row=row, column=0, sticky="ew", pady=d(3))
        ui.nav_buttons[name] = button
    ttk.Label(sidebar, text="F9  暂停 / 继续\nF10  紧急停止", style="Sidebar.TLabel", justify="left").grid(row=9, column=0, sticky="sw", padx=d(10), pady=d(14))
    ttk.Label(sidebar, text="非战斗每日 · 电量消耗 0", style="Sidebar.TLabel", font=("Microsoft YaHei UI", 8)).grid(row=10, column=0, sticky="w", padx=d(10))
    body = ttk.Frame(shell, padding=(d(24), d(17)))
    body.grid(row=0, column=1, sticky="nsew")
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    ui.pages = {name: ttk.Frame(body) for name in ui.nav_buttons}
    for page in ui.pages.values():
        page.grid(row=0, column=0, sticky="nsew")
        page.columnconfigure(0, weight=1)

    def heading(parent, title, hint):
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, d(14)))
        ttk.Label(frame, text=title, style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(frame, text=hint, style="Hint.TLabel", wraplength=d(650)).pack(anchor="w", pady=(d(3), 0))

    def card(parent, row, **kwargs):
        frame = ttk.Frame(parent, style="Card.TFrame", padding=d(12))
        frame.grid(row=row, column=0, sticky="ew", pady=(0, d(12)), **kwargs)
        return frame

    home = ui.pages["home"]
    home.rowconfigure(4, weight=1)
    heading(home, "启动", "启动并进入游戏，或直接开始今天的非战斗每日。")
    game = card(home, 1)
    game.columnconfigure(2, weight=1)
    ttk.Label(game, text="游戏配置", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, d(8)))
    ttk.Label(game, text="区服", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=(0, d(12)))
    ui.server_combo = ttk.Combobox(game, textvariable=ui.server, values=list(server_names.values()), state="readonly", width=8)
    ui.server_combo.grid(row=1, column=1, sticky="w")
    ui.server_combo.bind("<<ComboboxSelected>>", ui._select_server)
    ttk.Label(game, text="启动游戏会自动进入已保存账号的主界面", style="CardHint.TLabel").grid(row=1, column=2, columnspan=2, sticky="w", padx=d(14))
    ttk.Label(game, text="游戏路径", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=d(9), padx=(0, d(12)))
    path = ttk.Entry(game, textvariable=ui.game_path)
    path.grid(row=2, column=1, columnspan=2, sticky="ew", pady=d(9))
    browse = ttk.Button(game, text="浏览…", command=ui._choose_game)
    browse.grid(row=2, column=3, padx=(d(8), 0))
    ui.setting_controls.extend((ui.server_combo, path, browse))
    actions = ttk.Frame(game, style="Card.TFrame", borderwidth=0)
    actions.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(d(3), 0))
    ui.launch_button = ttk.Button(actions, text="启动游戏", style="Accent.TButton", command=ui._launch)
    ui.start_button = ttk.Button(actions, text="开始每日", command=ui._start)
    ui.check_button = ttk.Button(actions, text="检测窗口", command=ui._check)
    ui.pause_button = ttk.Button(actions, text="暂停", command=ui._pause)
    ui.stop_button = ttk.Button(actions, text="停止", command=ui._stop)
    for button in (ui.launch_button, ui.start_button, ui.check_button, ui.pause_button, ui.stop_button):
        button.pack(side="left", padx=(0, d(8)))
    metrics = ttk.Frame(home)
    metrics.grid(row=2, column=0, sticky="ew", pady=(0, d(12)))
    for column, (title, variable) in enumerate((("任务状态", ui.status), ("今日活跃度", ui.engagement), ("运行时长", ui.elapsed))):
        metrics.columnconfigure(column, weight=1, uniform="metrics")
        panel = ttk.Frame(metrics, style="Card.TFrame", padding=d(10))
        panel.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else d(8), 0))
        ttk.Label(panel, text=title, style="CardHint.TLabel").pack(anchor="w")
        ttk.Label(panel, textvariable=variable, style="CardTitle.TLabel", wraplength=d(215)).pack(anchor="w", pady=(d(5), 0))
    ttk.Label(home, text="本次任务", font=("Microsoft YaHei UI", 11, "bold")).grid(row=3, column=0, sticky="w", pady=(d(3), d(8)))
    ui.table = ttk.Treeview(home, columns=("task", "status", "detail"), show="headings", height=6, selectmode="browse")
    for name, label, size in (("task", "任务", 205), ("status", "状态", 80), ("detail", "结果说明", 390)):
        ui.table.heading(name, text=label)
        ui.table.column(name, width=d(size), minwidth=d(60), stretch=name == "detail")
    ui.table.grid(row=4, column=0, sticky="nsew")
    for name, label in task_names.items():
        ui.table.insert("", "end", iid=name, values=(label, "等待", ""))
    for tag, color in (("ok", "#14735d"), ("failed", "#b33c40"), ("partial", "#a5701c"), ("skipped", "#6a7b83")):
        ui.table.tag_configure(tag, foreground=color)
    ui.table.bind("<<TreeviewSelect>>", ui._task_detail)

    tasks = ui.pages["tasks"]
    heading(tasks, "每日任务", "选择每日内容；完成每日后，再领取已获得的周纪和城募奖励。")
    options = card(tasks, 1)
    for column in range(2):
        options.columnconfigure(column, weight=1)
    descriptions = {"scratch": "刮刮卡或饼铺每日盲盒", "coffee": "领取每日免费饮品", "video": "选择宣传员并安排营业",
                    "daily": "核实 400 活跃与四档奖励", "weekly": "领取已有任务和里程奖励", "city_fund": "领取成长任务及等级回馈"}
    for index, (name, label) in enumerate(task_names.items()):
        row, column = divmod(index, 2)
        item = ttk.Frame(options, style="Card.TFrame", borderwidth=0, padding=(d(4), d(8)))
        item.grid(row=row, column=column, sticky="ew")
        control = ttk.Checkbutton(item, text=label, variable=ui.tasks[name], style="Card.TCheckbutton")
        control.pack(anchor="w")
        ttk.Label(item, text=descriptions[name], style="CardHint.TLabel").pack(anchor="w", padx=d(23))
        ui.setting_controls.append(control)
    promoter = card(tasks, 2)
    promoter.columnconfigure(0, weight=1)
    ttk.Label(promoter, text="录像店宣传员", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
    ui.promoter_entry = ttk.Entry(promoter, textvariable=ui.promoter, width=28)
    ui.promoter_entry.grid(row=1, column=0, sticky="w", pady=d(9))
    ui.promoter_hint_label = ttk.Label(promoter, textvariable=ui.promoter_hint, style="CardHint.TLabel", wraplength=d(640))
    ui.promoter_hint_label.grid(row=2, column=0, sticky="w")
    ui.setting_controls.append(ui.promoter_entry)
    save = ttk.Button(tasks, text="保存设置", style="Accent.TButton", command=ui._save_settings)
    save.grid(row=3, column=0, sticky="w")
    ui.setting_controls.append(save)

    logs = ui.pages["logs"]
    logs.rowconfigure(1, weight=1)
    heading(logs, "运行日志", "查看运行过程、窗口检测和本次报告。")
    ui.output_tabs = ttk.Notebook(logs)
    ui.output_tabs.grid(row=1, column=0, sticky="nsew")
    ui.log = ui._text_tab("运行日志")
    ui.inspection_text = ui._text_tab("窗口检测")
    ui.report_text = ui._text_tab("运行报告")
    for tag, color in (("error", "#b33c40"), ("warning", "#986219"), ("info", "#263e49")):
        ui.log.tag_configure(tag, foreground=color)

    settings = ui.pages["settings"]
    heading(settings, "设置", "调整启动、游戏内日志和识别选项。")
    display = card(settings, 1)
    ttk.Label(display, text="启动与显示", style="CardTitle.TLabel").pack(anchor="w", pady=(0, d(8)))
    auto = ttk.Checkbutton(display, text="开始每日前自动启动并进入游戏", variable=ui.auto_launch, style="Card.TCheckbutton")
    auto.pack(anchor="w")
    ui.setting_controls.append(auto)
    ui.overlay_control = ttk.Checkbutton(display, text="在游戏左侧显示覆盖日志", variable=ui.overlay_logs, style="Card.TCheckbutton")
    ui.overlay_control.pack(anchor="w", pady=(d(8), 0))
    ttk.Label(display, text="透明日志可穿透鼠标；切换到其他窗口时自动隐藏。", style="CardHint.TLabel").pack(anchor="w", padx=d(23))
    timing = card(settings, 2)
    ttk.Label(timing, text="识别与等待", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, d(12)))
    for row, (label, variable) in enumerate((("操作间隔（秒）", ui.action_delay), ("界面超时（秒）", ui.transition_timeout), ("OCR 阈值", ui.ocr_confidence)), 1):
        ttk.Label(timing, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, d(24)), pady=d(5))
        entry = ttk.Entry(timing, textvariable=variable, width=10)
        entry.grid(row=row, column=1, sticky="w", pady=d(5))
        ui.setting_controls.append(entry)
    save = ttk.Button(settings, text="保存设置", style="Accent.TButton", command=ui._save_settings)
    save.grid(row=3, column=0, sticky="w")
    ui.setting_controls.append(save)
    ttk.Label(settings, text="首次账号登录、验证码和更新请在游戏中完成。", style="Hint.TLabel").grid(row=4, column=0, sticky="w", pady=d(15))

    footer = ttk.Frame(root, padding=(d(18), d(9)))
    footer.grid(row=1, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    ttk.Label(footer, textvariable=ui.summary, style="Hint.TLabel", wraplength=d(640)).grid(row=0, column=0, sticky="w")
    files = ttk.Frame(footer)
    files.grid(row=0, column=1, sticky="e")
    ui.screenshot_button = ttk.Button(files, text="打开截图", command=lambda: ui._open_path(ui.last_screenshot))
    ui.report_button = ttk.Button(files, text="打开报告", command=lambda: ui._open_path(ui.last_report_file))
    ui.directory_button = ttk.Button(files, text="运行目录", command=lambda: ui._open_path(ui.last_output_dir))
    for button in (ui.screenshot_button, ui.report_button, ui.directory_button):
        button.pack(side="left", padx=(d(6), 0))
    ttk.Label(footer, textvariable=ui.saved, style="Hint.TLabel", wraplength=d(1000)).grid(row=1, column=0, columnspan=2, sticky="w", pady=(d(6), 0))
    ui._show_page("home")
