"""实际调试总线的原生查看器与游戏覆盖；不创建另一个运行会话。"""
from __future__ import annotations

import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk,filedialog

from .localization_tk import PresentationVar
from . import theme
from .overlay import NativePresentation
from .windows import user32,W,C
from .trace_hotkeys import HotkeyPoller
from .trace_preferences import DEFAULTS, METRICS, TracePreferences, calibrated_box, panel_bounds
from copy import deepcopy


LABELS={'vision':'识别','decision':'决策','timeline':'时间线','perf':'性能'}
COLORS={'ocr':'#8edc7d','template':'#72c6ff','yolo':'#ffbe66','cv':'#e7a5ef'}


def recent_rows(snapshot,kind,now=None):
    now=time.time() if now is None else now
    ttl={'vision':1.8,'decision':30,'timeline':60,'perf':30}[kind]
    return [row for row in (snapshot or {}).get('items',{}).get(kind,[]) if -1<=now-row['created']<=ttl]


def vision_rows(snapshot,sources):
    """上游 Overlay 同样保留较新、同标签 IoU < .3 的 YOLO 框。"""
    rows=[];yolo=[]
    for row in reversed(recent_rows(snapshot,'vision')):
        if not sources.get(row['source'],True):continue
        if row['source']=='yolo':
            duplicate=False
            for old in yolo:
                if row['label']!=old['label']:continue
                intersection=max(0,min(row['x2'],old['x2'])-max(row['x1'],old['x1']))*max(0,min(row['y2'],old['y2'])-max(row['y1'],old['y1']))
                union=(row['x2']-row['x1'])*(row['y2']-row['y1'])+(old['x2']-old['x1'])*(old['y2']-old['y1'])-intersection
                if union>0 and intersection/union>=.3:duplicate=True;break
            if duplicate:continue
            yolo.append(row)
        rows.append(row)
    return list(reversed(rows))


def overlay_groups(data,state,layers,preferences):
    groups={}
    lines=[]
    battle=state.get('battle') or {};task=battle.get('task') or {}
    if layers['state'] and task:
        lines+=['当前条件',task.get('trigger','')[:64],task.get('conditions','')[:180]]
    groups['state']=lines;lines=[]
    if layers['decision']:
        rows=recent_rows(data,'decision')[-4:]
        if rows:lines+=['决策']+[row['trigger'][:40]+' → '+row['operation'][:70]+' ['+row['status'][:20]+']' for row in rows]
    groups['decision']=lines;lines=[]
    if layers['timeline']:
        rows=recent_rows(data,'timeline')[-4:]
        if rows:lines+=['时间线']+[row['title'][:64]+(' · '+row['detail'][:60] if row['detail'] else '') for row in rows]
    groups['timeline']=lines;lines=[]
    if layers['perf']:
        metrics={row['metric']:row for row in recent_rows(data,'perf') if preferences['metrics'].get(row['metric'],True)}
        if metrics:lines+=['性能']+[key+f"  {metrics[key]['value']:.2f} {metrics[key]['unit']}" for key in sorted(metrics,key=lambda key:(key not in METRICS,METRICS.index(key) if key in METRICS else key))[:10]]
    groups['perf']=lines
    return groups


