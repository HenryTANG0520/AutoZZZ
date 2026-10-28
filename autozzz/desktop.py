"""The actual game compendium layout, adapted to AutoZZZ's existing actions."""
from __future__ import annotations

from tkinter import ttk
from . import theme as t
from .game_widgets import Header, Compendium, EngagementMeter, TaskBoard, SettingRow, InsetPanel


def build_window(ui, task_names, server_names):
    root = ui.root
    ui.ui_scale = max(1., float(root.tk.call("tk", "scaling")) / (96 / 72))
    s = ui.ui_scale
    d = lambda n: round(n * s)
    width = min(d(1280), root.winfo_screenwidth()-d(40))
    height = min(d(860), root.winfo_screenheight()-d(70))
    root.geometry(f"{width}x{height}")
    root.minsize(min(d(1100), width), min(d(800), height))
    root.title("AutoZZZ · 快捷手册")
    root.configure(bg=t.BG)
    ui.style = t.apply_theme(root, s)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    Header(root, s, background=t.BG, height=d(65)).grid(row=0, column=0, sticky="ew")
    ui.compendium = Compendium(root, s)
    ui.compendium.grid(row=1, column=0, sticky="nsew", padx=d(18))
    body = ui.compendium.content
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    ui.nav_buttons = {}
    for col, (key, label) in enumerate((("tasks", "目标"), ("home", "日常"), ("combat", "训练"), ("applications", "应用"), ("logs", "记录"), ("settings", "设置"))):
        button = ttk.Button(ui.compendium.tabs, text=label, style="Nav.TButton", width=8, command=lambda key=key: ui._show_page(key))
        button.grid(row=0, column=col, padx=d(2))
        ui.nav_buttons[key] = button
    ui.pages = {name: ttk.Frame(body) for name in ("home", "tasks", "combat", "applications", "logs", "settings")}
    for page in ui.pages.values():
        page.grid(row=0, column=0, sticky="nsew")
        page.columnconfigure(0, weight=1)

    home = ui.pages["home"]
    home.rowconfigure(2, weight=1)
    ui.engagement_meter = EngagementMeter(home, s, ui.engagement)
    ui.engagement_meter.grid(row=0, column=0, sticky="ew")
    status = ttk.Frame(home)
    status.grid(row=1, column=0, sticky="ew", pady=(d(3), d(10)))
    status.columnconfigure(0, weight=1)
    t.wrapped_label(status, textvariable=ui.engagement, style="Hint.TLabel").grid(row=0, column=0, sticky="ew")
    ttk.Label(status, textvariable=ui.status, foreground=t.TEXT).grid(row=0, column=1, sticky="e", padx=d(15))
    ttk.Label(status, textvariable=ui.elapsed).grid(row=0, column=2, sticky="e")
    ui.table = TaskBoard(home, s, ui, task_names)
    ui.table.grid(row=2, column=0, sticky="nsew")
    ui.table.bind("<<TreeviewSelect>>", ui._task_detail)
    t.wrapped_label(home, textvariable=ui.plan, style="Hint.TLabel").grid(row=3, column=0, sticky="ew", pady=(d(9), 0))

    tasks = ui.pages["tasks"]
    t.page_heading(tasks, "每日计划", s, note="电量消耗 0")
    options = ttk.Frame(tasks)
    options.grid(row=1, column=0, sticky="nsew", pady=(0, d(16)))
    tasks.rowconfigure(1, weight=1)
    options.rowconfigure(0, weight=1)
    labels = {"scratch": "签到（刮卡 / 盲盒）", "coffee": "免费饮品", "video": "录像店营业",
              "daily": "每日活跃奖励", "weekly": "丽都周纪", "city_fund": "丽都城募"}
    ui.task_groups, ui.task_controls = {}, {}
    for col, (title, names) in enumerate((("每日任务", ("scratch", "coffee", "video", "daily")),
                                         ("收尾奖励", ("weekly", "city_fund")))):
        options.columnconfigure(col, weight=1, uniform="task-groups")
        panel = InsetPanel(options, s, height=d(250))
        panel.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else d(16), 0))
        form = panel.content
        form.columnconfigure(0, weight=1)
        ttk.Label(form, text=title, style="CardKicker.TLabel").grid(row=0, column=0, sticky="w", pady=(d(4), d(16)))
        ui.task_groups[title] = names
        for row, name in enumerate(names, 1):
            button = ttk.Checkbutton(form, text=labels[name], variable=ui.tasks[name], style="Card.TCheckbutton")
            button.grid(row=row, column=0, sticky="w", padx=d(4), pady=d(12))
            ui.task_controls[name] = button
            ui.setting_controls.append(button)
    promoter = InsetPanel(tasks, s, height=d(111))
    promoter.grid(row=2, column=0, sticky="ew")
    promoter.content.columnconfigure(1, weight=1)
    ttk.Label(promoter.content, text="宣传员（选填）", style="Card.TLabel").grid(row=0, column=0, padx=(0, d(20)), sticky="w")
    ui.promoter_entry = ttk.Entry(promoter.content, textvariable=ui.promoter, width=22)
    ui.promoter_entry.grid(row=0, column=1, sticky="w")
    ui.promoter_hint_label = t.wrapped_label(promoter.content, textvariable=ui.promoter_hint)
    ui.promoter_hint_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(d(7), 0))
    ui.setting_controls.append(ui.promoter_entry)

    logs = ui.pages["logs"]
    t.page_heading(logs, "运行记录", s)
    logs.rowconfigure(1, weight=1)
    ui.output_tabs = ttk.Notebook(logs)
    ui.output_tabs.grid(row=1, column=0, sticky="nsew")
    ui.log = ui._text_tab("运行日志")
    ui.inspection_text = ui._text_tab("窗口检测")
    ui.report_text = ui._text_tab("运行报告")
    for level, color in (("error", t.ERROR), ("warning", t.WARNING)):
        ui.log.tag_configure(level, foreground=color)

    _settings(ui, server_names)
    _footer(ui)
    ui._show_page("home")


