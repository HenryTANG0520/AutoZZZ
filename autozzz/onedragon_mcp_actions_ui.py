"""Local persistent consent for MCP input, login, close and a reviewed operation."""
import json
import queue
import threading
from .localization_tk import PresentationVar
import tkinter as tk
from tkinter import ttk


class ActionWindow:
    def __init__(self,parent,rpc,budget,scale=1):
        self.rpc,self.budget=rpc,budget;self.events=queue.Queue();self.busy=False;self.closed=False;self.state={}
        self.window=tk.Toplevel(parent);self.window.title('AutoZZZ · MCP操作会话')
        self.window.geometry(f'{round(780*scale)}x{round(620*scale)}')
        frame=ttk.Frame(self.window,padding=16);frame.pack(fill='both',expand=True)
        frame.columnconfigure(1,weight=1);frame.rowconfigure(3,weight=1);frame.rowconfigure(6,weight=1)
        self.mode=tk.StringVar(frame,'input');self.op=tk.StringVar(frame)
        ttk.Label(frame,text='准备方式').grid(row=0,column=0,sticky='w')
        runtime=ttk.Frame(frame);runtime.grid(row=0,column=1,sticky='ew')
        ttk.Combobox(runtime,textvariable=self.mode,values=['input','operation'],state='readonly',width=14).pack(side='left')
        ttk.Label(runtime,text='时限（秒）').pack(side='left',padx=(16,8))
        self.timeout=tk.StringVar(frame,'600');ttk.Entry(runtime,textvariable=self.timeout,width=8).pack(side='left')
        ttk.Label(frame,text='operation ID').grid(row=1,column=0,sticky='w',pady=8)
        ttk.Entry(frame,textvariable=self.op).grid(row=1,column=1,sticky='ew')
        ttk.Label(frame,text='参数（JSON对象）').grid(row=2,column=0,columnspan=2,sticky='w')
        self.args=tk.Text(frame,height=5,wrap='word');self.args.grid(row=3,column=0,columnspan=2,sticky='nsew');self.args.insert('1.0','{}')
        controls=ttk.Frame(frame);controls.grid(row=4,column=0,columnspan=2,sticky='w',pady=10)
        ttk.Button(controls,text='准备操作',command=self.prepare).pack(side='left',padx=(0,8))
        ttk.Button(controls,text='刷新',command=lambda:self.call('mcp_status')).pack(side='left')
        scopes=ttk.Frame(frame);scopes.grid(row=5,column=0,columnspan=2,sticky='w')
        self.scopes={key:tk.BooleanVar(frame,False)for key in ('input','login','close','operation')}
        for key,label in [('input','低层输入'),('login','启动/登录'),('close','关闭游戏'),('operation','上述operation')]:
            ttk.Checkbutton(scopes,text=label,variable=self.scopes[key]).pack(side='left',padx=(0,12))
        self.details=tk.Text(frame,height=8,wrap='word',state='disabled');self.details.grid(row=6,column=0,columnspan=2,sticky='nsew',pady=8)
        self.accept=tk.BooleanVar(frame,False)
        ttk.Checkbutton(frame,text='已核对以上操作与资源行为',variable=self.accept).grid(row=7,column=0,columnspan=2,sticky='w')
        actions=ttk.Frame(frame);actions.grid(row=8,column=0,columnspan=2,sticky='w',pady=10)
        ttk.Button(actions,text='启用本次会话',command=self.enable).pack(side='left',padx=(0,8))
        ttk.Button(actions,text='关闭权限',command=lambda:self.call('mcp_action_disable')).pack(side='left')
        self.message=PresentationVar(frame,'未启用')
        ttk.Label(frame,textvariable=self.message,wraplength=round(720*scale)).grid(row=9,column=0,columnspan=2,sticky='w')
        ttk.Label(frame,text='消费取决于操作，刷本预算不限制低层输入。已识别刷本operation仍使用已有预算。',wraplength=round(720*scale)).grid(row=10,column=0,columnspan=2,sticky='w',pady=(10,0))
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.poll_token=self.window.after(100,self.poll)
        self.call('mcp_status')

    def prepare(self):
        try:
            descriptor=dict(mode=self.mode.get())
            if descriptor['mode']=='operation':
                arguments=json.loads(self.args.get('1.0','end'))
                if not isinstance(arguments,dict):raise ValueError('参数须为JSON对象。')
                descriptor.update(op_id=self.op.get().strip(),args=arguments)
            self.call('mcp_action_prepare',descriptor=descriptor)
        except (ValueError,TypeError)as exc:self.message.set(str(exc))

    def enable(self):
        try:
            if not self.accept.get():raise ValueError('请先核对操作与资源行为。')
            scopes=[key for key,value in self.scopes.items()if value.get()]
            if not scopes:raise ValueError('请选择权限范围。')
            values=self.budget();budget=None
            if values['id']:
                budget={key:value if key=='id'else int(value)for key,value in values.items()}
            resource=self.state.get('execution',{}).get('resources',{})
            timeout=int(self.timeout.get())
            if timeout<30:raise ValueError('时限须为不小于30的整数秒。')
            self.call('mcp_action_enable',scopes=scopes,budget=budget,resource_confirmation=resource.get('confirmation_id')or None,timeout_seconds=timeout)
        except ValueError as exc:self.message.set(str(exc))

    def call(self,method,**parameters):
        if self.busy or self.closed:return
        self.busy=True;self.message.set('正在处理…')
        def work():
            try:self.events.put((method,self.rpc(method,parameters),None))
            except Exception as exc:self.events.put((method,None,str(exc)))
        threading.Thread(target=work,daemon=True,name='AutoZZZ-MCP-Action-UI').start()

    def poll(self):
        if self.closed:return
        try:
            method,value,error=self.events.get_nowait();self.busy=False
            if error:self.message.set(error)
            else:
                self.state=value;actions=value.get('actions',{});session=actions.get('session')
                execution=value.get('execution',{})
                self.details.configure(state='normal');self.details.delete('1.0','end')
                self.details.insert('end','\n'.join(row.get('text','')for row in execution.get('resources',{}).get('actions',[])))
                self.details.configure(state='disabled')
                self.message.set('会话已启用 · '+','.join(session['scopes'])if session else'已准备，可核对并启用'if actions.get('prepared')else'尚未准备操作')
                if execution.get('busy')and execution.get('state')=='preparing':self.call('mcp_status')
                if method=='mcp_action_enable':self.accept.set(False)
        except queue.Empty:pass
        self.poll_token=self.window.after(100,self.poll)

    def destroy(self):
        if self.closed:return
        self.closed=True;self.window.after_cancel(self.poll_token);self.window.destroy()

    def close(self):
        if self.busy:self.message.set('请等待当前请求完成。');return
        def disable():
            try:self.rpc('mcp_action_disable',{})
            except Exception:pass
        threading.Thread(target=disable,daemon=True).start();self.destroy()
