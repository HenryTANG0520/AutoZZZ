"""Local appearance editor and main-window-only background presentation."""
from __future__ import annotations

from copy import deepcopy
import queue
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from .appearance_preferences import AppearancePreferences, DEFAULTS
from .appearance_media import BackgroundPlayer, download_official, import_media, managed_path
from . import theme
from .localization import tr
from .localization_tk import PresentationVar

THEMES={'dark':'深色','light':'浅色','system':'跟随系统'}
LANGUAGES={'zh':'简体中文','en':'English','auto':'跟随系统'}
BACKGROUNDS={'none':'无背景','version_poster':'版本海报','static':'官方静态背景','dynamic':'官方动态背景','custom':'自定义图片或视频'}


class AppearanceController:
    def __init__(self,ui,store=None):
        self.ui=ui;self.root=ui.root;self.store=store or AppearancePreferences()
        self.closed=False;self.player=None;self._job=None;self._last_frame=-1;self._reported=None
        self.ui.table.texture_provider=self.frame
        try:self.value=self.store.load()
        except (ValueError,OSError,TypeError) as exc:
            self.value=deepcopy(DEFAULTS);ui._log('外观设置无法读取：'+str(exc),'warning')
        try:self.apply(self.value)
        except (ValueError,OSError,TypeError) as exc:
            ui._log('背景暂不可用：'+str(exc),'warning')
            self.value['background']='none'
        self._job=self.root.after(500,self.poll)
        self.root.bind('<Destroy>',self._destroyed,add='+')

    def _destroyed(self,event):
        if event.widget is self.root:self.close()

    @property
    def worker(self):return self.player.worker if self.player is not None else None

    def frame(self):return self.player.frame() if self.player is not None else None

    def apply(self,value):
        mode=value['background'];asset=value['assets'].get(mode)
        if mode!='none' and not asset:
            raise ValueError('请先下载或选择此背景。')
        path=managed_path(self.store,asset) if mode!='none' else None
        self.value=deepcopy(value)
        if path is not None and self.player is None:self.player=BackgroundPlayer()
        if self.player is not None:
            self.player.load(path,asset['kind'] if asset else None)
            self._last_frame=-1;self._reported=None
        self.visibility();self.ui.table.redraw()

    def visibility(self):
        if self.player is not None:
            self.player.visible(not self.closed and self.root.state()!='iconic' and getattr(self.ui,'current_page','home')=='home')

    def poll(self):
        self._job=None
        if self.closed:return
        self.visibility()
        if self.player is not None:
            if self.player.frame_number!=self._last_frame:
                self._last_frame=self.player.frame_number
                if getattr(self.ui,'current_page','home')=='home':self.ui.table.redraw()
            if self.player.error and self.player.error!=self._reported:
                self._reported=self.player.error;self.ui._log('背景：'+self.player.error,'warning')
        self._job=self.root.after(500,self.poll)

    def close(self):
        if self.closed:return
        self.closed=True
        if self._job is not None:
            try:self.root.after_cancel(self._job)
            except tk.TclError:pass
            self._job=None
        if self.player is not None:self.player.close()
        self.ui.table.texture_provider=None