class TraceOverlay:
    def __init__(self,root,scale,*,presentation=None):
        self.root=root;self.scale=scale;self.presentation=presentation or NativePresentation()
        self.window=tk.Toplevel(root,takefocus=False);self.window.withdraw();self.window.overrideredirect(True)
        self.window.title('AutoZZZ · 调试覆盖');self.window.attributes('-transparentcolor','#010101');self.window.attributes('-alpha',.85)
        self.canvas=tk.Canvas(self.window,background='#010101',highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        self.window.update_idletasks();self.handle=self.presentation.prepare(self.window)
        self.enabled=self.visible=self.closed=False;self.snapshot=None;self.state={};self.sources={key:True for key in COLORS}
        self.layers={key:True for key in ('vision','decision','timeline','perf','state')};self.font_size=11;self.opacity=85
        self.panels={};self.save_panel=lambda *_:None;self.panel_widgets={};self.preferences=deepcopy(DEFAULTS)
        self.warning=lambda message:None;self.last_error=''
        self.token=self.root.after(self.preferences['follow_interval_ms'],self.tick)

    def update(self,snapshot):self.snapshot=snapshot

    def update_state(self,state):
        self.state=state or {}
        control=self.state.get('control',{})
        if self.snapshot is not None and control.get('observation_epoch') is not None and control['observation_epoch']!=self.snapshot['epoch']:self.snapshot=None
        if control.get('manual_paused') or control.get('focus_waiting') or control.get('state')=='stopped':self.hide()

    def hide(self):
        if self.visible:self.window.withdraw();self.visible=False
        self.canvas.delete('all')
        for panel in self.panels.values():panel.hide()

    def tick(self):
        if self.closed:return
        try:self.draw()
        except (OSError,tk.TclError,ValueError,ZeroDivisionError) as exc:
            self.hide()
            if str(exc)!=self.last_error:self.warning('覆盖显示失败：'+str(exc))
            self.last_error=str(exc)
        if not self.closed:self.token=self.root.after(self.preferences['follow_interval_ms'],self.tick)

    def draw(self):
        data=self.snapshot or {};identity=data.get('window_identity') or {};control=data.get('control',{})
        current=self.state.get('control',{});epoch=current.get('observation_epoch')
        valid=(self.enabled and data.get('enabled') and -1<=time.time()-data.get('created',0)<.8 and identity
               and (epoch is None or epoch==data.get('epoch')) and not current.get('manual_paused')
               and not current.get('focus_waiting') and current.get('state')!='stopped'
               and control.get('fresh_frame')is not False and not control.get('manual_paused') and not control.get('focus_waiting') and control.get('state')!='stopped')
        if valid:
            pid=W.DWORD();user32.GetWindowThreadProcessId(identity['hwnd'],C.byref(pid))
            valid=pid.value==identity['pid'] and user32.IsWindow(identity['hwnd']) and not user32.IsIconic(identity['hwnd'])
        if not valid:self.hide();return
        from .windows import geometry
        front=self.presentation.game_bounds(identity['hwnd']);g=geometry(identity['hwnd']);bounds=(g.left,g.top,g.width,g.height)
        if front is not None:
            self.presentation.show(self.handle,front);self.visible=True;self.window.attributes('-alpha',self.opacity/100)
        elif self.visible:self.window.withdraw();self.visible=False
        self.paint(bounds[2],bounds[3],bounds,foreground=front is not None);self.last_error=''

    def paint(self,width,height,bounds=None,foreground=True):
        self.canvas.delete('all');data=self.snapshot or {};standard=data.get('standard_size',[1920,1080]);sx,sy=width/standard[0],height/standard[1]
        font=(theme.FONT,self.font_size)
        if self.layers['vision']:
            for row in vision_rows(data,self.sources):
                box=calibrated_box(row,standard,(width,height),self.preferences)
                color=COLORS.get(row['source'],'#eee')
                self.canvas.create_rectangle(*box,outline=color,width=2)
                self.canvas.create_text(box[0]+3,max(8,box[1]-3),text=row['label'][:60],fill=color,font=font,anchor='sw')
        groups=overlay_groups(data,self.state,self.layers,self.preferences);lines=[]
        if self.preferences['separate_panels']:
            from .trace_panels_ui import TracePanel
            for key,lines in groups.items():
                config=self.preferences['panels'][key]
                if key not in self.panels and not lines:continue
                if key not in self.panels:
                    self.panels[key]=TracePanel(self.root,key,self.scale,self.save_panel,presentation=self.presentation)
                    self.panel_widgets[key]=self.panels[key].canvas
                panel=self.panels[key]
                if not lines or not config['visible'] or bounds is not None and config['mode']=='game' and not foreground:
                    panel.hide();continue
                if config['mode']=='desktop':pb=(config['desktop_x'],config['desktop_y'],round(config['w']*self.scale),round(config['h']*self.scale))
                else:
                    x,y,w,h=panel_bounds(config,(width,height));pb=((bounds[0] if bounds else 0)+x,(bounds[1] if bounds else 0)+y,w,h)
                if bounds is not None:panel.show(lines,config,pb,self.preferences['text_color'])
                else:
                    panel.canvas.delete('all');panel.canvas.create_text(8,6,text='\n'.join(lines),anchor='nw',width=max(1,pb[2]-16),font=(theme.FONT,config['font_size']),fill=self.preferences['text_color'])
        else:
            for panel in self.panels.values():panel.hide()
            lines=[line for group in groups.values() for line in group]
        if lines and not self.preferences['separate_panels']:
            x=max(12,width-min(round(400*self.scale),round(width*.35)));y=round(40*self.scale)
            text=self.canvas.create_text(x+12,y+8,text='\n'.join(lines),anchor='nw',width=max(100,width-x-28),font=font,fill=self.preferences['text_color'])
            box=self.canvas.bbox(text);background=self.canvas.create_rectangle(x,y,width-12,min(height-12,box[3]+10),fill='#101820',outline='#555')
            self.canvas.tag_lower(background,text)

    def close(self):
        if self.closed:return
        self.closed=True
        for panel in self.panels.values():panel.close()
        try:
            if self.token is not None:self.root.after_cancel(self.token);self.token=None
            self.window.destroy()
        except tk.TclError:pass  # 父窗口销毁也会触发此清理路径。


class TraceWindow:
    def __init__(self,parent,scale,subscribe,*,presentation=None,preferences_path=None,initially_hidden=False):
        self.capture_root=getattr(getattr(getattr(subscribe,'__self__',None),'paths',None),'data',None)
        self.capture_pending=False;self.capture_message='';self.capture_options=None;self.captured=set();self.hotkey_token=None;self.capture_listeners=[]
        self.scale=scale;self.advanced=None;self.preference_store=TracePreferences(preferences_path)
        try:self.preferences=self.preference_store.load();self.preference_error=None
        except (OSError,ValueError) as exc:self.preferences=deepcopy(DEFAULTS);self.preference_error=str(exc)
        self.subscribe=subscribe;self.closed=False;self.version=0;self.snapshot=None;self.state={};self.displayed=None
        self.window=tk.Toplevel(parent)
        if initially_hidden:self.window.withdraw()
        self.window.title('AutoZZZ · 调试追踪');self.window.transient(parent)
        d=lambda n:round(n*scale);width,height=min(d(980),parent.winfo_screenwidth()-d(50)),min(d(740),parent.winfo_screenheight()-d(90))
        self.window.geometry(f'{width}x{height}');self.window.minsize(min(d(740),width),min(d(560),height));self.window.configure(background=theme.PANEL)
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(2,weight=1)
        bar=ttk.Frame(self.window,padding=d(14));bar.grid(row=0,column=0,sticky='ew')
        self.collect=tk.BooleanVar(self.window,True);self.frozen=tk.BooleanVar(self.window,False);self.cover=tk.BooleanVar(self.window,self.preferences['default_cover'])
        ttk.Checkbutton(bar,text='采集',variable=self.collect,command=self.configure).pack(side='left',padx=(0,d(14)))
        ttk.Checkbutton(bar,text='冻结列表',variable=self.frozen,command=self.refresh).pack(side='left',padx=(0,d(14)))
        self.overlay=TraceOverlay(self.window,scale,presentation=presentation)
        ttk.Checkbutton(bar,text='游戏覆盖',variable=self.cover,command=self.persist_overlay_options).pack(side='left',padx=(0,d(14)))
        ttk.Button(bar,text='覆盖设置…',command=self.open_advanced).pack(side='left',padx=(0,d(8)))
        ttk.Button(bar,text='清空',command=lambda:self.configure(clear=True)).pack(side='left')
        ttk.Button(bar,text='导出 JSON…',command=self.export).pack(side='right')
        filters=ttk.Frame(self.window,padding=(d(14),0,d(14),d(12)));filters.grid(row=1,column=0,sticky='ew');filters.columnconfigure(1,weight=1)
        ttk.Label(filters,text='筛选').grid(row=0,column=0,sticky='w',padx=(0,d(10)))
        self.query=tk.StringVar(self.window);ttk.Entry(filters,textvariable=self.query).grid(row=0,column=1,sticky='ew')
        self.query.trace_add('write',lambda *_:self.refresh())
        source_row=ttk.Frame(filters);source_row.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(d(10),0))
        self.sources={key:tk.BooleanVar(self.window,self.preferences['sources'][key]) for key in COLORS}
        for key,label in (('ocr','OCR'),('template','模板'),('yolo','YOLO'),('cv','CV')):
            ttk.Checkbutton(source_row,text=label,variable=self.sources[key],command=self.persist_sources).pack(side='left',padx=(0,d(14)))
        self.layers={key:tk.BooleanVar(self.window,self.preferences['layers'][key]) for key in ('vision','decision','timeline','perf','state')}
        ttk.Label(source_row,text='覆盖：').pack(side='left',padx=(d(14),d(6)))
        for key in self.layers:ttk.Checkbutton(source_row,text=LABELS.get(key,'条件'),variable=self.layers[key],command=self.persist_overlay_options).pack(side='left',padx=(0,d(8)))
        self.book=ttk.Notebook(self.window);self.book.grid(row=2,column=0,sticky='nsew',padx=d(14));self.tables={};self.details={};self.records={}
        definitions={'vision':(('source','来源',80),('label','标签',300),('box','坐标',190),('score','置信度',85)),
                     'decision':(('time','时间',85),('source','来源',110),('trigger','触发器',190),('operation','操作',200),('status','状态',80)),
                     'timeline':(('time','时间',85),('category','分类',110),('title','事件',300),('level','等级',75)),
                     'perf':(('metric','指标',300),('value','值',120),('unit','单位',80),('age','距采样（秒）',130))}
        for kind,columns in definitions.items():
            page=ttk.Frame(self.book);page.columnconfigure(0,weight=1);page.rowconfigure(0,weight=1);self.book.add(page,text=LABELS[kind])
            table=ttk.Treeview(page,columns=[item[0] for item in columns],show='headings',style='Config.Treeview');table.grid(row=0,column=0,sticky='nsew')
            for key,label,size in columns:table.heading(key,text=label);table.column(key,width=d(size),minwidth=d(55),stretch=True)
            vertical=ttk.Scrollbar(page,command=table.yview);vertical.grid(row=0,column=1,sticky='ns');table.configure(yscrollcommand=vertical.set)
            horizontal=ttk.Scrollbar(page,orient='horizontal',command=table.xview);horizontal.grid(row=1,column=0,sticky='ew');table.configure(xscrollcommand=horizontal.set)
            detail=tk.Text(page,height=6,wrap='word',font=(theme.FONT,10),background=theme.FIELD,foreground=theme.TEXT,relief='flat',state='disabled');detail.grid(row=2,column=0,sticky='ew',pady=(d(10),0))
            scroll=ttk.Scrollbar(page,command=detail.yview);scroll.grid(row=2,column=1,sticky='ns');detail.configure(yscrollcommand=scroll.set)
            self.tables[kind]=table;self.details[kind]=detail;self.records[kind]={}
            table.bind('<<TreeviewSelect>>',lambda _,kind=kind:self.detail(kind))
        footer=ttk.Frame(self.window,padding=d(14));footer.grid(row=3,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        self.status=PresentationVar(self.window,'正在启用追踪…');theme.wrapped_label(footer,textvariable=self.status,style='Hint.TLabel').grid(row=0,column=0,columnspan=2,sticky='ew')
        appearance=ttk.Frame(footer);appearance.grid(row=1,column=0,sticky='w',pady=(d(10),0))
        self.font_size=tk.StringVar(self.window,str(self.preferences['base_font_size']));self.opacity=tk.StringVar(self.window,str(self.preferences['base_opacity']))
        for label,var,low,high in (('覆盖字号',self.font_size,8,28),('不透明度 %',self.opacity,5,100)):
            ttk.Label(appearance,text=label).pack(side='left',padx=(0,d(8)))
            entry=ttk.Spinbox(appearance,textvariable=var,from_=low,to=high,width=5,command=self.persist_appearance);entry.pack(side='left',padx=(0,d(16)))
            entry.bind('<FocusOut>',lambda _:self.persist_appearance());entry.bind('<Return>',lambda _:self.persist_appearance())
        ttk.Button(appearance,text='带覆盖截图',command=self.request_capture).pack(side='left')
        ttk.Button(footer,text='关闭',command=self.close).grid(row=1,column=1,sticky='e',pady=(d(10),0))
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.bind('<Escape>',lambda _:self.close())
        self.window.bind('<Destroy>',lambda event:self.close() if event.widget is self.window else None,add='+')
        self.overlay.warning=self.error
        self.overlay.preferences=deepcopy(self.preferences);self.overlay.save_panel=self.save_panel
        self.hotkeys=HotkeyPoller(user32.GetAsyncKeyState);self.hotkeys.configure(self.preferences['hotkeys'])
        self.poll_hotkeys()
        self.configure()
        if self.preference_error:self.status.set('覆盖设置读取失败：'+self.preference_error)

    def open_advanced(self):
        from .onedragon_trace_settings_ui import TraceSettingsWindow
        if self.advanced is not None and not self.advanced.closed:
            self.advanced.window.lift();return
        draft=deepcopy(self.preferences)
        for row in recent_rows(self.snapshot or {},'perf'):
            if len(draft['metrics'])<100:draft['metrics'].setdefault(row['metric'],True)
        self.advanced=TraceSettingsWindow(self.window,self.scale,draft,self.save_preferences,groups=overlay_groups(self.snapshot or {},self.state,self.overlay.layers,self.preferences))

    def save_panel(self,key,changes):
        value=deepcopy(self.preferences);value['panels'][key].update(changes)
        try:self.save_preferences(value)
        except (OSError,ValueError) as exc:self.error('覆盖布局保存失败：'+str(exc))

    def save_preferences(self,value):
        self.preference_store.save(value);self.preferences=deepcopy(value);self.overlay.preferences=deepcopy(value)
        self.hotkeys.configure(value['hotkeys'])
        self.font_size.set(str(value['base_font_size']));self.opacity.set(str(value['base_opacity']));self.cover.set(value['default_cover'])
        for key,var in self.layers.items():var.set(value['layers'][key])
        for key,var in self.sources.items():var.set(value['sources'][key])
        self.overlay_options()
        self.status.set('覆盖设置已保存。')

    def poll_hotkeys(self):
        if self.closed:return
        identity=(self.overlay.snapshot or {}).get('window_identity') or {};active=False
        if identity and -1<=time.time()-(self.overlay.snapshot or {}).get('created',0)<.8:
            pid=W.DWORD();user32.GetWindowThreadProcessId(identity['hwnd'],C.byref(pid))
            active=pid.value==identity['pid'] and user32.GetForegroundWindow()==identity['hwnd']
        for action in self.hotkeys.poll(active):
            if action=='toggle':self.cover.set(not self.cover.get());self.persist_overlay_options()
            elif action=='capture':self.request_capture()
        self.hotkey_token=self.window.after(self.preferences['input_poll_interval_ms'],self.poll_hotkeys)

    def request_capture(self):
        if self.closed or self.capture_pending:return
        if self.capture_root is None:self.status.set('当前会话不提供截图目录。');return
        self.capture_pending=True;self.collect.set(True)
        self.capture_options=dict(layers={key:var.get() for key,var in self.layers.items()},sources={key:var.get() for key,var in self.sources.items()},
                                  preferences=deepcopy(self.preferences),font_size=self.overlay.font_size,opacity=self.overlay.opacity,
                                  scale=self.scale,tk_scaling=float(self.window.tk.call('tk','scaling')))
        self.version=self.subscribe(True,False,capture=True)
        self.capture_message='等待游戏前台的新画面，30 秒内截图。';self.status.set(self.capture_message)

    def receive_capture(self,snapshot):
        value=snapshot['capture'];key=(value.get('path'),value.get('sha256'))
        if not self.capture_pending or key in self.captured:return
        self.capture_pending=False
        listeners,self.capture_listeners=self.capture_listeners,[]
        try:
            from io import BytesIO
            from .trace_capture import read_capture,compose_overlay,atomic_bytes
            if value['epoch']!=snapshot['epoch'] or value['generation']!=snapshot['generation']:raise ValueError('截图观察已过期。')
            captured_snapshot=snapshot.get('capture_snapshot') or snapshot
            if captured_snapshot['epoch']!=value['epoch'] or captured_snapshot['generation']!=value['generation']:raise ValueError('截图追踪观察不匹配。')
            current_epoch=self.state.get('control',{}).get('observation_epoch')
            if current_epoch is not None and current_epoch!=value['epoch']:raise ValueError('截图观察已过期。')
            image,path=read_capture(self.capture_root,value)
            for callback in listeners:
                try:callback(dict(value))
                except Exception:pass  # A separate editor cannot interrupt the running application's trace.
            state={'battle':captured_snapshot.get('capture_battle',{})} if not value.get('paused') else {}
            result=compose_overlay(image,captured_snapshot,state,**self.capture_options)
            stream=BytesIO();result.save(stream,format='PNG');target=path.with_name(path.stem+self.preferences['capture_suffix']+'.png')
            atomic_bytes(target,stream.getvalue());self.captured.add(key)
            self.capture_message='截图已保存：'+str(target);self.status.set(self.capture_message)
        except (OSError,ValueError,KeyError) as exc:self.capture_message='截图保存失败：'+str(exc);self.status.set(self.capture_message)

    def capture_once(self,callback):
        if self.closed:raise ValueError('原追踪窗口已关闭。')
        self.capture_listeners.append(callback)
        if not self.capture_pending:self.request_capture()

    def configure(self,clear=False):
        if self.closed:return
        self.capture_pending=False;self.capture_listeners.clear();self.capture_message=''
        self.version=self.subscribe(bool(self.collect.get()),clear)
        self.snapshot=self.displayed=None;self.frozen.set(False);self.overlay.update(None);self.overlay.hide();self.refresh()

    def update(self,snapshot,version=None):
        if self.closed or version is not None and version!=self.version:return
        if snapshot is None:
            self.displayed=None;self.frozen.set(False);self.overlay.hide()
            self.capture_pending=False;self.capture_listeners.clear();self.capture_message=''
        if snapshot and 'capture' in snapshot:self.receive_capture(snapshot)
        self.snapshot=snapshot;self.overlay.update(snapshot)
        if not self.frozen.get():self.displayed=snapshot
        self.refresh()

    def update_state(self,state):self.state=state or {};self.overlay.update_state(self.state)

    def error(self,message,version=None):
        if version is not None and version!=self.version:return
        self.capture_pending=False;self.capture_listeners.clear()
        self.overlay.update(None);self.overlay.hide();self.status.set('追踪读取失败：'+message[:180])

    def persist_appearance(self):
        if not self.overlay_options():return
        value=deepcopy(self.preferences);value.update(base_font_size=self.overlay.font_size,base_opacity=self.overlay.opacity)
        try:self.save_preferences(value)
        except (OSError,ValueError) as exc:self.status.set('覆盖外观保存失败：'+str(exc))

    def persist_sources(self):
        self.persist_overlay_options();self.refresh()

    def persist_overlay_options(self):
        value=deepcopy(self.preferences);value.update(default_cover=bool(self.cover.get()),sources={key:var.get() for key,var in self.sources.items()},layers={key:var.get() for key,var in self.layers.items()})
        try:self.save_preferences(value)
        except (OSError,ValueError) as exc:self.status.set('覆盖设置保存失败：'+str(exc))
        self.overlay_options()

    def overlay_options(self):
        self.overlay.enabled=bool(self.cover.get())
        self.overlay.layers={key:var.get() for key,var in self.layers.items()};self.overlay.sources={key:var.get() for key,var in self.sources.items()}
        if not self.overlay.enabled:self.overlay.hide()
        try:
            font=int(self.font_size.get());opacity=int(self.opacity.get())
            if not 8<=font<=28 or not 5<=opacity<=100:raise ValueError()
        except ValueError:
            self.status.set('覆盖字号须为 8—28，不透明度须为 5—100。');return False
        self.overlay.font_size=font;self.overlay.opacity=opacity
        return True

    def refresh(self):
        if self.closed:return
        if not self.frozen.get():self.displayed=self.snapshot
        data=self.displayed or {};query=self.query.get().casefold().split();counts=[]
        valid_appearance=self.overlay_options()
        for kind,table in self.tables.items():
            rows=list(data.get('items',{}).get(kind,[]))
            if kind=='vision':rows=[row for row in rows if self.sources.get(row['source']) is None or self.sources[row['source']].get()]
            if kind=='perf':
                metrics={row['metric']:row for row in rows};rows=[metrics[key] for key in sorted(metrics,key=lambda key:(key not in METRICS,METRICS.index(key) if key in METRICS else key))]
            rows=[row for row in rows if all(word in json.dumps(row,ensure_ascii=False).casefold() for word in query)]
            selected=table.selection();old=self.records[kind];records={}
            for index,row in enumerate(reversed(rows) if kind!='perf' else rows):
                key=str(index);records[key]=row
                if kind=='vision':values=(row['source'],row['label'],f"({row['x1']:.0f}, {row['y1']:.0f})—({row['x2']:.0f}, {row['y2']:.0f})",f"{row['score']:.3f}")
                elif kind=='decision':values=(time.strftime('%H:%M:%S',time.localtime(row['created'])),row['source'],row['trigger'],row['operation'],row['status'])
                elif kind=='timeline':values=(time.strftime('%H:%M:%S',time.localtime(row['created'])),row['category'],row['title'],row['level'])
                else:values=(row['metric'],f"{row['value']:.3f}",row['unit'],f"{max(0,time.time()-row['created']):.2f}")
                if table.exists(key):table.item(key,values=values)
                else:table.insert('','end',iid=key,values=values)
            for key in table.get_children():
                if key not in records:table.delete(key)
            self.records[kind]=records
            if selected and old.get(selected[0])!=records.get(selected[0]):table.selection_remove(*selected)
            self.detail(kind);counts.append(LABELS[kind]+f' {len(rows)}'+(' / '+str(data['counts'][kind]) if data.get('truncated',{}).get(kind) else '')+('（含截断文字）' if any(row.get('truncated_fields') for row in rows) else ''))
        if valid_appearance:
            message=('列表已冻结 · ' if self.frozen.get() else '采集中 · ' if data.get('enabled') else '采集已关闭 · ')+ ' / '.join(counts)
            if self.cover.get() and self.overlay.last_error:message+=' · 覆盖显示失败：'+self.overlay.last_error[:100]
            if self.capture_message:message+=' · '+self.capture_message
            self.status.set(message)

    def detail(self,kind):
        table=self.tables[kind];selection=table.selection();row=self.records[kind].get(selection[0]) if selection else None
        text=self.details[kind];value=json.dumps(row,ensure_ascii=False,indent=2) if row else ''
        if text.get('1.0','end-1c')!=value:text.configure(state='normal');text.delete('1.0','end');text.insert('1.0',value);text.configure(state='disabled')

    def export(self):
        target=filedialog.asksaveasfilename(parent=self.window,defaultextension='.json',filetypes=[('追踪 JSON','*.json')],initialfile='autozzz-trace.json')
        if not target:return
        try:Path(target).write_text(json.dumps(self.displayed or {},ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');self.status.set('已导出：'+str(target))
        except (OSError,ValueError) as exc:self.status.set('导出失败：'+str(exc))

    def close(self):
        if self.closed:return
        self.closed=True;self.hotkeys.close();self.capture_listeners=[]
        if self.hotkey_token is not None:self.window.after_cancel(self.hotkey_token);self.hotkey_token=None
        self.capture_pending=False;self.subscribe(False,False);self.overlay.close()
        try:self.window.destroy()
        except tk.TclError:pass
