"""实际应用运行窗口；配置、资源预算和运行反馈沿用 AutoZZZ 交互。"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk,filedialog
import uuid

from . import theme
from .config import GAME_PATHS,ROOT,Settings
from .onedragon_runtime import RuntimePaths
from .onedragon_session import RuntimeSession


class ExecutionWindow:
    def __init__(self,ui,app_id,app_name,profile):
        self.ui,self.app_id,self.profile=ui,app_id,profile
        self.closed=self.closing=self.busy=self.prepared=self.connected=False
        self.paused=False
        self.bootstrapping=False
        self.loading_account=True
        self.account_name=""
        self.instance_idx=None
        self.application_states={}
        self.last_state={}
        self.last_report=None
        self.resource_confirmation_id=""
        self.resource_required=False
        self.poll_token=None
        self.events=queue.Queue()
        self.window=tk.Toplevel(ui.root)
        self.window.title("AutoZZZ · "+app_name)
        d=lambda n:round(n*ui.ui_scale)
        width,height=min(d(960),ui.root.winfo_screenwidth()-d(50)),min(d(740),ui.root.winfo_screenheight()-d(90))
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(d(780),width),min(d(620),height))
        self.window.configure(background=theme.PANEL)
        self.window.transient(ui.root)
        self.window.columnconfigure(0,weight=1)
        self.window.rowconfigure(3,weight=1)
        self.status=tk.StringVar(self.window,"正在连接运行环境…")
        same_profile=ui.server.get()==("国服" if profile=="cn" else "国际服")
        self.game_path=tk.StringVar(self.window,ui.game_path.get() if same_profile else GAME_PATHS[profile])
        self.region=tk.StringVar(self.window,"cn" if profile=="cn" else "asia")
        self.traces=[(var,var.trace_add("write",self.connection_changed)) for var in (self.game_path,self.region)]
        self.window.bind("<Destroy>",self.disposed,add="+")
        self.auto_launch=tk.BooleanVar(self.window,app_id not in ("auto_battle","dodge_assistant","commission_assistant","operation_debug"))
        self.timeout=tk.StringVar(self.window,"600")
        self.allowance=tk.StringVar(self.window,"20")
        self.rounds=tk.StringVar(self.window,"1")
        self.per_battle=tk.StringVar(self.window,"20")
        self.uses_charge=app_id=="charge_plan"
        header=ttk.Frame(self.window,padding=d(18))
        header.grid(row=0,column=0,sticky="ew")
        header.columnconfigure(0,weight=1)
        ttk.Label(header,text=app_name,style="PageTitle.TLabel").grid(row=0,column=0,sticky="w")
        self.account_badge=ttk.Label(header,text=("国服" if profile=="cn" else "国际服")+" · 试验",style="Hint.TLabel")
        self.account_badge.grid(row=0,column=1,sticky="e")
        settings=ttk.Frame(self.window,padding=(d(18),0,d(18),d(12)))
        settings.grid(row=1,column=0,sticky="ew")
        settings.columnconfigure(1,weight=1)
        ttk.Label(settings,text="游戏程序").grid(row=0,column=0,sticky="w",padx=(0,d(14)))
        path=ttk.Entry(settings,textvariable=self.game_path,width=24)
        path.grid(row=0,column=1,sticky="ew")
        browse=ttk.Button(settings,text="浏览",command=self.browse)
        browse.grid(row=0,column=2,padx=(d(8),0))
        row=ttk.Frame(settings)
        row.grid(row=1,column=0,columnspan=3,sticky="ew",pady=(d(12),0))
        launch=ttk.Checkbutton(row,text="自动进入游戏",variable=self.auto_launch,style="Config.TCheckbutton")
        launch.pack(side="left",padx=(0,d(18)))
        ttk.Label(row,text="时限（秒，暂停不计）").pack(side="left",padx=(0,d(8)))
        timeout=ttk.Entry(row,textvariable=self.timeout,width=7)
        timeout.pack(side="left")
        self.settings_controls=[path,browse,launch,timeout]
        if profile=="global":
            region=ttk.Combobox(row,textvariable=self.region,values=("asia","us","eu","twhkmo"),state="readonly",width=8)
            region.pack(side="right")
            self.settings_controls.append(region)
            ttk.Label(row,text="账号时区").pack(side="right",padx=d(8))
        self.options=ttk.Frame(self.window)
        self.options.grid(row=2,column=0,sticky="ew")
        self.options.columnconfigure(0,weight=1)
        self.budget_frame=ttk.LabelFrame(self.options,text="本轮战斗预算",padding=d(12))
        self.budget_frame.grid(row=0,column=0,sticky="ew",padx=d(18),pady=(0,d(12)))
        for col,(name,var) in enumerate((("总电量上限",self.allowance),("最多场次",self.rounds),("每场电量上限",self.per_battle))):
            ttk.Label(self.budget_frame,text=name).grid(row=0,column=col*2,sticky="w",padx=(0,d(8)))
            entry=ttk.Entry(self.budget_frame,textvariable=var,width=6)
            entry.grid(row=0,column=col*2+1,sticky="w",padx=(0,d(18)))
            self.settings_controls.append(entry)
        theme.wrapped_label(self.budget_frame,text="仅使用现有电量 · 不补充电量或合成电池",style="Hint.TLabel").grid(row=1,column=0,columnspan=5,sticky="ew",pady=(d(9),0))
        self.budget_button=ttk.Button(self.budget_frame,text="预算记录…",command=self.budget_records)
        self.budget_button.grid(row=1,column=5,sticky="e",pady=(d(9),0))
        self.settings_controls.append(self.budget_button)
        self.assistant_frame=ttk.Frame(self.options,padding=(d(18),0,d(18),d(12)))
        self.assistant_frame.grid(row=1,column=0,sticky="ew")
        ttk.Label(self.assistant_frame,text="助手模式").pack(side="left",padx=(0,d(12)))
        self.mode_labels=["对话","闪避","战斗"]
        self.mode=tk.StringVar(self.window,self.mode_labels[0])
        self.mode_selector=ttk.Combobox(self.assistant_frame,textvariable=self.mode,values=self.mode_labels,state="readonly",width=16)
        self.mode_selector.pack(side="left")
        self.mode_selector.bind("<<ComboboxSelected>>",self.set_assistant_mode)
        self.resource_frame=ttk.LabelFrame(self.options,text="本轮资源操作",padding=d(12))
        self.resource_frame.grid(row=2,column=0,sticky="ew",padx=d(18),pady=(0,d(12)))
        self.resource_frame.columnconfigure(0,weight=1)
        self.resource_text=tk.Text(self.resource_frame,height=4,wrap="word",font=("Microsoft YaHei UI",10),
                                   background=theme.FIELD,foreground=theme.TEXT,borderwidth=0,padx=d(8),pady=d(6),state="disabled")
        self.resource_text.grid(row=0,column=0,sticky="ew")
        resource_scroll=ttk.Scrollbar(self.resource_frame,command=self.resource_text.yview)
        self.resource_text.configure(yscrollcommand=resource_scroll.set)
        resource_scroll.grid(row=0,column=1,sticky="ns")
        self.resource_accepted=tk.BooleanVar(self.window,False)
        self.resource_check=ttk.Checkbutton(self.resource_frame,text="允许本轮按以上配置消费或拆解",variable=self.resource_accepted,
                                           style="Config.TCheckbutton",command=self.update_buttons)
        self.resource_check.grid(row=1,column=0,columnspan=2,sticky="w",pady=(d(9),0))
        area=ttk.Frame(self.window,padding=(d(18),0))
        area.grid(row=3,column=0,sticky="nsew")
        area.columnconfigure(0,weight=1)
        area.rowconfigure(0,weight=1)
        self.log=tk.Text(area,wrap="word",font=("Microsoft YaHei UI",10),background=theme.FIELD,foreground=theme.TEXT,
                         insertbackground=theme.ACCENT,borderwidth=0,padx=d(12),pady=d(12),state="disabled")
        scroll=ttk.Scrollbar(area,command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0,column=0,sticky="nsew")
        scroll.grid(row=0,column=1,sticky="ns")
        footer=ttk.Frame(self.window,padding=d(18))
        footer.grid(row=4,column=0,sticky="ew")
        footer.columnconfigure(0,weight=1)
        theme.wrapped_label(footer,textvariable=self.status,style="Hint.TLabel").grid(row=0,column=0,sticky="ew",pady=(0,d(12)))
        actions=ttk.Frame(footer)
        actions.grid(row=1,column=0,sticky="w")
        self.prepare_button=ttk.Button(actions,text="准备运行",command=self.prepare)
        self.start_button=ttk.Button(actions,text="开始运行",command=self.start,style="Accent.TButton")
        self.pause_button=ttk.Button(actions,text="暂停 F9",command=self.pause)
        self.stop_button=ttk.Button(actions,text="停止 F10",command=self.stop,style="Danger.TButton")
        self.close_button=ttk.Button(actions,text="关闭",command=self.close)
        self.report_button=ttk.Button(actions,text="报告",command=lambda:ui._open_path(self.last_report))
        for button in (self.prepare_button,self.start_button,self.pause_button,self.stop_button,self.report_button,self.close_button):button.pack(side="left",padx=(0,d(8)))
        self.pause_button.pack_configure(padx=(d(12),d(8)))
        self.report_button.pack_configure(padx=(d(12),d(8)))
        self.window.protocol("WM_DELETE_WINDOW",self.close)
        self.window.bind("<F10>",lambda _:self.stop())
        if app_id in ("auto_battle","dodge_assistant"):
            self.write("战斗助手需先在游戏中进入战斗；自动选本请使用体力刷本。")
        if app_id=="notorious_hunt":
            self.write("每周免费挑战；不自动切换深度追猎。")
        self.session=RuntimeSession(RuntimePaths.discover(ROOT,profile),self.events.put)
        self.worker=self.session.worker
        self.update_buttons()
        self.poll_token=self.window.after(80,self.poll)

    def browse(self):
        path=filedialog.askopenfilename(parent=self.window,filetypes=[("绝区零程序","ZenlessZoneZero.exe")])
        if path:self.game_path.set(path)

    def budget_records(self):
        from .budget_ui import BudgetWindow
        BudgetWindow(self.window,self.profile,self.ui.ui_scale)

    def connection_changed(self,*_):
        self.prepared=False
        if hasattr(self,"resource_accepted"):self.resource_accepted.set(False)
        if hasattr(self,"prepare_button"):self.update_buttons()

    def disposed(self,event):
        if event.widget is self.window:
            if self.poll_token is not None:
                self.window.after_cancel(self.poll_token)
                self.poll_token=None
            for variable,token in self.traces:variable.trace_remove("write",token)
            self.traces.clear()

    def write(self,message):
        self.log.configure(state="normal")
        self.log.insert("end",str(message)+"\n")
        if int(self.log.index("end-1c").split(".")[0])>600:self.log.delete("1.0","100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def update_buttons(self):
        self.prepare_button.configure(style="TButton" if self.prepared else "Accent.TButton")
        self.start_button.configure(style="Accent.TButton" if self.prepared else "TButton")
        self.prepare_button.state(["!disabled"] if self.connected and not self.loading_account and not self.busy and not self.closing else ["disabled"])
        confirmed=not self.resource_required or (self.resource_accepted.get() and bool(self.resource_confirmation_id))
        self.start_button.state(["!disabled"] if self.connected and self.prepared and confirmed and not self.busy and not self.closing else ["disabled"])
        self.pause_button.state(["!disabled"] if self.busy and (self.bootstrapping or self.last_state.get("state")=="running") and not self.closing else ["disabled"])
        self.pause_button.configure(text="继续 F9" if self.paused else "暂停 F9")
        self.stop_button.state(["!disabled"] if self.busy and not self.closing else ["disabled"])
        self.report_button.state(["!disabled"] if self.last_report is not None else ["disabled"])
        for control in self.settings_controls:control.state(["disabled"] if self.busy or self.closing or self.loading_account else ["!disabled"])
        if self.uses_charge:self.budget_frame.grid()
        else:self.budget_frame.grid_remove()
        assistant=self.last_state.get("assistant",{})
        if self.app_id=="commission_assistant" or assistant.get("mode") is not None:self.assistant_frame.grid()
        else:self.assistant_frame.grid_remove()
        self.mode_selector.state(["!disabled"] if assistant.get("mode") is not None and self.busy and not self.closing else ["disabled"])
        if self.last_state.get("resources",{}).get("actions"):self.resource_frame.grid()
        else:self.resource_frame.grid_remove()
        if self.resource_required:self.resource_check.grid()
        else:self.resource_check.grid_remove()
        self.resource_check.state(["!disabled"] if self.prepared and not self.busy and not self.closing else ["disabled"])

    def set_assistant_mode(self,_event=None):
        if self.mode_selector.instate(["disabled"]):return
        self.session.send("rpc",method="set_assistant_mode",tag="assistant-mode",
                          params=dict(mode=self.mode_labels.index(self.mode.get()),job_id=self.last_state.get("job_id","")))

    def prepare(self):
        if self.busy or self.closing:return
        try:Settings(server=self.profile,game_path=self.game_path.get()).validate("enter_game")
        except (TypeError,ValueError) as exc:
            self.status.set(str(exc))
            return
        self.busy=True
        self.prepared=False
        self.resource_accepted.set(False)
        self.status.set("正在准备运行环境")
        self.session.send("prepare",app_id=self.app_id,server=self.profile,game_path=self.game_path.get(),region=self.region.get(),instance_idx=self.instance_idx,instance_name=self.account_name)
        self.update_buttons()

    def start(self):
        if not self.prepared or self.busy or self.closing:return
        if self.resource_required and (not self.resource_accepted.get() or not self.resource_confirmation_id):
            self.status.set("请先确认本轮资源操作。")
            return
        try:
            timeout=int(self.timeout.get())
            if not 30<=timeout<=7200:raise ValueError("运行时限须为 30—7200 秒。")
            budget=None
            if self.uses_charge:
                allowance,rounds,maximum=int(self.allowance.get()),int(self.rounds.get()),int(self.per_battle.get())
                if not 1<=allowance<=300 or not 1<=rounds<=15 or not 20<=maximum<=100:raise ValueError("电量上限 1—300，场次 1—15，每场上限 20—100。")
                budget=dict(id="onedragon-"+uuid.uuid4().hex,allowance=allowance,battle_limit=rounds,max_per_battle=maximum)
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.busy=True
        self.status.set("正在开始运行")
        confirmation=self.resource_confirmation_id if self.resource_required else None
        self.session.send("start",app_id=self.app_id,budget=budget,timeout_seconds=timeout,auto_launch=self.auto_launch.get(),resource_confirmation=confirmation)
        self.update_buttons()

    def pause(self):
        if self.busy:self.session.pause(not self.paused)

    def stop(self):
        self.session.stop()
        self.status.set("正在停止…")

    def poll(self):
        self.poll_token=None
        if self.closed:return
        try:
            while True:
                event=self.events.get_nowait()
                kind=event["type"]
                if kind=="connected":
                    self.connected=True
                    self.status.set("正在读取当前账号配置")
                    self.session.send("rpc",method="account_list",tag="run-account-list",params={})
                elif kind=="rpc_result" and event["tag"]=="run-account-list":
                    snapshot=event["result"]
                    self.instance_idx=snapshot["current"]
                    self.account_name=next(item["name"] for item in snapshot["instances"] if item["idx"]==snapshot["current"])
                    self.session.send("rpc",method="account_describe",tag="run-account",params=dict(idx=snapshot["current"],server=self.profile))
                elif kind=="rpc_result" and event["tag"]=="run-account":
                    result=event["result"]
                    if result["path_configured"]:self.game_path.set(result["values"]["game_path"])
                    self.region.set(result["values"]["game_region"])
                    name=self.account_name if len(self.account_name)<=16 else self.account_name[:16]+"…"
                    self.account_badge.configure(text=("国服" if self.profile=="cn" else "国际服")+" · "+name)
                    self.write("当前账号配置："+self.account_name)
                    self.loading_account=False
                    self.status.set("已连接 · 待准备")
                elif kind=="rpc_result" and event["tag"]=="assistant-mode":
                    self.events.put(dict(type="state",state=event["result"]))
                elif kind=="window":self.ui.overlay_game_handle=event["handle"]
                elif kind=="report":
                    from pathlib import Path
                    self.last_report=Path(event["path"])
                elif kind=="log":
                    self.write(event["message"])
                    if self.busy and not self.ui.closing:self.ui.overlay.append(event["message"])
                elif kind=="phase":
                    self.bootstrapping=event.get("bootstrapping",self.bootstrapping)
                    self.status.set(event["phase"])
                    if event.get("minimize") and not self.ui.closing:
                        self.ui.overlay.begin(event["phase"])
                        self.ui.root.iconify()
                elif kind=="bootstrap_paused":
                    self.paused=event["paused"]
                    self.status.set("已暂停" if self.paused else "正在进入游戏")
                elif kind=="state":
                    state=event["state"]
                    self.last_state=state
                    resources=state.get("resources",{})
                    from .onedragon_resources import resource_confirmation_label
                    self.resource_check.configure(text=resource_confirmation_label(resources.get("actions",[])))
                    token=resources.get("confirmation_id","")
                    if token!=self.resource_confirmation_id and state["state"]=="prepared":self.resource_accepted.set(False)
                    self.resource_confirmation_id=token
                    self.resource_required=resources.get("confirmation_required",False)
                    if resources.get("confirmed"):self.resource_accepted.set(True)
                    self.resource_text.configure(state="normal")
                    self.resource_text.delete("1.0","end")
                    self.resource_text.insert("end","\n".join("• "+item["text"] for item in resources.get("actions",[])))
                    self.resource_text.configure(state="disabled")
                    assistant=state.get("assistant",{})
                    keys=assistant.get("keys") or state.get("prepared_requirements",{}).get("shortcuts",{})
                    self.mode_labels=["对话","闪避"+("（"+keys["dodge"]+"）" if keys.get("dodge") else ""),
                                      "战斗"+("（"+keys["battle"]+"）" if keys.get("battle") else "")]
                    self.mode_selector.configure(values=self.mode_labels)
                    if assistant.get("mode") is not None:self.mode.set(self.mode_labels[assistant["mode"]])
                    for item in state.get("applications",[]):
                        if self.application_states.get(item["id"])!=item["state"]:
                            self.application_states[item["id"]]=item["state"]
                            label={"pending":"等待","running":"运行中","success":"完成","failed":"失败","skipped":"记录已完成","interrupted":"结果未核实"}.get(item["state"],item["state"])
                            self.write(item["name"]+" · "+label+(" · "+item["detail"] if item.get("detail") else ""))
                    if state.get("window_handle") is not None:self.ui.overlay_game_handle=state["window_handle"]
                    self.busy=state["busy"]
                    self.bootstrapping=False
                    self.prepared=state["state"]=="prepared"
                    if self.prepared and self.app_id=="one_dragon":
                        self.write("执行顺序："+" → ".join(item["name"] for item in state.get("prepared_applications",[])))
                    if self.prepared:
                        requirements=state.get("prepared_requirements",{})
                        for policy in requirements.get("policies",[]):self.write("已准备策略："+policy["name"])
                        if requirements.get("routes"):self.write(f"已准备路线：{len(requirements['routes'])} 条")
                    self.uses_charge=state.get("uses_charge",False)
                    control=state.get("control",{})
                    self.paused=control.get("manual_paused",False)
                    name={"ready":"运行环境已就绪","prepared":"已准备","running":"正在运行","initializing":"正在初始化",
                          "preparing":"正在准备策略和模型","stopping":"正在停止","stopped":"已停止","failed":"运行失败","success":"运行完成"}.get(state["state"],state["state"])
                    if state["state"]=="success" and state.get("redemption"):
                        name="流程结束 · 兑换奖励到账未核实"
                    if state["state"]=="success" and any(not item["reward_verified"] for item in state.get("resource_results",[])):
                        name="流程结束 · 拆解结果未核实"
                    if control.get("focus_waiting"):name="等待游戏恢复焦点"
                    if self.paused:name="已暂停"
                    if state.get("time_limited"):name="已达到运行时限"
                    if state.get("budget",{}).get("limited"):name="已达到本轮预算"
                    hunt=state.get("hunt",{})
                    fights=[]
                    if hunt.get("free_completed"):fights.append(f"免费 {hunt['free_completed']} 场")
                    if hunt.get("paid_completed"):fights.append(f"深度追猎 {hunt['paid_completed']} 场")
                    if fights:name+=" · "+"，".join(fights)
                    notifications=state.get("notifications",{})
                    if notifications.get("pending") and state.get("busy") and state["state"]=="success":name="等待通知发送"
                    if notifications.get("sent") or notifications.get("failed"):
                        name+=f" · 通知已发送 {notifications.get('sent',0)} / 失败 {notifications.get('failed',0)}"
                    elif self.app_id=="notify" and state["state"]=="success" and not notifications.get("enabled",False):
                        name="通知已关闭"
                    detail=state.get("error") or state.get("preparation_step") or state.get("run",{}).get("last_status","")
                    if state["state"]=="success" and not fights and hunt.get("skipped"):
                        name="计划已跳过"
                        detail=hunt["skipped"][-1]["status"]
                    self.status.set(name+(" · "+detail[:100] if detail else ""))
                    if not self.busy and state["state"] not in ("ready","prepared"):
                        self.prepared=False
                        if not self.ui.closing:self.ui.overlay.finish(name)
                        if not self.closing:
                            self.ui.root.deiconify()
                            self.window.deiconify()
                    if not self.ui.closing:self.ui.overlay.set_status(name)
                elif kind=="error":
                    self.busy=event.get("busy",False)
                    if self.loading_account:self.connected=False
                    if not self.busy:self.bootstrapping=False
                    self.status.set(event["message"][:160])
                    self.write(event["message"])
                    if not self.busy and not self.closing:
                        self.ui.root.deiconify()
                        self.window.deiconify()
                elif kind=="closed":
                    self.connected=self.prepared=self.busy=self.bootstrapping=False
                    if self.closing:
                        self.closed=True
                        self.window.destroy()
                        return
                self.update_buttons()
        except queue.Empty:pass
        self.poll_token=self.window.after(80,self.poll)

    def close(self):
        if not self.closing:
            self.closing=True
            self.status.set("正在停止并关闭…")
            self.session.close()
            self.update_buttons()
            if not self.worker.is_alive():
                self.closed=True
                self.window.destroy()
        return True


def open_execution(ui,app_id,app_name,profile):
    existing=getattr(ui,"execution_window",None)
    if existing is not None and not existing.closed:
        existing.window.deiconify()
        existing.window.lift()
        return existing
    ui.execution_window=ExecutionWindow(ui,app_id,app_name,profile)
    return ui.execution_window