def _settings(ui, server_names):
    s = ui.ui_scale
    d = lambda n: round(n*s)
    page = ui.pages["settings"]
    page.rowconfigure(0, weight=1)
    page.columnconfigure(0, weight=0)
    page.columnconfigure(1, weight=1)
    side = InsetPanel(page, s, width=d(186))
    side.grid(row=0, column=0, sticky="ns", padx=(0, d(16)))
    right = InsetPanel(page, s)
    right.grid(row=0, column=1, sticky="nsew")
    side.content.columnconfigure(0, weight=1)
    ttk.Label(side.content, text="设置", style="CardKicker.TLabel", anchor="center").grid(row=0, column=0, sticky="ew", pady=(d(7), d(24)))
    sections, buttons = {}, {}
    right.content.columnconfigure(0, weight=1)
    right.content.rowconfigure(0, weight=1)
    for index, (key, label) in enumerate((("game", "游戏"), ("run", "运行"), ("recognition", "识别")), 1):
        section = ttk.Frame(right.content, style="Card.TFrame")
        section.grid(row=0, column=0, sticky="nsew")
        section.columnconfigure(0, weight=1)
        sections[key] = section
        button = ttk.Button(side.content, text=label, command=lambda key=key: show(key))
        button.grid(row=index, column=0, sticky="ew", pady=d(7))
        buttons[key] = button

    def show(key):
        ui.current_settings_section = key
        for name, section in sections.items():
            if name == key:
                section.grid()
            else:
                section.grid_remove()
            buttons[name].configure(style="Selected.Options.TButton" if name == key else "Options.TButton")

    ui.settings_sections, ui.settings_buttons = sections, buttons
    ui.show_settings_section = show

    def title(section, label, hint=None):
        ttk.Label(section, text=label, style="CardKicker.TLabel").grid(row=0, column=0, sticky="w", pady=(d(8), d(18)))
        if hint:
            t.wrapped_label(section, text=hint).grid(row=8, column=0, sticky="ew", pady=(d(20), 0))

    def row(section, index, label, width=290):
        frame = SettingRow(section, s, label, value_width=width)
        frame.grid(row=index, column=0, sticky="ew", pady=d(4))
        return frame

    game = sections["game"]
    title(game, "游戏连接", "登录验证与需确认的更新请在游戏中完成。")
    line = row(game, 1, "区服")
    ui.server_combo = line.mount(ttk.Combobox(line, textvariable=ui.server, values=list(server_names.values()), state="readonly", width=10))
    ui.server_combo.bind("<<ComboboxSelected>>", ui._select_server)
    line = row(game, 2, "游戏程序", 440)
    path_form = ttk.Frame(line, style="Card.TFrame")
    path_form.columnconfigure(0, weight=1)
    entry = ttk.Entry(path_form, textvariable=ui.game_path, width=15)
    entry.grid(row=0, column=0, sticky="ew", padx=(0, d(6)))
    browse = ttk.Button(path_form, text="浏览", command=ui._choose_game)
    browse.grid(row=0, column=1)
    line.mount(path_form)
    ttk.Label(game, text="ZenlessZoneZero.exe", style="CardHint.TLabel").grid(row=3, column=0, sticky="w", pady=(0, d(6)))
    line = row(game, 4, "自动启动游戏")
    auto = line.mount(ttk.Checkbutton(line, text="启用", variable=ui.auto_launch))
    t.wrapped_label(game, textvariable=ui.readiness).grid(row=5, column=0, sticky="ew", pady=(d(20), 0))
    ui.setting_controls.extend((ui.server_combo, entry, browse, auto))

    run = sections["run"]
    title(run, "运行偏好", "自动关闭仅在每日及所选收尾奖励全部核实后执行。")
    line = row(run, 1, "完成后关闭游戏")
    ui.close_game_control = line.mount(ttk.Checkbutton(line, text="启用", variable=ui.close_game_on_success))
    line = row(run, 2, "游戏覆盖日志")
    ui.overlay_control = line.mount(ttk.Checkbutton(line, text="显示", variable=ui.overlay_logs))
    ui.setting_controls.extend((ui.close_game_control, ui.overlay_control))

    recognition = sections["recognition"]
    title(recognition, "识别与等待")
    for index, (label, variable) in enumerate((("操作间隔（秒） · 0.3—5", ui.action_delay), ("界面超时（秒） · 10—180", ui.transition_timeout), ("OCR 阈值 · 0.40—0.99", ui.ocr_confidence)), 1):
        line = row(recognition, index, label)
        control = line.mount(ttk.Entry(line, textvariable=variable, width=10))
        ui.setting_controls.append(control)
    show("game")


