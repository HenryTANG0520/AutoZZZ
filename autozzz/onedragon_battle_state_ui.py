"""上游条件战斗的只读状态面板；不建立另一条游戏连接。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


class BattleStateWindow:
    def __init__(self,parent,scale):
        self.window=tk.Toplevel(parent);self.window.title('AutoZZZ · 战斗状态');self.window.transient(parent)
        d=lambda n:round(n*scale)
        width,height=min(d(780),parent.winfo_screenwidth()-d(50)),min(d(590),parent.winfo_screenheight()-d(90))
        self.window.geometry(f'{width}x{height}');self.window.minsize(min(d(590),width),min(d(430),height))
        self.window.configure(background=theme.PANEL);self.window.columnconfigure(0,weight=1);self.window.rowconfigure(2,weight=1)
        header=ttk.Frame(self.window,padding=d(16));header.grid(row=0,column=0,sticky='ew');header.columnconfigure(0,weight=1)
        self.status=tk.StringVar(self.window,'尚未加载战斗策略')
        theme.wrapped_label(header,textvariable=self.status,style='PageTitle.TLabel').grid(row=0,column=0,sticky='ew')
        self.trigger=tk.StringVar(self.window,'—');self.conditions=tk.StringVar(self.window,'—');self.elapsed=tk.StringVar(self.window,'—')
        for row,(name,var) in enumerate((('触发器',self.trigger),('条件集',self.conditions),('持续时间 / 优先级',self.elapsed)),1):
            ttk.Label(header,text=name,style='Hint.TLabel').grid(row=row,column=0,sticky='w',pady=(d(10),0))
            if row in (1,2):
                condition_area=ttk.Frame(header);condition_area.grid(row=row,column=1,sticky='ew',padx=(d(16),0),pady=(d(10),0));condition_area.columnconfigure(0,weight=1)
                text=tk.Text(condition_area,height=1 if row==1 else 3,wrap='word',font=(theme.FONT,10),background=theme.FIELD,foreground=theme.TEXT,relief='flat',state='disabled')
                text.grid(row=0,column=0,sticky='ew')
                scroll=ttk.Scrollbar(condition_area,command=text.yview);scroll.grid(row=0,column=1,sticky='ns');text.configure(yscrollcommand=scroll.set)
                if row==1:self.trigger_text=text
                else:self.condition_text=text
            else:theme.wrapped_label(header,textvariable=var).grid(row=row,column=1,sticky='ew',padx=(d(16),0),pady=(d(10),0))
        header.columnconfigure(0,weight=0);header.columnconfigure(1,weight=1)
        header.grid_slaves(row=0)[0].grid_configure(columnspan=2)
        search=ttk.Frame(self.window,padding=(d(16),0,d(16),d(12)));search.grid(row=1,column=0,sticky='ew');search.columnconfigure(1,weight=1)
        ttk.Label(search,text='状态关键词').grid(row=0,column=0,padx=(0,d(12)))
        self.query=tk.StringVar(self.window);entry=ttk.Entry(search,textvariable=self.query);entry.grid(row=0,column=1,sticky='ew')
        self.query.trace_add('write',lambda *_:self.refresh())
        body=ttk.Frame(self.window,padding=(d(16),0));body.grid(row=2,column=0,sticky='nsew');body.columnconfigure(0,weight=1);body.rowconfigure(0,weight=1)
        self.table=ttk.Treeview(body,columns=('name','age','value'),show='headings',style='Config.Treeview')
        for key,label,width in (('name','状态',360),('age','距触发（秒）',105),('value','状态值',90)):
            self.table.heading(key,text=label);self.table.column(key,width=d(width),minwidth=d(60),stretch=key=='name')
        self.table.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(body,command=self.table.yview);scroll.grid(row=0,column=1,sticky='ns');self.table.configure(yscrollcommand=scroll.set)
        self.table.tag_configure('recent',foreground=theme.SUCCESS)
        footer=ttk.Frame(self.window,padding=d(16));footer.grid(row=3,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        self.count=tk.StringVar(self.window,'0 个状态');ttk.Label(footer,textvariable=self.count,style='Hint.TLabel').grid(row=0,column=0,sticky='w')
        ttk.Button(footer,text='关闭',command=self.window.destroy).grid(row=0,column=1,sticky='e')
        self.window.bind('<Escape>',lambda _:self.window.destroy());self.snapshot={}

    def update(self,snapshot):
        self.snapshot=snapshot or {};self.refresh()

    def refresh(self):
        if not self.window.winfo_exists():return
        data=self.snapshot;task=data.get('task') or {}
        name=data.get('policy') or '战斗策略';name=name if len(name)<=80 else name[:79]+'…'
        self.status.set(name+(' · 运行中' if data.get('running') else ' · 未运行') if data.get('available') else '尚未加载战斗策略')
        self.trigger.set(task.get('trigger','—'));self.conditions.set(task.get('conditions','—'))
        for text,value in ((self.trigger_text,self.trigger.get()),(self.condition_text,self.conditions.get())):
            if text.get('1.0','end-1c')!=value:
                text.configure(state='normal');text.delete('1.0','end');text.insert('1.0',value);text.configure(state='disabled')
        self.elapsed.set(f"{task['elapsed']:.2f} 秒 / {task.get('priority','—')}" if task else '—')
        keywords=self.query.get().casefold().split();rows=data.get('states',[])
        # 上游同样把命中关键词的记录排在前面，不丢弃其他策略状态。
        rows=sorted(rows,key=lambda item:(not any(word in item['name'].casefold() for word in keywords),item['name']))
        ids={item['name'] for item in rows}
        for item in self.table.get_children():
            if item not in ids:self.table.delete(item)
        for index,item in enumerate(rows):
            values=(item['name'],f"{item['age']:.2f}",'—' if item['value'] is None else item['value'])
            attrs=dict(values=values,tags=('recent',) if item['age']<1 else ())
            if self.table.exists(item['name']):self.table.item(item['name'],**attrs);self.table.move(item['name'],'',index)
            else:self.table.insert('',index,iid=item['name'],**attrs)
        self.count.set(f"{len(rows)} 个状态"+(' · 显示上限 1024' if data.get('truncated') else ''))
