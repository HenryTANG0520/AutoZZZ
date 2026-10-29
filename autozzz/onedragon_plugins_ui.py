"""原生插件管理；异步导入与关闭始终等待原 SDK 作业。"""
from __future__ import annotations

from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from urllib.parse import urlsplit
import webbrowser

from . import theme
from .config import ROOT
from .onedragon_runtime import OneDragonClient,RuntimePaths,RequestPending

MARKET='https://onedragon-anyone.github.io/plugin-registry/'
GUIDE='https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/docs/develop/guides/application_plugin_guide.md'


def scroll_table(parent,**options):
    frame=ttk.Frame(parent);frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
    table=ttk.Treeview(frame,**options);table.grid(row=0,column=0,sticky='nsew')
    vertical=ttk.Scrollbar(frame,command=table.yview);vertical.grid(row=0,column=1,sticky='ns')
    horizontal=ttk.Scrollbar(frame,orient='horizontal',command=table.xview);horizontal.grid(row=1,column=0,sticky='ew')
    table.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
    return frame,table


class PluginSession:
    def __init__(self,paths,emit):
        self.paths,self.emit=paths,emit;self.commands=queue.Queue();self.closing=threading.Event();self.client=None
        self.worker=threading.Thread(target=self.work,name='AutoZZZ-Plugins',daemon=False);self.worker.start()
    def send(self,method,**params):
        if not self.closing.is_set():self.commands.put((method,params))
    def close(self):self.closing.set()
    @staticmethod
    def call(client,method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=.5)
            except RequestPending:continue
    def work(self):
        client=OneDragonClient(self.paths);self.client=client
        try:
            try:client.start()
            except RequestPending:
                while True:
                    try:client.finish_start(timeout=.5);break
                    except RequestPending:continue
            if self.closing.is_set():return
            self.emit(dict(type='result',method='plugin_catalog',result=self.call(client,'plugin_catalog')))
            while not self.closing.is_set():
                try:method,params=self.commands.get(timeout=.2)
                except queue.Empty:continue
                if self.closing.is_set():break
                try:
                    result=self.call(client,method,**params)
                    if method in ('plugin_preview','plugin_install','plugin_remove','plugin_restore'):
                        job=result['job_id']
                        while True:
                            try:state=self.call(client,'plugin_status')
                            except Exception as exc:
                                if not client.alive or not client.protocol_available:raise
                                self.emit(dict(type='query_error',message=str(exc)));time.sleep(.1);continue
                            if state['job_id']!=job:raise RuntimeError('插件作业编号已变化，请检查原工作进程。')
                            if not state['busy']:
                                if state['state']=='failed':raise ValueError(state['error'])
                                result=state['result'];break
                            self.emit(dict(type='phase',state=state['state']));time.sleep(.1)
                    self.emit(dict(type='result',method=method,result=result))
                except Exception as exc:
                    self.emit(dict(type='error',message=str(exc),fatal=not client.alive or not client.protocol_available))
                    if not client.alive or not client.protocol_available:raise
        except Exception as exc:self.emit(dict(type='error',message=str(exc),fatal=True))
        finally:
            while True:
                try:client.close(timeout=5);break
                except RequestPending:continue
                except Exception as exc:self.emit(dict(type='query_error',message='等待原进程退出：'+str(exc)));time.sleep(.2)
            self.emit(dict(type='closed'))


