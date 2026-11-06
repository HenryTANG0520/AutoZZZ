"""Independent source-review window; selected branch never changes the runtime SDK."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from .localization_tk import PresentationVar
from . import theme
from .config import ROOT
from .onedragon_runtime import OneDragonClient,RuntimePaths,RequestPending


class SourceBranchesWindow:
    def __init__(self,ui,profile):
        self.closed=self.closing=self.worker_done=False;self.events=queue.Queue();self.commands=queue.Queue();self.busy=False
        self.ui=ui;self.profile=profile;self.sdk_window=None;self.lifecycle_lock=threading.Lock();self.worker_finalizing=False
        self.window=tk.Toplevel(ui.root);self.window.title('AutoZZZ · 上游源码工作副本');self.window.transient(ui.root)
        d=lambda n:round(n*ui.ui_scale)
        self.window.geometry(f'{d(860)}x{d(620)}');self.window.minsize(d(680),d(480))
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(2,weight=1)
        header=ttk.Frame(self.window,padding=d(16));header.grid(row=0,column=0,sticky='ew');header.columnconfigure(0,weight=1)
        theme.wrapped_label(header,text='选择并同步上游分支后，可构建独立执行SDK候选。当前引擎在明确激活前保持不变。',style='Hint.TLabel').grid(row=0,column=0,sticky='ew')
        self.path=tk.StringVar(self.window,'正在读取工作副本…');theme.wrapped_label(header,textvariable=self.path).grid(row=1,column=0,sticky='ew',pady=(d(10),0))
        bar=ttk.Frame(self.window,padding=(d(16),0));bar.grid(row=1,column=0,sticky='ew');bar.columnconfigure(1,weight=1)
        ttk.Label(bar,text='分支').grid(row=0,column=0,padx=(0,d(8)))
        self.branch=tk.StringVar(self.window);self.choices=ttk.Combobox(bar,textvariable=self.branch);self.choices.grid(row=0,column=1,sticky='ew')
        self.buttons=[]
        for col,(label,action) in enumerate((('创建副本','create'),('查询远端分支','fetch'),('切换并同步','select'),('同步当前分支','sync')),2):
            button=ttk.Button(bar,text=label,command=lambda action=action:self.request(action));button.grid(row=0,column=col,padx=d(4));self.buttons.append(button)
        self.detail=tk.Text(self.window,wrap='word',state='disabled');self.detail.grid(row=2,column=0,sticky='nsew',padx=d(16),pady=d(14))
        footer=ttk.Frame(self.window,padding=(d(16),0,d(16),d(16)));footer.grid(row=3,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        self.status=PresentationVar(self.window,'正在连接…');theme.wrapped_label(footer,textvariable=self.status,style='Hint.TLabel').grid(row=0,column=0,columnspan=3,sticky='ew')
        ttk.Button(footer,text='执行SDK…',command=self.open_sdk).grid(row=1,column=0,padx=d(110),sticky='w',pady=(d(12),0))
        ttk.Button(footer,text='刷新状态',command=lambda:self.commands.put(('catalog',None))).grid(row=1,column=0,sticky='w',pady=(d(12),0))
        ttk.Button(footer,text='取消操作',command=lambda:self.commands.put(('cancel',None))).grid(row=1,column=1,padx=d(8))
        ttk.Button(footer,text='关闭',command=self.close).grid(row=1,column=2)
        self.worker=threading.Thread(target=self.work,name='AutoZZZ-Source-UI',daemon=False);self.worker.start()
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.after(80,self.poll)

    def request(self,action):
        if self.busy or self.closing:return
        self.busy=True;self.commands.put((action,self.branch.get().strip() if action=='select' else None))
        self.status.set('正在处理…');self.set_buttons()

    def set_buttons(self):
        for button in self.buttons:button.state(['disabled'] if self.busy or self.closing else ['!disabled'])

    @staticmethod
    def call(client,method,**params):
        key=client.submit(method,**params)
        while True:
            try:return client.result(key,timeout=.5)
            except RequestPending:continue

    def work(self):
        client=OneDragonClient(RuntimePaths.discover_sdk_control(ROOT,self.profile))
        try:
            try:client.start()
            except RequestPending:
                while True:
                    try:client.finish_start(timeout=.5);break
                    except RequestPending:continue
            self.events.put(('catalog',self.call(client,'source_catalog')))
            job=None
            while not self.closing or job is not None:
                try:action,branch=self.commands.get(timeout=.2)
                except queue.Empty:action=None
                try:
                    if action=='catalog' and job is None:self.events.put(('catalog',self.call(client,'source_catalog')))
                    elif action=='cancel' or self.closing:self.call(client,'source_cancel')
                    elif action is not None and job is None:
                        state=self.call(client,'source_start',action=action,branch=branch);job=state['job_id']
                    if job is not None:
                        state=self.call(client,'source_status')
                        if state['job_id']!=job:raise ValueError('源码任务编号发生变化。')
                        if not state['busy']:
                            job=None
                            if state['state']=='success':self.events.put(('catalog',state['result']))
                            else:self.events.put(('error',state['error'] or '操作已取消'))
                except Exception as exc:job=None;self.events.put(('error',str(exc)))
        except Exception as exc:self.events.put(('error',str(exc)))
        finally:
            while True:
                try:client.close(timeout=1);break
                except RequestPending:continue
            with self.lifecycle_lock:self.worker_finalizing=True;child=self.sdk_window
            if child is not None:
                child.request_close()
                child.worker.join()  # Parent helper remains alive until the child SDK job and process exit.
            self.events.put(('closed',None))

    def open_sdk(self):
        with self.lifecycle_lock:
            if self.closing or self.worker_done or self.worker_finalizing:return
            if self.sdk_window is not None and not self.sdk_window.closed:
                self.sdk_window.window.deiconify();self.sdk_window.window.lift();return
            from .sdk_upgrade_ui import SDKUpgradeWindow
            self.sdk_window=SDKUpgradeWindow(self)

    def poll(self):
        while True:
            try:kind,value=self.events.get_nowait()
            except queue.Empty:break
            if kind=='closed':
                self.worker_done=True
                if self.closing:self.closed=True;self.window.destroy();return
                self.busy=True;self.set_buttons();continue
            self.busy=False;self.set_buttons()
            if kind=='error':self.status.set(value[:350])
            else:
                self.path.set(value['path']);self.choices.configure(values=value['branches'])
                if value['branch']:self.branch.set(value['branch'])
                self.status.set(('有本地修改，切换/同步将保留并拒绝覆盖。' if value['dirty'] else '源码状态已更新。')+' 当前执行 SDK 保持固定版本。')
                text=f"仓库：{value['repository']}\n分支：{value['branch']}\n提交：{value['head']}\n\n本地变更：\n{value['changes'] or '无'}\n\n差异摘要：\n{value['diff'] or '无'}"
                self.detail.configure(state='normal');self.detail.delete('1.0','end');self.detail.insert('1.0',text);self.detail.configure(state='disabled')
        if not self.closed:self.window.after(100,self.poll)

    def close(self):
        if self.closed or self.closing:return
        if self.worker_done:
            self.closed=True;self.window.destroy();return
        self.closing=True;self.set_buttons();self.status.set('正在取消源码操作并等待原进程退出…')
        if self.sdk_window is not None:self.sdk_window.close()
