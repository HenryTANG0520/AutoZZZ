"""AutoZZZ 原生画中画；后台显示不激活窗口，点击才请求切回游戏。"""
from __future__ import annotations

import base64
from io import BytesIO
import queue
import tkinter as tk

from PIL import Image,ImageTk

from .config import ROOT,GAME_PATHS
from .onedragon_runtime import RuntimePaths
from .onedragon_pip_session import PipSession
from .overlay import NativePresentation
from .windows import W,user32


class PipPresentation(NativePresentation):
    def prepare(self,window):
        handle=self.user32.GetAncestor(window.winfo_id(),2)
        style=(self.get_style(handle,-20)|0x80000|0x80|0x8000000)&~0x20
        self.set_style(handle,-20,style)
        if self.get_style(handle,-20)&0x8080080!=0x8080080:raise OSError('画中画无法启用禁止激活样式。')
        self.user32.SetLayeredWindowAttributes.argtypes=[W.HWND,W.DWORD,W.BYTE,W.DWORD]
        self.user32.SetLayeredWindowAttributes.restype=W.BOOL
        if not self.user32.SetLayeredWindowAttributes(handle,0,255,2):raise OSError('画中画无法初始化图层透明度。')
        if self.protect_capture and not self.user32.SetWindowDisplayAffinity(handle,0x11):raise OSError('画中画无法从桌面截图中排除。')
        return handle


