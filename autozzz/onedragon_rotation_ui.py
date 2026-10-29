"""逐账号展示轮换预检及问题，不提供尚未完成的执行入口。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


class RotationPreviewDialog:
    def __init__(self,parent,plan,scale,edit_account):
        self.plan,self.edit_account=plan,edit_account
        self.window=tk.Toplevel(parent);self.window.title('AutoZZZ · 轮换预检');self.window.transient(parent)
        self.window.configure(background=theme.PANEL)
        d=lambda value:round(value*scale)
        width,height=min(d(940),parent.winfo_screenwidth()-d(50)),min(d(650),parent.winfo_screenheight()-d(90))
        self.window.geometry(f'{width}x{height}');self.window.minsize(min(d(720),width),min(d(480),height))
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(2,weight=1)
        head=ttk.Frame(self.window,padding=d(18));head.grid(row=0,column=0,sticky='ew');head.columnconfigure(0,weight=1)
        ttk.Label(head,text='轮换预检',style='PageTitle.TLabel').grid(row=0,column=0,sticky='w')
        ttk.Label(head,text='配置通过' if plan['configuration_valid'] else f"{len(plan['issues'])} 处问题",style='Hint.TLabel').grid(row=0,column=1)
        body=ttk.Frame(self.window,padding=(d(18),0));body.grid(row=1,column=0,sticky='ew');body.columnconfigure(0,weight=1)
        self.table=ttk.Treeview(body,columns=('order','name','region','login','apps'),show='headings',height=5,style='Config.Treeview',selectmode='browse')
        for key,label,width in (('order','顺序',50),('name','账号配置',230),('region','服务器',100),('login','登录资料',100),('apps','应用',70)):
            self.table.heading(key,text=label);self.table.column(key,width=d(width),minwidth=d(50),stretch=key=='name')
        self.table.grid(row=0,column=0,sticky='ew')
        scroll=ttk.Scrollbar(body,command=self.table.yview);scroll.grid(row=0,column=1,sticky='ns');self.table.configure(yscrollcommand=scroll.set)
        self.table.bind('<<TreeviewSelect>>',self.select)
        regions={'cn':'国服','cn_b':'B服','asia':'亚服','us':'美服','eu':'欧服','twhkmo':'港澳台'}
        for index,row in enumerate(plan['instances']):
            self.table.insert('','end',iid=str(row['idx']),values=(index+1,row['name'],regions.get(row['region'],row['region']),
                '已填写' if row['login_configured'] else '需填写' if row['login_required'] else '保持当前',len(row['applications'])))
        detail=ttk.Frame(self.window,padding=(d(18),d(12),d(18),0));detail.grid(row=2,column=0,sticky='nsew')
        detail.columnconfigure(0,weight=1);detail.rowconfigure(0,weight=1)
        self.details=tk.Text(detail,wrap='word',background=theme.FIELD,foreground=theme.TEXT,font=(theme.FONT,10),relief='flat',padx=d(12),pady=d(12),height=9)
        self.details.grid(row=0,column=0,sticky='nsew')
        scrollbar=ttk.Scrollbar(detail,command=self.details.yview);scrollbar.grid(row=0,column=1,sticky='ns');self.details.configure(yscrollcommand=scrollbar.set)
        footer=ttk.Frame(self.window,padding=d(18));footer.grid(row=3,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        start=next(row['name'] for row in plan['instances'] if row['idx']==plan['start_idx'])
        theme.wrapped_label(footer,text=f"{len(plan['instances'])} 个账号 · 返回起点：{start}"+(' · 涉及电量消耗' if plan['uses_charge'] else ''),style='Hint.TLabel').grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,d(12)))
        ttk.Button(footer,text='编辑所选账号…',command=self.edit).grid(row=1,column=0,sticky='w')
        ttk.Button(footer,text='关闭',command=self.window.destroy).grid(row=1,column=1,sticky='e')
        self.window.bind('<Escape>',lambda _:self.window.destroy())
        self.window.bind('<Destroy>',self.disposed,add='+')
        if plan['instances']:self.table.selection_set(str(plan['instances'][0]['idx']));self.select()
        self.window.grab_set()

    def select(self,_event=None):
        selection=self.table.selection()
        if not selection:return
        idx=int(selection[0]);row=next(row for row in self.plan['instances'] if row['idx']==idx)
        lines=[row['name'],row['game_path'] or '尚未配置游戏路径','', '应用顺序']
        lines.extend(f"{index+1}. {item['name']}" for index,item in enumerate(row['applications']))
        if not row['applications']:lines.append('尚未启用应用')
        transitions=[item for item in self.plan['transitions'] if item['to_idx']==idx]
        if transitions:
            lines+=['','账号切换']
            names={item['idx']:item['name'] for item in self.plan['instances']}
            for item in transitions:
                previous=names.get(item['from_idx'],f"当前配置 {item['from_idx']}")
                lines.append(previous+' → '+row['name']+'：'+('重启客户端并登录' if item['restart_client'] else '登出并登录')+('（返回起点）' if item['returning'] else ''))
        if row['resources']:
            lines+=['','资源操作']+[item['text'] for item in row['resources']]
        issues=[item['message'] for item in self.plan['issues'] if item['instance_idx']==idx]
        if issues:lines+=['','需要修复']+issues
        self.details.configure(state='normal');self.details.delete('1.0','end');self.details.insert('1.0','\n'.join(lines));self.details.configure(state='disabled')

    def edit(self):
        selection=self.table.selection()
        if not selection:return
        idx,callback=int(selection[0]),self.edit_account
        self.window.destroy();callback(idx)

    def disposed(self,event):
        if event.widget is self.window:self.edit_account=None
