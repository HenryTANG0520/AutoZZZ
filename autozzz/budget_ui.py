"""待核对电量的原生界面。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .localization_tk import PresentationVar
from . import theme
from .battle_budget import budget_directory
from .budget_records import BudgetRecords
from .onedragon_charge_recovery import NativeChargeRecovery


class BudgetWindow:
    def __init__(self,parent,server,scale=1.):
        directory = budget_directory()
        self.records = BudgetRecords(directory)
        self.recovery = NativeChargeRecovery(directory)
        self.server,self.items=server,{}
        self.window=tk.Toplevel(parent)
        self.window.title("AutoZZZ · 待核对消耗")
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
        ttk.Label(header,text="待核对消耗",style="PageTitle.TLabel").grid(row=0,column=0,sticky="w")
        ttk.Label(header,text="国服" if server=="cn" else "国际服",style="Hint.TLabel").grid(row=0,column=1)
        area=ttk.Frame(self.window,padding=(d(16),0))
        area.grid(row=1,column=0,sticky="nsew")
        area.columnconfigure(0,weight=1)
        area.rowconfigure(0,weight=1)
        self.table=ttk.Treeview(area,columns=("id","count","cost"),show="headings",selectmode="browse",height=5)
        for key,label,width in (("id","预算编号",350),("count","待核对次数",100),("cost","预留电量",100)):
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
        self.hint = theme.wrapped_label(footer, text='', style='Hint.TLabel')
        self.hint.grid(row=0, column=0, sticky='ew')
        self.status=PresentationVar(self.window)
        theme.wrapped_label(footer,textvariable=self.status,style="Hint.TLabel").grid(row=1,column=0,sticky="ew",pady=(d(8),d(12)))
        actions=ttk.Frame(footer)
        actions.grid(row=2,column=0,sticky="ew")
        self.account=ttk.Button(actions,text="按预留上限计入",command=self.resolve)
        self.account.pack(side="left",padx=(0,d(8)))
        ttk.Button(actions,text="重新读取",command=self.refresh).pack(side="left",padx=(0,d(8)))
        ttk.Button(actions,text="关闭",command=self.window.destroy).pack(side="right")
        self.refresh()

    def selected(self):
        selection = self.table.selection()
        item = self.items.get(selection[0]) if selection else None
        verified = item is not None and 'verified_reservation' in item
        self.account.configure(text='按胜利记录结清' if verified else '按预留上限计入')
        self.hint.configure(text='实际消耗与结果未知；计入后，预留电量额度不退回。' if item and not verified else '')
        self.account.state(['!disabled'] if item is not None else ['disabled'])

    def refresh(self):
        self.table.delete(*self.table.get_children())
        self.items={}
        try:
            self.items={str(i):item for i,item in enumerate(self.records.pending(self.server))}
            for key,item in self.items.items():self.table.insert("","end",iid=key,values=(item["id"],item["pending"],item["cost"]))
            self.status.set("选择要核对的预算" if self.items else "没有待核对消耗")
        except Exception as exc:self.status.set(str(exc))
        self.selected()

    def resolve(self):
        selection = self.table.selection()
        item = self.items.get(selection[0]) if selection else None
        if item is None:
            return
        try:
            if 'verified_reservation' in item:
                result = self.recovery.recover_verified(item['id'], item['revision'], item['verified_reservation'])
                message = f"已结清 {result['cost']} 电量的预留，胜利已核实。"
            else:
                result = self.records.account_reserved_upper_bound(item['id'], item['revision'])
                message = f"已按预留上限 {result['cost']} 电量计入；结果未知。"
            self.refresh()
            self.status.set(message)
        except Exception as exc:self.status.set(str(exc))
