"""账号与登录设置的原生窗口；清单和登录设置分别保存。"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import filedialog,messagebox,simpledialog,ttk

from . import theme
from .config import ROOT
from .onedragon_forms import ScalarForm,enable_scroll
from .onedragon_runtime import RuntimePaths
from .onedragon_session import RuntimeSession


class AccountsWindow:
    def __init__(self,ui,profile,on_close=lambda:None):
        self.ui,self.profile,self.on_close=ui,profile,on_close
        self.busy,self.closed,self.closing,self.dirty,self.connected=False,False,False,False,False
        self.snapshot,self.description,self.form={},None,None
        self.events=queue.Queue()
        self.callback=None
        self.poll_token=None
        self.window=tk.Toplevel(ui.root)
        self.window.title("AutoZZZ · 账号配置")
        d=lambda value:round(value*ui.ui_scale)
        width,height=min(d(1000),ui.root.winfo_screenwidth()-d(50)),min(d(760),ui.root.winfo_screenheight()-d(90))
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(d(780),width),min(d(600),height))
        self.window.transient(ui.root)
        self.window.configure(background=theme.PANEL)
        self.window.columnconfigure(0,weight=1)
        self.window.rowconfigure(2,weight=1)
        header=ttk.Frame(self.window,padding=d(18))
        header.grid(row=0,column=0,sticky="ew")
        header.columnconfigure(0,weight=1)
        ttk.Label(header,text="账号配置",style="PageTitle.TLabel").grid(row=0,column=0,sticky="w")
        ttk.Label(header,text="国服 / B服" if profile=="cn" else "国际服",style="Hint.TLabel").grid(row=0,column=1)
        table_area=ttk.Frame(self.window,padding=(d(18),0))
        table_area.grid(row=1,column=0,sticky="ew")
        table_area.columnconfigure(0,weight=1)
        self.table=ttk.Treeview(table_area,columns=("name","current","enabled","login"),show="headings",selectmode="browse",height=4,style="Config.Treeview")
        for key,title,width in (("name","账号配置",260),("current","当前",75),("enabled","一条龙参与",100),("login","切换前重新登录",150)):
            self.table.heading(key,text=title)
            self.table.column(key,width=d(width),minwidth=d(70),stretch=key=="name")
        self.table.grid(row=0,column=0,sticky="ew")
        scroll=ttk.Scrollbar(table_area,command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0,column=1,sticky="ns")
        self.table.bind("<<TreeviewSelect>>",self.select)
        actions=ttk.Frame(table_area)
        actions.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(d(10),d(16)))
        self.actions=[]
        for col,(label,command) in enumerate((("新增",self.create),("重命名",self.rename),("设为当前",lambda:self.action("activate")),
                                             ("参与 / 不参与",self.toggle_enabled),("重新登录 / 保持",self.toggle_login),("移除配置",self.archive))):
            button=ttk.Button(actions,text=label,command=command)
            button.grid(row=col//3,column=col%3,padx=(0,d(8)),pady=(0,d(5)),sticky="w")
            self.actions.append(button)
        body=ttk.Frame(self.window,padding=(d(18),0))
        body.grid(row=2,column=0,sticky="nsew")
        body.columnconfigure(0,weight=1)
        body.rowconfigure(0,weight=1)
        self.canvas=tk.Canvas(body,highlightthickness=0,background=theme.PANEL)
        vertical=ttk.Scrollbar(body,command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vertical.set)
        self.canvas.grid(row=0,column=0,sticky="nsew")
        vertical.grid(row=0,column=1,sticky="ns")
        self.content=ttk.Frame(self.canvas,padding=(0,0,d(10),d(8)))
        anchor=self.canvas.create_window(0,0,window=self.content,anchor="nw")
        self.canvas.bind("<Configure>",lambda event:self.canvas.itemconfigure(anchor,width=event.width))
        self.content.bind("<Configure>",lambda _:self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        footer=ttk.Frame(self.window,padding=d(18))
        footer.grid(row=3,column=0,sticky="ew")
        footer.columnconfigure(0,weight=1)
        theme.wrapped_label(footer,text="选择当前配置不会立即切换游戏中的登录账号。",style="Hint.TLabel").grid(row=0,column=0,columnspan=3,sticky="ew",pady=(0,d(8)))
        self.status=tk.StringVar(self.window,"正在连接…")
        theme.wrapped_label(footer,textvariable=self.status,style="Hint.TLabel").grid(row=1,column=0,columnspan=3,sticky="ew",pady=(0,d(10)))
        self.save_button=ttk.Button(footer,text="保存登录设置",command=self.save,style="Accent.TButton")
        self.save_button.grid(row=2,column=0,sticky="w")
        self.reload_button=ttk.Button(footer,text="重新读取",command=self.reload)
        self.reload_button.grid(row=2,column=1,padx=d(8))
        ttk.Button(footer,text="关闭",command=self.close).grid(row=2,column=2)
        self.session=RuntimeSession(RuntimePaths.discover(ROOT,profile),self.events.put)
        self.worker=self.session.worker
        self.window.protocol("WM_DELETE_WINDOW",self.close)
        self.window.bind("<Destroy>",self.disposed,add="+")
        self.update_buttons()
        self.poll_token=self.window.after(80,self.poll)

    def selected(self):
        selection=self.table.selection()
        return int(selection[0]) if selection else None

    def row(self):return next((item for item in self.snapshot.get("instances",[]) if item["idx"]==self.selected()),None)

    def allow_leave(self):
        return not self.dirty or messagebox.askokcancel("放弃登录设置草稿","登录设置尚未保存，是否放弃？",parent=self.window)

    def request(self,method,callback,**params):
        if self.busy or self.closing or not self.connected:return
        self.busy=True
        self.callback=callback
        self.status.set("正在处理…")
        self.session.send("rpc",method=method,tag=method,params=params)
        self.update_buttons()

    def reload(self):
        if self.allow_leave():self.request("account_list",self.loaded)

    def loaded(self,result,select=None):
        self.snapshot=result
        self.table.configure(height=min(4,max(2,len(result["instances"]))))
        self.table.delete(*self.table.get_children())
        for item in result["instances"]:
            self.table.insert("","end",iid=str(item["idx"]),values=(item["name"],"●" if item["active"] else "",
                              "参与" if item["enabled"] else "不参与","重新登录" if item["force_login"] else "保持"))
        idx=result.get("created",select or result["current"])
        self.description=None
        self.dirty=False
        if idx is not None:self.table.selection_set(str(idx))

    def select(self,_event=None):
        if self.busy or self.closing:return
        idx=self.selected()
        if idx is None or self.description is not None and idx==self.description["idx"]:return
        if not self.allow_leave():
            if self.description is not None:self.table.selection_set(str(self.description["idx"]))
            return
        self.request("account_describe",self.show_account,idx=idx,server=self.profile)

    def show_account(self,result):
        for child in self.content.winfo_children():child.destroy()
        self.form=None
        self.description=result
        self.table.selection_set(str(result["idx"]))
        values=result["values"]
        fields=[]
        labels={"game_path":"游戏程序","game_region":"服务器","game_language":"游戏语言","account":"登录账号",
                "bilibili_account_name":"B站账号昵称","use_custom_win_title":"自定义窗口标题","custom_win_title":"窗口标题"}
        for key in ("game_path","game_region","game_language","use_custom_win_title","custom_win_title","account","bilibili_account_name"):
            kind="enum" if key in ("game_region","game_language") else "bool" if key=="use_custom_win_title" else "str"
            options=result["regions"] if key=="game_region" else [dict(label="简体中文",value="cn"),dict(label="English",value="en")] if key=="game_language" else []
            fields.append(dict(name=key,label=labels[key],kind=kind,value=values[key],options=options,
                               group="登录凭据" if key in ("account","bilibili_account_name") else "游戏与窗口"))
        fields.extend([dict(name="password",label="密码（留空保留）",kind="secret",value="",options=[],group="登录凭据"),
                       dict(name="clear_password",label="清除本地密码",kind="bool",value=False,options=[],group="登录凭据")])
        self.form=ScalarForm(self.content,fields,self.changed,self.ui.ui_scale)
        self.form.frame.pack(fill="x")
        line=ttk.Frame(self.content)
        line.pack(fill="x",pady=(0,10))
        self.browse_button=ttk.Button(line,text="选择游戏程序…",command=self.browse)
        self.browse_button.pack(side="left")
        theme.wrapped_label(self.content,text="密码以明文保存在本机配置；不会显示在运行报告中。",style="Hint.TLabel").pack(fill="x",pady=(0,10))
        enable_scroll(self.canvas,self.content)
        self.dirty=False
        self.status.set("已设置密码" if result["password_set"] else "未设置密码 · 可使用游戏保存的登录状态")
        self.canvas.yview_moveto(0)

    def changed(self):
        self.dirty=True
        self.status.set("登录设置未保存")
        self.update_buttons()

    def browse(self):
        selected=filedialog.askopenfilename(parent=self.window,filetypes=[("绝区零程序","ZenlessZoneZero.exe")])
        if selected:
            entry=self.form.controls["game_path"]
            entry.delete(0,"end")
            entry.insert(0,selected)

    def save(self):
        if self.description is None or self.form is None:return
        try:values=self.form.values()
        except (TypeError,ValueError) as exc:
            self.status.set(str(exc))
            return
        self.request("account_save",self.show_account,idx=self.description["idx"],server=self.profile,revision=self.description["revision"],values=values)

    def action(self,action,**params):
        if self.busy or not self.allow_leave():return
        idx=self.selected()
        if action!="create" and idx is None:return
        self.request("account_action",lambda result:self.loaded(result,idx if action!="archive" else None),action=action,revision=self.snapshot["revision"],idx=idx,**params)

    def create(self):
        if not self.allow_leave():return
        name=simpledialog.askstring("新增账号配置","配置名称",parent=self.window)
        if name:self.action("create",name=name)

    def rename(self):
        row=self.row()
        if row is None:return
        name=simpledialog.askstring("重命名账号配置","配置名称",initialvalue=row["name"],parent=self.window)
        if name:self.action("rename",name=name)

    def toggle_enabled(self):
        row=self.row()
        if row:self.action("options",enabled=not row["enabled"],force_login=row["force_login"])

    def toggle_login(self):
        row=self.row()
        if row:self.action("options",enabled=row["enabled"],force_login=not row["force_login"])

    def archive(self):
        if self.row() is not None and messagebox.askokcancel("移除账号配置","移除本机配置并保留归档，不删除游戏账号。",parent=self.window):self.action("archive")

    def update_buttons(self):
        locked=self.busy or self.closing or not self.connected
        row=self.row()
        if row is not None:
            self.actions[3].configure(text="退出一条龙" if row["enabled"] else "加入一条龙")
            self.actions[4].configure(text="保持登录状态" if row["force_login"] else "切换前重新登录")
        for widget in self.actions+[self.reload_button,self.table]:widget.state(["disabled"] if locked else ["!disabled"])
        self.save_button.state(["!disabled"] if not locked and self.dirty else ["disabled"])
        if self.form is not None:
            for control in self.form.controls.values():
                widget=getattr(control,"widget",control)
                if hasattr(widget,"state"):widget.state(["disabled"] if locked else ["!disabled"])
        if hasattr(self,"browse_button"):self.browse_button.state(["disabled"] if locked else ["!disabled"])

    def poll(self):
        self.poll_token=None
        if self.closed:return
        try:
            while True:
                event=self.events.get_nowait()
                if event["type"]=="connected":
                    self.connected=True
                    self.reload()
                elif event["type"]=="rpc_result":
                    self.busy=False
                    callback,self.callback=self.callback,None
                    if not self.closing and callback is not None:callback(event["result"])
                elif event["type"]=="error":
                    self.busy=False
                    self.callback=None
                    self.status.set(event["message"])
                elif event["type"]=="closed":
                    self.connected=False
                    if self.closing:
                        self.closed=True
                        self.window.destroy()
                        self.on_close()
                        return
                self.update_buttons()
        except queue.Empty:pass
        self.poll_token=self.window.after(80,self.poll)

    def disposed(self,event):
        if event.widget is self.window and self.poll_token is not None:
            self.window.after_cancel(self.poll_token)
            self.poll_token=None

    def close(self):
        if self.closing:return True
        if not self.allow_leave():return False
        self.closing=True
        self.status.set("正在等待原进程退出…")
        self.session.close()
        self.update_buttons()
        if not self.worker.is_alive():
            self.closed=True
            self.window.destroy()
            self.on_close()
        return True


def open_accounts(ui,profile,on_close=lambda:None):
    existing=getattr(ui,"account_window",None)
    if existing is not None and not existing.closed:
        existing.window.deiconify()
        existing.window.lift()
        return existing
    ui.account_window=AccountsWindow(ui,profile,on_close)
    return ui.account_window
