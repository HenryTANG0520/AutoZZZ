"""Stable control connection for reviewed SDK candidates; no gameplay RPCs."""
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk,messagebox

from .config import ROOT
from .onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending
from . import theme


class SDKUpgradeWindow:
    def __init__(self,owner):
        self.owner=owner;self.ui=owner.ui;self.profile=owner.profile;self.closed=self.closing=self.busy=False
        self.events=queue.Queue();self.commands=queue.Queue();self.catalog=None;self.review=None
        self.window=tk.Toplevel(owner.window);self.window.title('AutoZZZ · 执行SDK');self.window.transient(owner.window)
        d=lambda n:round(n*self.ui.ui_scale);self.window.geometry(f'{d(940)}x{d(700)}');self.window.minsize(d(760),d(560))
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(2,weight=1)
        header=ttk.Frame(self.window,padding=d(16));header.grid(row=0,column=0,sticky='ew');header.columnconfigure(0,weight=1)
        self.identity=tk.StringVar(self.window,('国服'if self.profile=='cn'else'国际服')+' · 正在连接稳定控制入口…')
        theme.wrapped_label(header,textvariable=self.identity).grid(row=0,column=0,sticky='ew')
        bar=ttk.Frame(self.window,padding=(d(16),0));bar.grid(row=1,column=0,sticky='ew');bar.columnconfigure(1,weight=1)
        ttk.Label(bar,text='可选依赖组').grid(row=0,column=0,padx=(0,d(8)))
        self.groups=tk.StringVar(self.window,'dev');ttk.Entry(bar,textvariable=self.groups).grid(row=0,column=1,sticky='ew')
        self.offline=tk.BooleanVar(self.window,True);ttk.Checkbutton(bar,text='仅使用本地依赖缓存',variable=self.offline).grid(row=0,column=2,padx=d(10))
        self.controls=[]
        for column,(label,callback)in enumerate((('审核当前分支',self.review_source),('准备uv工具',lambda:self.send('sdk_prepare_tool')),('构建候选',self.build)),3):
            button=ttk.Button(bar,text=label,command=callback);button.grid(row=0,column=column,padx=d(4));self.controls.append(button)
        theme.wrapped_label(bar,text='依赖组以逗号分隔；留空仅安装基础依赖。dev 包含上游 MCP 依赖；gamepad 使用独立安装入口。',style='Hint.TLabel').grid(row=1,column=0,columnspan=6,sticky='ew',pady=d(8))
        body=ttk.Panedwindow(self.window,orient='vertical');body.grid(row=2,column=0,sticky='nsew',padx=d(16),pady=d(12))
        listing=ttk.Frame(body);listing.columnconfigure(0,weight=1);listing.rowconfigure(0,weight=1);body.add(listing,weight=1)
        self.table=ttk.Treeview(listing,columns=('branch','revision','state','groups'),show='headings',selectmode='browse',height=5)
        for key,label,width in (('branch','分支',180),('revision','提交',160),('state','状态',160),('groups','依赖组',220)):
            self.table.heading(key,text=label);self.table.column(key,width=d(width))
        self.table.grid(row=0,column=0,sticky='nsew');scroll=ttk.Scrollbar(listing,command=self.table.yview);scroll.grid(row=0,column=1,sticky='ns');self.table.configure(yscrollcommand=scroll.set)
        self.table.bind('<<TreeviewSelect>>',lambda _:self.show_candidate())
        self.detail=tk.Text(body,wrap='word',state='disabled',height=12);body.add(self.detail,weight=2)
        actions=ttk.Frame(self.window,padding=(d(16),0));actions.grid(row=3,column=0,sticky='ew')
        for column,(label,callback)in enumerate((('刷新',lambda:self.send('sdk_catalog')),('刷新资料并探测',self.refresh_data),('激活',self.activate),
                                               ('更新桥接并重建',self.rebuild),('回退稳定版',self.rollback),('恢复中断切换…',lambda:self.send('sdk_activation_review')))):
            button=ttk.Button(actions,text=label,command=callback);button.grid(row=0,column=column,padx=d(3),pady=d(5));self.controls.append(button)
        ttk.Button(actions,text='打开候选目录…',command=self.open_candidate_directory).grid(row=1,column=0,columnspan=2,sticky='w',padx=d(3),pady=d(4))
        footer=ttk.Frame(self.window,padding=d(16));footer.grid(row=4,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        self.status=tk.StringVar(self.window,'候选构建、依赖同步与探测都不会启动游戏。')
        theme.wrapped_label(footer,textvariable=self.status).grid(row=0,column=0,sticky='ew')
        ttk.Button(footer,text='取消作业',command=lambda:self.commands.put(('sdk_cancel',{}))).grid(row=0,column=1,padx=d(8))
        ttk.Button(footer,text='关闭',command=self.close).grid(row=0,column=2)
        self.worker=threading.Thread(target=self.work,name='AutoZZZ-SDK-UI',daemon=False);self.worker.start()
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.after(80,self.poll)

    def buttons(self):
        for button in self.controls:button.state(['disabled']if self.busy or self.closing else['!disabled'])
    def send(self,method,**params):
        if self.busy or self.closing:return
        self.busy=True;self.commands.put((method,params));self.buttons()
    def selected(self):
        chosen=self.table.selection();return chosen[0]if chosen else None
    def write(self,text):self.detail.configure(state='normal');self.detail.delete('1.0','end');self.detail.insert('1.0',text);self.detail.configure(state='disabled')
    def review_source(self):self.send('sdk_review',groups=[g.strip()for g in self.groups.get().split(',')if g.strip()])
    def rebuild(self):
        candidate=self.selected()
        if candidate:self.send('sdk_review',groups=[g.strip()for g in self.groups.get().split(',')if g.strip()],source_candidate=candidate)
    def build(self):
        if self.review is None:self.status.set('请先审核当前分支或现有候选。');return
        if self.review['pending']:self.status.set('请先恢复当前资料中的待核对修改。');return
        if not messagebox.askokcancel('构建执行SDK',f"提交 {self.review['revision']}\n依赖组：{', '.join(self.review['groups'])or'基础'}\n将复制独立Python及当前资料，按该uv.lock安装依赖并执行兼容探测。"+
            ('\n仅使用本地缓存。'if self.offline.get()else'\n允许联网下载锁定依赖。'),parent=self.window):return
        self.send('sdk_build',review_id=self.review['id'],review_digest=self.review['review_digest'],confirmed=True,offline=self.offline.get())
    def refresh_data(self):
        candidate=self.selected()
        if candidate and messagebox.askokcancel('刷新资料并探测','携带当前账号资料建立新快照并重新探测？当前SDK选择保持不变。',parent=self.window):self.send('sdk_refresh',candidate_id=candidate,confirmed=True)
    def activate(self):
        candidate=self.selected()
        if candidate and self.catalog and messagebox.askokcancel('激活执行SDK','激活选中候选并启动全新SDK进程核实实际提交？需要重新打开运行窗口。',parent=self.window):
            self.send('sdk_activate',candidate_id=candidate,selection_revision=self.catalog['selection_revision'],confirmed=True)
    def rollback(self):
        if self.catalog and messagebox.askokcancel('回退稳定SDK','把当前资料携带到稳定SDK的新快照并核实兼容性？候选、原资料及所有预算保留。',parent=self.window):
            self.send('sdk_rollback',selection_revision=self.catalog['selection_revision'],confirmed=True)
    def show_candidate(self):
        if self.catalog is None:return
        row=next((r for r in self.catalog['candidates']if r['id']==self.selected()),None)
        if row:self.write(json.dumps(row,ensure_ascii=False,indent=2))
    def open_candidate_directory(self):
        if self.catalog is None:return
        row=next((r for r in self.catalog['candidates']if r['id']==self.selected()),None)
        if row:
            from .runtime_selection import candidate_root
            path=candidate_root(self.catalog['stable_data'],row['id'])
            if str(path)!=row['path']or not path.is_dir():self.status.set('候选目录已变化，请刷新后重试。');return
            os.startfile(path)

    @staticmethod
    def call(client,method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=.5)
            except RequestPending:pass
    def work(self):
        client=OneDragonClient(RuntimePaths.discover(ROOT,self.profile,use_selection=False));job=None
        try:
            try:client.start(timeout=.5)
            except RequestPending:
                while True:
                    try:client.finish_start(timeout=.5);break
                    except RequestPending:pass
            self.events.put(('sdk_catalog',self.call(client,'sdk_catalog')))
            while not self.closing or job is not None:
                try:method,params=self.commands.get(timeout=.2)
                except queue.Empty:method=None;params={}
                if self.closing:method='sdk_cancel';params={}
                if method is not None:
                    value=self.call(client,method,**params)
                    if isinstance(value,dict)and value.get('state')=='working':job=value['job_id']
                    elif method!='sdk_cancel':self.events.put((method,value))
                if job is not None:
                    state=self.call(client,'sdk_status',job_id=job);self.events.put(('job',state))
                    if not state['busy']:
                        job=None;self.events.put(('sdk_catalog',self.call(client,'sdk_catalog')))
        except Exception as exc:self.events.put(('error',str(exc)))
        finally:
            while True:
                try:client.close(timeout=.5);break
                except RequestPending:pass
            self.events.put(('closed',None))

    def recovery_dialog(self,review):
        if not review['pending']:self.status.set('没有待核对的SDK切换。');return
        self.write(json.dumps(review,ensure_ascii=False,indent=2))
        if review['conflict']:self.status.set('选择记录存在外部变更，现场保留，未覆盖。');return
        dialog=tk.Toplevel(self.window);dialog.title('恢复SDK切换');dialog.transient(self.window);dialog.grab_set()
        theme.wrapped_label(dialog,text='核对下方切换记录后选择恢复方向。关闭此窗口不修改选择。').pack(fill='x',padx=20,pady=16)
        def choose(choice):dialog.destroy();self.send('sdk_recover_activation',journal_digest=review['journal_digest'],choice=choice,confirmed=True)
        ttk.Button(dialog,text='恢复切换前',command=lambda:choose('before')).pack(side='left',padx=12,pady=16)
        ttk.Button(dialog,text='继续切换后并核实',command=lambda:choose('after')).pack(side='left',padx=12,pady=16)
        ttk.Button(dialog,text='取消',command=dialog.destroy).pack(side='left',padx=12,pady=16)
    def poll(self):
        if self.closed:return
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='closed':self.closed=True;self.window.destroy();return
                if kind=='job':
                    self.busy=value['busy'];self.status.set(value.get('error')or value.get('phase')or value['state'])
                    if not value['busy']:self.write(json.dumps(value,ensure_ascii=False,indent=2))
                else:
                    self.busy=False
                    if kind=='error':self.status.set(value)
                    elif kind=='sdk_catalog':
                        self.catalog=value;current=value['selected'];self.identity.set(('国服'if self.profile=='cn'else'国际服')+' · '+(current['revision']if current else value['stable_revision']))
                        self.table.delete(*self.table.get_children())
                        for row in value['candidates']:self.table.insert('','end',iid=row['id'],values=(row['branch'],row['revision'][:12],row['state'],', '.join(row['groups']or [])))
                        self.status.set(value['selection_error']or('当前有待核对的SDK切换。'if value['activation_pending']else'稳定控制连接可用于构建、激活和回退。'))
                    elif kind=='sdk_review':self.review=value;self.write(json.dumps(value,ensure_ascii=False,indent=2));self.status.set('审核已生成；构建会绑定此提交、依赖组和资料摘要。')
                    elif kind=='sdk_activation_review':self.recovery_dialog(value)
                    else:self.write(json.dumps(value,ensure_ascii=False,indent=2))
                self.buttons()
        except queue.Empty:pass
        self.window.after(80,self.poll)

    def request_close(self):
        # Thread-safe parent lifecycle hook: does not touch Tk.
        self.closing=True;self.commands.put(('sdk_cancel',{}))
    def close(self):
        if self.closed:return
        self.request_close();self.buttons();self.status.set('正在取消原作业并等待SDK工作进程实际退出…')
