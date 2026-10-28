"""待核对电量的原生界面。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme
from .battle_budget import budget_directory
from .budget_records import BudgetRecords


class BudgetWindow:
    def __init__(self,parent,server,scale=1.):
        self.records=BudgetRecords(budget_directory())
        self.server,self.items=server,{}
        self.window=tk.Toplevel(parent)
        self.window.title("AutoZZZ · 待核对出战")
        d=lambda value:round(value*scale)
        self.window.geometry(f"{d(700)}x{d(400)}")
        self.window.minsize(d(580),d(330))
        self.window.configure(background=theme.PANEL)
        self.window.transient(parent)
        self.window.columnconfigure(0,weight=1)
        self.window.rowconfigure(1,weight=1)
        header=ttk.Frame(self.window,padding=d(16))
        header.grid(row=0,column=0,sticky="ew")
        header.columnconfigure(0,weight=1)
        ttk.Label(header,text="待核对出战",style="PageTitle.TLabel").grid(row=0,column=0,sticky="w")
        ttk.Label(header,text="国服" if server=="cn" else "国际服",style="Hint.TLabel").grid(row=0,column=1)
        area=ttk.Frame(self.window,padding=(d(16),0))
        area.grid(row=1,column=0,sticky="nsew")
        area.columnconfigure(0,weight=1)
        area.rowconfigure(0,weight=1)
        self.table=ttk.Treeview(area,columns=("id","count","cost"),show="headings",selectmode="browse",height=5)
        for key,label,width in (("id","预算编号",350),("count","待核对场次",100),("cost","预留电量",100)):
            self.table.heading(key,text=label)
            self.table.column(key,width=d(width),minwidth=d(50),stretch=key=="id")
        self.table.grid(row=0,column=0,sticky="nsew")
        scroll=ttk.Scrollbar(area,command=self.table.yview)
        scroll.grid(row=0,column=1,sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind("<<TreeviewSelect>>",lambda _:self.selected())
        footer=ttk.Frame(self.window,padding=d(16))
        footer.grid(row=2,column=0,sticky="ew")
        footer.columnconfigure(0,weight=1)
        theme.wrapped_label(footer,text="实际消耗与战斗结果未知；计入后，预留电量额度不退回。",style="Hint.TLabel").grid(row=0,column=0,sticky="ew")
        self.status=tk.StringVar(self.window)
        theme.wrapped_label(footer,textvariable=self.status,style="Hint.TLabel").grid(row=1,column=0,sticky="ew",pady=(d(8),d(12)))
        actions=ttk.Frame(footer)
        actions.grid(row=2,column=0,sticky="ew")
        self.account=ttk.Button(actions,text="按预留上限计入",command=self.resolve)
        self.account.pack(side="left",padx=(0,d(8)))
        ttk.Button(actions,text="重新读取",command=self.refresh).pack(side="left",padx=(0,d(8)))
        ttk.Button(actions,text="关闭",command=self.window.destroy).pack(side="right")
        self.refresh()

    def selected(self):self.account.state(["!disabled"] if self.table.selection() else ["disabled"])

    def refresh(self):
        self.table.delete(*self.table.get_children())
        self.items={}
        try:
            self.items={str(i):item for i,item in enumerate(self.records.pending(self.server))}
            for key,item in self.items.items():self.table.insert("","end",iid=key,values=(item["id"],item["pending"],item["cost"]))
            self.status.set("选择要核对的预算" if self.items else "没有待核对出战")
        except Exception as exc:self.status.set(str(exc))
        self.selected()

    def resolve(self):
        selection=self.table.selection()
        if not selection:return
        item=self.items[selection[0]]
        try:
            result=self.records.account_reserved_upper_bound(item["id"],item["revision"])
            self.refresh()
            self.status.set(f"已按预留上限 {result['cost']} 电量计入；战斗结果未知。")
        except Exception as exc:self.status.set(str(exc))
