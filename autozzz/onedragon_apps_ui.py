"""按应用分组的一条龙原生配置页面；保存经独立解释器校验后发布。"""
from __future__ import annotations

import copy
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from . import theme
from .config import ROOT
from .onedragon_forms import Records, ScalarForm, enable_scroll
from .onedragon_runtime import OneDragonClient, RequestPending, RuntimePaths


GROUPS = (
    ("执行与队伍", ("_group","teams","battle_assistant")),
    ("日常与奖励", ("daily_signin","scratch_card","hou_hou_bakery","coffee","random_play","engagement_reward","ridu_weekly","city_fund","email","redemption_code")),
    ("战斗与刷本", ("charge_plan","notorious_hunt","shiyu_defense","auto_battle","dodge_assistant","intel_board","life_on_line")),
    ("探索与经营", ("lost_void","withered_domain","suibian_temple","trigrams_collection","world_patrol")),
    ("辅助工具", ("drive_disc_dismantle","commission_assistant","predefined_team_checker","mouse_sensitivity_checker","operation_debug","screenshot_helper","notify")),
)
NOTES = {
    "charge_plan":"刷本会消耗电量；补充方式在下方设置。",
    "notorious_hunt":"每周免费次数与后续电量消耗以游戏为准。",
    "drive_disc_dismantle":"拆解后的驱动盘无法恢复。",
    "suibian_temple":"采购等选项会消耗丁尼、观行币等资源。",
    "teams":"队名与角色顺序须与游戏内预编队伍一致。",
    "battle_assistant":"条件战斗仍在接入；当前设置尚不用于原预输入战斗。",
}
COLLECTIONS = {"plans","plan","teams","defense_teams","applications","codes"}


