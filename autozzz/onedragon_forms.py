"""一条龙配置表单和记录编辑器；界面只编辑草稿，不运行游戏。"""
from __future__ import annotations

import copy
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from . import theme


def enable_scroll(canvas,content):
    tag = "AutoZZZ-Scroll-"+str(id(canvas))
    def scroll(event):
        canvas.yview_scroll(-int(event.delta/120),"units")
        return "break"
    if not getattr(canvas,"_autozzz_scroll_bound",False):
        canvas.bind_class(tag,"<MouseWheel>",scroll)
        canvas._autozzz_scroll_bound = True
    def apply(widget):
        if widget.winfo_class() not in ("Text","Treeview","TCombobox","Canvas") and tag not in widget.bindtags():
            widget.bindtags((tag,)+widget.bindtags())
        for child in widget.winfo_children():apply(child)
    apply(content)


class Choice:
    def __init__(self, parent, options: list[dict], value: Any, *, editable=False, custom=False):
        self.options = options
        self.custom = custom
        self.variable = tk.StringVar(parent)
        self.widget = ttk.Combobox(parent, textvariable=self.variable, width=22,
                                   state="normal" if editable else "readonly")
        self.set_options(options, value)

    def set_options(self, options, value):
        self.options = options
        self.widget.configure(values=[option["label"] for option in options])
        self.variable.set(next((o["label"] for o in options if o["value"] == value), str(value or "")))

    def get(self):
        for option in self.options:
            if option["label"] == self.variable.get():
                return option["value"]
        if self.custom:return self.variable.get()
        raise ValueError("请选择列表中的有效选项。")