class PipView:
    def __init__(self,root,scale,activate,dismiss,save,*,presentation=None):
        self.scale=scale;self.activate=activate;self.dismiss=dismiss;self.save=save
        self.window=tk.Toplevel(root,takefocus=False);self.window.withdraw();self.window.overrideredirect(True)
        self.window.title('AutoZZZ · 画中画');self.window.attributes('-topmost',True)
        self.canvas=tk.Canvas(self.window,highlightthickness=1,highlightbackground='#777',background='#111',takefocus=False)
        self.canvas.pack(fill='both',expand=True)
        self.window.update_idletasks();self.presentation=presentation or PipPresentation();self.handle=self.presentation.prepare(self.window)
        self.image=None;self.photo=None;self.visible=False;self.bounds=None;self.aspect=9/16;self.hover=False;self.press=None;self.game_hwnd=None;self.frame_identity=None
        self.width=round(480*scale);self.x=self.y=-1;self.configured=False;self.placed=False
        self.canvas.bind('<ButtonPress-1>',self.down);self.canvas.bind('<B1-Motion>',self.motion);self.canvas.bind('<ButtonRelease-1>',self.up)
        self.canvas.bind('<ButtonRelease-3>',lambda _:self.hide_by_user());self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<Enter>',lambda _:self.highlight(True));self.canvas.bind('<Leave>',lambda _:self.highlight(False))
        self.canvas.bind('<Motion>',self.cursor)
        self.window.protocol('WM_DELETE_WINDOW',self.hide_by_user)

    def settings(self,values):
        if self.configured:return
        self.width=round(values['width']*self.scale)
        self.x=round(values['x']*self.scale);self.y=round(values['y']*self.scale)
        self.placed=(values['x'],values['y'])!=(-1,-1)
        self.configured=True

    def _size(self,width):
        limit=min(round(1920*self.scale),self.window.winfo_screenwidth()-20)
        width=max(min(round(320*self.scale),limit),min(limit,width))
        return width,round((width-2)*self.aspect)+2

    def place(self,x,y,width):
        width,height=self._size(width)
        self.x,self.y,self.width=x,y,width;self.bounds=(x,y,width,height);self.placed=True
        self.presentation.show(self.handle,self.bounds);self.visible=True;self.paint()

    def update(self,data):
        identity=data.get('identity') or {}
        key=(identity.get('hwnd'),identity.get('pid'),data.get('epoch'))
        if key!=self.frame_identity:self.press=None;self.frame_identity=key
        self.game_hwnd=identity.get('hwnd')
        if self.game_hwnd and user32.GetForegroundWindow()==self.game_hwnd:
            self.hide();return
        if not data.get('visible'):
            self.hide();return
        payload=data.get('image')
        if payload is not None:
            if payload.get('format')!='jpeg' or len(payload.get('data',''))>1_700_000:raise ValueError('画中画图片格式或大小无效。')
            image=Image.open(BytesIO(base64.b64decode(payload['data'],validate=True)))
            if image.size!=(payload['width'],payload['height']) or image.width>1920 or image.height>1080:raise ValueError('画中画图片尺寸无效。')
            image.load();self.image=image.convert('RGB');self.aspect=self.image.height/self.image.width
        if self.image is None:return
        width,height=self._size(self.width)
        x=self.x if self.placed else max(0,self.window.winfo_screenwidth()-width-24)
        y=self.y if self.placed else max(0,self.window.winfo_screenheight()-height-80)
        if not self.visible or payload is not None:self.place(x,y,width)

    def paint(self):
        if not self.visible or self.image is None or self.bounds is None:return
        width,height=self.bounds[2:]
        self.photo=ImageTk.PhotoImage(self.image.resize((max(1,width-2),max(1,height-2)),Image.Resampling.LANCZOS),master=self.window)
        self.canvas.delete('all');self.canvas.create_image(1,1,image=self.photo,anchor='nw')
        if self.hover:
            self.canvas.create_rectangle(width-34,4,width-4,34,fill='#222',outline='')
            self.canvas.create_line(width-24,14,width-14,24,fill='white',width=2)
            self.canvas.create_line(width-14,14,width-24,24,fill='white',width=2)

    def highlight(self,value):self.hover=value;self.paint()

    def cursor(self,event):
        if self.press is not None:return
        edge=self.edge(event)
        self.canvas.configure(cursor='size_we' if edge in ('l','r') else 'size_ns' if edge in ('t','b') else 'arrow')

    def edge(self,event):
        if self.bounds is None:return ''
        width,height=self.bounds[2:]
        return 'l' if event.x<8 else 'r' if event.x>width-8 else 't' if event.y<8 else 'b' if event.y>height-8 else ''

    def down(self,event):
        if self.bounds is None:return
        if self.hover and event.x>self.bounds[2]-34 and event.y<34:self.hide_by_user();return
        self.press=(event.x_root,event.y_root,self.bounds,self.edge(event),False)

    def motion(self,event):
        if self.press is None:return
        startx,starty,bounds,edge,moved=self.press;dx,dy=event.x_root-startx,event.y_root-starty
        if not moved and abs(dx)+abs(dy)<5:return
        x,y,width,height=bounds
        if edge:
            new_width=width+(dx if edge=='r' else -dx if edge=='l' else round((dy if edge=='b' else -dy)/self.aspect))
            new_width,new_height=self._size(new_width)
            if edge=='l':x+=width-new_width
            if edge=='t':y+=height-new_height
            self.place(x,y,new_width)
        else:self.place(x+dx,y+dy,width)
        self.press=(startx,starty,bounds,edge,True)

    def up(self,event):
        previous=self.press;self.press=None
        if previous is None:return
        if previous[4]:self.save_geometry()
        elif not previous[3]:self.activate()

    def wheel(self,event):
        if self.bounds is None or not event.delta:return
        self.place(self.x,self.y,self.width+round(40*self.scale*(1 if event.delta>0 else -1)));self.save_geometry()

    def save_geometry(self):
        if self.bounds is not None:self.save(dict(width=max(320,min(1920,round(self.width/self.scale))),x=round(self.x/self.scale),y=round(self.y/self.scale)))

    def hide_by_user(self):
        self.save_geometry();self.hide();self.dismiss()

    def hide(self):
        if self.visible:self.window.withdraw();self.visible=False
        self.image=self.photo=None;self.press=None

    def close(self):self.save_geometry();self.window.destroy();self.image=self.photo=None


