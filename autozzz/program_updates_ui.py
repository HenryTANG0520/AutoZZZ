"""程序版本与归档；安装只在主窗口及原工作进程正常退出后进行。"""
from __future__ import annotations

from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import webbrowser

from . import theme
from .config import ROOT
from .onedragon_runtime import RuntimePaths
from .onedragon_session import RuntimeSession
from .onedragon_plugins_ui import scroll_table


class ProgramUpdatesWindow:
    def __init__(self,ui,profile):
        self.ui,self.profile=ui,profile;self.connected=self.busy=self.closing=self.closed=False
        self.catalog=None;self.job=None;self.plan=None;self.callback=None;self.next_poll=0.;self.events=queue.Queue()
        self.cancel_requested=False
        self.window=tk.Toplevel(ui.root);self.window.title('AutoZZZ · 程序版本');self.window.transient(ui.root)
        d=lambda n:round(n*ui.ui_scale);self.window.geometry(f'{d(900)}x{d(650)}');self.window.minsize(d(700),d(520))
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(1,weight=1)
        header=ttk.Frame(self.window,padding=d(18));header.grid(row=0,column=0,sticky='ew');header.columnconfigure(0,weight=1)
        self.current=tk.StringVar(self.window,'正在读取程序版本…');theme.wrapped_label(header,textvariable=self.current,style='ConfigTitle.TLabel').grid(row=0,column=0,sticky='ew')
        self.check_button=ttk.Button(header,text='检查更新',command=self.check);self.check_button.grid(row=0,column=1,padx=d(8))
        self.import_button=ttk.Button(header,text='导入完整包…',command=self.import_package);self.import_button.grid(row=0,column=2)
        paging=ttk.Frame(header);paging.grid(row=1,column=0,columnspan=3,sticky='ew',pady=(d(12),0));paging.columnconfigure(1,weight=1)
        self.channel=tk.StringVar(self.window,'稳定版');self.channel_box=ttk.Combobox(paging,textvariable=self.channel,values=('稳定版','测试版'),state='readonly',width=9)
        self.channel_box.grid(row=0,column=0,sticky='w');self.channel_box.bind('<<ComboboxSelected>>',lambda _:self.check())
        self.page_label=tk.StringVar(self.window,'第 1 页');ttk.Label(paging,textvariable=self.page_label).grid(row=0,column=2,padx=d(10))
        self.previous_button=ttk.Button(paging,text='上一页',command=lambda:self.check(self.catalog['page']-1));self.previous_button.grid(row=0,column=1,sticky='e')
        self.next_button=ttk.Button(paging,text='下一页',command=lambda:self.check(self.catalog['page']+1));self.next_button.grid(row=0,column=3)
        self.book=ttk.Notebook(self.window);self.book.grid(row=1,column=0,sticky='nsew',padx=d(18))
        self.tables={}
        for name,label,columns in (('release','可用版本',(('version','版本',180),('published','发布时间',230),('bytes','下载大小',130))),
                                    ('backup','本机归档',(('version','版本',180),('path','归档目录',510)))):
            frame,table=scroll_table(self.book,columns=tuple(row[0] for row in columns),show='headings',style='Config.Treeview',selectmode='browse')
            for key,title,width in columns:table.heading(key,text=title);table.column(key,width=d(width),minwidth=d(80))
            self.book.add(frame,text=label);self.tables[name]=table;table.bind('<<TreeviewSelect>>',lambda _:self.buttons())
        self.book.bind('<<NotebookTabChanged>>',lambda _:self.buttons())
        footer=ttk.Frame(self.window,padding=d(18));footer.grid(row=2,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        self.progress=ttk.Progressbar(footer,maximum=100);self.progress.grid(row=0,column=0,sticky='ew',pady=(0,d(10)))
        self.status=tk.StringVar(self.window,'尚未检查更新');theme.wrapped_label(footer,textvariable=self.status,style='Hint.TLabel').grid(row=1,column=0,sticky='ew')
        actions=ttk.Frame(footer);actions.grid(row=2,column=0,sticky='ew',pady=(d(14),0));actions.columnconfigure(0,weight=1)
        self.page_button=ttk.Button(actions,text='发布页面',command=lambda:webbrowser.open('https://github.com/HenryTANG0520/AutoZZZ/releases'));self.page_button.grid(row=0,column=0,sticky='w')
        self.operation_button=ttk.Button(actions,text='下载并验证',command=self.operation);self.operation_button.grid(row=0,column=1,padx=d(6))
        self.cancel_button=ttk.Button(actions,text='取消',command=self.cancel);self.cancel_button.grid(row=0,column=2,padx=d(6))
        self.apply_button=ttk.Button(actions,text='关闭并安装',command=self.apply,style='Accent.TButton');self.apply_button.grid(row=0,column=3,padx=d(6))
        ttk.Button(actions,text='关闭',command=self.close).grid(row=0,column=4,padx=(d(6),0))
        self.session=RuntimeSession(RuntimePaths.discover(ROOT,profile),self.events.put);self.worker=self.session.worker
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.buttons();self.window.after(80,self.poll)

    def buttons(self):
        if self.closed:return
        allowed=self.connected and not self.busy and self.job is None and not self.closing
        self.check_button.state(['!disabled'] if allowed else ['disabled'])
        self.channel_box.configure(state='readonly' if allowed else 'disabled')
        self.previous_button.state(['!disabled'] if allowed and self.catalog and self.catalog.get('page',1)>1 else ['disabled'])
        self.next_button.state(['!disabled'] if allowed and self.catalog and self.catalog.get('has_more') else ['disabled'])
        installed=bool(self.catalog and self.catalog['installed'])
        self.import_button.state(['!disabled'] if allowed and installed else ['disabled'])
        kind='backup' if self.book.index('current')==1 else 'release'
        self.operation_button.configure(text='验证并还原' if kind=='backup' else '下载并验证')
        self.operation_button.state(['!disabled'] if allowed and installed and self.tables[kind].selection() else ['disabled'])
        self.cancel_button.state(['!disabled'] if self.job is not None and self.job['busy'] and not self.closing and not self.cancel_requested else ['disabled'])
        self.apply_button.state(['!disabled'] if allowed and self.plan else ['disabled'])

    def request(self,method,callback,**params):
        if not self.connected or self.busy or self.closing:return
        self.busy=True;self.callback=callback;self.session.send('rpc',method=method,tag=method,params=params);self.buttons()

    def loaded(self,result):
        self.catalog=result;self.current.set('当前版本 · '+result['current'])
        self.channel.set('测试版' if result.get('channel')=='beta' else '稳定版');self.page_label.set('第 '+str(result.get('page',1))+' 页')
        for table in self.tables.values():table.delete(*table.get_children())
        for row in result['releases']:self.tables['release'].insert('','end',iid=row['id'],values=(row['version'],row['published'],f"{row['bytes']/1024**2:.1f} MB"))
        for index,row in enumerate(result['history']):self.tables['backup'].insert('','end',iid=str(index),values=(row['version'],row['path']))
        if not result['installed']:self.status.set('源码环境；请从完整便携包运行后安装更新。')
        elif not result['releases'] and result['revision']:self.status.set(result.get('unavailable_reason') or '本页没有可安装版本。')
        self.buttons()

    def check(self,page=1):
        self.request('program_check',self.job_result,channel='beta' if self.channel.get()=='测试版' else 'stable',page=page)

    def job_result(self,result):
        self.job=result;self.next_poll=time.monotonic()+.4;self.progress.configure(value=result['progress']*100);self.status.set(result['detail'])
        if not result['busy']:
            self.job=None;self.cancel_requested=False
            if result['state']=='success':
                if result['kind']=='check':self.loaded(result['result'])
                else:self.plan=result['result'];self.status.set('已验证 '+self.plan['version']+'；可关闭并安装。')
        self.buttons()

    def import_package(self):
        path=filedialog.askopenfilename(parent=self.window,title='AutoZZZ 完整更新包',filetypes=[('ZIP','*.zip')])
        if path:self.plan=None;self.request('program_import',self.job_result,path=path)

    def operation(self):
        kind='backup' if self.book.index('current')==1 else 'release';selected=self.tables[kind].selection()
        if not selected or self.catalog is None:return
        self.plan=None
        if kind=='backup':self.request('program_restore',self.job_result,path=self.catalog['history'][int(selected[0])]['path'])
        else:self.request('program_download',self.job_result,release_id=selected[0],revision=self.catalog['revision'])

    def cancel(self):
        if self.job is not None:self.cancel_requested=True;self.buttons()

    def apply(self):
        if self.plan is None or self.busy or self.job is not None or self.closing or self.ui.closing:return
        if not messagebox.askokcancel('安装程序版本','安装 '+self.plan['version']+' 并重启 AutoZZZ？配置和记录会保留，旧程序会归档。',parent=self.window):return
        self.ui.pending_program_update=dict(self.plan);self.ui._close()
        if not self.ui.closing:self.ui.pending_program_update=None

    def poll(self):
        if self.closed:return
        try:
            while True:
                event=self.events.get_nowait()
                if event['type']=='connected':self.connected=True;self.request('program_catalog',self.loaded)
                elif event['type']=='rpc_result':
                    self.busy=False;callback=self.callback;self.callback=None
                    if callback is not None:callback(event['result'])
                elif event['type']=='error':self.busy=False;self.callback=None;self.status.set(event['message']);self.next_poll=time.monotonic()+.8
                elif event['type']=='closed':self.closed=True;self.window.destroy();return
                self.buttons()
        except queue.Empty:pass
        if self.job is not None and not self.busy and not self.closing and time.monotonic()>=self.next_poll:self.request('program_cancel' if self.cancel_requested else 'program_status',self.job_result,job_id=self.job['job_id'])
        self.window.after(80,self.poll)

    def close(self):
        if self.closing:return True
        self.closing=True;self.status.set('正在取消并等待原工作进程退出…');self.session.close();self.buttons();return True


def open_program_updates(ui):
    previous=getattr(ui,'program_updates_window',None)
    if previous is not None and not previous.closed:previous.window.deiconify();previous.window.lift();return previous
    ui.program_updates_window=ProgramUpdatesWindow(ui,'global' if ui.server.get()=='国际服' else 'cn')
    return ui.program_updates_window
