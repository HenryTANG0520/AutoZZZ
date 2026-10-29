"""原生模型下载窗口；等待和取消始终作用于原后台任务。"""
from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import ttk

from . import theme
from .config import ROOT
from .onedragon_runtime import RuntimePaths
from .onedragon_session import RuntimeSession


def megabytes(value):
    return f'{value/1024/1024:.1f} MB'


class DownloadsWindow:
    def __init__(self, ui, profile):
        self.ui, self.profile = ui, profile
        self.closed = self.closing = self.connected = self.busy = False
        self.callback, self.job, self.catalog = None, None, None
        self.cancel_requested, self.next_poll = False, 0.
        self.events, self.poll_token = queue.Queue(), None
        self.window = tk.Toplevel(ui.root)
        self.window.title('AutoZZZ · 资源下载')
        self.window.transient(ui.root)
        self.window.configure(background=theme.PANEL)
        d = lambda value: round(value*ui.ui_scale)
        width,height = min(d(940),ui.root.winfo_screenwidth()-d(50)),min(d(600),ui.root.winfo_screenheight()-d(90))
        self.window.geometry(f'{width}x{height}')
        self.window.minsize(min(d(720),width),min(d(440),height))
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(1,weight=1)
        head = ttk.Frame(self.window,padding=d(18));head.grid(row=0,column=0,sticky='ew');head.columnconfigure(0,weight=1)
        ttk.Label(head,text='资源下载',style='PageTitle.TLabel').grid(row=0,column=0,sticky='w')
        ttk.Label(head,text='国服' if profile=='cn' else '国际服',style='Hint.TLabel').grid(row=0,column=1,padx=d(16))
        self.reload_button = ttk.Button(head,text='检查本地',command=self.reload);self.reload_button.grid(row=0,column=2)
        body = ttk.Frame(self.window,padding=(d(18),0));body.grid(row=1,column=0,sticky='nsew')
        body.columnconfigure(0,weight=1);body.rowconfigure(0,weight=1)
        self.table = ttk.Treeview(body,columns=('name','model','selected','ready','size'),show='headings',selectmode='browse',style='Config.Treeview')
        for name,label,width in (('name','识别用途',100),('model','版本',330),('selected','当前选择',70),('ready','本地文件',80),('size','文件大小',90)):
            self.table.heading(name,text=label);self.table.column(name,width=d(width),minwidth=d(65),stretch=name=='model')
        self.table.grid(row=0,column=0,sticky='nsew')
        vertical = ttk.Scrollbar(body,command=self.table.yview);vertical.grid(row=0,column=1,sticky='ns')
        horizontal = ttk.Scrollbar(body,orient='horizontal',command=self.table.xview);horizontal.grid(row=1,column=0,sticky='ew')
        self.table.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        self.table.bind('<<TreeviewSelect>>',lambda _:self.buttons())
        foot = ttk.Frame(self.window,padding=d(18));foot.grid(row=2,column=0,sticky='ew');foot.columnconfigure(0,weight=1)
        self.source = tk.StringVar(self.window)
        theme.wrapped_label(foot,textvariable=self.source,style='Hint.TLabel').grid(row=0,column=0,sticky='ew')
        self.progress = ttk.Progressbar(foot,maximum=100);self.progress.grid(row=1,column=0,sticky='ew',pady=d(10))
        self.status = tk.StringVar(self.window,'正在读取模型目录…')
        theme.wrapped_label(foot,textvariable=self.status,style='Hint.TLabel').grid(row=2,column=0,sticky='ew')
        actions = ttk.Frame(foot);actions.grid(row=3,column=0,sticky='ew',pady=(d(15),0));actions.columnconfigure(0,weight=1)
        self.recover_button = ttk.Button(actions,text='恢复未完成安装',command=self.recover);self.recover_button.grid(row=0,column=0,sticky='w')
        buttons = ttk.Frame(actions);buttons.grid(row=0,column=1,sticky='e')
        self.download_button = ttk.Button(buttons,text='下载 / 修复',command=self.download,style='Accent.TButton');self.download_button.pack(side='left',padx=(0,d(8)))
        self.cancel_button = ttk.Button(buttons,text='取消下载',command=self.cancel);self.cancel_button.pack(side='left',padx=(0,d(8)))
        ttk.Button(buttons,text='关闭',command=self.close).pack(side='left')
        self.session = RuntimeSession(RuntimePaths.discover(ROOT,profile),self.events.put)
        self.worker = self.session.worker
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Destroy>',self.disposed,add='+')
        self.buttons();self.poll_token = self.window.after(80,self.poll)

    def buttons(self):
        allowed = self.connected and not self.busy and not self.closing and self.job is None
        pending = bool(self.catalog and self.catalog['pending'])
        self.reload_button.state(['!disabled'] if allowed else ['disabled'])
        self.table.state(['!disabled'] if allowed else ['disabled'])
        self.download_button.state(['!disabled'] if allowed and self.catalog and self.table.selection() and not pending else ['disabled'])
        self.recover_button.state(['!disabled'] if allowed and pending else ['disabled'])
        if pending: self.recover_button.grid()
        else: self.recover_button.grid_remove()
        self.cancel_button.state(['!disabled'] if self.job and self.job['busy'] and self.job['state']!='installing' and not self.cancel_requested and not self.closing else ['disabled'])

    def request(self, method, callback, **params):
        if not self.connected or self.busy or self.closing:return
        self.busy,self.callback = True,callback
        self.session.send('rpc',method=method,tag=method,params=params);self.buttons()

    def reload(self):
        self.request('model_catalog',self.loaded)

    def loaded(self, result):
        previous = self.table.selection()
        self.catalog = result
        self.table.delete(*self.table.get_children())
        for row in result['items']:
            self.table.insert('','end',iid=row['id'],values=(row['label'],row['model']+(' · 推荐' if row['recommended'] else ' · 备用'),
                '✓' if row['current']==row['model'] else '', '齐全' if row['ready'] else '缺失',megabytes(row['bytes'])))
        if result['items']:
            self.table.selection_set(previous[0] if previous and self.table.exists(previous[0]) else result['items'][0]['id'])
        labels={'cnb':'CNB','github':'GitHub','gitee':'Gitee'}
        self.source.set('下载源：'+' → '.join(labels.get(name,name) for name in result['sources']) if result['sources'] else '')
        self.status.set('上次安装中断，请恢复后再准备任务。' if result['pending'] else '模型替换后需重新准备任务。')
        self.buttons()

    def download(self):
        if self.job is not None or self.catalog is None:return
        selected = self.table.selection()
        if selected:self.request('model_download_start',self.job_result,resource_id=selected[0],revision=self.catalog['revision'])

    def job_result(self, result):
        self.job,self.next_poll = result,time.monotonic()+.4
        self.progress.configure(value=result['progress']*100)
        transfer = (megabytes(result['downloaded_bytes'])+(' / '+megabytes(result['total_bytes']) if result['total_bytes'] else '')
                    +' · '+megabytes(result['bytes_per_second'])+'/s') if result['state']=='downloading' else ''
        self.status.set(' · '.join(value for value in (result['detail'],transfer) if value))
        if not result['busy']:
            detail = result['detail'];self.job = None;self.cancel_requested = False
            def reloaded(catalog): self.loaded(catalog);self.status.set(detail)
            self.request('model_catalog',reloaded)
        self.buttons()

    def cancel(self):
        if self.job is None:return
        self.cancel_requested,self.next_poll = True,0.
        self.status.set('正在取消，等待原下载退出…');self.buttons()

    def recover(self):
        def recovered(result):
            self.reload()
        self.request('model_install_recover',recovered)

    def poll(self):
        if self.closed:return
        try:
            while True:
                event = self.events.get_nowait()
                if event['type']=='connected':self.connected=True;self.reload()
                elif event['type']=='rpc_result':
                    self.busy = False;callback,self.callback=self.callback,None
                    if not self.closing and callback:
                        try:callback(event['result'])
                        except Exception as exc:self.status.set('显示失败：'+str(exc))
                elif event['type']=='error':
                    self.busy = False;self.callback=None;self.next_poll=time.monotonic()+.8;self.status.set(event['message'])
                elif event['type']=='closed':self.closed=True;self.window.destroy();return
                self.buttons()
        except queue.Empty:pass
        if self.job is not None and not self.busy and not self.closing and time.monotonic()>=self.next_poll:
            method = 'model_download_cancel' if self.cancel_requested and self.job['state'] not in ('cancelling','installing') else 'model_download_status'
            self.request(method,self.job_result,job_id=self.job['job_id'])
        self.poll_token = self.window.after(80,self.poll)

    def close(self):
        if self.closing:return True
        self.closing=True;self.status.set('正在取消下载并等待原进程退出…')
        self.session.close();self.buttons()
        if not self.worker.is_alive():self.closed=True;self.window.destroy()
        return True

    def disposed(self,event):
        if event.widget is self.window:
            if self.poll_token is not None:self.window.after_cancel(self.poll_token);self.poll_token=None
            self.status=self.source=self.callback=None


def open_downloads(ui,profile):
    existing = getattr(ui,'downloads_window',None)
    if existing is not None and not existing.closed:
        if existing.profile != profile:
            existing.close()
            def reopen():
                if ui.closing:return
                if existing.closed and not existing.worker.is_alive():open_downloads(ui,profile)
                else:ui.root.after(80,reopen)
            ui.root.after(80,reopen);return existing
        existing.window.deiconify();existing.window.lift();return existing
    ui.downloads_window = DownloadsWindow(ui,profile)
    return ui.downloads_window
