"""Explicit environment network checks; a result never silently changes configuration."""
import queue
import time
import tkinter as tk
from tkinter import ttk

from .localization_tk import PresentationVar
from . import theme
from .config import ROOT
from .onedragon_runtime import RuntimePaths
from .onedragon_session import RuntimeSession


class EnvironmentToolsWindow:
    def __init__(self,ui,profile):
        self.ui=ui;self.closed=self.closing=self.connected=self.busy=False
        self.catalog=self.job=self.callback=None;self.next_poll=0;self.events=queue.Queue();self.candidates={}
        d=lambda n:round(n*ui.ui_scale)
        self.window=tk.Toplevel(ui.root);self.window.title('AutoZZZ · 下载源与代理');self.window.transient(ui.root)
        self.window.geometry(f'{d(760)}x{d(540)}');self.window.minsize(d(570),d(400))
        frame=ttk.Frame(self.window,padding=d(18));frame.pack(fill='both',expand=True)
        frame.columnconfigure(0,weight=1);frame.rowconfigure(3,weight=1)
        self.current=tk.StringVar(self.window,'正在读取环境设置…');self.status=PresentationVar(self.window)
        theme.wrapped_label(frame,textvariable=self.current).grid(row=0,column=0,sticky='ew')
        bar=ttk.Frame(frame);bar.grid(row=1,column=0,sticky='ew',pady=d(12));self.actions=[]
        for text,command in (('刷新',self.reload),('依赖源测速',lambda:self.start('pip_test')),('获取 GitHub 代理',lambda:self.start('proxy_discover'))):
            button=ttk.Button(bar,text=text,command=command);button.pack(side='left',padx=(0,d(8)));self.actions.append(button)
        self.cancel_button=ttk.Button(bar,text='取消',command=self.cancel);self.cancel_button.pack(side='right')
        theme.wrapped_label(frame,text='测速读取包索引，不安装依赖；代理地址仅作为候选，应用后才保存。',style='Hint.TLabel').grid(row=2,column=0,sticky='ew',pady=(0,d(10)))
        self.rows=ttk.Treeview(frame,columns=('source','latency','result'),show='headings',height=5)
        for key,label,width in (('source','来源',330),('latency','响应耗时',95),('result','结果',190)):
            self.rows.heading(key,text=label);self.rows.column(key,width=d(width),minwidth=d(60),stretch=key!='latency')
        self.rows.grid(row=3,column=0,sticky='nsew')
        selection=ttk.Frame(frame);selection.grid(row=4,column=0,sticky='ew',pady=d(12));selection.columnconfigure(0,weight=1)
        self.selected=tk.StringVar(self.window);self.choices=ttk.Combobox(selection,textvariable=self.selected,state='readonly')
        self.choices.grid(row=0,column=0,sticky='ew')
        self.apply_button=ttk.Button(selection,text='应用选中结果',command=self.apply);self.apply_button.grid(row=0,column=1,padx=(d(10),0))
        theme.wrapped_label(frame,textvariable=self.status,style='Hint.TLabel').grid(row=5,column=0,sticky='ew')
        ttk.Button(frame,text='关闭',command=self.close).grid(row=6,column=0,sticky='e',pady=(d(12),0))
        self.session=RuntimeSession(RuntimePaths.discover(ROOT,profile),self.events.put);self.worker=self.session.worker
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.buttons();self.window.after(80,self.poll)

    def buttons(self):
        active=self.connected and not self.busy and not self.closing
        for button in self.actions:button.state(['!disabled'] if active and (not self.job or not self.job.get('busy')) else ['disabled'])
        self.cancel_button.state(['!disabled'] if active and self.job and self.job.get('busy') else ['disabled'])
        self.apply_button.state(['!disabled'] if active and self.candidates and self.job and not self.job.get('busy') else ['disabled'])

    def request(self,method,callback,**params):
        if not self.connected or self.busy or self.closing:return
        self.busy=True;self.callback=callback;self.session.send('rpc',method=method,tag=method,params=params);self.buttons()

    def reload(self):self.request('environment_tools_catalog',self.loaded)

    def loaded(self,result):
        self.catalog=result;self.current.set('当前 Python 依赖源：'+result['pip_source'])
        self.status.set('配置已读取');self.buttons()

    def start(self,action):
        if not self.catalog:return
        self.job=None;self.candidates={};self.selected.set('');self.choices.configure(values=())
        self.rows.delete(*self.rows.get_children())
        self.request('environment_tools_start',self.updated,action=action,revision=self.catalog['revision'])

    def updated(self,result):
        self.job=result;self.next_poll=time.monotonic()+.5;self.status.set(result.get('detail',''))
        self.rows.delete(*self.rows.get_children())
        for row in result.get('results',[]):
            self.rows.insert('','end',values=(row['label']+' · '+row['value'],
                '—' if row['elapsed_ms'] is None else str(row['elapsed_ms'])+' ms',row['error'] or '可用'))
        if not result.get('busy'):
            self.candidates={row['label']+' · '+row['value']:row['value'] for row in result.get('results',[]) if not row['error']}
            if result.get('state')!='success':self.candidates={}
            self.choices.configure(values=list(self.candidates))
            self.selected.set(next((label for label,value in self.candidates.items() if value==result.get('candidate')),''))
        self.buttons()

    def apply(self):
        value=self.candidates.get(self.selected.get())
        if not value or not self.job:return
        def applied(result):
            self.loaded(result);self.job=None;self.candidates={};self.status.set('已保存；后续依赖下载将使用新设置');self.buttons()
        self.request('environment_tools_apply',applied,job_id=self.job['job_id'],value=value,revision=self.catalog['revision'])

    def cancel(self):
        if self.job:self.request('environment_tools_cancel',self.updated,job_id=self.job['job_id'])

    def poll(self):
        if self.closed:return
        try:
            while True:
                event=self.events.get_nowait()
                if event['type']=='connected':self.connected=True;self.reload()
                elif event['type']=='rpc_result':
                    self.busy=False;callback,self.callback=self.callback,None
                    if callback and not self.closing:callback(event['result'])
                elif event['type']=='error':
                    self.busy=False;self.callback=None;self.status.set(event['message']);self.next_poll=time.monotonic()+1
                elif event['type']=='closed':self.closed=True;self.window.destroy();return
                self.buttons()
        except queue.Empty:pass
        if self.job and self.job.get('busy') and not self.busy and not self.closing and time.monotonic()>=self.next_poll:
            self.request('environment_tools_status',self.updated,job_id=self.job['job_id'])
        if self.closing and not self.worker.is_alive():self.closed=True;self.window.destroy();return
        self.window.after(80,self.poll)

    def close(self):
        if not self.closing:
            self.closing=True;self.status.set('正在取消检查并等待原连接退出…');self.session.close();self.buttons()
        return True
