"""AutoZZZ launcher for the isolated native developer pages."""
import queue
import tkinter as tk
from tkinter import ttk

from .config import ROOT
from .onedragon_runtime import RuntimePaths
from .onedragon_devtools import DevtoolsSession
from . import theme


class DevtoolsWindow:
    def __init__(self,ui,profile):
        self.ui,self.profile=ui,profile;self.closed=self.closing=False;self.events=queue.Queue()
        self.window=tk.Toplevel(ui.root);self.window.title('AutoZZZ · 开发工具');self.window.transient(ui.root)
        d=lambda n:round(n*ui.ui_scale);self.window.geometry(f'{d(520)}x{d(250)}');self.window.minsize(d(360),d(200))
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(1,weight=1)
        self.status=tk.StringVar(self.window,'正在打开独立开发工具…')
        theme.wrapped_label(self.window,textvariable=self.status).grid(row=0,column=0,sticky='ew',padx=d(20),pady=d(18))
        theme.wrapped_label(self.window,text='图像分析、模板管理、画面管理\n修改保存到当前区服资料；关闭工具后，重新准备任务生效。').grid(row=1,column=0,sticky='new',padx=d(20))
        self.close_button=ttk.Button(self.window,text='关闭开发工具',command=self.close);self.close_button.grid(row=2,column=0,sticky='e',padx=d(20),pady=d(16))
        self.session=DevtoolsSession(RuntimePaths.discover(ROOT,profile),self.events.put,profile);self.worker=self.session.worker
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.after(80,self.poll)

    def poll(self):
        if self.closed:return
        try:
            while True:
                event=self.events.get_nowait()
                if event['type']=='ready':self.status.set(('国际服' if self.profile=='global' else '国服')+' · 开发工具已打开')
                elif event['type'] in ('error','status'):self.status.set(event['message'])
                elif event['type']=='closed':
                    self.closed=True;self.window.destroy();return
        except queue.Empty:pass
        self.window.after(80,self.poll)

    def close(self):
        if self.closing:return
        self.closing=True;self.close_button.state(['disabled']);self.status.set('等待原开发工具进程退出…');self.session.close()


def open_devtools(ui,profile):
    existing=getattr(ui,'devtools_window',None)
    if existing is not None and not existing.closed:
        existing.window.deiconify();existing.window.lift();return existing
    ui.devtools_window=DevtoolsWindow(ui,profile)
    return ui.devtools_window
