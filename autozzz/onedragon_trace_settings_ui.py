"""Presentation-only calibration and layout editor for the debug trace overlay."""
from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import ttk

from .localization_tk import PresentationVar
from . import theme
from .trace_preferences import DEFAULTS, METRICS, ALL_PANELS, PANEL_LABELS, validate


class TraceSettingsWindow:
    def __init__(self, parent, scale, value, save,groups=None):
        self.closed = False; self.base_value=deepcopy(value);self.save = save;self.scale=scale;self.groups=groups;self.layout_window=None; self.variables = {}
        self.window = tk.Toplevel(parent); self.window.transient(parent)
        self.window.title('AutoZZZ · 覆盖设置')
        d = lambda n: round(n * scale)
        self.window.geometry(f'{d(620)}x{d(560)}'); self.window.minsize(d(550), d(500))
        self.window.configure(background=theme.PANEL)
        self.window.columnconfigure(0, weight=1); self.window.rowconfigure(0, weight=1)
        book = ttk.Notebook(self.window); book.grid(row=0, column=0, sticky='nsew', padx=d(14), pady=d(14))
        calibration = ttk.Frame(book, padding=d(14)); book.add(calibration, text='识别框')
        ttk.Label(calibration, text='偏移以屏幕像素计；先缩放，再偏移。', style='Hint.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0,d(16)))
        for row, (key, label, low, high, increment) in enumerate((('offset_x','水平偏移',-1920,1920,1),('offset_y','垂直偏移',-1920,1920,1),('scale_x','水平缩放',.5,1.5,.01),('scale_y','垂直缩放',.5,1.5,.01)), 1):
            self.field(calibration, key, label, value[key], row, low, high, increment, d)
        keys=ttk.Frame(book,padding=d(14));book.add(keys,text='快捷键')
        ttk.Label(keys,text='仅在游戏前台生效；留空禁用。F9 / F10 保留。',style='Hint.TLabel').pack(anchor='w',pady=(0,d(12)))
        for key,label in (('toggle','覆盖显隐'),('capture','带覆盖截图')):
            ttk.Label(keys,text=label).pack(anchor='w')
            var=tk.StringVar(self.window,value['hotkeys'][key]);self.variables[('hotkeys',key)]=var
            ttk.Entry(keys,textvariable=var,width=28).pack(anchor='w',pady=(d(5),d(14)))
        metric_page=ttk.Frame(book,padding=d(14));book.add(metric_page,text='性能指标')
        metric_canvas=tk.Canvas(metric_page,highlightthickness=0)
        metric_scroll=ttk.Scrollbar(metric_page,command=metric_canvas.yview)
        metric_canvas.configure(yscrollcommand=metric_scroll.set)
        metric_scroll.pack(side='right',fill='y');metric_canvas.pack(side='left',fill='both',expand=True)
        metrics=ttk.Frame(metric_canvas);metric_canvas.create_window((0,0),window=metrics,anchor='nw')
        metrics.bind('<Configure>',lambda _:metric_canvas.configure(scrollregion=metric_canvas.bbox('all')))
        metric_labels=('OCR 识别耗时','目标识别耗时','图像处理耗时','节点执行耗时','覆盖刷新耗时')
        metric_keys=list(value['metrics'])
        for row, key in enumerate(metric_keys):
            label=dict(zip(METRICS,metric_labels)).get(key,key)
            var=tk.BooleanVar(self.window,value['metrics'][key]); self.variables[('metrics',key)]=var
            ttk.Checkbutton(metrics,text=label,variable=var).grid(row=row,column=0,sticky='w',pady=d(5))
        self.custom_metric=tk.StringVar(self.window)
        ttk.Entry(metrics,textvariable=self.custom_metric,width=30).grid(row=len(metric_keys),column=0,sticky='w',pady=d(6))
        def add_metric():
            key=self.custom_metric.get().strip()
            if not key or key in self.base_value['metrics']:return
            try:
                draft=self.draft();draft['metrics'][key]=True
                validate(draft)
            except ValueError as exc:self.status.set(str(exc));return
            self.base_value['metrics'][key]=True
            var=tk.BooleanVar(self.window,True);self.variables[('metrics',key)]=var
            ttk.Checkbutton(metrics,text=key,variable=var).grid(row=len(self.base_value['metrics'])+1,column=0,sticky='w',pady=d(3))
            self.custom_metric.set('')
        ttk.Button(metrics,text='添加指标',command=add_metric).grid(row=len(metric_keys),column=1,padx=d(8))
        panels = ttk.Frame(book, padding=d(14)); book.add(panels, text='面板布局')
        var=tk.BooleanVar(self.window,value['separate_panels']);self.variables['separate_panels']=var
        ttk.Checkbutton(panels,text='分别布局条件、决策、时间线和性能面板',variable=var).pack(anchor='w')
        ttk.Label(panels,text='位置和尺寸以 1920 × 1080 为基准，随游戏窗口缩放。',style='Hint.TLabel').pack(anchor='w',pady=d(10))
        ttk.Button(panels,text='拖拽布局…',command=self.open_layout).pack(anchor='w',pady=(0,d(8)))
        panel_book=ttk.Notebook(panels);panel_book.pack(fill='both',expand=True)
        for key,label in zip(ALL_PANELS,PANEL_LABELS):
            page=ttk.Frame(panel_book,padding=d(10));panel_book.add(page,text=label)
            for row,(field,title,low,high) in enumerate((('x','水平位置',0,1919),('y','垂直位置',0,1079),('w','宽度',120,1920),('h','高度',60,1080),('font_size','字号',8,28))):
                self.field(page,('panels',key,field),title,value['panels'][key][field],row//2,low,high,1,d,column=(row%2)*2)
        floating=ttk.Frame(book,padding=d(14));book.add(floating,text='浮窗')
        ttk.Label(floating,text='桌面面板可拖动、缩放、收起；游戏面板保持穿透。',style='Hint.TLabel').grid(row=0,column=0,columnspan=5,sticky='w',pady=(0,d(12)))
        for row,(key,label) in enumerate(zip(ALL_PANELS,PANEL_LABELS),1):
            ttk.Label(floating,text=label).grid(row=row,column=0,sticky='w',padx=(0,d(10)),pady=d(8))
            for field,item in value['panels'][key].items():
                if ('panels',key,field) not in self.variables:self.variables[('panels',key,field)]=tk.BooleanVar(self.window,item) if type(item)is bool else tk.StringVar(self.window,str(item))
            mode_value=self.variables[('panels',key,'mode')];mode_label=tk.StringVar(self.window,{'game':'随游戏','desktop':'桌面'}[mode_value.get()])
            modes=ttk.Combobox(floating,values=('随游戏','桌面'),textvariable=mode_label,state='readonly',width=9)
            modes.bind('<<ComboboxSelected>>',lambda _,v=mode_value,label=mode_label:v.set({'随游戏':'game','桌面':'desktop'}[label.get()]))
            mode_value.trace_add('write',lambda *_,v=mode_value,label=mode_label:label.set({'game':'随游戏','desktop':'桌面'}[v.get()]))
            modes.grid(row=row,column=1,padx=d(6))
            ttk.Spinbox(floating,textvariable=self.variables[('panels',key,'opacity')],from_=5,to=100,width=4).grid(row=row,column=2,padx=d(6))
            ttk.Checkbutton(floating,text='显示',variable=self.variables[('panels',key,'visible')]).grid(row=row,column=3,padx=d(6))
            ttk.Checkbutton(floating,text='收起',variable=self.variables[('panels',key,'collapsed')]).grid(row=row,column=4,padx=d(6))
        ttk.Label(floating,text='数值为不透明度 %；“显示”可恢复已关闭的面板。',style='Hint.TLabel').grid(row=len(ALL_PANELS)+1,column=0,columnspan=5,sticky='w',pady=d(12))
        appearance=ttk.Frame(book,padding=d(14));book.add(appearance,text='外观')
        for row,(key,label) in enumerate((('text_color','文字颜色 #RRGGBB'),('capture_suffix','合成截图后缀'))):
            var=tk.StringVar(self.window,value[key]);self.variables[key]=var
            ttk.Label(appearance,text=label).grid(row=row,column=0,sticky='w',padx=(0,d(12)),pady=d(8))
            ttk.Entry(appearance,textvariable=var,width=18).grid(row=row,column=1,sticky='w')
        for row,(key,label,low,high) in enumerate((('follow_interval_ms','覆盖刷新（毫秒）',30,1000),('input_poll_interval_ms','快捷键轮询（毫秒）',20,200),('state_poll_interval_ms','状态采样（毫秒）',50,2000)),2):self.field(appearance,key,label,value[key],row,low,high,1,d)
        logs=ttk.Frame(book,padding=d(14));book.add(logs,text='日志与截图')
        for row,(key,label) in enumerate((('log_panel_enabled','显示游戏日志'),('patched_capture_enabled','保存原始操作截图时附加覆盖图'))):
            var=tk.BooleanVar(self.window,value[key]);self.variables[key]=var
            ttk.Checkbutton(logs,text=label,variable=var).grid(row=row,column=0,columnspan=2,sticky='w',pady=d(8))
        self.field(logs,'log_max_lines','最多保留日志行数',value['log_max_lines'],2,1,500,1,d)
        self.field(logs,'log_fade_seconds','日志保留秒数（0为不淡出）',value['log_fade_seconds'],3,0,3600,1,d)
        footer=ttk.Frame(self.window,padding=(d(14),0,d(14),d(14)));footer.grid(row=1,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        self.status=PresentationVar(self.window)
        theme.wrapped_label(footer,textvariable=self.status,style='Hint.TLabel').grid(row=0,column=0,columnspan=3,sticky='ew',pady=(0,d(10)))
        ttk.Button(footer,text='恢复默认',command=self.defaults).grid(row=1,column=0,sticky='w')
        ttk.Button(footer,text='取消',command=self.close).grid(row=1,column=1,padx=d(8))
        ttk.Button(footer,text='保存',command=self.apply).grid(row=1,column=2)
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.bind('<Escape>',lambda _:self.close())
        self.window.bind('<Destroy>',lambda event:self.close() if event.widget is self.window else None,add='+')

    def draft(self):
        value=deepcopy(self.base_value)
        for key,var in self.variables.items():
            parts=key if isinstance(key,tuple) else (key,);target=value
            for part in parts[:-1]:target=target[part]
            raw=var.get();target[parts[-1]]=raw if type(raw)is bool or parts[0] in ('hotkeys','text_color','capture_suffix') or parts[-1]=='mode' else float(raw) if parts[-1] in ('scale_x','scale_y') else int(raw)
        return validate(value)

    def open_layout(self):
        from .trace_layout_ui import TraceLayoutWindow
        if self.layout_window is not None and not self.layout_window.closed:self.layout_window.window.lift();return
        try:value=self.draft()
        except (ValueError,tk.TclError) as exc:self.status.set('请先修正设置：'+str(exc));return
        def apply(value):
            self.variables['separate_panels'].set(True)
            for key,panel in value['panels'].items():
                for field,item in panel.items():self.variables[('panels',key,field)].set(item)
        self.layout_window=TraceLayoutWindow(self.window,self.scale,value,apply,self.groups)

    def field(self,parent,key,label,value,row,low,high,increment,d,column=0):
        var=tk.StringVar(self.window,str(value));self.variables[key]=var
        ttk.Label(parent,text=label).grid(row=row,column=column,sticky='w',padx=(0,d(10)),pady=d(5))
        ttk.Spinbox(parent,textvariable=var,from_=low,to=high,increment=increment,width=12).grid(row=row,column=column+1,sticky='w',padx=(0,d(12)),pady=d(5))

    def defaults(self):
        self.base_value=deepcopy(DEFAULTS)
        for key,var in self.variables.items():
            value=DEFAULTS
            try:
                for part in key if isinstance(key,tuple) else (key,):value=value[part]
            except KeyError:
                if isinstance(key,tuple) and key[0]=='metrics':value=True
                else:raise
            var.set(value)
        self.status.set('默认设置将在保存后应用。')

    def apply(self):
        try:
            value=self.draft()
            self.save(validate(value))
        except (ValueError,OSError,tk.TclError) as exc:self.status.set('保存失败：'+str(exc));return False
        self.close();return True

    def close(self):
        if self.closed:return
        self.closed=True
        try:self.window.destroy()
        except tk.TclError:pass
