"""Explicit virtual-gamepad installation, one SDK process and one installer job."""
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox

from .localization_tk import PresentationVar
from . import theme
from .config import ROOT
from .onedragon_runtime import RuntimePaths
from .onedragon_session import RuntimeSession


class GamepadInstallWindow:
    def __init__(self,ui,profile):
        self.ui,self.profile=ui,profile
        self.closed=self.closing=self.connected=self.busy=False
        self.catalog=self.job=self.callback=None;self.next_poll=0;self.poll_token=None
        self.events=queue.Queue();self.window=tk.Toplevel(ui.root)
        self.window.title('AutoZZZ · 虚拟手柄');self.window.transient(ui.root)
        d=lambda n:round(n*ui.ui_scale)
        self.window.geometry(f'{min(d(680),ui.root.winfo_screenwidth()-d(40))}x{min(d(460),ui.root.winfo_screenheight()-d(80))}')
        self.window.minsize(d(440),d(300));self.window.columnconfigure(0,weight=1);self.window.rowconfigure(0,weight=1)
        body=ttk.Frame(self.window,padding=d(20));body.grid(sticky='nsew');body.columnconfigure(0,weight=1)
        ttk.Label(body,text='虚拟手柄',style='PageTitle.TLabel').grid(row=0,column=0,sticky='w')
        self.package=tk.StringVar(self.window,'正在检查依赖…');self.driver=tk.StringVar(self.window)
        self.status=PresentationVar(self.window)
        theme.wrapped_label(body,textvariable=self.package).grid(row=1,column=0,sticky='ew',pady=(d(20),d(8)))
        theme.wrapped_label(body,textvariable=self.driver).grid(row=2,column=0,sticky='ew',pady=d(8))
        theme.wrapped_label(body,text='安装或修复会更改系统驱动，可能需要 Windows 管理员确认及重启。',style='Hint.TLabel').grid(row=3,column=0,sticky='ew',pady=d(8))
        self.progress=ttk.Progressbar(body,mode='indeterminate');self.progress.grid(row=4,column=0,sticky='ew',pady=d(12))
        theme.wrapped_label(body,textvariable=self.status,style='Hint.TLabel').grid(row=5,column=0,sticky='ew')
        buttons=ttk.Frame(body);buttons.grid(row=6,column=0,sticky='ew',pady=d(20));buttons.columnconfigure(0,weight=1)
        self.check=ttk.Button(buttons,text='重新检查',command=self.reload);self.check.grid(row=0,column=0,sticky='w')
        self.install=ttk.Button(buttons,text='安装',command=lambda:self.start('install'),style='Accent.TButton');self.install.grid(row=0,column=1,padx=d(5))
        self.repair=ttk.Button(buttons,text='修复',command=lambda:self.start('repair'));self.repair.grid(row=0,column=2,padx=d(5))
        self.cancel_button=ttk.Button(buttons,text='取消下载',command=self.cancel);self.cancel_button.grid(row=0,column=3,padx=d(5))
        ttk.Button(buttons,text='关闭',command=self.close).grid(row=0,column=4)
        self.session=RuntimeSession(RuntimePaths.discover(ROOT,profile),self.events.put);self.worker=self.session.worker
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.bind('<Destroy>',self.disposed,add='+')
        self.buttons();self.poll_token=self.window.after(80,self.poll)

    def buttons(self):
        available=self.connected and not self.busy and not self.closing and self.job is None
        for widget in (self.check,self.install,self.repair):widget.state(['!disabled'] if available else ['disabled'])
        self.cancel_button.state(['!disabled'] if self.job and self.job.get('busy') and self.job.get('state') not in ('installing','starting') and not self.busy and not self.closing else ['disabled'])

    def request(self,method,callback,**params):
        if self.busy or not self.connected or self.closing:return
        self.busy=True;self.callback=callback;self.session.send('rpc',method=method,tag=method,params=params);self.buttons()

    def reload(self):self.request('gamepad_catalog',self.loaded)

    def loaded(self,result):
        self.catalog=result
        self.package.set('Python 依赖：'+('已校验 vgamepad '+result['version'] if result['python_ready'] else '未安装或需要修复'))
        driver=result['driver']
        self.driver.set('系统驱动：'+('已注册但被禁用' if driver.get('disabled') else '已注册' if driver.get('registered') else '未检测到或无法读取')+' · 游戏输入尚未验收')
        self.status.set(result.get('error') or ('安装器要求重启 Windows，当前尚未重启' if result.get('restart_required') else '检查完成'));self.buttons()

    def start(self,action):
        if not self.catalog or self.job is not None:return
        if not messagebox.askokcancel('虚拟手柄驱动',('修复' if action=='repair' else '安装')+' ViGEmBus 1.17.333.0 系统驱动及固定版本依赖？可能需要管理员确认或重启，程序不会自动重启 Windows。',parent=self.window):return
        self.request('gamepad_install_start',self.updated,action=action,revision=self.catalog['revision'],confirmed=True)

    def updated(self,result):
        self.status.set(result.get('detail',''));self.next_poll=time.monotonic()+.5
        if result.get('busy'):
            self.job=result;self.progress.start(12)
        else:
            self.job=None;self.progress.stop()
            suffix=' · 需重启 Windows' if result.get('restart_required') else ''
            if result.get('process_restart_required'):suffix+=' · 需关闭此窗口后重新准备任务'
            self.status.set(result.get('detail','')+suffix)
        self.buttons()

    def cancel(self):
        if self.job:self.request('gamepad_install_cancel',self.updated,job_id=self.job['job_id'])

    def poll(self):
        if self.closed:return
        try:
            while True:
                event=self.events.get_nowait()
                if event['type']=='connected':self.connected=True;self.reload()
                elif event['type']=='rpc_result':
                    self.busy=False;callback,self.callback=self.callback,None
                    if callback and not self.closing:callback(event['result'])
                elif event['type']=='error':self.busy=False;self.callback=None;self.status.set(event['message']);self.next_poll=time.monotonic()+1
                elif event['type']=='closed':self.closed=True;self.window.destroy();return
                self.buttons()
        except queue.Empty:pass
        if self.job and not self.busy and not self.closing and time.monotonic()>=self.next_poll:
            self.request('gamepad_install_status',self.updated,job_id=self.job['job_id'])
        if self.closing and not self.worker.is_alive():self.closed=True;self.window.destroy();return
        self.poll_token=self.window.after(80,self.poll)

    def close(self):
        if not self.closing:
            self.closing=True;self.status.set('正在等待原下载/驱动安装器结束…');self.session.close();self.buttons()
        return True

    def disposed(self,event):
        if event.widget is self.window and self.poll_token is not None:
            self.window.after_cancel(self.poll_token);self.poll_token=None


def open_gamepad_install(ui,profile):
    existing=getattr(ui,'gamepad_install_window',None)
    if existing is not None and not existing.closed:
        existing.window.deiconify();existing.window.lift();return existing
    ui.gamepad_install_window=GamepadInstallWindow(ui,profile)
    return ui.gamepad_install_window