class ApplicationsPanel:
    def __init__(self,ui:Any):
        self.ui,self.root,self.page = ui,ui.root,ui.pages["applications"]
        self.scale = ui.ui_scale
        d = lambda n:round(n*self.scale)
        self.busy = self.closing = self.closed = self.dirty = False
        self.worker:threading.Thread|None = None
        self.events:queue.Queue = queue.Queue()
        self.apps,self.cache,self.options = {},{},{}
        self.current_id = ""
        self.description = None
        self.form = None
        self.collections = {}
        self.profile = tk.StringVar(self.root,ui.server.get())
        self.loaded_profile = self.profile.get()
        self.status = tk.StringVar(self.root,"尚未读取配置")
        self.title = tk.StringVar(self.root,"应用配置")
        self.note = tk.StringVar(self.root)
        self.controls = []
        self.frozen = []
        self.page.columnconfigure(0,weight=1)
        self.page.rowconfigure(1,weight=1)
        heading = ttk.Frame(self.page)
        heading.grid(row=0,column=0,sticky="ew",pady=(0,d(15)))
        heading.columnconfigure(0,weight=1)
        ttk.Label(heading,text="一条龙应用",style="PageTitle.TLabel").grid(row=0,column=0,sticky="w")
        ttk.Label(heading,text="开发中 · 仅配置",style="Hint.TLabel").grid(row=0,column=1,sticky="e",padx=d(18))
        self.profile_box = ttk.Combobox(heading,textvariable=self.profile,values=["国服","国际服"],state="readonly",width=8)
        self.profile_box.grid(row=0,column=2,sticky="e")
        self.profile_box.bind("<<ComboboxSelected>>",self.change_profile)
        self.load_button = ttk.Button(heading,text="读取配置",command=self.load)
        self.load_button.grid(row=0,column=3,padx=(d(10),0))
        self.controls += [self.profile_box,self.load_button]
        body = ttk.Frame(self.page)
        body.grid(row=1,column=0,sticky="nsew")
        body.columnconfigure(1,weight=1)
        body.rowconfigure(0,weight=1)
        left = ttk.Frame(body)
        left.grid(row=0,column=0,sticky="ns",padx=(0,d(18)))
        left.rowconfigure(0,weight=1)
        self.tree = ttk.Treeview(left,show="tree",selectmode="browse",height=10,style="Config.Treeview")
        self.tree.column("#0",width=d(215),stretch=False)
        scroll = ttk.Scrollbar(left,command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0,column=0,sticky="ns")
        scroll.grid(row=0,column=1,sticky="ns")
        self.tree.bind("<<TreeviewSelect>>",self.select)
        right = ttk.Frame(body)
        right.grid(row=0,column=1,sticky="nsew")
        right.columnconfigure(0,weight=1)
        right.rowconfigure(2,weight=1)
        theme.wrapped_label(right,textvariable=self.title,style="ConfigTitle.TLabel").grid(row=0,column=0,sticky="ew",pady=(d(8),d(12)))
        theme.wrapped_label(right,textvariable=self.note,style="Hint.TLabel").grid(row=1,column=0,sticky="ew",pady=(0,d(14)))
        self.canvas = tk.Canvas(right,background=theme.PANEL,highlightthickness=0)
        self.scroll = ttk.Scrollbar(right,command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.grid(row=2,column=0,sticky="nsew")
        self.scroll.grid(row=2,column=1,sticky="ns")
        self.content = ttk.Frame(self.canvas,padding=(0,0,d(12),0))
        self.anchor = self.canvas.create_window(0,0,window=self.content,anchor="nw")
        self.content.columnconfigure(0,weight=1)
        self.content.bind("<Configure>",lambda _:self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",lambda e:self.canvas.itemconfigure(self.anchor,width=e.width))
        footer = ttk.Frame(self.page)
        footer.grid(row=2,column=0,sticky="ew",pady=(d(13),0))
        footer.columnconfigure(0,weight=1)
        theme.wrapped_label(footer,textvariable=self.status,style="Hint.TLabel").grid(row=0,column=0,sticky="ew",padx=(0,d(12)))
        self.save_button = ttk.Button(footer,text="保存配置",command=self.save,style="Accent.TButton")
        self.save_button.grid(row=0,column=1,sticky="e")
        self.reload_button = ttk.Button(footer,text="重新读取",command=self.reload)
        self.reload_button.grid(row=0,column=2,padx=(d(8),0))
        self.controls += [self.save_button,self.reload_button]
        self._buttons()
        self.root.after(80,self._poll)

    @staticmethod
    def call(client,method,**params):
        request = client.submit(method,**params)
        while True:
            try: return client.result(request,timeout=5)
            except RequestPending: continue

    def _run(self,operation:Callable,success:Callable):
        if self.busy or self.closing: return
        profile = "global" if self.profile.get()=="国际服" else "cn"
        paths = RuntimePaths.discover(ROOT,profile)
        self.busy = True
        self._freeze()
        self.status.set("正在读取…" if not self.dirty else "正在保存…")
        self._buttons()
        def work():
            client = OneDragonClient(paths)
            try:
                client.start()
                self.events.put((success,operation(client),None))
            except Exception as exc: self.events.put((None,None,str(exc)))
            finally:
                try:
                    while True:
                        try:
                            client.close(timeout=5)
                            break
                        except RequestPending: self.events.put((None,None,"正在等待原进程退出…"))
                except Exception as exc: self.events.put((None,None,str(exc)))
                self.events.put(("done",None,None))
        self.worker = threading.Thread(target=work,name="AutoZZZ-Application-Configuration",daemon=False)
        self.worker.start()

    def _poll(self):
        if self.closed: return
        try:
            while True:
                callback,result,error = self.events.get_nowait()
                if callback=="done":
                    self.busy = False
                    self._thaw()
                    self._buttons()
                elif error: self.status.set(error)
                elif not self.closing:
                    try:callback(result)
                    except Exception as exc:self.status.set("配置显示失败："+str(exc))
        except queue.Empty: pass
        if self.closing and (self.worker is None or not self.worker.is_alive()):
            self.closed = True
            return
        self.root.after(80,self._poll)

    def _buttons(self):
        for widget in self.controls: widget.state(["disabled"] if self.busy else ["!disabled"])
        self.save_button.state(["!disabled"] if self.dirty and not self.busy and self.description is not None else ["disabled"])
        self.reload_button.state(["!disabled"] if self.description is not None and not self.busy else ["disabled"])

    def _freeze(self):
        def visit(widget):
            if isinstance(widget,(ttk.Entry,ttk.Combobox,ttk.Checkbutton,ttk.Button,ttk.Treeview)):
                self.frozen.append((widget,widget.state()))
                widget.state(["disabled"])
            elif isinstance(widget,tk.Text):
                self.frozen.append((widget,widget.cget("state")))
                widget.configure(state="disabled")
            for child in widget.winfo_children():visit(child)
        visit(self.content)

    def _thaw(self):
        for widget,state in self.frozen:
            if not widget.winfo_exists():continue
            if isinstance(widget,tk.Text):widget.configure(state=state)
            else:widget.state(["!disabled"]+list(state))
        self.frozen.clear()

    def allow_leave(self):
        return self.busy or not self.dirty or messagebox.askokcancel("放弃未保存修改","当前草稿尚未保存，是否放弃？",parent=self.root)

    def change_profile(self,_event=None):
        if not self.allow_leave():
            self.profile.set(self.loaded_profile)
            return
        self.loaded_profile = self.profile.get()
        self.apps,self.cache,self.options = {},{},{}
        self.current_id = ""
        self.tree.delete(*self.tree.get_children())
        self._clear()
        self.status.set("尚未读取配置")

    def _initial(self,client):
        catalog = self.call(client,"catalog")
        if catalog["failures"]: raise RuntimeError("部分应用未能加载，请查看运行日志。")
        options = self.call(client,"configuration_options")
        extras = [dict(id=k,name=n,configurable=True) for k,n in (("_group","执行顺序"),("teams","预编队伍"),("battle_assistant","战斗设置"))]
        apps = catalog["applications"]+extras+options["challenges"]
        descriptions = {a["id"]:self.call(client,"describe_configuration",app_id=a["id"]) for a in apps if a["configurable"]}
        return dict(apps=apps,options=options,descriptions=descriptions)

    def load(self):
        if self.allow_leave(): self._run(self._initial,self._loaded)

    def _loaded(self,result):
        self.apps = {a["id"]:a for a in result["apps"]}
        self.cache,self.options = result["descriptions"],result["options"]
        self.tree.delete(*self.tree.get_children())
        added = set()
        for i,(label,ids) in enumerate(GROUPS+(("挑战方案",tuple(a["id"] for a in self.options["challenges"])),)):
            parent = "group:"+str(i)
            self.tree.insert("","end",iid=parent,text=label,open=True)
            for app_id in ids:
                if app_id in self.apps:
                    self.tree.insert(parent,"end",iid=app_id,text=self.apps[app_id]["name"])
                    added.add(app_id)
        remaining = set(self.apps)-added
        if remaining:
            self.tree.insert("","end",iid="group:other",text="其他应用",open=True)
            for key in sorted(remaining):self.tree.insert("group:other","end",iid=key,text=self.apps[key]["name"])
        self.current_id = ""
        self.dirty = False
        self.tree.selection_set("_group")
        self.select()

    def select(self,_event=None):
        selection = self.tree.selection()
        if self.busy and _event is not None:
            if self.current_id and selection and selection[0]!=self.current_id:self.tree.selection_set(self.current_id)
            return
        if not selection or selection[0].startswith("group:") or selection[0]==self.current_id:return
        key = selection[0]
        if not self.allow_leave():
            if self.current_id:self.tree.selection_set(self.current_id)
            return
        self.current_id = key
        self.render(self.cache.get(key))

    def _clear(self):
        for child in self.content.winfo_children():child.destroy()
        self.description,self.form,self.collections = None,None,{}
        self.dirty = False
        self.title.set("应用配置")
        self.note.set("")
        self._buttons()

    def render(self,description):
        self._clear()
        self.title.set(self.apps[self.current_id]["name"])
        self.note.set(NOTES.get(self.current_id,""))
        self.description = copy.deepcopy(description)
        if description is None:
            ttk.Label(self.content,text="该应用的专用设置尚在接入。",style="Hint.TLabel").grid(row=0,column=0,sticky="w")
        else:
            simple = [f for f in description["fields"] if f["kind"] not in COLLECTIONS]
            records = [f for f in description["fields"] if f["kind"] in COLLECTIONS]
            row = 0
            # 主计划放在前面；辅助的双倍活动计划放在普通选项后面。
            for field in [f for f in records if f["kind"]!="plan"]:
                editor = Records(self.content,field,self.options,self.current_id,self.changed,self.scale)
                editor.frame.grid(row=row,column=0,sticky="nsew",pady=(0,round(22*self.scale)))
                self.collections[field["name"]] = editor
                row += 1
            self.form = ScalarForm(self.content,simple,self.changed,self.scale)
            self.form.frame.grid(row=row,column=0,sticky="ew")
            row += 1
            for field in [f for f in records if f["kind"]=="plan"]:
                editor = Records(self.content,field,self.options,self.current_id,self.changed,self.scale)
                editor.frame.grid(row=row,column=0,sticky="ew",pady=(round(10*self.scale),0))
                self.collections[field["name"]] = editor
                row += 1
        enable_scroll(self.canvas,self.content)
        self.canvas.yview_moveto(0)
        self.status.set(self.profile.get()+" · 已读取" if description is not None else "配置暂未开放")
        self._buttons()

    def changed(self):
        self.dirty = True
        self.status.set(self.profile.get()+" · 未保存")
        self._buttons()

    def save(self):
        if self.busy or self.description is None:return
        try:
            values = self.form.values() if self.form is not None else {}
            values.update({name:editor.values() for name,editor in self.collections.items()})
            original = {f["name"]:f["value"] for f in self.description["fields"]}
            values = {name:value for name,value in values.items() if value!=original[name]}
        except (TypeError,ValueError) as exc:
            self.status.set(str(exc))
            return
        if not values:
            self.dirty = False
            self.status.set("无未保存修改")
            self._buttons()
            return
        key,revision = self.current_id,self.description["revision"]
        def work(client):
            result = self.call(client,"save_configuration",app_id=key,values=values,revision=revision)
            try:options = self.call(client,"configuration_options")
            except Exception as exc:return dict(description=result,options=None,warning=str(exc))
            return dict(description=result,options=options)
        def saved(result):
            self.cache[key] = result["description"]
            if result["options"] is not None:
                self.options = result["options"]
                for description in self.cache.values():
                    for field in description["fields"]:
                        if field["name"]=="predefined_team_idx":field["options"]=[dict(label="当前游戏队伍",value=-1)]+self.options["teams"]
            self.render(result["description"])
            self.status.set(self.profile.get()+" · 已保存"+("；选项刷新失败，请重新读取。" if result.get("warning") else ""))
        self._run(work,saved)

    def reload(self):
        if self.description is not None and self.allow_leave():
            key = self.current_id
            def loaded(result):
                self.cache[key] = result
                self.render(result)
            self._run(lambda client:self.call(client,"describe_configuration",app_id=key),loaded)

    def close(self,discard=False):
        if self.closing:return True
        if not discard and not self.allow_leave():return False
        self.closing = True
        self._buttons()
        return True