class PipController:
    def __init__(self,ui,*,session_factory=PipSession,presentation=None):
        self.ui=ui;self.factory=session_factory;self.session=None;self.profile=None;self.target=None
        self.closing=False;self.failed=False;self.version=0;self.epoch=-1;self.token=None;self.retry=None;self.last_error=''
        self.view=PipView(ui.root,ui.ui_scale,self.activate,self.dismiss,self.save_geometry,presentation=presentation)
        self.tick()

    @property
    def worker_alive(self):return self.session is not None and self.session.worker.is_alive()

    def selection(self):
        profile='cn' if self.ui.server.get()=='国服' else 'global'
        path=self.ui.game_path.get();server=profile
        execution=getattr(self.ui,'execution_window',None)
        if execution is not None and execution.busy:
            profile=execution.profile
            path=execution.last_state.get('window_path') or execution.game_path.get()
            server=profile
        if path==GAME_PATHS['global']:server='global'
        elif path==GAME_PATHS['cn']:server='cn'
        return profile,server,path

    def toggle(self):
        self.version+=1
        if self.failed:self.retry=bool(self.ui.pip_enabled.get())
        elif self.session is not None:self.session.send('enabled',enabled=bool(self.ui.pip_enabled.get()),version=self.version)

    def activate(self):
        if self.closing:return
        if self.session is not None and self.epoch>=0:
            try:self.session.allow_user_focus();self.session.send('activate',epoch=self.epoch)
            except Exception as exc:self.ui._log('画中画切回请求未提交：'+str(exc))

    def dismiss(self):
        if self.closing:return
        if self.session is not None:self.session.send('dismiss')

    def save_geometry(self,values):
        if self.session is not None:self.session.send('geometry',values=values)

    def tick(self):
        if self.closing:
            if self.worker_alive:self.token=self.ui.root.after(50,self.tick)
            return
        profile,server,path=self.selection()
        retry=self.retry
        if self.failed and retry is not None and not self.worker_alive:
            self.session=None;self.failed=False;self.retry=None
        if self.session is not None and self.profile!=profile:
            if not self.session.closing:self.view.save_geometry()
            self.view.hide();self.session.close();self.ui.pip_button.state(['disabled'])
            if not self.worker_alive:
                self.session=None;self.failed=False;self.view.configured=False;self.view.bounds=None;self.view.placed=False;self.epoch=-1;self.version=0
        if self.session is None and not self.failed:
            self.profile=profile;self.target=(server,path)
            self.session=self.factory(RuntimePaths.discover(ROOT,profile),server,path)
            if retry is not None:self.session.send('enabled',enabled=retry,version=self.version)
        elif self.session is not None and not self.session.closing and self.target!=(server,path):
            self.view.hide();self.target=(server,path);self.session.send('target',server=server,game_path=path)
        if self.session is not None:
            try:
                while True:
                    event=self.session.events.get_nowait();kind=event['type']
                    if kind=='settings':
                        self.view.settings(event['settings']['values'])
                        if event.get('version')==self.version or event.get('version')is None and self.version==0:
                            self.ui.pip_enabled.set(event['settings']['values']['enabled'])
                        if not self.session.closing:self.ui.pip_button.state(['!disabled'])
                    elif kind=='error':
                        self.view.hide();self.ui._log('画中画：'+event['message'])
                        if event.get('settings') and event.get('version')==self.version:
                            self.ui.pip_enabled.set(event['settings']['values']['enabled'])
                    elif kind=='activated' and not event.get('foreground'):self.ui._log('Windows 尚未允许切回游戏，请手动切换。')
                    elif kind=='closed' and not self.session.closing:
                        self.failed=True;self.view.hide();self.ui.pip_button.state(['!disabled'] if event.get('exited') else ['disabled'])
            except queue.Empty:pass
            state=self.session.take_state()
            if state is not None and state.get('target')==path and not self.session.closing:
                self.epoch=state['epoch']
                if state.get('error') and state['error']!=self.last_error:self.ui._log('画中画：'+state['error'])
                self.last_error=state.get('error','')
                try:self.view.update(state)
                except Exception as exc:self.view.hide();self.ui._log('画中画：'+str(exc))
        if self.view.visible and self.view.game_hwnd and user32.GetForegroundWindow()==self.view.game_hwnd:self.view.hide()
        self.token=self.ui.root.after(33,self.tick)

    def close(self):
        if self.closing:return
        self.closing=True;self.view.close()
        if self.session is not None:self.session.close()