class AppearanceWindow:
    def __init__(self,ui,store=None):
        self.ui=ui;self.store=store or AppearancePreferences();self.value=self.store.load()
        self.closed=False;self.worker=None;self.cancel=threading.Event();self.events=queue.Queue();self.job=None
        self.root=tk.Toplevel(ui.root);self.root.title('外观');self.root.protocol('WM_DELETE_WINDOW',self.close)
        scale=getattr(ui,'ui_scale',1);d=lambda n:round(n*scale)
        self.root.geometry(f'{d(660)}x{d(560)}');self.root.minsize(d(600),d(520))
        self.root.configure(bg=theme.PANEL)
        self.theme_labels={key:tr(label) for key,label in THEMES.items()}
        self.language_labels={key:tr(label) for key,label in LANGUAGES.items()}
        self.background_labels={key:tr(label) for key,label in BACKGROUNDS.items()}
        form=ttk.Frame(self.root,padding=d(22));form.pack(fill='both',expand=True);form.columnconfigure(1,weight=1)
        self.theme=tk.StringVar(self.root,self.theme_labels[self.value['theme']]);self.accent=tk.StringVar(self.root,self.value['accent'])
        self.language=tk.StringVar(self.root,self.language_labels[self.value['language']]);self.background=tk.StringVar(self.root,self.background_labels[self.value['background']])
        self.status=PresentationVar(self.root,'主题、主题色与语言在下次启动时生效。')
        self.asset_status=PresentationVar(self.root)
        ttk.Label(form,text='外观',style='PageTitle.TLabel').grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,d(18)))
        for row,(label,variable,values) in enumerate((('主题',self.theme,self.theme_labels.values()),('界面语言',self.language,self.language_labels.values())),1):
            ttk.Label(form,text=label,style='Config.TLabel').grid(row=row,column=0,sticky='w',padx=(0,d(22)),pady=d(8))
            ttk.Combobox(form,textvariable=variable,values=list(values),state='readonly').grid(row=row,column=1,sticky='ew',pady=d(8))
        ttk.Label(form,text='主题色',style='Config.TLabel').grid(row=3,column=0,sticky='w',pady=d(8))
        colors=ttk.Frame(form);colors.grid(row=3,column=1,sticky='ew');colors.columnconfigure(0,weight=1)
        ttk.Entry(colors,textvariable=self.accent,width=13).grid(row=0,column=0,sticky='ew',padx=(0,d(8)))
        ttk.Button(colors,text='选择颜色…',command=self.choose_color).grid(row=0,column=1)
        ttk.Label(form,text='#RRGGBB；文字对比度会自动调整。',style='Hint.TLabel').grid(row=4,column=1,sticky='w')
        ttk.Separator(form).grid(row=5,column=0,columnspan=2,sticky='ew',pady=d(16))
        ttk.Label(form,text='首页背景',style='Config.TLabel').grid(row=6,column=0,sticky='w',pady=d(8))
        combo=ttk.Combobox(form,textvariable=self.background,values=list(self.background_labels.values()),state='readonly');combo.grid(row=6,column=1,sticky='ew')
        combo.bind('<<ComboboxSelected>>',lambda _:self.refresh_asset())
        actions=ttk.Frame(form);actions.grid(row=7,column=0,columnspan=2,sticky='w',pady=d(10))
        self.choose_button=ttk.Button(actions,text='选择文件…',command=self.choose_file);self.choose_button.grid(row=0,column=0,padx=(0,d(8)))
        self.download_button=ttk.Button(actions,text='获取官方背景',command=self.download);self.download_button.grid(row=0,column=1)
        self.cancel_button=ttk.Button(actions,text='取消',command=self.cancel.set);self.cancel_button.grid(row=0,column=2,padx=(d(8),0))
        theme.wrapped_label(form,textvariable=self.asset_status,style='Hint.TLabel').grid(row=8,column=0,columnspan=2,sticky='ew',pady=d(5))
        theme.wrapped_label(form,textvariable=self.status,style='Hint.TLabel').grid(row=9,column=0,columnspan=2,sticky='ew',pady=d(12))
        form.rowconfigure(10,weight=1)
        self.save_button=ttk.Button(form,text='保存外观',style='Accent.TButton',command=self.save);self.save_button.grid(row=11,column=1,sticky='e',pady=(d(8),0))
        self.refresh_asset();self.job=self.root.after(100,self.poll)

    def _mode(self):return next(key for key,value in self.background_labels.items() if value==self.background.get())

    def refresh_asset(self):
        busy=self.worker is not None and self.worker.is_alive();mode=self._mode()
        self.choose_button.configure(state='disabled' if busy or mode!='custom' else 'normal')
        self.download_button.configure(state='disabled' if busy or mode not in ('version_poster','static','dynamic') else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled');self.save_button.configure(state='disabled' if busy else 'normal')
        asset=self.value['assets'].get(mode)
        self.asset_status.set('无背景。' if mode=='none' else ('视频 · 静音循环，离开首页后暂停。' if asset and asset['kind']=='video' else '图片已就绪。' if asset else '尚未准备背景。图片 ≤20 MB，视频 ≤160 MB。'))

    def choose_color(self):
        result=colorchooser.askcolor(self.accent.get(),parent=self.root)
        if result[1]:self.accent.set(result[1])

    def start(self,mode,operation):
        if self.worker is not None and self.worker.is_alive():return
        self.cancel.clear();self.status.set('正在准备背景…')
        def work():
            try:self.events.put(('ok',mode,operation()))
            except Exception as exc:self.events.put(('error',str(exc)))
        self.worker=threading.Thread(target=work,name='AutoZZZ-background-prepare',daemon=False);self.worker.start();self.refresh_asset()

    def choose_file(self):
        path=filedialog.askopenfilename(parent=self.root,title='选择背景图片或视频',filetypes=[('图片和视频','*.png *.jpg *.jpeg *.webp *.gif *.bmp *.mp4 *.webm *.avi *.mov *.mkv')])
        if path:self.start('custom',lambda:import_media(path,self.store,self.cancel))

    def download(self):
        mode=self._mode();self.start(mode,lambda:download_official(mode,self.store,self.cancel))

    def poll(self):
        self.job=None
        if self.closed:return
        while not self.events.empty():
            event=self.events.get_nowait()
            if event[0]=='ok':self.value['assets'][event[1]]=event[2];self.status.set('背景已准备；保存后应用到首页。')
            else:self.status.set(event[1])
        self.refresh_asset();self.job=self.root.after(100,self.poll)

    def save(self):
        try:
            value=deepcopy(self.value)
            value.update(theme=next(key for key,label in self.theme_labels.items() if label==self.theme.get()),
                         language=next(key for key,label in self.language_labels.items() if label==self.language.get()),
                         accent=self.accent.get(),background=self._mode())
            asset=value['assets'].get(value['background'])
            if value['background']!='none' and not asset:raise ValueError('请先准备所选背景。')
            if asset:managed_path(self.store,asset)
            self.store.save(value)
            self.ui.appearance_controller.apply(value)
            self.value=value;self.status.set('背景已应用；主题、主题色与语言在下次启动时生效。')
        except (ValueError,OSError,TypeError) as exc:messagebox.showerror('无法保存外观',str(exc),parent=self.root)

    def close(self):
        if self.closed:return
        self.closed=True;self.cancel.set()
        if self.job is not None:self.root.after_cancel(self.job);self.job=None
        self.root.destroy()


def open_appearance(ui):
    previous=getattr(ui,'appearance_window',None)
    if previous is not None and not previous.closed:previous.root.lift();return previous
    try:ui.appearance_window=AppearanceWindow(ui)
    except (ValueError,OSError,TypeError) as exc:
        messagebox.showerror('无法读取外观设置',str(exc),parent=ui.root);return None
    return ui.appearance_window
