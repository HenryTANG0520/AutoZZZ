"""Independent combat form: named preset, explicit energy cap, no daily opt-in."""
from pathlib import Path
import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from .battle import BattlePlan, CATEGORIES
from .config import ROOT, RESOURCE_ROOT
from . import theme

class BattlePanel:
    def __init__(self, ui):
        self.ui=ui
        try:self.plan=BattlePlan.load(ROOT/"battle.json")
        except (ValueError,TypeError,OSError):self.plan=BattlePlan()
        self.vars={name:tk.StringVar(ui.root,str(getattr(self.plan,name))) for name in self.plan.__dataclass_fields__}
        if "combat" not in ui.pages:
            body=ui.pages["home"].master
            page=ttk.Frame(body);page.grid(row=0,column=0,sticky="nsew");page.columnconfigure(0,weight=1)
            ui.pages["combat"]=page
            rail=ui.nav_buttons["tasks"].master
            for key in ("logs","settings"):
                button=ui.nav_buttons[key];button.grid_configure(row=int(button.grid_info()["row"])+1)
            button=ttk.Button(rail,text="自动战斗",style="Nav.TButton",command=lambda:ui._show_page("combat"))
            button.grid(row=4,column=0,sticky="ew",pady=3);ui.nav_buttons["combat"]=button
        page=ui.pages["combat"]
        for widget in ui.nav_buttons["tasks"].master.winfo_children():
            try:
                if widget.cget("text")=="非战斗每日 · 电量消耗 0":widget.configure(text="每日不耗电量 · 战斗按独立预算")
            except tk.TclError:pass
        s = ui.ui_scale
        d = lambda n: round(n * s)
        theme.page_heading(page, "自动战斗（试验）", s, note="仅用现有电量 · 不使用备用电或电池")
        forms = ttk.Frame(page)
        forms.grid(row=1, column=0, sticky="ew")
        for col in (0, 1):
            forms.columnconfigure(col, weight=1, uniform="combat-forms")
        self.controls={}
        self.catalog={}
        path=ROOT/"training_catalog.json"
        if not path.exists():path=RESOURCE_ROOT/"assets"/"training_catalog.json"
        if path.exists():self.catalog=json.loads(path.read_text(encoding="utf-8"))
        groups = (
            ("副本与队伍", (("category", "训练模式"), ("subcategory", "训练项目"),
                ("stage", "目标副本"), ("team", "游戏内预编队名"), ("expected_roster", "角色顺序（逗号，选填）"),
                ("battles", "场次 · 1—15"), ("energy_budget", "总电量上限 · 20—300"), ("max_energy", "每场电量上限"))),
            ("JSON 预输入策略", (("strategy_file", "JSON 文件（选填）"), ("script", "动作序列"),
                ("hold_unit", "长按单位（秒）"), ("intro_wait", "转场等待（秒）"),
                ("cycles", "每场脚本轮数"), ("timeout", "结算超时（秒）"))))
        for col, (title, fields) in enumerate(groups):
            from .game_widgets import InsetPanel
            panel = InsetPanel(forms, s, height=d(316))
            panel.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else d(12), 0))
            form = panel.content
            form.columnconfigure(1, weight=1)
            ttk.Label(form, text=title, style="CardKicker.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, d(5)))
            for row, (name, label) in enumerate(fields, 1):
                ttk.Label(form, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, d(10)), pady=d(2))
                control = ttk.Combobox(form, textvariable=self.vars[name], width=12, font=("Microsoft YaHei UI", 9), style="Compact.TCombobox") if name in ("category", "subcategory", "stage", "strategy_file") else ttk.Entry(form, textvariable=self.vars[name], width=12, font=("Microsoft YaHei UI", 9), style="Compact.TEntry")
                control.grid(row=row, column=1, sticky="ew", pady=d(2))
                self.controls[name] = control
                ui.setting_controls.append(control)
                if name == "strategy_file":
                    from .battle_strategy import BUILTINS
                    control.configure(values=("",) + BUILTINS)
            if col == 1:
                button = ttk.Button(form, text="选择 JSON 文件…", command=self.browse_strategy)
                button.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(d(9), 0))
                ui.setting_controls.append(button)
        self.controls["category"].configure(values=CATEGORIES,state="readonly")
        self.controls["category"].bind("<<ComboboxSelected>>",lambda _:self.refresh_choices("category"))
        self.controls["subcategory"].bind("<<ComboboxSelected>>",lambda _:self.refresh_choices("subcategory"))
        self.refresh_choices()
        self.strategy_hint = tk.StringVar(ui.root)
        self.strategy_hint_label=theme.wrapped_label(page, textvariable=self.strategy_hint, style="Hint.TLabel")
        self.strategy_hint_label.grid(row=2, column=0, sticky="ew", pady=(d(8), d(3)))
        self.vars["strategy_file"].trace_add("write", self.refresh_strategy_hint)
        self.refresh_strategy_hint()
        theme.wrapped_label(page, text="A 鼠标普攻 · E / Q 技能 · Space 换人 · h 长按单位 · - 换人标记 · | 轮末", style="Hint.TLabel").grid(row=3, column=0, sticky="ew", pady=(d(8), d(10)))
        buttons=ttk.Frame(page);buttons.grid(row=4,column=0,sticky="w")
        for label,command in (("保存方案",self.save),("读取副本目录",lambda:ui._start("battle_catalog")),("准备队伍（不出战）",lambda:ui._start("battle_prepare")),("开始战斗",lambda:ui._start("battle"))):
            button=ttk.Button(buttons,text=label,command=command,style="Accent.TButton" if label=="开始战斗" else "TButton");button.pack(side="left",padx=(0,d(10)));ui.setting_controls.append(button)
        from .onedragon_ui import open_strategies
        self.condition_button = ttk.Button(buttons, text="YAML 条件策略…", command=lambda: open_strategies(ui))
        self.condition_button.pack(side="left", padx=(d(12), 0))
        ui.setting_controls.append(self.condition_button)

    def refresh_strategy_hint(self, *_args):
        selected = bool(self.vars["strategy_file"].get().strip())
        self.strategy_hint.set("文件覆盖队名、角色顺序及策略参数；电量上限沿用左侧设置。" if selected else "")
        if selected:self.strategy_hint_label.grid()
        else:self.strategy_hint_label.grid_remove()

    def refresh_choices(self, changed=None):
        group=self.catalog.get(self.vars["category"].get(),{})
        self.controls["subcategory"].configure(values=list(group))
        if changed=="category" and group:self.vars["subcategory"].set(next(iter(group)))
        stages=group.get(self.vars["subcategory"].get(),[])
        self.controls["stage"].configure(values=stages)
        if changed and stages:self.vars["stage"].set(stages[0])

    def browse_strategy(self):
        path=filedialog.askopenfilename(parent=self.ui.root,title="选择战斗策略",filetypes=[("JSON策略","*.json")])
        if path:self.vars["strategy_file"].set(path)

    def read(self):
        values={name:var.get() for name,var in self.vars.items()}
        for name in ("hold_unit","intro_wait","timeout"):values[name]=float(values[name])
        for name in ("cycles","max_energy","battles","energy_budget"):values[name]=int(values[name])
        plan=BattlePlan(**values);plan.validate()
        from .battle_strategy import resolve_strategy
        resolve_strategy(plan,root=ROOT,resource_root=RESOURCE_ROOT)
        return plan

    def save(self):
        try:self.read().save(ROOT/"battle.json");self.ui._log("独立战斗方案已保存；每日设置未改写。")
        except (ValueError,TypeError,OSError) as exc:messagebox.showerror("战斗方案",str(exc),parent=self.ui.root)