class ScalarForm:
    def __init__(self, parent, fields: list[dict], changed: Callable, scale=1.):
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(1, weight=1)
        self.fields, self.readers, self.controls = fields, {}, {}
        self.traces = []
        self.frame.bind("<Destroy>",self._destroyed,add="+")
        self.ready = False
        d = lambda n: round(n*scale)
        row,last_group = 0,None
        for field in fields:
            group = field.get("group")
            if group and group!=last_group:
                ttk.Label(self.frame,text=group,style="ConfigTitle.TLabel").grid(row=row,column=0,columnspan=2,sticky="w",pady=(d(8),d(14)))
                row+=1
                last_group=group
            name, kind, value = field["name"], field["kind"], field["value"]
            line = ttk.Frame(self.frame)
            line.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0,d(14)))
            line.columnconfigure(0, minsize=d(205))
            line.columnconfigure(1, weight=1)
            if kind != "bool":
                label = ttk.Label(line, text=field["label"], style="Config.TLabel", wraplength=d(195))
                label.grid(row=0, column=0, sticky="nw", padx=(0,d(14)), pady=d(4))
            if kind == "bool":
                variable = tk.BooleanVar(line, bool(value))
                widget = ttk.Checkbutton(line, variable=variable, text=field["label"],style="Config.TCheckbutton")
                reader = variable.get
            elif kind in ("enum", "agent", "suggestion"):
                choice = Choice(line, field["options"], value, editable=kind in ("agent","suggestion"),custom=kind=="suggestion")
                widget, variable, reader = choice.widget, choice.variable, choice.get
                self.controls[name] = choice
            elif kind == "agents":
                widget = ttk.Frame(line)
                widget.columnconfigure((0,1,2),weight=1,uniform="agents")
                choices = []
                for col in range(3):
                    original = value[col] if col < len(value) else "unknown"
                    options = copy.deepcopy(field["options"])
                    if original is None: options[0]["value"] = None
                    choice = Choice(widget, options, original, editable=True)
                    choice.widget.configure(width=10)
                    choice.widget.grid(row=0,column=col,sticky="ew",padx=(0,d(6)))
                    token=choice.variable.trace_add("write",lambda *_: self.ready and changed())
                    self.traces.append((choice.variable,token))
                    choices.append(choice)
                reader = lambda choices=choices: [c.get() for c in choices]
                variable = None
            elif kind in ("lines","priority"):
                container = ttk.Frame(line)
                text = tk.Text(container, height=4, wrap="word", font=("Microsoft YaHei UI",10),
                                 background=theme.FIELD,foreground=theme.TEXT,insertbackground=theme.ACCENT,
                                 borderwidth=0,padx=d(8),pady=d(6))
                text.pack(fill="x")
                labels = {o["value"]:o["label"] for o in field["options"]} if kind=="priority" else {}
                initial = "\n".join(labels.get(v,v) for v in value)
                text.insert("1.0",initial)
                text.edit_modified(False)
                def modified(_event, widget=text):
                    if widget.edit_modified():
                        widget.edit_modified(False)
                        if self.ready: changed()
                text.bind("<<Modified>>",modified)
                def reader(widget=text,original=copy.deepcopy(value),initial=initial,labels=labels):
                    text = widget.get("1.0","end-1c")
                    if text==initial:return original
                    lines = [v.strip() for v in text.splitlines() if v.strip()]
                    if labels:
                        by_name={label:value for value,label in labels.items()}
                        for name in lines:
                            if name not in by_name:raise ValueError("未识别的物品："+name)
                        return [by_name[name] for name in lines]
                    return lines
                if kind=="priority" and field["options"]:
                    add = ttk.Frame(container)
                    add.pack(fill="x",pady=(d(6),0))
                    choice = Choice(add,field["options"],field["options"][0]["value"])
                    choice.widget.pack(side="left",fill="x",expand=True)
                    def append(text=text,choice=choice):
                        label=choice.variable.get()
                        current=text.get("1.0","end-1c")
                        if label not in current.splitlines():text.insert("end",("\n" if current else "")+label)
                    ttk.Button(add,text="添加",command=append).pack(side="left",padx=(d(6),0))
                widget=container
                variable = None
                order_hint=field.get('order_hint','每行一项 · 从上到下优先')
                if order_hint:ttk.Label(line,text=order_hint,style="Hint.TLabel").grid(row=1,column=1,sticky="w",pady=(d(5),0))
            else:
                variable = tk.StringVar(line,str(value or "") if kind=="str" else str(value))
                widget = ttk.Entry(line,textvariable=variable,width=24)
                if kind=="secret":widget.configure(show="●")
                def reader(variable=variable, kind=kind):
                    text = variable.get()
                    try: return int(text) if kind=="int" else float(text) if kind=="float" else text
                    except ValueError: raise ValueError("请输入"+("整数。" if kind=="int" else "数字。")) from None
                if "minimum" in field and "maximum" in field:
                    ttk.Label(line,text=f"{field['minimum']}—{field['maximum']}",style="Hint.TLabel").grid(row=1,column=1,sticky="w",pady=(d(5),0))
            widget.grid(row=0,column=0 if kind=="bool" else 1,columnspan=2 if kind=="bool" else 1,sticky="w" if kind=="bool" else "ew")
            if field.get("hint"):
                theme.wrapped_label(line,text=field["hint"],style="Hint.TLabel").grid(row=2,column=0 if kind=="bool" else 1,columnspan=2 if kind=="bool" else 1,sticky="ew",pady=(d(5),0))
            if variable is not None:
                token=variable.trace_add("write",lambda *_: self.ready and changed())
                self.traces.append((variable,token))
            self.readers[name] = reader
            self.controls.setdefault(name,widget)
            row+=1
        self.ready = True

    def _destroyed(self,event):
        if event.widget is not self.frame:return
        self.ready=False
        for variable,token in self.traces:variable.trace_remove("write",token)
        self.traces.clear()
        # 让持有 Tcl 变量的读取器在主线程释放，不能留给下一次后台垃圾回收。
        self.readers.clear()
        self.controls.clear()

    def values(self) -> dict:
        result = {}
        for field in self.fields:
            try: result[field["name"]] = self.readers[field["name"]]()
            except ValueError as exc: raise ValueError(field["label"]+"："+str(exc)) from None
        return result


def scalar(name, label, value, kind="str", options=None):
    return dict(name=name,label=label,value=value,kind=kind,options=options or [])


