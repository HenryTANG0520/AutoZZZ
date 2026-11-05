"""Local MCP lifecycle and single-use consent; RPC supplied by existing SDK connection."""
import json
import queue
import threading
from .localization_tk import PresentationVar
import tkinter as tk
from tkinter import ttk


class McpWindow:
    def __init__(self,parent,rpc,scale=1):
        self.rpc=rpc;self.events=queue.Queue();self.busy=False;self.closing=False;self.closed=False;self.poll_token=None
        self.window=tk.Toplevel(parent);self.window.title('AutoZZZ · MCP服务')
        self.window.geometry(f'{round(800*scale)}x{round(790*scale)}')
        frame=ttk.Frame(self.window,padding=round(16*scale));frame.pack(fill='both',expand=True)
        frame.columnconfigure(1,weight=1);frame.rowconfigure(10,weight=1)
        self.port=tk.StringVar(frame,'23001');self.url=tk.StringVar(frame);self.token=tk.StringVar(frame)
        self.message=PresentationVar(frame,'尚未启动');self.prepared=tk.StringVar(frame,'尚未准备应用')
        ttk.Label(frame,text='本机 MCP 服务').grid(row=0,column=0,columnspan=3,sticky='w')
        ttk.Label(frame,text='端口').grid(row=1,column=0,sticky='w',pady=8)
        ttk.Entry(frame,textvariable=self.port,width=10).grid(row=1,column=1,sticky='w')
        actions=ttk.Frame(frame);actions.grid(row=2,column=0,columnspan=3,sticky='w')
        for text,fn in [('启动',self.start),('停止',lambda:self.call('mcp_stop')),('刷新',lambda:self.call('mcp_status'))]:
            ttk.Button(actions,text=text,command=fn).pack(side='left',padx=(0,8))
        ttk.Button(actions,text='操作会话…',command=lambda:self.show_actions(scale)).pack(side='left')
        self.action_window=None
        ttk.Label(frame,text='连接地址').grid(row=3,column=0,sticky='w',pady=8)
        ttk.Entry(frame,textvariable=self.url,state='readonly').grid(row=3,column=1,columnspan=2,sticky='ew')
        ttk.Label(frame,text='Bearer令牌').grid(row=4,column=0,sticky='w')
        ttk.Entry(frame,textvariable=self.token,show='•',state='readonly').grid(row=4,column=1,sticky='ew')
        ttk.Button(frame,text='复制令牌',command=self.copy_token).grid(row=4,column=2,padx=(8,0))
        ttk.Label(frame,text='执行授权').grid(row=5,column=0,sticky='w',pady=(16,8))
        ttk.Label(frame,textvariable=self.prepared).grid(row=5,column=1,columnspan=2,sticky='w')
        limits=ttk.Frame(frame);limits.grid(row=6,column=0,columnspan=3,sticky='ew')
        limits.columnconfigure(1,weight=1);limits.columnconfigure(3,weight=1)
        self.fields={}
        for index,(key,label,value,width) in enumerate([('id','已有预算编号','',25),('allowance','总电量','100',8),('battle_limit','场次','1',6),('max_per_battle','单场','100',6)]):
            row=index//2;column=index%2*2
            ttk.Label(limits,text=label).grid(row=row,column=column,sticky='w',padx=(0,8),pady=4)
            var=tk.StringVar(frame,value);self.fields[key]=var
            ttk.Entry(limits,textvariable=var,width=width).grid(row=row,column=column+1,sticky='ew',padx=(0,8),pady=4)
        self.accepted=tk.BooleanVar(frame,False)
        ttk.Checkbutton(frame,text='确认下方列出的本轮资源操作',variable=self.accepted).grid(row=7,column=0,columnspan=3,sticky='w',pady=8)
        consent=ttk.Frame(frame);consent.grid(row=8,column=0,columnspan=3,sticky='w')
        ttk.Button(consent,text='授权准备的应用一次',command=self.grant).pack(side='left',padx=(0,8))
        ttk.Button(consent,text='撤销未使用授权',command=lambda:self.call('mcp_revoke')).pack(side='left')
        edits=ttk.LabelFrame(frame,text='编辑会话',padding=8);edits.grid(row=9,column=0,columnspan=3,sticky='ew',pady=(12,0))
        edits.columnconfigure(1,weight=1)
        scopes=ttk.Frame(edits);scopes.grid(row=0,column=0,columnspan=3,sticky='w')
        self.edit_scopes={key:tk.BooleanVar(frame,False) for key in ('config','screens')}
        for key,label in [('config','配置'),('screens','画面区域')]:
            ttk.Checkbutton(scopes,text=label,variable=self.edit_scopes[key]).pack(side='left',padx=(0,12))
        ttk.Button(scopes,text='启用本次编辑',command=self.enable_edit).pack(side='left',padx=(0,8))
        ttk.Button(scopes,text='关闭编辑',command=lambda:self.call('mcp_edit_disable')).pack(side='left')
        self.edit_message=tk.StringVar(frame,'未启用')
        ttk.Label(edits,textvariable=self.edit_message).grid(row=1,column=0,columnspan=3,sticky='w',pady=4)
        ttk.Label(edits,text='归档').grid(row=2,column=0,sticky='w')
        self.archive=tk.StringVar(frame)
        self.archive_combo=ttk.Combobox(edits,textvariable=self.archive,state='readonly')
        self.archive_combo.grid(row=2,column=1,sticky='ew',padx=8)
        ttk.Button(edits,text='恢复所选归档',command=self.restore_edit).grid(row=2,column=2)
        self.archive_map={}
        self.log= tk.Text(frame,height=12,wrap='word',state='disabled');self.log.grid(row=10,column=0,columnspan=3,sticky='nsew',pady=10)
        ttk.Label(frame,textvariable=self.message,wraplength=round(720*scale)).grid(row=11,column=0,columnspan=3,sticky='w')
        ttk.Label(frame,text='编辑将改写配置/画面资料并撤销未用运行授权；关闭窗口停止MCP及其任务。',wraplength=round(720*scale)).grid(row=12,column=0,columnspan=3,sticky='w',pady=(8,0))
        self.state={};self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.poll_token=self.window.after(100,self.poll);self.call('mcp_status')

    def start(self):
        try:port=int(self.port.get())
        except ValueError:self.message.set('端口须为整数');return
        self.call('mcp_start',port=port)

    def copy_token(self):
        if not self.token.get():return
        self.window.clipboard_clear();self.window.clipboard_append(self.token.get())
        self.message.set('本次连接令牌已复制')

    def show_actions(self,scale=1):
        from .onedragon_mcp_actions_ui import ActionWindow
        if self.action_window is not None and not self.action_window.closed:
            self.action_window.window.lift();return
        self.action_window=ActionWindow(self.window,self.rpc,lambda:{key:value.get()for key,value in self.fields.items()},scale)

    def enable_edit(self):
        scopes=[key for key,value in self.edit_scopes.items() if value.get()]
        if not scopes:self.message.set('请先选择配置和/或画面区域');return
        self.call('mcp_edit_enable',scopes=scopes)

    def restore_edit(self):
        selected=self.archive_map.get(self.archive.get())
        if selected is None:self.message.set('请选择归档');return
        self.call('mcp_edit_restore',kind=selected[0],id=selected[1])

    def grant(self):
        try:
            execution=self.state.get('execution',{})
            budget=None
            if execution.get('uses_charge'):
                budget={key:var.get() if key=='id' else int(var.get()) for key,var in self.fields.items()}
            resources=execution.get('resources',{})
            if resources.get('confirmation_required') and not self.accepted.get():raise ValueError('请先核对并确认资源操作清单')
            self.call('mcp_grant',budget=budget,resource_confirmation=resources.get('confirmation_id') or None)
        except ValueError as exc:self.message.set(str(exc))

    def call(self,method,**params):
        if self.busy:return
        self.busy=True;self.message.set('正在处理…')
        def work():
            try:self.events.put((method,self.rpc(method,params),None))
            except Exception as exc:self.events.put((method,None,str(exc)))
        threading.Thread(target=work,name='AutoZZZ-MCP-UI',daemon=True).start()

    def poll(self):
        if self.closed:return
        try:
            method,value,error=self.events.get_nowait();self.busy=False
            if error:self.message.set(error)
            else:
                self.state=value;self.url.set(value.get('url',''))
                if 'bearer_token' in value:self.token.set(value['bearer_token'])
                if not value.get('running'):self.token.set('')
                execution=value.get('execution',{})
                editing=value.get('editing',{});enabled=editing.get('session')
                self.edit_message.set(('账号 '+str(enabled['instance_idx'])+' · '+
                    ' / '.join('配置'if s=='config'else'画面区域'for s in enabled['scopes'])+'编辑已启用')if enabled else'未启用')
                self.archive_map={}
                for kind,rows in editing.get('archives',{}).items():
                    for row in rows:
                        label=('配置'if kind=='config'else'画面')+' · '+row['metadata'].get('action','')+' · '+row['id']
                        self.archive_map[label]=(kind,row['id'])
                self.archive_combo.configure(values=list(self.archive_map))
                if self.archive.get()not in self.archive_map:self.archive.set('')
                self.prepared.set(execution.get('prepared_app') or '尚未准备应用')
                resource_text=[row.get('text','') for row in execution.get('resources',{}).get('actions',[])]
                lines=resource_text+[row['message'] for row in value.get('logs',[])]
                self.log.configure(state='normal');self.log.delete('1.0','end');self.log.insert('end','\n'.join(lines));self.log.configure(state='disabled')
                self.message.set('已启动' if value.get('running') else '已停止')
                if method=='mcp_grant':self.accepted.set(False)
            if self.closing:
                if method=='mcp_stop' and not error:self.destroy();return
                self.closing=False
        except queue.Empty:pass
        self.poll_token=self.window.after(100,self.poll)

    def destroy(self):
        if self.closed:return
        self.closed=True
        if self.action_window is not None:self.action_window.destroy()
        if self.poll_token is not None:
            self.window.after_cancel(self.poll_token);self.poll_token=None
        self.window.destroy()

    def close(self):
        if self.busy:self.message.set('请等待当前请求完成后关闭');return
        self.closing=True;self.call('mcp_stop')