class ArchiveWindow:
    def __init__(self,owner,rows):
        self.owner=owner;self.rows={row['id']:row for row in rows};self.window=tk.Toplevel(owner.window);self.window.title('AutoZZZ · 插件归档');self.window.transient(owner.window)
        d=owner.d;self.window.geometry(f'{d(740)}x{d(520)}');self.window.minsize(d(600),d(420));self.window.columnconfigure(0,weight=1);self.window.rowconfigure(0,weight=1);self.window.rowconfigure(1,weight=1)
        frame,self.archives=scroll_table(self.window,columns=('date','action','names'),show='headings',style='Config.Treeview',height=5,selectmode='browse');frame.grid(row=0,column=0,sticky='nsew',padx=d(14),pady=d(14))
        for key,label,width in (('date','时间',210),('action','操作',110),('names','插件',320)):self.archives.heading(key,text=label);self.archives.column(key,width=d(width))
        for identifier,row in self.rows.items():self.archives.insert('','end',iid=identifier,values=(row.get('created',''),{'remove':'移除','overwrite':'覆盖','restore-overwrite':'恢复前覆盖'}.get(row['action'],row['action']),'、'.join(item['name'] for item in row['plugins'])))
        frame,self.packages=scroll_table(self.window,columns=('name','current'),show='headings',style='Config.Treeview',height=5);frame.grid(row=1,column=0,sticky='nsew',padx=d(14))
        self.packages.heading('name',text='插件');self.packages.heading('current',text='当前状态')
        self.overwrite=tk.BooleanVar(self.window,False);bar=ttk.Frame(self.window,padding=d(14));bar.grid(row=2,column=0,sticky='ew');bar.columnconfigure(0,weight=1)
        ttk.Checkbutton(bar,text='允许覆盖所选同名插件',variable=self.overwrite).grid(row=0,column=0,sticky='w')
        ttk.Button(bar,text='恢复所选',command=self.restore).grid(row=0,column=1,padx=d(8));ttk.Button(bar,text='关闭',command=self.window.destroy).grid(row=0,column=2)
        self.archives.bind('<<TreeviewSelect>>',lambda _:self.select())
        if rows:self.archives.selection_set(rows[-1]['id']);self.select()
    def select(self):
        self.packages.delete(*self.packages.get_children());selection=self.archives.selection()
        if not selection:return
        current={row['name'].casefold() for row in self.owner.catalog['plugins']}
        for row in self.rows[selection[0]]['plugins']:self.packages.insert('','end',iid=row['name'],values=(row['name'],'将覆盖' if row['name'].casefold()in current else '未安装'))
        self.packages.selection_set(self.packages.get_children())
    def restore(self):
        archives=self.archives.selection();names=list(self.packages.selection())
        if not archives or not names:return
        current={row['name'].casefold() for row in self.owner.catalog['plugins']};existing=[name for name in names if name.casefold()in current]
        if existing and not self.overwrite.get():messagebox.showerror('需要覆盖确认','勾选允许覆盖，或取消选择当前已安装的插件。',parent=self.window);return
        self.owner.send('plugin_restore',identifier=archives[0],names=names,revision=self.owner.catalog['revision'],overwrites=existing)
        self.window.destroy()


