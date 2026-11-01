"""Drag and dock presentation panels in a draft preview, never on the game surface."""
from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import ttk
from .trace_preferences import PANELS,panel_bounds
from . import theme


def move_panel(panel,x,y,*,resize=False):
    panel=dict(panel);panel['x'],panel['y'],_,_=panel_bounds(panel,(1920,1080))
    result=dict(panel)
    if resize:
        result['w']=max(120,min(1920-panel['x'],round(x-panel['x'])))
        result['h']=max(60,min(1080-panel['y'],round(y-panel['y'])))
    else:
        x=max(0,min(1920-panel['w'],round(x)));y=max(0,min(1080-panel['h'],round(y)))
        for edge in (0,1920-panel['w']):
            if abs(x-edge)<24:x=edge
        for edge in (0,1080-panel['h']):
            if abs(y-edge)<24:y=edge
        result.update(x=x,y=y)
    return result


class TraceLayoutWindow:
    def __init__(self,parent,scale,value,apply,groups=None):
        self.closed=False;self.value=deepcopy(value);self.apply=apply;self.groups=groups or {};self.drag=None;self.selected='state'
        self.window=tk.Toplevel(parent);self.window.transient(parent);self.window.title('AutoZZZ · 拖拽布局')
        d=lambda n:round(n*scale);self.window.geometry(f'{d(760)}x{d(520)}');self.window.minsize(d(600),d(440))
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(1,weight=1)
        bar=ttk.Frame(self.window,padding=d(12));bar.grid(row=0,column=0,sticky='ew')
        self.panel=tk.StringVar(self.window,'条件');self.dock=tk.StringVar(self.window,'自由')
        combo=ttk.Combobox(bar,textvariable=self.panel,values=('条件','决策','时间线','性能'),state='readonly',width=8);combo.pack(side='left')
        combo.bind('<<ComboboxSelected>>',lambda _:self.select())
        ttk.Combobox(bar,textvariable=self.dock,values=('自由','左上','右上','左下','右下'),state='readonly',width=8).pack(side='left',padx=d(8))
        ttk.Button(bar,text='停靠',command=self.docking).pack(side='left')
        self.canvas=tk.Canvas(self.window,background='#18222c',highlightthickness=0);self.canvas.grid(row=1,column=0,sticky='nsew',padx=d(12))
        self.canvas.bind('<Configure>',lambda _:self.paint());self.canvas.bind('<ButtonPress-1>',self.down)
        self.canvas.bind('<B1-Motion>',self.motion);self.canvas.bind('<ButtonRelease-1>',lambda _:setattr(self,'drag',None))
        footer=ttk.Frame(self.window,padding=d(12));footer.grid(row=2,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        ttk.Label(footer,text='拖动移动，右下角缩放；仅修改草稿。',style='Hint.TLabel').grid(row=0,column=0,sticky='w')
        ttk.Button(footer,text='取消',command=self.close).grid(row=0,column=1,padx=d(8))
        ttk.Button(footer,text='应用到草稿',command=self.commit).grid(row=0,column=2)
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.bind('<Escape>',lambda _:self.close())
        self.window.bind('<Destroy>',lambda e:self.close() if e.widget is self.window else None,add='+')

    def select(self):
        self.selected=PANELS[('条件','决策','时间线','性能').index(self.panel.get())];self.paint()

    def docking(self):
        panel=self.value['panels'][self.selected];dock=self.dock.get()
        if dock=='自由':return
        self.value['panels'][self.selected]=move_panel(panel,0 if dock.startswith('左') else 1920-panel['w'],0 if dock.endswith('上') else 1080-panel['h'])
        self.paint()

    def paint(self):
        self.canvas.delete('all');w,h=self.canvas.winfo_width(),self.canvas.winfo_height()
        for key,label in zip(PANELS,('条件','决策','时间线','性能')):
            x,y,pw,ph=panel_bounds(self.value['panels'][key],(w,h));color='#70d5b5' if key==self.selected else '#7d98b0'
            self.canvas.create_rectangle(x,y,x+pw,y+ph,outline=color,fill='#263745',width=2,tags=(key,))
            lines=self.groups.get(key) or [label]
            self.canvas.create_text(x+6,y+5,text='\n'.join(lines)[:220],anchor='nw',width=max(1,pw-12),fill=theme.TEXT,font=(theme.FONT,9),tags=(key,))
            self.canvas.create_rectangle(x+pw-9,y+ph-9,x+pw,y+ph,fill=color,outline=color,tags=(key,))

    def down(self,event):
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height();x,y=event.x*1920/w,event.y*1080/h
        for key in reversed(PANELS):
            px,py,pw,ph=panel_bounds(self.value['panels'][key],(1920,1080))
            if px<=x<=px+pw and py<=y<=py+ph:
                self.selected=key;self.panel.set(('条件','决策','时间线','性能')[PANELS.index(key)])
                self.drag=(key,dict(self.value['panels'][key]),x-px,y-py,x>=px+pw-30 and y>=py+ph-30);break
        self.paint()

    def motion(self,event):
        if self.drag is None:return
        key,panel,dx,dy,resize=self.drag;x,y=event.x*1920/self.canvas.winfo_width(),event.y*1080/self.canvas.winfo_height()
        self.value['panels'][key]=move_panel(panel,x if resize else x-dx,y if resize else y-dy,resize=resize);self.paint()

    def commit(self):self.value['separate_panels']=True;self.apply(self.value);self.close()

    def close(self):
        if self.closed:return
        self.closed=True
        try:self.window.destroy()
        except tk.TclError:pass