class PlanForm:
    def __init__(self,parent,row:dict,options:dict,app_id:str,changed:Callable,scale:float):
        self.original = copy.deepcopy(row)
        self.training = options["training"]
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(1,weight=1)
        self.selectors = {}
        self.ready = False
        d = lambda n: round(n*scale)
        categories = [dict(label=v["label"],value=k) for k,v in self.training.items() if app_id!="notorious_hunt" or k=="恶名狩猎"]
        for number,(name,label,choices,value) in enumerate((
            ("category_name","训练模式",categories,row.get("category_name",categories[0]["value"])),
            ("mission_type_name","训练项目",[],row.get("mission_type_name")),
            ("mission_name","目标副本",[],row.get("mission_name")),
        )):
            ttk.Label(self.frame,text=label,style="Config.TLabel").grid(row=number,column=0,sticky="w",padx=(0,d(20)),pady=d(7))
            choice = Choice(self.frame,choices,value)
            choice.widget.grid(row=number,column=1,sticky="ew",pady=d(7))
            self.selectors[name] = choice
        self._refresh_type(row.get("mission_type_name"),row.get("mission_name"))
        def category_changed(_):
            self._refresh_type(None,None)
        def type_changed(_):
            self._refresh_mission(None)
        self.selectors["category_name"].widget.bind("<<ComboboxSelected>>",category_changed)
        self.selectors["mission_type_name"].widget.bind("<<ComboboxSelected>>",type_changed)
        fields = [
            scalar("predefined_team_idx","游戏内预编队伍",row.get("predefined_team_idx",-1),"enum",[dict(label="当前游戏队伍",value=-1)]+options["teams"]),
            scalar("auto_battle_config","战斗策略",row.get("auto_battle_config","全配队通用"),"enum",options["policies"]),
            scalar("plan_times","计划次数",row.get("plan_times",1),"int"),
            scalar("run_times","已完成次数",row.get("run_times",0),"int"),
            scalar("card_num","怪物卡数量",row.get("card_num","1"),"enum",options["cards"]),
            scalar("level","挑战等级（可输入）",row.get("level","默认等级"),"suggestion",options["levels"]),
            scalar("notorious_hunt_buff_num","恶名狩猎增益",row.get("notorious_hunt_buff_num",1),"enum",[dict(label=f"第 {i} 个",value=i) for i in (1,2,3)]),
        ]
        self.form = ScalarForm(self.frame,fields,changed,scale)
        self.form.frame.grid(row=3,column=0,columnspan=2,sticky="ew",pady=(d(12),0))
        self.ready = True

    def _refresh_type(self, preferred, mission):
        category = self.selectors["category_name"].get()
        types = self.training[category]["types"]
        choices = [dict(label=v["label"],value=k) for k,v in types.items()] or [dict(label="无需选择",value="")]
        self.selectors["mission_type_name"].set_options(choices,preferred if preferred in types else choices[0]["value"])
        self._refresh_mission(mission)

    def _refresh_mission(self, preferred):
        types = self.training[self.selectors["category_name"].get()]["types"]
        missions = types.get(self.selectors["mission_type_name"].get(),{}).get("missions",[])
        choices = missions or [dict(label="无需选择",value=None)]
        self.selectors["mission_name"].set_options(choices,preferred if preferred in [v["value"] for v in choices] else choices[0]["value"])

    def values(self):
        row = dict(self.original,tab_name="训练",**self.form.values())
        row.update({name:choice.get() for name,choice in self.selectors.items()})
        for name in ("plan_times","run_times"):
            if row[name]<0: raise ValueError("次数必须为非负整数。")
        return row