def finished(ui,report):
    ui.running=ui.paused=ui.focus_waiting=False
    ui._freeze_clock()
    battle=report.get("battle",{})
    prepared=report.get("operation")=="battle_prepare"
    catalog=report.get("operation")=="battle_catalog"
    if catalog and report.get("status")=="success":
        path=ROOT/"training_catalog.json"
        if path.exists():ui.combat_panel.catalog=json.loads(path.read_text(encoding="utf-8"));ui.combat_panel.refresh_choices()
    good=report.get("status")=="success"
    label=("副本和队伍已准备，未出战" if prepared else "战斗结算已核实") if good else ("战斗已停止" if report.get("status")=="stopped" else "战斗未完成")
    if catalog and good:label="训练目录已读取，未出战"
    ui.status.set(label)
    energy=report.get("stamina_spent",0)
    detail="出战已发送，电量消耗待核实" if (battle.get("energy_pending") or battle.get("pending")) else f"本次电量消耗 {energy}"
    ui.summary.set(f"{label} · {detail}"+(" · "+report["error"] if report.get("error") else ""))
    ui._replace_text(ui.report_text,json.dumps(report,ensure_ascii=False,indent=2))
    if report.get("run_dir"):
        ui.last_output_dir=Path(report["run_dir"]);ui.last_report_file=ui.last_output_dir/"report.json"
        ui._find_screenshot(ui.last_output_dir)
    ui._log(ui.summary.get(),"info" if good else "warning")
    ui._finish_overlay()
    if not good:ui._restore()


