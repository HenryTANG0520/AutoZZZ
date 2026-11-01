"""Independently positioned, capture-excluded, non-activating trace panels."""
from __future__ import annotations

import tkinter as tk
from . import theme
from .overlay import NativePresentation


class TracePanel:
    def __init__(self,parent,key,scale,save,*,presentation=None):
        self.key=key;self.scale=scale;self.save=save;self.drag=None;self.visible=False;self.closed=False;self.mode=None
        self.window=tk.Toplevel(parent,takefocus=False);self.window.withdraw();self.window.overrideredirect(True)
        self.window.title('AutoZZZ · '+{'state':'条件','decision':'决策','timeline':'时间线','perf':'性能'}[key])
        self.window.configure(background='#101820');self.bar=tk.Frame(self.window,background='#243b4a',height=round(26*scale))
        self.label=tk.Label(self.bar,text={'state':'条件','decision':'决策','timeline':'时间线','perf':'性能'}[key],background='#243b4a',foreground='#fff',font=(theme.FONT,9))
        self.label.pack(side='left',fill='x',expand=True)
        for text,command in (('×',lambda:self.change(visible=False)),('−',lambda:self.change(collapsed=not self.config['collapsed']))):
            tk.Button(self.bar,text=text,command=command,relief='flat',background='#243b4a',foreground='#fff',takefocus=False,width=2).pack(side='right')
        self.canvas=tk.Canvas(self.window,background='#101820',highlightthickness=1,highlightbackground='#555',takefocus=False)
        self.window.update_idletasks();self.presentation=presentation or NativePresentation();self.handle=self.presentation.prepare(self.window)
        for widget in (self.bar,self.label):
            widget.bind('<ButtonPress-1>',self.down);widget.bind('<B1-Motion>',self.motion);widget.bind('<ButtonRelease-1>',self.up)
        self.canvas.bind('<ButtonPress-1>',self.resize_down);self.canvas.bind('<B1-Motion>',self.motion);self.canvas.bind('<ButtonRelease-1>',self.up)
        self.window.protocol('WM_DELETE_WINDOW',lambda:self.change(visible=False))

    def change(self,**changes):
        if self.closed:return
        try:self.save(self.key,changes)
        except (OSError,ValueError):self.hide();raise

    def show(self,lines,config,bounds,color):
        self.config=config;self.bounds=bounds;desktop=config['mode']=='desktop'
        self.mode=config['mode']
        if desktop:self.bar.pack(side='top',fill='x')
        else:self.bar.pack_forget()
        if desktop and config['collapsed']:self.canvas.pack_forget();bounds=(*bounds[:3],round(26*self.scale))
        else:self.canvas.pack(fill='both',expand=True)
        self.canvas.delete('all');self.canvas.create_text(8,6,text='\n'.join(lines),anchor='nw',width=max(1,bounds[2]-16),font=(theme.FONT,config['font_size']),fill=color)
        if desktop and not config['collapsed']:
            self.canvas.create_line(bounds[2]-16,bounds[3]-round(26*self.scale)-4,bounds[2]-4,bounds[3]-round(26*self.scale)-16,fill='#9db4be',width=2)
        if self.drag is not None:bounds=(self.window.winfo_x(),self.window.winfo_y(),self.window.winfo_width(),self.window.winfo_height())
        self.window.attributes('-alpha',config['opacity']/100)
        self.presentation.show(self.handle,bounds)
        # Tk mapping/alpha can rebuild extended styles. Reapply protection to the actual mapped wrapper.
        self.handle=self.presentation.prepare(self.window)
        style=self.presentation.get_style(self.handle,-20)
        self.presentation.set_style(self.handle,-20,(style&~0x20) if desktop else (style|0x20))
        self.visible=True

    def down(self,event):
        if self.mode!='desktop':return
        self.drag=(False,event.x_root,event.y_root,self.window.winfo_x(),self.window.winfo_y(),self.window.winfo_width(),self.window.winfo_height())

    def resize_down(self,event):
        if self.mode!='desktop' or self.config['collapsed']:return
        if event.x>=self.canvas.winfo_width()-24 and event.y>=self.canvas.winfo_height()-24:
            self.drag=(True,event.x_root,event.y_root,self.window.winfo_x(),self.window.winfo_y(),self.window.winfo_width(),self.window.winfo_height())

    def motion(self,event):
        if self.drag is None:return
        resize,sx,sy,x,y,w,h=self.drag;dx,dy=event.x_root-sx,event.y_root-sy
        if resize:w=max(round(120*self.scale),w+dx);h=max(round(60*self.scale),h+dy)
        else:x+=dx;y+=dy
        self.presentation.show(self.handle,(x,y,w,h))

    def up(self,event):
        if self.drag is None:return
        resize,*_=self.drag;self.drag=None
        changes=dict(desktop_x=self.window.winfo_x(),desktop_y=self.window.winfo_y())
        if resize:changes.update(w=max(120,min(1920,round(self.window.winfo_width()/self.scale))),h=max(60,min(1080,round(self.window.winfo_height()/self.scale))))
        self.change(**changes)

    def hide(self):
        if self.closed:return
        self.visible=False;self.drag=None;self.canvas.delete('all');self.window.withdraw()

    def close(self):
        if self.closed:return
        self.closed=True;self.drag=None
        try:self.window.destroy()
        except tk.TclError:pass
