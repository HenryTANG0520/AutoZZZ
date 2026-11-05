"""Native six-step avatar template editor; screenshots remain outside recognition."""
from __future__ import annotations
import base64
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox,simpledialog

from . import theme
from .config import ROOT
from .onedragon_runtime import RuntimePaths
from .onedragon_session import RuntimeSession
from .agent_template_hotkeys import DEFAULTS,ACTIONS,validate_bindings,HotkeyPoller


class TemplateFloat:
    def __init__(self,owner):
        from .overlay import NativePresentation
        self.owner=owner;self.closed=False
        self.window=tk.Toplevel(owner.ui.root,takefocus=False);self.window.withdraw();self.window.overrideredirect(True)
        self.body=ttk.Frame(self.window,padding=8);self.body.pack(fill='both',expand=True)
        self.title=ttk.Label(self.body,textvariable=owner.progress);self.title.grid(row=0,column=0,columnspan=3,sticky='ew')
        self.title.bind('<ButtonPress-1>',self.start_drag);self.title.bind('<B1-Motion>',self.drag);self.title.bind('<ButtonRelease-1>',self.end_drag)
        ttk.Button(self.body,text='截图',command=owner.capture).grid(row=1,column=0,pady=5)
        ttk.Button(self.body,text='切换窗口',command=owner.switch_window).grid(row=1,column=1,padx=4)
        ttk.Button(self.body,text='展开/收起',command=owner.toggle_expand).grid(row=1,column=2)
        self.steps=ttk.Frame(self.body);self.steps.grid(row=2,column=0,columnspan=3,sticky='ew')
        self.buttons=[]
        for i,step in enumerate(owner.catalog['steps']):
            button=ttk.Button(self.steps,text=step['name'],command=lambda i=i:owner.choose_step(i));button.grid(row=i//3,column=i%3,sticky='ew',padx=2,pady=3);self.buttons.append(button)
        self.presentation=NativePresentation();self.window.update_idletasks()
        try:self.handle=self.presentation.prepare(self.window)
        except Exception:self.window.destroy();raise
        # The float accepts clicks, but retains source capture exclusion and NOACTIVATE.
        self.presentation.set_style(self.handle,-20,self.presentation.get_style(self.handle,-20)&~0x20)

    def refresh(self):
        from PIL import Image,ImageTk
        if self.owner.preferences['values']['float_expanded']:self.steps.grid()
        else:self.steps.grid_remove()
        for i,button in enumerate(self.buttons):
            done=bool(self.owner.draft and self.owner.draft['images'][i])
            button.configure(text=('✓ ' if done else '')+self.owner.catalog['steps'][i]['name'])
            if done:
                image=Image.open(BytesIO(base64.b64decode(self.owner.draft['images'][i])));image.thumbnail((48,48))
                self.owner.photos['float'+str(i)]=ImageTk.PhotoImage(image,master=self.window);button.configure(image=self.owner.photos['float'+str(i)],compound='top')
        self.window.update_idletasks();values=self.owner.preferences['values'];x,y=values['float_x'],values['float_y']
        if x==-1:x=40
        if y==-1:y=140
        self.window.deiconify()
        self.presentation.show(self.handle,(x,y,self.body.winfo_reqwidth(),self.body.winfo_reqheight()))

    def start_drag(self,event):self.origin=(event.x_root,event.y_root,self.window.winfo_x(),self.window.winfo_y())
    def drag(self,event):
        x,y,wx,wy=self.origin;self.window.geometry(f'+{wx+event.x_root-x}+{wy+event.y_root-y}')
    def end_drag(self,event):
        self.owner.preferences['values'].update(float_x=self.window.winfo_x(),float_y=self.window.winfo_y());self.owner.save_preferences()
    def close(self):
        if not self.closed:self.closed=True;self.window.destroy()


class AgentTemplatesWindow:
    def __init__(self,ui,profile):
        self.ui=ui;self.profile=profile;self.connected=self.busy=self.closing=self.closed=False;self.callback=None
        self.events=queue.Queue();self.catalog=None;self.draft=None;self.preferences=None;self.selected=0;self.float=None;self.dirty=False
        self.photos={};self.capture_deadline=0.;self.capture_waiting=None;self.next_game_probe=0.;self.game=None;self.pending_import=None
        from .windows import user32
        self.hotkeys=HotkeyPoller(user32.GetAsyncKeyState)
        self.window=tk.Toplevel(ui.root);self.window.title('AutoZZZ · 代理人头像模板');self.window.transient(ui.root)
        d=lambda n:round(n*ui.ui_scale);self.window.geometry(f'{d(1020)}x{d(760)}');self.window.minsize(d(760),d(600))
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(1,weight=1)
        bar=ttk.Frame(self.window,padding=d(12));bar.grid(row=0,column=0,sticky='ew');bar.columnconfigure(4,weight=1)
        self.controls=[]
        for i,(label,fn) in enumerate((('新建',lambda:self.open_agent()),('打开',self.open_selected),('删除',self.delete),('刷新',self.reload))):
            button=ttk.Button(bar,text=label,command=fn);button.grid(row=0,column=i,padx=d(3));self.controls.append(button)
        self.search=tk.StringVar(self.window);entry=ttk.Entry(bar,textvariable=self.search);entry.grid(row=0,column=4,sticky='ew',padx=d(12))
        self.search.trace_add('write',lambda *_:self.filter_list())
        self.book=ttk.Notebook(self.window);self.book.grid(row=1,column=0,sticky='nsew',padx=d(12))
        listing=ttk.Frame(self.book);listing.rowconfigure(0,weight=1);listing.columnconfigure(0,weight=1);self.book.add(listing,text='模板列表')
        self.table=ttk.Treeview(listing,columns=('id','progress','support'),show='headings',selectmode='browse');self.table.heading('id',text='模板 ID');self.table.heading('progress',text='完成');self.table.heading('support',text='引擎角色');self.table.column('id',width=d(300));self.table.column('progress',width=d(80));self.table.column('support',width=d(180));self.table.grid(row=0,column=0,sticky='nsew')
        sy=ttk.Scrollbar(listing,command=self.table.yview);sy.grid(row=0,column=1,sticky='ns');self.table.configure(yscrollcommand=sy.set);self.table.bind('<Double-1>',lambda _:self.open_selected())
        self.editor=ttk.Frame(self.book,padding=d(12));self.editor.columnconfigure(0,weight=1);self.editor.rowconfigure(3,weight=1);self.book.add(self.editor,text='六步编辑')
        name=ttk.Frame(self.editor);name.grid(row=0,column=0,sticky='ew');name.columnconfigure(1,weight=1)
        ttk.Label(name,text='角色模板 ID').grid(row=0,column=0,padx=(0,d(10)));self.name=tk.StringVar(self.window);self.name_entry=ttk.Combobox(name,textvariable=self.name);self.name_entry.grid(row=0,column=1,sticky='ew')
        self.save_button=ttk.Button(name,text='保存六类模板',command=self.save);self.save_button.grid(row=0,column=2,padx=(d(10),0));self.controls.append(self.save_button)
        self.name.trace_add('write',lambda *_:self.changed_name())
        self.step_bar=ttk.Frame(self.editor);self.step_bar.grid(row=1,column=0,sticky='ew',pady=d(10));self.step_buttons=[]
        self.hint=tk.StringVar(self.window,'小写英文开头，可含数字和下划线；各步骤使用独立PNG截图。')
        theme.wrapped_label(self.editor,textvariable=self.hint).grid(row=2,column=0,sticky='ew')
        preview=ttk.Frame(self.editor);preview.grid(row=3,column=0,sticky='nsew',pady=d(12));preview.columnconfigure(0,weight=1);preview.rowconfigure(0,weight=1)
        self.preview=tk.Canvas(preview,highlightthickness=0,background=theme.FIELD);self.preview.grid(row=0,column=0,sticky='nsew');self.preview.bind('<Configure>',lambda _:self.draw())
        self.crop=ttk.Label(preview,anchor='center');self.crop.grid(row=0,column=1,padx=d(12))
        actions=ttk.Frame(self.editor);actions.grid(row=4,column=0,sticky='ew')
        self.import_button=ttk.Button(actions,text='导入PNG…',command=self.import_png);self.import_button.grid(row=0,column=0)
        self.capture_button=ttk.Button(actions,text='截图当前步骤',command=self.capture);self.capture_button.grid(row=0,column=1,padx=d(8));self.controls.extend((self.import_button,self.capture_button))
        self.side=tk.StringVar(self.window,'左侧');self.side_box=ttk.Combobox(actions,textvariable=self.side,values=('左侧','右侧'),state='readonly',width=7);self.side_box.grid(row=0,column=2);self.side_box.bind('<<ComboboxSelected>>',lambda _:self.change_side())
        self.mask=tk.BooleanVar(self.window,False);ttk.Checkbutton(actions,text='查看遮罩',variable=self.mask,command=self.draw).grid(row=0,column=3,padx=d(8))
        self.float_enabled=tk.BooleanVar(self.window,False);ttk.Checkbutton(actions,text='悬浮操作',variable=self.float_enabled,command=self.toggle_float).grid(row=0,column=4)
        ttk.Button(actions,text='快捷键…',command=self.edit_hotkeys).grid(row=0,column=5,padx=d(8))
        archive=ttk.Frame(self.book,padding=d(12));archive.columnconfigure(0,weight=1);archive.rowconfigure(0,weight=1);self.book.add(archive,text='归档与恢复')
        self.archives=ttk.Treeview(archive,columns=('agent','action','id'),show='headings',selectmode='browse')
        for key,label,width in (('agent','模板',180),('action','操作',100),('id','归档编号',340)):self.archives.heading(key,text=label);self.archives.column(key,width=d(width))
        self.archives.grid(row=0,column=0,sticky='nsew');sy=ttk.Scrollbar(archive,command=self.archives.yview);sy.grid(row=0,column=1,sticky='ns');self.archives.configure(yscrollcommand=sy.set)
        ttk.Button(archive,text='恢复选中归档前的版本',command=self.restore).grid(row=1,column=0,sticky='w',pady=d(10))
        ttk.Button(archive,text='恢复未完成保存',command=self.recover).grid(row=2,column=0,sticky='w')
        footer=ttk.Frame(self.window,padding=d(12));footer.grid(row=2,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        self.progress=tk.StringVar(self.window,'尚未打开模板');self.status=tk.StringVar(self.window,'连接只读模板服务…')
        theme.wrapped_label(footer,textvariable=self.status).grid(row=0,column=0,sticky='ew');ttk.Label(footer,textvariable=self.progress).grid(row=1,column=0,sticky='w',pady=d(6))
        self.session=RuntimeSession(RuntimePaths.discover(ROOT,profile),self.events.put);self.worker=self.session.worker
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.buttons();self.window.after(80,self.poll)

    def buttons(self):
        allowed=self.connected and not self.busy and not self.closing
        for button in self.controls:button.state(['!disabled'] if allowed else ['disabled'])
        for button in self.step_buttons:button.state(['!disabled'] if allowed and self.draft else ['disabled'])
        for button in (self.save_button,self.import_button,self.capture_button):button.state(['!disabled'] if allowed and self.draft else ['disabled'])
        self.side_box.configure(state='readonly' if allowed and self.draft and self.selected==2 else 'disabled')

    def request(self,method,callback,**params):
        if not self.connected or self.busy or self.closing:return False
        self.busy=True;self.callback=callback;self.session.send('rpc',method=method,tag=method,params=params);self.buttons();return True

    def poll(self):
        if self.closed:return
        try:
            while True:
                event=self.events.get_nowait()
                if event['type']=='connected':self.connected=True;self.reload()
                elif event['type']=='rpc_result':
                    self.busy=False;callback=self.callback;self.callback=None
                    if callback is not None and not self.closing:callback(event['result'])
                elif event['type']=='error':self.busy=False;self.callback=None;self.status.set(event['message'])
                elif event['type']=='closed':self.closed=True;self.window.destroy();return
        except queue.Empty:pass
        if not self.closing:
            if self.pending_import is not None and not self.busy:
                path,index,token=self.pending_import;self.pending_import=None
                if self.draft and self.draft['token']==token:self.import_png(path,index)
            self.poll_keys();self.buttons()
        self.window.after(80,self.poll)

    def reload(self):
        def loaded(value):
            self.catalog=value;self.filter_list();self.archives.delete(*self.archives.get_children())
            self.name_entry.configure(values=[row['template_id']+' · '+row['name'] for row in value.get('registered_templates',[])])
            for row in value['archives']:self.archives.insert('','end',iid=row['id'],values=(row['metadata'].get('agent',''),row['metadata'].get('action',''),row['id']))
            self.status.set('存在未完成保存，请在归档页恢复。' if value['pending'] else '六类模板使用一条龙参考区域与遮罩；重拍后需重新准备战斗。')
            if not value['pending'] and self.draft and self.draft.get('registration') and not self.draft['registration']['registered']:
                self.status.set('未注册素材：可编辑保存；不会新增引擎角色、状态或技能。')
            if self.preferences is None:self.request('agent_template_preferences',self.preferences_loaded)
        self.request('agent_template_catalog',loaded)

    def preferences_loaded(self,value):
        self.preferences=value;self.float_enabled.set(value['values']['float_enabled'])
        try:self.hotkeys.configure(validate_bindings(value['values']['hotkeys']))
        except ValueError as exc:self.status.set(str(exc));self.hotkeys.configure(DEFAULTS)

    def save_preferences(self):
        if self.preferences is None or self.busy or self.closing:return
        self.request('agent_template_preferences_save',self.preferences_loaded,values=deepcopy(self.preferences['values']),revision=self.preferences['revision'])

    def filter_list(self):
        if self.catalog is None:return
        self.table.delete(*self.table.get_children());query=self.search.get().strip().lower()
        for row in self.catalog['agents']:
            registration=row.get('registration',{})
            if query in row['id'] or query in registration.get('name','').lower():self.table.insert('','end',iid=row['id'],values=(row['id'],f"{row['completed']}/{row['total']}",registration.get('name') or '未注册素材'))

    def confirm_close(self):return not self.dirty or messagebox.askokcancel('放弃模板草稿','有未保存的截图或模板名称修改，放弃？',parent=self.window)
    def open_selected(self):
        selected=self.table.selection()
        if selected:self.open_agent(selected[0])
    def open_agent(self,agent=None):
        if not self.confirm_close():return
        self.request('agent_template_open',self.opened,agent=agent)
    def opened(self,draft):
        self.capture_waiting=None;self.pending_import=None
        self.draft=draft;self.selected=0;self.name.set(draft['agent'] or '');self.name_entry.configure(state='disabled' if draft['agent'] else 'normal');self.dirty=False
        if not self.step_buttons:
            for i,step in enumerate(self.catalog['steps']):
                self.step_bar.columnconfigure(i%3,weight=1);button=ttk.Button(self.step_bar,text=step['name'],command=lambda i=i:self.choose_step(i));button.grid(row=i//3,column=i%3,sticky='ew',padx=2,pady=2);self.step_buttons.append(button)
        self.book.select(1);self.display(draft);self.toggle_float(save=False)
        if draft['agent'] is None:self.status.set('可选择已有角色或外观ID，也可输入自定义素材ID；保存素材不会新增引擎角色或技能。')
        elif not draft.get('registration',{}).get('registered',True):self.status.set('未注册素材：可编辑保存；不会新增引擎角色、状态或技能。')
    def changed_name(self):
        if self.draft is not None and self.draft['agent'] is None:self.dirty=bool(self.name.get() or self.draft['changed'])
    def choose_step(self,index):
        if self.draft is None or self.busy:return
        self.selected=index;self.side.set('右侧' if self.draft['sides'][index]=='right' else '左侧');self.hint.set(self.catalog['steps'][index]['hint'].replace('{side}',self.side.get()));self.draw();self.buttons();self.refresh_float()
        if index in self.draft['changed'] and self.draft['selected']!=index:self.request('agent_template_view',self.display,token=self.draft['token'],index=index)
    def display(self,draft):
        self.draft=draft;self.dirty=bool(draft['changed']);self.selected=draft['selected'];self.side.set('右侧' if draft['sides'][self.selected]=='right' else '左侧')
        self.progress.set(f"{self.name.get() or '新模板'} · {sum(bool(x) for x in draft['images'])}/6 · {self.catalog['steps'][self.selected]['name']}")
        self.hint.set(self.catalog['steps'][self.selected]['hint'].replace('{side}',self.side.get()));self.draw();self.buttons();self.refresh_float()
    def draw(self):
        if self.draft is None:return
        from PIL import Image,ImageTk
        self.preview.delete('all');encoded=self.draft.get('preview') if self.draft.get('selected')==self.selected else None
        if encoded:
            image=Image.open(BytesIO(base64.b64decode(encoded))).convert('RGB');image.thumbnail((max(1,self.preview.winfo_width()-12),max(1,self.preview.winfo_height()-12)))
            self.photos['preview']=ImageTk.PhotoImage(image,master=self.window);self.preview.create_image(self.preview.winfo_width()/2,self.preview.winfo_height()/2,image=self.photos['preview'])
        else:self.preview.create_text(12,18,anchor='nw',text='导入或截图后显示客户区与裁剪区域',fill=theme.MUTED)
        encoded=self.draft['masks' if self.mask.get() else 'images'][self.selected]
        if encoded:
            image=Image.open(BytesIO(base64.b64decode(encoded)));image.thumbnail((round(150*self.ui.ui_scale),round(180*self.ui.ui_scale)))
            self.photos['crop']=ImageTk.PhotoImage(image,master=self.window);self.crop.configure(image=self.photos['crop'],text='')
        else:self.crop.configure(image='',text='待截图')
        for i,button in enumerate(self.step_buttons):button.configure(text=('✓ ' if self.draft['images'][i] else '')+self.catalog['steps'][i]['name'])
    def import_png(self,path=None,index=None):
        if self.draft is None:return
        path=path or filedialog.askopenfilename(parent=self.window,title='游戏客户区截图',filetypes=[('PNG','*.png')])
        if path:self.request('agent_template_import',self.display,token=self.draft['token'],index=self.selected if index is None else index,path=str(path),side='right' if self.side.get()=='右侧' else 'left')
    def change_side(self):
        if self.draft:self.request('agent_template_side',self.display,token=self.draft['token'],side='right' if self.side.get()=='右侧' else 'left')
    def save(self):
        if self.draft is None:return
        def saved(value):
            self.dirty=False;self.display(value['draft']);self.name.set(value['draft']['agent']);self.name_entry.configure(state='disabled');self.reload()
            if value['draft'].get('registration',{}).get('registered',True):self.status.set('模板已保存并归档旧版本；重新准备后生效。')
            else:self.status.set('未注册素材已保存并归档旧版本；不会新增引擎角色、状态或技能。')
        self.request('agent_template_save',saved,token=self.draft['token'],agent=self.name.get().split(' · ',1)[0].strip())
    def delete(self):
        selected=self.table.selection()
        if not selected or not messagebox.askokcancel('删除模板','删除六类模板并归档，可在归档页恢复？',parent=self.window):return
        agent=selected[0]
        def ready(draft):self.request('agent_template_remove',lambda _:self.reload(),agent=agent,signature=draft['signature'])
        if self.confirm_close():self.dirty=False;self.draft=None;self.request('agent_template_open',ready,agent=agent)
    def restore(self):
        selected=self.archives.selection()
        if selected and self.confirm_close() and messagebox.askokcancel('恢复模板','恢复到该次归档前的版本？后续有修改时会拒绝覆盖。',parent=self.window):
            def restored(_):
                self.dirty=False;self.draft=None;self.book.select(0)
                if self.float:self.float.close();self.float=None
                self.reload()
            self.request('agent_template_restore',restored,identifier=selected[0])
    def recover(self):self.request('agent_template_recover',lambda _:self.reload())

    def capture(self,index=None):
        if self.draft is None or self.busy or self.closing:return
        if self.profile!=('global' if self.ui.server.get()=='国际服' else 'cn'):self.status.set('区服已变化，请关闭并重新打开模板窗口。');return
        if index is not None:self.choose_step(index)
        self.capture_waiting=self.selected;self.capture_deadline=time.monotonic()+30
        execution=getattr(self.ui,'execution_window',None)
        if execution is not None and not execution.closed and execution.last_state.get('busy'):
            if execution.profile!=self.profile:self.status.set('运行任务区服不同，请关闭模板窗口后重新打开。');self.capture_waiting=None;return
            from .onedragon_trace_ui import TraceWindow
            trace=execution.trace_window
            if trace is None or trace.closed:
                trace=TraceWindow(execution.window,self.ui.ui_scale,execution.session.trace);trace.window.withdraw();execution.trace_window=trace
            trace.update_state(execution.last_state)
            selected=self.selected;token=self.draft['token']
            def captured(value):
                if self.closed or self.closing or self.draft is None or self.draft['token']!=token or self.capture_waiting!=selected:return
                from .trace_capture import read_capture
                _,path=read_capture(trace.capture_root,value);self.capture_waiting=None;self.pending_import=(path,selected,token)
            trace.capture_once(captured)
            self.capture_source='running'
        else:self.capture_source='standalone'
        self.status.set('等待游戏前台截图（30秒）；F10取消，不会向游戏发送按键。')

    def poll_keys(self):
        import os
        from .windows import user32,find_game,W,C
        if self.preferences is None or self.draft is None:return
        if time.monotonic()>=self.next_game_probe:
            self.next_game_probe=time.monotonic()+.5
            try:self.game=find_game(self.ui.game_path.get())
            except Exception:self.game=None
        active=self.game is not None and user32.GetForegroundWindow()==self.game.hwnd
        editing_visible=self.book.index('current')==1 and bool(self.window.winfo_viewable())
        current_pid=W.DWORD();user32.GetWindowThreadProcessId(user32.GetForegroundWindow(),C.byref(current_pid))
        enabled=(active or current_pid.value==os.getpid()) and (editing_visible or self.float is not None)
        for action in self.hotkeys.poll(enabled):
            if action in ACTIONS[:6]:self.capture(ACTIONS.index(action))
            elif action=='switch':self.switch_window()
            elif action=='expand':self.toggle_expand()
        if self.capture_waiting is not None:
            if user32.GetAsyncKeyState(0x79)&0x8000:self.capture_waiting=None;self.status.set('截图已取消。')
            elif time.monotonic()>self.capture_deadline:self.capture_waiting=None;self.status.set('截图等待超时；可回到游戏后重新请求。')
            elif active and self.capture_source=='standalone' and not self.busy:
                selected=self.capture_waiting;self.capture_waiting=None
                self.request('agent_template_capture',lambda value:self.display(value['draft']),token=self.draft['token'],index=selected,
                    game_path=self.ui.game_path.get(),side='right' if self.side.get()=='右侧' else 'left')

    def switch_window(self):
        from .windows import user32,find_game
        try:game=find_game(self.ui.game_path.get())
        except Exception as exc:self.status.set(str(exc));return
        if user32.GetForegroundWindow()==game.hwnd:self.window.deiconify();self.window.lift();self.window.focus_force()
        else:
            # Only this explicit button/hotkey asks Windows to switch the selected game to front.
            if not user32.SetForegroundWindow(game.hwnd):self.status.set('Windows未允许切换，请手动切到游戏。')

    def edit_hotkeys(self):
        if self.preferences is None:return
        dialog=tk.Toplevel(self.window);dialog.title('模板快捷键');dialog.transient(self.window)
        body=ttk.Frame(dialog,padding=16);body.pack(fill='both',expand=True);body.columnconfigure(1,weight=1)
        ttk.Label(body,text='支持 Win、F1—F24、numpad_0—9、Home 等命名键、vk_数字。留空禁用；F9/F10保留。',wraplength=470).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,12))
        labels=[s['name'] for s in self.catalog['steps']]+['切换窗口','展开/收起悬浮窗'];variables={}
        for i,(key,label) in enumerate(zip(ACTIONS,labels),1):
            ttk.Label(body,text=label).grid(row=i,column=0,sticky='w',padx=(0,14),pady=4)
            var=tk.StringVar(dialog,self.preferences['values']['hotkeys'][key]);variables[key]=var;ttk.Entry(body,textvariable=var,width=25).grid(row=i,column=1,sticky='ew')
        modifier=tk.StringVar(dialog,'Ctrl');ttk.Combobox(body,textvariable=modifier,values=('Ctrl','Alt','Shift','Ctrl+Shift','Ctrl+Alt','Win','Ctrl+Win'),state='readonly',width=12).grid(row=9,column=0,sticky='w',pady=10)
        def batch():
            for i,key in enumerate(ACTIONS[:6]):variables[key].set(modifier.get()+'+'+str(i+1))
        ttk.Button(body,text='批量设置六个截图键',command=batch).grid(row=9,column=1,sticky='e')
        def save():
            try:values=validate_bindings({key:var.get() for key,var in variables.items()})
            except ValueError as exc:messagebox.showerror('快捷键',str(exc),parent=dialog);return
            if self.busy:return
            self.preferences['values']['hotkeys']=values;self.save_preferences();dialog.destroy()
        ttk.Button(body,text='保存',command=save).grid(row=10,column=1,sticky='e');dialog.grab_set()

    def toggle_expand(self):
        if self.preferences is None or self.busy:return
        self.preferences['values']['float_expanded']=not self.preferences['values']['float_expanded'];self.refresh_float();self.save_preferences()
    def toggle_float(self,save=True):
        if self.preferences is None:return
        if save and self.busy:self.float_enabled.set(self.preferences['values']['float_enabled']);return
        enabled=self.float_enabled.get() and self.draft is not None
        self.preferences['values']['float_enabled']=self.float_enabled.get()
        if enabled and self.float is None:
            try:self.float=TemplateFloat(self)
            except Exception as exc:self.status.set('悬浮操作不可用：'+str(exc));self.float=None
        elif not enabled and self.float is not None:self.float.close();self.float=None
        self.refresh_float()
        if save:self.save_preferences()
    def refresh_float(self):
        if self.float is not None:self.float.refresh()
    def close(self,confirmed=False):
        if self.closing:return True
        if not confirmed and not self.confirm_close():return False
        self.closing=True;self.capture_waiting=None;self.hotkeys.close()
        if self.float is not None:self.float.close();self.float=None
        self.status.set('等待原模板服务退出…');self.session.close();self.buttons();return True


def open_agent_templates(ui):
    previous=getattr(ui,'agent_templates_window',None)
    if previous is not None and not previous.closed:
        previous.window.deiconify();previous.window.lift();return previous
    profile='global' if ui.server.get()=='国际服' else 'cn'
    ui.agent_templates_window=AgentTemplatesWindow(ui,profile)
    return ui.agent_templates_window
