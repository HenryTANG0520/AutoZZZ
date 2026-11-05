"""Local MCP lifecycle, diagnostics and consent on the existing SDK connection."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from .localization import application_name
from .localization_tk import PresentationVar


STATUS_POLL_MS = 5000
RUN_LABELS = {
    'idle': '暂无任务', 'ready': '就绪', 'initializing': '正在初始化', 'preparing': '准备中',
    'prepared': '已准备', 'running': '运行中', 'paused': '已暂停', 'focus_wait': '等待游戏恢复焦点',
    'stopping': '正在停止', 'stopped': '已停止', 'success': '已完成', 'failed': '失败',
}


class McpWindow:
    def __init__(self,parent,rpc,scale=1):
        self.rpc=rpc;self.events=queue.Queue();self.busy=False;self.closing=False;self.closed=False;self.poll_token=None
        self.status_poll_token=None;self._quiet=False;self._deferred=None
        self.window=tk.Toplevel(parent);self.window.title('AutoZZZ · MCP服务')
        self.window.geometry(f'{round(800*scale)}x{round(790*scale)}')
        frame=ttk.Frame(self.window,padding=round(16*scale));frame.pack(fill='both',expand=True)
        frame.columnconfigure(1,weight=1);frame.rowconfigure(11,weight=1)
        self.port=tk.StringVar(frame,'23001');self.url=tk.StringVar(frame);self.token=tk.StringVar(frame)
        self.message=PresentationVar(frame,'尚未启动');self.prepared=tk.StringVar(frame,'尚未准备应用')
        ttk.Label(frame,text='本机 MCP 服务').grid(row=0,column=0,columnspan=3,sticky='w')
        ttk.Label(frame,text='端口').grid(row=1,column=0,sticky='w',pady=8)
        ttk.Entry(frame,textvariable=self.port,width=10).grid(row=1,column=1,sticky='w')
        actions=ttk.Frame(frame);actions.grid(row=2,column=0,columnspan=3,sticky='w')
        self.service_buttons={}
        for text,fn in [('启动',self.start),('停止',lambda:self.call('mcp_stop')),
                        ('重启',lambda:self.start(restart=True)),('刷新',lambda:self.call('mcp_status'))]:
            button=ttk.Button(actions,text=text,command=fn)
            button.pack(side='left',padx=(0,8))
            self.service_buttons[text]=button
        ttk.Button(actions,text='操作会话…',command=lambda:self.show_actions(scale)).pack(side='left')
        self.action_window=None
        ttk.Label(frame,text='连接地址').grid(row=3,column=0,sticky='w',pady=8)
        ttk.Entry(frame,textvariable=self.url,state='readonly').grid(row=3,column=1,sticky='ew')
        self.address_button=ttk.Button(frame,text='复制地址',command=self.copy_address)
        self.address_button.grid(row=3,column=2,padx=(8,0),sticky='ew')
        ttk.Label(frame,text='Bearer令牌').grid(row=4,column=0,sticky='w')
        ttk.Entry(frame,textvariable=self.token,show='•',state='readonly').grid(row=4,column=1,sticky='ew')
        self.token_button=ttk.Button(frame,text='复制令牌',command=self.copy_token)
        self.token_button.grid(row=4,column=2,padx=(8,0),sticky='ew')
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
        self.run_message=PresentationVar(frame,'暂无任务')
        ttk.Label(frame,text='当前运行状态').grid(row=10,column=0,sticky='w',pady=(10,0))
        ttk.Label(frame,textvariable=self.run_message,wraplength=round(570*scale)).grid(row=10,column=1,columnspan=2,sticky='w',pady=(10,0))
        self.details=ttk.Notebook(frame)
        self.details.grid(row=11,column=0,columnspan=3,sticky='nsew',pady=10)
        _,self.log=self._text_pane('服务日志')
        self.resource_tab,self.resource_list=self._text_pane('资源操作清单')
        ttk.Label(frame,textvariable=self.message,wraplength=round(720*scale)).grid(row=12,column=0,columnspan=3,sticky='w')
        ttk.Label(frame,text='编辑会改写所选资料并撤销未用授权。停止或重启服务、关闭窗口会停止 MCP 启动的任务。',wraplength=round(720*scale)).grid(row=13,column=0,columnspan=3,sticky='w',pady=(8,0))
        self.state={};self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.poll_token=self.window.after(100,self.poll)
        self.status_poll_token=self.window.after(STATUS_POLL_MS,self.refresh_status)
        self.call('mcp_status')

    def _text_pane(self,title):
        pane=ttk.Frame(self.details)
        pane.columnconfigure(0,weight=1);pane.rowconfigure(0,weight=1)
        widget=tk.Text(pane,height=8,wrap='word',state='disabled')
        scrollbar=ttk.Scrollbar(pane,orient='vertical',command=widget.yview)
        widget.configure(yscrollcommand=scrollbar.set)
        widget.grid(row=0,column=0,sticky='nsew')
        scrollbar.grid(row=0,column=1,sticky='ns')
        self.details.add(pane,text=title)
        return pane,widget

    def start(self,*,restart=False):
        try:port=int(self.port.get())
        except ValueError:self.message.set('端口须为整数');return
        if not 1024<=port<=65535:
            self.message.set('MCP端口须为1024—65535。');return
        self.call('mcp_restart' if restart else 'mcp_start',port=port)

    def copy_token(self):
        self._copy(self.token.get(),'本次连接令牌已复制')

    def copy_address(self):
        self._copy(self.url.get(),'连接地址已复制')

    def _copy(self,text,status):
        if not text:return
        self.window.clipboard_clear();self.window.clipboard_append(text)
        self.message.set(status)

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

    def call(self,method,*,quiet=False,**params):
        if self.closed or (self.closing and method!='mcp_stop'):return
        if self.busy:
            if self._quiet and not quiet and method!='mcp_status' and (self._deferred is None or self.closing):
                self._deferred=(method,params)
            return
        self.busy=True;self._quiet=quiet
        if not quiet:self.message.set('正在处理…')
        self._update_buttons()

        def work():
            value=None
            try:
                if method=='mcp_restart':
                    value=self.rpc('mcp_stop',{})
                    if value.get('running'):raise RuntimeError('MCP服务尚未停止。')
                    value=self.rpc('mcp_start',params)
                else:
                    value=self.rpc(method,params)
                self.events.put((method,value,None,quiet))
            except Exception as exc:
                # A failed restart may still have completed the stop step.
                self.events.put((method,value,str(exc),quiet))
        threading.Thread(target=work,name='AutoZZZ-MCP-UI',daemon=True).start()

    def refresh_status(self):
        self.status_poll_token=None
        if self.closed:return
        if not self.closing and self.window.winfo_viewable():
            self.call('mcp_status',quiet=True)
        self.status_poll_token=self.window.after(STATUS_POLL_MS,self.refresh_status)

    def _update_buttons(self):
        running=bool(self.state.get('running'))
        available=not self.closing and (not self.busy or self._quiet)
        choices={'停止','重启','刷新'} if running else {'启动','刷新'}
        for label,button in self.service_buttons.items():
            enabled=available and label in choices
            button.state(['!disabled'] if enabled else ['disabled'])
        for button,value in ((self.address_button,self.url),(self.token_button,self.token)):
            button.state(['!disabled'] if available and value.get() else ['disabled'])

    @staticmethod
    def _set_text(widget,value):
        if widget.get('1.0','end-1c')==value:return False
        first,last=widget.yview()
        widget.configure(state='normal')
        widget.delete('1.0','end');widget.insert('end',value)
        widget.configure(state='disabled')
        widget.yview_moveto(1.0 if last>=.999 else first)
        return True

    def _show_run_status(self,status,execution):
        if not status:
            run=execution.get('run',{})
            state=execution.get('state','idle')
            control=execution.get('control',{}).get('state')
            if state=='running' and control in ('paused','focus_wait'):state=control
            status=dict(state=state,source=run.get('source'),
                        app=run.get('app') or execution.get('prepared_app'),
                        step=execution.get('preparation_step') or run.get('current_node'),
                        duration_seconds=run.get('duration_seconds'))
        state=RUN_LABELS.get(status.get('state'),'状态未知')
        source={'mcp':'MCP','autozzz':'AutoZZZ','http':'HTTP'}.get(status.get('source'))
        app=status.get('app') or ''
        duration=status.get('duration_seconds')
        elapsed=f'{duration:.1f}s' if type(duration) in (int,float) and 0<=duration<1e10 else ''
        detail=' · '.join(part for part in (source,application_name(app,app),status.get('step'),elapsed) if part)
        self.run_message.set_status(state,detail)

    def _apply_status(self,value):
        previous=self.state.get('execution',{})
        stopped=self.state.get('running') and not value.get('running')
        if self.action_window is not None and (stopped or 'bearer_token' in value):
            self.action_window.destroy()
            self.action_window=None
        self.state={key:item for key,item in value.items() if key!='bearer_token'}
        self.url.set(value.get('url',''))
        if 'bearer_token' in value:self.token.set(value['bearer_token'])
        if not value.get('running'):self.token.set('')
        execution=value.get('execution',{})
        editing=value.get('editing',{});enabled=editing.get('session')
        self.edit_message.set(('账号 '+str(enabled['instance_idx'])+' · '+
            ' / '.join('配置'if s=='config'else'画面区域'for s in enabled['scopes'])+'编辑已启用')if enabled else'未启用')
        archives={}
        for kind,rows in editing.get('archives',{}).items():
            for row in rows:
                label=('配置'if kind=='config'else'画面')+' · '+row['metadata'].get('action','')+' · '+row['id']
                archives[label]=(kind,row['id'])
        if archives!=self.archive_map:
            self.archive_map=archives
            self.archive_combo.configure(values=list(archives))
            if self.archive.get()not in archives:self.archive.set('')
        self.prepared.set(execution.get('prepared_app') or '尚未准备应用')
        resources=execution.get('resources',{})
        if resources!=previous.get('resources',{}) or execution.get('job_id')!=previous.get('job_id'):
            self.accepted.set(False)
        resource_text='\n'.join(row.get('text','') for row in resources.get('actions',[]))
        if self._set_text(self.resource_list,resource_text) and resources.get('confirmation_required'):
            self.details.select(self.resource_tab)
        lines=[]
        for row in value.get('logs',[]):
            timestamp=row.get('time')
            prefix=time.strftime('[%H:%M:%S] ',time.localtime(timestamp)) if type(timestamp) in (int,float) else ''
            lines.append(prefix+row['message'])
        self._set_text(self.log,'\n'.join(lines))
        self._show_run_status(value.get('run_status'),execution)

    def poll(self):
        if self.closed:return
        self.poll_token=None
        try:
            method,value,error,quiet=self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy=False;self._quiet=False
            previous=self.state.get('execution',{})
            was_running=bool(self.state.get('running'))
            if error and self.token.get():error=error.replace(self.token.get(),'[已隐藏]')
            if value is not None:
                self._apply_status(value)
            if quiet:
                if error:self.run_message.set('状态读取失败')
                elif was_running!=bool(self.state.get('running')):
                    self.message.set('已启动' if self.state.get('running') else '已停止')
            elif error:
                self.message.set(error)
            else:
                self.message.set('已启动' if value.get('running') else '已停止')
                if method=='mcp_grant':self.accepted.set(False)
            if self.closing and method=='mcp_stop':
                if not error:self.destroy();return
                self.closing=False
            deferred,self._deferred=self._deferred,None
            if deferred is not None:
                method,params=deferred
                current=self.state.get('execution',{})
                changed=any(previous.get(key)!=current.get(key) for key in ('job_id','prepared_app','resources'))
                if method=='mcp_grant' and (error or changed):
                    self.message.set('准备任务已变化，请重新核对授权。')
                else:
                    self.call(method,**params)
            self._update_buttons()
        self.poll_token=self.window.after(100,self.poll)

    def destroy(self):
        if self.closed:return
        self.closed=True
        if self.action_window is not None:self.action_window.destroy()
        if self.poll_token is not None:
            self.window.after_cancel(self.poll_token);self.poll_token=None
        if self.status_poll_token is not None:
            self.window.after_cancel(self.status_poll_token);self.status_poll_token=None
        self.token.set('');self._deferred=None
        self.window.destroy()

    def close(self):
        if self.busy and not self._quiet:self.message.set('请等待当前请求完成后关闭');return
        self.closing=True
        self._update_buttons()
        self.call('mcp_stop')