def start(ui, operation):
    from dataclasses import replace
    import threading
    import time
    from .gui import SERVER_NAMES
    if ui.busy or ui.closing:return
    try:
        plan=BattlePlan() if operation=="battle_catalog" else ui.combat_panel.read()
        server=next(key for key,label in SERVER_NAMES.items() if label==ui.server.get())
        settings=replace(ui.settings,server=server,game_path=ui.game_path.get().strip(),auto_launch=ui.auto_launch.get(),
                         overlay_logs=ui.overlay_logs.get(),action_delay=float(ui.action_delay.get()),
                         transition_timeout=int(ui.transition_timeout.get()),ocr_confidence=float(ui.ocr_confidence.get()))
        settings.validate("battle")
        from .battle_strategy import resolve_strategy
        display_plan,strategy=resolve_strategy(plan,root=ROOT,resource_root=RESOURCE_ROOT)
    except (ValueError,TypeError,StopIteration) as exc:
        messagebox.showerror("战斗方案",str(exc),parent=ui.root);return
    ui.runner=None;ui.operation=operation;ui.overlay_game_handle=None;ui._daily_goal_complete=False
    ui.stop_requested.clear();ui.busy=ui.running=True
    ui.paused=ui.stopping=ui.worker_done=ui.focus_waiting=False
    ui.started_at=time.monotonic();ui.status.set("准备副本" if operation=="battle_prepare" else "一次战斗")
    ui.summary.set(f"{plan.subcategory} → {plan.stage} · 队伍 {display_plan.team} · 电量上限 {plan.max_energy}"
                   +(f" · 策略 {strategy['name']}" if strategy else ""))
    ui._refresh_controls();ui.overlay.begin(ui.status.get());ui._log(ui.summary.get());ui.root.iconify()
    def work():
        notified=threading.Event()
        def send(event):
            if event.get("type")=="finished":notified.set()
            ui.emit(event)
        try:
            from .app import Runner
            runner=Runner(settings,send)
            stopped=ui.stop_requested.is_set()
            if stopped:runner.stop()
            ui.emit(dict(type="runner_ready",runner=runner,stop_applied=stopped))
            report=runner.run(battle_plan=plan,prepare_only=operation=="battle_prepare",catalog_only=operation=="battle_catalog")
            if not notified.is_set():ui.emit(dict(type="finished",report=report.to_dict()))
        except Exception as exc:ui.emit(dict(type="error",message=str(exc)))
        finally:ui.emit(dict(type="worker_done"))
    ui.worker=threading.Thread(target=work,name="AutoZZZ-"+operation,daemon=False);ui.worker.start()