class PluginWindow:
    def __init__(self,ui,profile):
        self.ui,self.profile=ui,profile;self.closed=self.closing=self.busy=False;self.connected=False;self.generation=0
        self.catalog=dict(plugins=[],revision='',directory='');self.review=None;self.events=queue.Queue();self.token=None;self.archive_window=None
        self.window=tk.Toplevel(ui.root);self.window.title('AutoZZZ · 插件');self.window.transient(ui.root);self.window.configure(background=theme.PANEL)
        self.d=lambda value:round(value*ui.ui_scale);d=self.d
        width,height=min(d(980),ui.root.winfo_screenwidth()-d(50)),min(d(740),ui.root.winfo_screenheight()-d(90))
        self.window.geometry(f'{width}x{height}');self.window.minsize(min(d(760),width),min(d(590),height));self.window.columnconfigure(0,weight=1);self.window.rowconfigure(2,weight=1)
        header=ttk.Frame(self.window,padding=d(16));header.grid(row=0,column=0,sticky='ew');header.columnconfigure(0,weight=1)
        ttk.Label(header,text='插件',style='PageTitle.TLabel').grid(row=0,column=0,sticky='w')
        self.server=tk.StringVar(self.window,'国服' if profile=='cn' else '国际服');self.profile_box=ttk.Combobox(header,textvariable=self.server,values=('国服','国际服'),width=8,state='readonly');self.profile_box.grid(row=0,column=1)
        self.profile_box.bind('<<ComboboxSelected>>',self.change_profile)
        self.refresh_button=ttk.Button(header,text='刷新',command=self.refresh);self.refresh_button.grid(row=0,column=2,padx=(d(10),0))
        actions=ttk.Frame(self.window,padding=(d(16),0,d(16),d(12)));actions.grid(row=1,column=0,sticky='ew')
        self.controls=[]
        for text,command in (('导入 ZIP…',lambda:self.choose('zip')),('导入文件夹…',lambda:self.choose('directory')),('打开目录',lambda:self.send('plugin_directory')),('插件市场',lambda:self.url(MARKET)),('开发说明',lambda:self.url(GUIDE))):
            button=ttk.Button(actions,text=text,command=command);button.pack(side='left',padx=(0,d(8)));self.controls.append(button)
        self.book=ttk.Notebook(self.window);self.book.grid(row=2,column=0,sticky='nsew',padx=d(16));installed=ttk.Frame(self.book);installed.columnconfigure(0,weight=1);installed.rowconfigure(0,weight=1);self.book.add(installed,text='已安装')
        frame,self.table=scroll_table(installed,columns=('name','version','author','state'),show='headings',style='Config.Treeview',selectmode='browse');frame.grid(row=0,column=0,sticky='nsew')
        for key,label,size in (('name','插件',300),('version','版本',110),('author','作者',160),('state','加载',130)):self.table.heading(key,text=label);self.table.column(key,width=d(size),minwidth=d(70))
        self.detail=tk.StringVar(self.window);theme.wrapped_label(installed,textvariable=self.detail,style='Hint.TLabel').grid(row=1,column=0,columnspan=2,sticky='ew',pady=d(10))
        self.accept=tk.BooleanVar(self.window,False);self.accept_check=ttk.Checkbutton(installed,text='允许加载所选插件的 Python 代码（具有助手权限）',variable=self.accept,command=self.buttons);self.accept_check.grid(row=2,column=0,columnspan=2,sticky='w')
        operations=ttk.Frame(installed,padding=(0,d(10),0,0));operations.grid(row=3,column=0,columnspan=2,sticky='ew')
        self.enable_button=ttk.Button(operations,text='启用加载',command=self.enable);self.enable_button.pack(side='left',padx=(0,d(8)))
        self.remove_button=ttk.Button(operations,text='移除…',command=self.remove);self.remove_button.pack(side='left',padx=(0,d(8)))
        self.archive_button=ttk.Button(operations,text='归档与恢复…',command=lambda:self.send('plugin_archives'));self.archive_button.pack(side='left',padx=(0,d(8)))
        self.home_button=ttk.Button(operations,text='项目主页',command=lambda:self.url((self.selected() or {}).get('homepage','')));self.home_button.pack(side='left')
        self.table.bind('<<TreeviewSelect>>',lambda _:self.select())
        self.import_page=ttk.Frame(self.book);self.import_page.columnconfigure(0,weight=1);self.import_page.rowconfigure(1,weight=1);self.book.add(self.import_page,text='导入预览')
        self.source=tk.StringVar(self.window,'选择 ZIP 或文件夹');theme.wrapped_label(self.import_page,textvariable=self.source,style='Hint.TLabel').grid(row=0,column=0,sticky='ew',pady=d(10))
        frame,self.previews=scroll_table(self.import_page,columns=('name','new','old','author'),show='headings',style='Config.Treeview');frame.grid(row=1,column=0,sticky='nsew')
        for key,label,size in (('name','插件',290),('new','新版本',120),('old','当前版本',120),('author','作者',170)):self.previews.heading(key,text=label);self.previews.column(key,width=d(size),minwidth=d(65))
        self.previews.tag_configure('downgrade',foreground=theme.ERROR)
        self.overwrite=tk.BooleanVar(self.window,False);self.downgrade=tk.BooleanVar(self.window,False)
        self.overwrite_check=ttk.Checkbutton(self.import_page,text='覆盖所选同名插件（旧版保留归档）',variable=self.overwrite,command=self.buttons);self.overwrite_check.grid(row=2,column=0,sticky='w',pady=(d(10),0))
        self.downgrade_check=ttk.Checkbutton(self.import_page,text='允许所选插件降级',variable=self.downgrade,command=self.buttons);self.downgrade_check.grid(row=3,column=0,sticky='w',pady=(d(8),0))
        self.install_button=ttk.Button(self.import_page,text='安装所选',command=self.install,style='Accent.TButton');self.install_button.grid(row=4,column=0,sticky='e',pady=d(10));self.previews.bind('<<TreeviewSelect>>',lambda _:self.buttons())
        footer=ttk.Frame(self.window,padding=d(16));footer.grid(row=3,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        self.status=tk.StringVar(self.window,'正在连接…');theme.wrapped_label(footer,textvariable=self.status,style='Hint.TLabel').grid(row=0,column=0,columnspan=2,sticky='ew')
        ttk.Label(footer,text='安装后默认不加载；启用后重新读取应用。',style='Hint.TLabel').grid(row=1,column=0,sticky='w',pady=(d(10),0))
        self.close_button=ttk.Button(footer,text='关闭',command=self.close);self.close_button.grid(row=1,column=1,sticky='e',pady=(d(10),0))
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.connect();self.poll()

    def connect(self):
        self.generation+=1;generation=self.generation
        self.session=PluginSession(RuntimePaths.discover(ROOT,self.profile),lambda event:self.events.put(dict(event,generation=generation)))
        self.worker=self.session.worker;self.buttons()
    def selected(self):return next((row for row in self.catalog['plugins'] if row['name']in self.table.selection()),None)
    def select(self):
        self.accept.set(False);row=self.selected();self.detail.set(' · '.join(str(row.get(key,'')) for key in ('app_id','description','error') if row.get(key)) if row else '');self.buttons()
    def buttons(self):
        ready=self.connected and not self.busy and not self.closing and not getattr(self,'switching',False);row=self.selected();active=bool(row and row['enabled'])
        for widget in self.controls+[self.archive_button,self.accept_check,self.overwrite_check,self.downgrade_check]:widget.state(['!disabled'] if ready else ['disabled'])
        self.refresh_button.state(['!disabled'] if not self.busy and not self.closing else ['disabled']);self.profile_box.state(['!disabled'] if not self.busy and not self.closing else ['disabled'])
        self.enable_button.configure(text='停用加载' if active else '启用加载');self.enable_button.state(['!disabled'] if ready and row and (active or self.accept.get()) and not row.get('error') else ['disabled'])
        self.remove_button.state(['!disabled'] if ready and row and row.get('path') else ['disabled']);self.home_button.state(['!disabled'] if ready and row and row.get('homepage') else ['disabled'])
        selected=set(self.previews.selection());rows=[row for row in (self.review or {}).get('plugins',[]) if row['name']in selected]
        valid=bool(rows) and (self.overwrite.get() or not any(row['exists'] for row in rows)) and (self.downgrade.get() or not any(row['downgrade'] for row in rows))
        self.install_button.state(['!disabled'] if ready and valid else ['disabled'])
    def send(self,method,**params):
        if self.busy or self.closing or not self.connected or getattr(self,'switching',False):return
        self.busy=True;self.status.set('正在处理…');self.buttons();self.session.send(method,**params)
    def refresh(self):
        if self.closing or self.busy:return
        if self.worker.is_alive():self.send('plugin_catalog')
        else:self.connected=False;self.connect()
    def change_profile(self,*_):
        if self.busy:return
        self.next_profile='cn' if self.server.get()=='国服' else 'global'
        if self.next_profile==self.profile:return
        self.busy=True;self.connected=False;self.switching=True;self.status.set('等待原连接退出…');self.session.close();self.buttons()
    def choose(self,kind):
        path=filedialog.askopenfilename(parent=self.window,filetypes=[('插件 ZIP','*.zip')]) if kind=='zip' else filedialog.askdirectory(parent=self.window)
        if not path:return
        self.review=None;self.overwrite.set(False);self.downgrade.set(False);self.previews.delete(*self.previews.get_children());self.book.select(self.import_page);self.source.set(str(path));self.send('plugin_preview',path=path,kind=kind)
    def install(self):
        if self.review is None or self.install_button.instate(['disabled']):return
        names=list(self.previews.selection());overwrites=[row['name'] for row in self.review['plugins'] if row['exists'] and row['name']in names]
        self.send('plugin_install',token=self.review['token'],names=names,overwrites=overwrites,revision=self.review['revision'],allow_downgrade=self.downgrade.get())
    def enable(self):
        row=self.selected()
        if row and not self.enable_button.instate(['disabled']):self.send('plugin_enabled',name=row['name'],enabled=not row['enabled'],revision=self.catalog['revision'])
    def remove(self):
        row=self.selected()
        if row and messagebox.askyesno('移除插件',f"移除 {row['app_name']}？旧文件保留归档，账号配置保留。",parent=self.window):self.send('plugin_remove',name=row['name'],revision=self.catalog['revision'])
    def url(self,url):
        if urlsplit(url).scheme not in ('https','http'):self.status.set('主页须为 HTTP 或 HTTPS 链接。');return
        webbrowser.open(url)
    def show_catalog(self,data):
        self.catalog=data;selection=self.table.selection();self.table.delete(*self.table.get_children())
        for row in data['plugins']:self.table.insert('','end',iid=row['name'],values=(row['app_name'],row['version'],row['author'],'已启用' if row['enabled'] else '未加载'))
        if selection and self.table.exists(selection[0]):self.table.selection_set(selection)
        self.select()
    def poll(self):
        if self.closed:return
        try:
            while True:
                event=self.events.get_nowait()
                if event['generation']!=self.generation:continue
                kind=event['type']
                if kind=='result':
                    self.busy=False;self.connected=True;method=event['method'];result=event['result']
                    if method in ('plugin_catalog','plugin_enabled'):self.show_catalog(result);self.status.set(f"{len(result['plugins'])} 个插件")
                    elif method=='plugin_preview':
                        self.review=result;self.previews.delete(*self.previews.get_children())
                        for row in result['plugins']:self.previews.insert('','end',iid=row['name'],values=(row['name'],row['version'],row['old_version'],row['author']),tags=('downgrade',) if row['downgrade'] else ())
                        self.previews.selection_set(self.previews.get_children());self.status.set('预览完成；选择需要安装的插件。')
                    elif method=='plugin_archives':self.archive_window=ArchiveWindow(self,result)
                    elif method=='plugin_directory':self.ui._open_path(Path(result));self.status.set(str(result))
                    else:
                        self.show_catalog(result['catalog']);self.review=None;self.previews.delete(*self.previews.get_children());self.book.select(0)
                        self.status.set('；'.join(row['name']+'：'+row['message'] for row in result['results']))
                elif kind=='phase':self.status.set({'previewing':'正在预览','installing':'正在安装','removing':'正在移除','restoring':'正在恢复'}.get(event['state'],event['state']))
                elif kind=='query_error':self.status.set('继续等待原插件作业：'+event['message'][:100])
                elif kind=='error':
                    self.busy=False
                    if event.get('fatal'):self.connected=False
                    self.status.set(event['message'][:220])
                elif kind=='closed':self.connected=False
                self.buttons()
        except queue.Empty:pass
        if not self.worker.is_alive():
            if self.closing:self.closed=True;self.window.destroy();return
            if getattr(self,'next_profile',self.profile)!=self.profile:
                self.profile=self.next_profile;self.busy=False;self.switching=False;self.review=None;self.show_catalog(dict(plugins=[],revision='',directory=''));self.previews.delete(*self.previews.get_children());self.connect()
        self.token=self.window.after(80,self.poll)
    def close(self):
        if self.closing:return
        self.closing=True;self.status.set('等待原插件操作和工作进程退出…');self.session.close();self.buttons()


def open_plugins(ui,profile):
    existing=getattr(ui,'plugin_window',None)
    if existing is not None and not existing.closed:existing.window.deiconify();existing.window.lift();return existing
    ui.plugin_window=PluginWindow(ui,profile);return ui.plugin_window