def _footer(ui):
    d = lambda n: round(n*ui.ui_scale)
    footer = ttk.Frame(ui.root, style="Footer.TFrame", padding=(d(24), d(9)))
    footer.grid(row=2, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    ui.summary_label = t.wrapped_label(footer, textvariable=ui.summary, style="FooterStatus.TLabel")
    ui.summary_label.grid(row=0, column=0, sticky="ew")
    t.wrapped_label(footer, textvariable=ui.history_context, style="Footer.TLabel").grid(row=1, column=0, sticky="ew", pady=(d(3), d(8)))
    actions = ttk.Frame(footer, style="Footer.TFrame")
    actions.grid(row=2, column=0, sticky="ew")
    ui.start_button = ttk.Button(actions, text="开始每日  ▶", command=ui._start, style="Accent.TButton")
    ui.launch_button = ttk.Button(actions, text="进入游戏", command=ui._launch)
    ui.check_button = ttk.Button(actions, text="检测窗口", command=ui._check)
    ui.reward_button = ttk.Button(actions, text="仅补领奖", command=ui._reward_only)
    ui.pause_button = ttk.Button(actions, text="暂停 F9", command=ui._pause)
    ui.stop_button = ttk.Button(actions, text="停止 F10", command=ui._stop, style="Danger.TButton")
    ui.save_button = ttk.Button(actions, text="保存设置", command=ui._save_settings)
    for button in (ui.start_button, ui.launch_button, ui.check_button, ui.reward_button, ui.pause_button, ui.stop_button, ui.save_button):
        button.pack(side="left", padx=(d(12) if button in (ui.pause_button, ui.save_button) else 0, d(6)))
    ui.setting_controls.append(ui.save_button)
    files = ttk.Frame(actions, style="Footer.TFrame")
    files.pack(side="right")
    ui.report_button = ttk.Button(files, text="报告", command=lambda: ui._open_path(ui.last_report_file))
    ui.screenshot_button = ttk.Button(files, text="截图", command=lambda: ui._open_path(ui.last_screenshot))
    ui.directory_button = ttk.Button(files, text="目录", command=lambda: ui._open_path(ui.last_output_dir))
    for button in (ui.report_button, ui.screenshot_button, ui.directory_button):
        button.pack(side="left", padx=(d(4), 0))
    ui.save_label = t.wrapped_label(footer, textvariable=ui.saved, style="Footer.TLabel")
    ui.save_label.grid(row=3, column=0, sticky="ew", pady=(d(6), 0))
