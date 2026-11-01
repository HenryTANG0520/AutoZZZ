"""Presentation-only calibration and layout editor for the debug trace overlay."""
from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import ttk

from . import theme
from .trace_preferences import DEFAULTS, METRICS, PANELS, validate


class TraceSettingsWindow:
    def __init__(self, parent, scale, value, save):
        self.closed = False; self.save = save; self.variables = {}
        self.window = tk.Toplevel(parent); self.window.transient(parent)
        self.window.title('AutoZZZ · 覆盖设置')
        d = lambda n: round(n * scale)
        self.window.geometry(f'{d(620)}x{d(490)}'); self.window.minsize(d(550), d(430))
        self.window.configure(background=theme.PANEL)
        self.window.columnconfigure(0, weight=1); self.window.rowconfigure(0, weight=1)
        book = ttk.Notebook(self.window); book.grid(row=0, column=0, sticky='nsew', padx=d(14), pady=d(14))
        calibration = ttk.Frame(book, padding=d(14)); book.add(calibration, text='识别框')
        ttk.Label(calibration, text='偏移以屏幕像素计；先缩放，再偏移。', style='Hint.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0,d(16)))
        for row, (key, label, low, high, increment) in enumerate((('offset_x','水平偏移',-1920,1920,1),('offset_y','垂直偏移',-1920,1920,1),('scale_x','水平缩放',.5,1.5,.01),('scale_y','垂直缩放',.5,1.5,.01)), 1):
            self.field(calibration, key, label, value[key], row, low, high, increment, d)
        metrics = ttk.Frame(book, padding=d(14)); book.add(metrics, text='性能指标')
        metric_labels=('OCR 识别耗时','目标识别耗时','图像处理耗时','节点执行耗时','覆盖刷新耗时')
        for row, (key,label) in enumerate(zip(METRICS,metric_labels)):
            var=tk.BooleanVar(self.window,value['metrics'][key]); self.variables[('metrics',key)]=var
            ttk.Checkbutton(metrics,text=label,variable=var).grid(row=row,column=0,sticky='w',pady=d(5))
        panels = ttk.Frame(book, padding=d(14)); book.add(panels, text='面板布局')
        var=tk.BooleanVar(self.window,value['separate_panels']);self.variables['separate_panels']=var
        ttk.Checkbutton(panels,text='分别布局条件、决策、时间线和性能面板',variable=var).pack(anchor='w')
        ttk.Label(panels,text='位置和尺寸以 1920 × 1080 为基准，随游戏窗口缩放。',style='Hint.TLabel').pack(anchor='w',pady=d(10))
        panel_book=ttk.Notebook(panels);panel_book.pack(fill='both',expand=True)
        for key,label in zip(PANELS,('条件','决策','时间线','性能')):
            page=ttk.Frame(panel_book,padding=d(10));panel_book.add(page,text=label)
            for row,(field,title,low,high) in enumerate((('x','水平位置',0,1919),('y','垂直位置',0,1079),('w','宽度',120,1920),('h','高度',60,1080),('font_size','字号',8,24))):
                self.field(page,('panels',key,field),title,value['panels'][key][field],row,low,high,1,d)
        footer=ttk.Frame(self.window,padding=(d(14),0,d(14),d(14)));footer.grid(row=1,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        self.status=tk.StringVar(self.window)
        theme.wrapped_label(footer,textvariable=self.status,style='Hint.TLabel').grid(row=0,column=0,columnspan=3,sticky='ew',pady=(0,d(10)))
        ttk.Button(footer,text='恢复默认',command=self.defaults).grid(row=1,column=0,sticky='w')
        ttk.Button(footer,text='取消',command=self.close).grid(row=1,column=1,padx=d(8))
        ttk.Button(footer,text='保存',command=self.apply).grid(row=1,column=2)
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.bind('<Escape>',lambda _:self.close())
        self.window.bind('<Destroy>',lambda event:self.close() if event.widget is self.window else None,add='+')

    def field(self,parent,key,label,value,row,low,high,increment,d):
        var=tk.StringVar(self.window,str(value));self.variables[key]=var
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',padx=(0,d(20)),pady=d(5))
        ttk.Spinbox(parent,textvariable=var,from_=low,to=high,increment=increment,width=12).grid(row=row,column=1,sticky='w',pady=d(5))

    def defaults(self):
        for key,var in self.variables.items():
            value=DEFAULTS
            for part in key if isinstance(key,tuple) else (key,):value=value[part]
            var.set(value)
        self.status.set('默认设置将在保存后应用。')

    def apply(self):
        try:
            value=deepcopy(DEFAULTS)
            for key,var in self.variables.items():
                parts=key if isinstance(key,tuple) else (key,);target=value
                for part in parts[:-1]:target=target[part]
                raw=var.get()
                target[parts[-1]]=raw if type(raw) is bool else float(raw) if parts[-1] in ('scale_x','scale_y') else int(raw)
            self.save(validate(value))
        except (ValueError,OSError,tk.TclError) as exc:self.status.set('保存失败：'+str(exc));return False
        self.close();return True

    def close(self):
        if self.closed:return
        self.closed=True
        try:self.window.destroy()
        except tk.TclError:pass