class RecordDialog:
    def __init__(self,parent,kind,row,options,app_id,on_accept,scale=1.):
        self.window = tk.Toplevel(parent)
        self.window.title({"plans":"刷本计划","plan":"双倍活动计划","teams":"预编队伍","defense_teams":"防卫战队伍","codes":"兑换码"}[kind])
        d = lambda n: round(n*scale)
        width,height = min(d(760),parent.winfo_screenwidth()-d(50)),min(d(720),parent.winfo_screenheight()-d(90))
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(d(660),width),min(d(520),height))
        self.window.configure(background=theme.PANEL)
        self.window.columnconfigure(0,weight=1)
        self.window.rowconfigure(0,weight=1)
        self.canvas = tk.Canvas(self.window,highlightthickness=0,background=theme.PANEL)
        scroll = ttk.Scrollbar(self.window,command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.grid(row=0,column=0,sticky="nsew")
        scroll.grid(row=0,column=1,sticky="ns")
        content = ttk.Frame(self.canvas,padding=d(20))
        anchor = self.canvas.create_window(0,0,window=content,anchor="nw")
        self.canvas.bind("<Configure>",lambda e:self.canvas.itemconfigure(anchor,width=e.width))
        content.bind("<Configure>",lambda _:self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.kind,self.original,self.accept = kind,copy.deepcopy(row),on_accept
        self.weaknesses = {}
        if kind in ("plans","plan"):
            self.form = PlanForm(content,row,options,app_id,lambda:None,scale)
        else:
            if kind=="teams":
                fields = [scalar("idx","编队位置",row["idx"],"enum",options["teams"]),
                          scalar("name","游戏内队名",row["name"]),
                          scalar("auto_battle","战斗策略",row["auto_battle"],"enum",options["policies"]),
                          scalar("agent_id_list","角色顺序",row["agent_id_list"],"agents",options["agents"])]
            elif kind=="defense_teams":
                fields = [scalar("team_idx","预编队伍",row.get("team_idx",0),"enum",options["teams"]),
                          scalar("for_critical","用于剧变节点",row.get("for_critical",True),"bool")]
            else:
                fields = [scalar("code","兑换码",row.get("code","")),scalar("expires","到期日（YYYYMMDD）",row.get("expires",20261231),"int")]
            self.form = ScalarForm(content,fields,lambda:None,scale)
            if kind=="teams": self.form.controls["idx"].widget.state(["disabled"])
        self.form.frame.pack(fill="x")
        if kind=="defense_teams":
            group = ttk.LabelFrame(content,text="适用弱点",padding=d(10))
            group.pack(fill="x",pady=(d(12),0))
            for i,option in enumerate(options["weaknesses"]):
                variable = tk.BooleanVar(group,option["value"] in row.get("weakness_list",[]))
                ttk.Checkbutton(group,text=option["label"],variable=variable,style="Config.TCheckbutton").grid(row=i//4,column=i%4,sticky="w",padx=d(10),pady=d(5))
                self.weaknesses[option["value"]] = variable
        footer = ttk.Frame(self.window,padding=d(18))
        footer.grid(row=1,column=0,columnspan=2,sticky="ew")
        footer.columnconfigure(0,weight=1)
        self.status = tk.StringVar(footer)
        theme.wrapped_label(footer,textvariable=self.status,style="Hint.TLabel").grid(row=0,column=0,columnspan=2,sticky="ew",pady=(0,d(8)))
        ttk.Button(footer,text="加入草稿",command=self.confirm,style="Accent.TButton").grid(row=1,column=0,sticky="e",padx=d(8))
        ttk.Button(footer,text="取消",command=self.window.destroy).grid(row=1,column=1,sticky="e")
        self.window.transient(parent.winfo_toplevel())
        self.window.grab_set()
        self.window.bind("<Destroy>",self._destroyed,add="+")
        enable_scroll(self.canvas,content)

    def _destroyed(self,event):
        if event.widget is not self.window:return
        self.status=None
        self.weaknesses.clear()
        self.accept=None
        if isinstance(self.form,PlanForm):self.form.selectors.clear()
        self.form=None

    def confirm(self):
        try:
            values = self.form.values()
            if self.kind=="defense_teams": values["weakness_list"]=[name for name,v in self.weaknesses.items() if v.get()]
            self.accept(values)
        except (TypeError,ValueError) as exc:
            self.status.set(str(exc))
            return
        self.window.destroy()


class Records:
    def __init__(self,parent,field,options,app_id,changed,scale=1.):
        self.frame = ttk.Frame(parent)
        self.field,self.options,self.app_id,self.changed,self.scale = field,options,app_id,changed,scale
        self.kind = field["kind"]
        self.rows = copy.deepcopy([field["value"]] if self.kind=="plan" else field["value"])
        self.frame.columnconfigure(0,weight=1)
        self.frame.rowconfigure(1,weight=1)
        d = lambda n: round(n*scale)
        ttk.Label(self.frame,text=field["label"],style="ConfigTitle.TLabel").grid(row=0,column=0,sticky="w",pady=(0,d(10)))
        titles = {"plans":["副本","队伍","进度"],"plan":["副本","队伍","次数"],"teams":["位置","队名","角色","策略"],
                  "defense_teams":["队伍","适用弱点","剧变"],"applications":["顺序","应用","启用"],"codes":["兑换码","到期日"]}[self.kind]
        columns = [str(i) for i in range(len(titles))]
        self.table = ttk.Treeview(self.frame,columns=columns,show="headings",selectmode="browse",height=5,style="Config.Treeview")
        for col,title in zip(columns,titles):
            self.table.heading(col,text=title)
            self.table.column(col,width=d(150 if col=="0" else 110),minwidth=d(65),stretch=True)
        vertical = ttk.Scrollbar(self.frame,command=self.table.yview)
        horizontal = ttk.Scrollbar(self.frame,orient="horizontal",command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        self.table.grid(row=1,column=0,sticky="nsew")
        vertical.grid(row=1,column=1,sticky="ns")
        horizontal.grid(row=2,column=0,sticky="ew")
        buttons = ttk.Frame(self.frame)
        buttons.grid(row=3,column=0,sticky="w",pady=(d(9),0))
        self.buttons = {}
        actions = []
        if self.kind not in ("teams","applications","plan"): actions.append(("新增",lambda:self.edit(True)))
        if self.kind!="applications": actions.append(("编辑",self.edit))
        if self.kind not in ("teams","applications","plan"): actions.append(("移除",self.remove))
        if self.kind in ("plans","applications"): actions.extend((("上移",lambda:self.move(-1)),("下移",lambda:self.move(1))))
        if self.kind=="applications": actions.insert(0,("启用 / 停用",self.toggle))
        for label,command in actions:
            button = ttk.Button(buttons,text=label,command=command)
            button.pack(side="left",padx=(0,d(6)))
            self.buttons[label] = button
        self.table.bind("<Double-1>",lambda _:self.toggle() if self.kind=="applications" else self.edit())
        self.table.bind("<<TreeviewSelect>>",lambda _:self.update_action_label())
        self.dialog = None
        self.refresh()

    def refresh(self,selected=0):
        limit = 8 if self.kind in ("teams","applications") else 5
        self.table.configure(height=min(limit,max(2,len(self.rows))))
        self.table.delete(*self.table.get_children())
        agent_labels = {a["value"]:a["label"] for a in self.options["agents"]}
        weakness_labels = {a["value"]:a["label"] for a in self.options["weaknesses"]}
        teams = {a["value"]:a["label"] for a in self.options["teams"]}
        for i,row in enumerate(self.rows):
            if self.kind in ("plans","plan"):
                values = (row.get("mission_name") or row.get("mission_type_name") or row.get("category_name"),
                          teams.get(row.get("predefined_team_idx",-1),"当前队伍"),f"{row.get('run_times',0)} / {row.get('plan_times',1)}")
            elif self.kind=="teams": values=(row["idx"]+1,row["name"]," / ".join(agent_labels.get(a,a) for a in row["agent_id_list"]),row["auto_battle"])
            elif self.kind=="defense_teams": values=(teams.get(row["team_idx"],row["team_idx"]+1)," / ".join(weakness_labels.get(a,a) for a in row["weakness_list"]),"是" if row["for_critical"] else "否")
            elif self.kind=="applications": values=(i+1,row["name"],"是" if row["enabled"] else "否")
            else: values=(row["code"],row["expires"])
            self.table.insert("","end",iid=str(i),values=values)
        if self.rows:
            selected = max(0,min(selected,len(self.rows)-1))
            self.table.selection_set(str(selected))
            self.table.see(str(selected))
        self.update_action_label()

    def update_action_label(self):
        if self.kind!="applications":return
        idx=self.selected()
        enabled=idx is not None and self.rows[idx]["enabled"]
        self.buttons["启用 / 停用"].configure(text="停用此应用" if enabled else "启用此应用")

    def selected(self):
        selection = self.table.selection()
        return int(selection[0]) if selection else None

    def edit(self,new=False):
        if self.table.instate(["disabled"]):return
        idx = self.selected()
        if not new and idx is None: return
        if self.dialog is not None and self.dialog.window.winfo_exists():
            self.dialog.window.lift()
            return
        row = {} if new else self.rows[idx]
        def accept(value):
            if new: self.rows.append(value)
            else: self.rows[idx] = value
            self.changed()
            self.refresh(len(self.rows)-1 if new else idx)
        self.dialog = RecordDialog(self.frame,self.kind,row,self.options,self.app_id,accept,self.scale)

    def remove(self):
        if self.table.instate(["disabled"]):return
        idx = self.selected()
        if idx is not None:
            self.rows.pop(idx)
            self.refresh(idx)
            self.changed()

    def move(self,offset):
        if self.table.instate(["disabled"]):return
        idx = self.selected()
        if idx is not None and 0<=idx+offset<len(self.rows):
            self.rows[idx],self.rows[idx+offset]=self.rows[idx+offset],self.rows[idx]
            self.refresh(idx+offset)
            self.changed()

    def toggle(self):
        if self.table.instate(["disabled"]):return
        idx = self.selected()
        if idx is not None:
            self.rows[idx]["enabled"] = not self.rows[idx]["enabled"]
            self.refresh(idx)
            self.changed()

    def values(self):
        return copy.deepcopy(self.rows[0] if self.kind=="plan" else self.rows)
