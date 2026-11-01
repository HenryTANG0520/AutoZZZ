"""Windows schedules, configuration review and occurrence history in one native window."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from .config import ROOT
from .schedules import Schedule, ScheduleStore
from .schedule_manager import ScheduleManager
from .schedule_authorization import daily_review, authorize, requires_resources
from .schedule_history import history, record_path, request_stop

TRIGGERS = {'每天': 'daily', '每周': 'weekly', '单次': 'once', '登录时': 'logon'}
MODES = {'原每日流程': 'daily', '指定账号应用': 'native', '全部参与账号': 'rotation'}
SERVERS = {'国服': 'cn', '国际服': 'global'}
STATES = {'starting':'准备中', 'running':'运行中', 'success':'完成', 'partial':'部分完成', 'failed':'失败',
          'stopped':'已停止', 'interrupted':'已中断', 'skipped':'已跳过', 'unreadable':'无法读取'}


def editor_plan(original, values, flags, weekdays):
    mode = MODES[values['mode']]
    trigger = TRIGGERS[values['trigger']]
    def number(key, optional=False):
        raw = values[key].strip()
        return None if optional and not raw else int(raw)
    plan = replace(original, name=values['name'].strip(), enabled=False,
        trigger=trigger, time=values['time'].strip() if trigger!='logon' else '09:00', date=values['date'].strip() if trigger=='once' else '',
        weekdays=weekdays if trigger=='weekly' else original.weekdays, server=SERVERS[values['server']], mode=mode,
        instance=number('instance') if mode=='native' else None,
        app_id=values['app_id'].strip() if mode=='native' else 'one_dragon', timeout=number('timeout') if mode!='daily' else original.timeout,
        allowance=number('allowance', True) if mode!='daily' else None,
        battles=number('battles', True) if mode!='daily' else None,
        maximum=number('maximum', True) if mode!='daily' else None,
        missed=flags['missed'], wake=flags['wake'], highest=flags['highest'], authorization={}, binding_error='')
    plan.validate()
    return plan


def native_query(plan, directory=False):
    from .onedragon_runtime import OneDragonClient, RuntimePaths, RequestPending
    client = OneDragonClient(RuntimePaths.discover(ROOT, plan.server))
    try:
        try: client.start(timeout=.2)
        except RequestPending:
            while True:
                try: client.finish_start(timeout=.2); break
                except RequestPending: continue
        def call(method, **params):
            request=client.submit(method,**params)
            while True:
                try:return client.result(request,timeout=.2)
                except RequestPending:continue
        if directory:return dict(accounts=call('account_list'),catalog=call('catalog'))
        return call('schedule_review', server=plan.server,
            **(dict(instance_idx=plan.instance, app_id=plan.app_id) if plan.mode=='native' else {}))
    finally:
        while True:
            try: client.close(timeout=.2); break
            except RequestPending: continue


def review_text(plan, review):
    lines = [plan.name, '区服：'+next(k for k,v in SERVERS.items() if v==plan.server)]
    if plan.mode=='daily':
        settings = review['settings']
        lines += ['游戏：'+settings['game_path'], '执行已保存的每日与收尾设置（不消耗电量）。']
    else:
        for row in review.get('instances', []):
            lines.append('账号 '+str(row['idx'])+' · '+row['name'])
            lines.append('应用：'+json.dumps(row['applications'], ensure_ascii=False))
            for action in row['resources']:
                lines.append(json.dumps(action, ensure_ascii=False, indent=2))
        lines.extend(str(issue) for issue in review.get('issues', []))
    if review.get('uses_charge'):
        lines.append(f'每次触发额度：{plan.allowance} 电量 / {plan.battles} 次 / 单次上限 {plan.maximum}')
    lines.append('配置变化后需重新核对；关闭助手不会取消已保存的系统计划。')
    return '\n'.join(lines)


class SchedulesWindow:
    def __init__(self, ui):
        self.ui=ui; self.closed=False; self.closing=False; self.worker=None; self.events=queue.Queue(); self.catalog=None
        self.store=ScheduleStore(ROOT); self.manager=ScheduleManager(self.store); self.editor=None
        self.window=tk.Toplevel(ui.root); self.window.title('AutoZZZ · 定时任务'); self.window.transient(ui.root)
        d=lambda n:round(n*ui.ui_scale)
        self.window.geometry(f'{d(940)}x{d(650)}'); self.window.minsize(d(720),d(500))
        self.window.columnconfigure(0,weight=1); self.window.rowconfigure(1,weight=1)
        bar=ttk.Frame(self.window,padding=d(12)); bar.grid(row=0,column=0,sticky='ew')
        self.controls=[]
        for i,(label,fn) in enumerate((('新增',lambda:self.edit()),('编辑',self.edit_selected),('启用',self.enable),
            ('停用',self.disable),('立即运行',self.run),('删除',self.remove),('刷新',self.refresh))):
            button=ttk.Button(bar,text=label,command=fn); button.grid(row=0,column=i,padx=d(3)); self.controls.append(button)
        self.book=ttk.Notebook(self.window); self.book.grid(row=1,column=0,sticky='nsew',padx=d(12))
        self.plans=self.table('计划',('name','trigger','mode','state'),('名称','时间','执行内容','状态'),(230,180,180,170))
        self.records=self.table('运行记录',('name','started','state'),('计划','开始时间','结果'),(270,280,170))
        self.plans.bind('<Double-1>',lambda _:self.edit_selected())
        footer=ttk.Frame(self.window,padding=d(12)); footer.grid(row=2,column=0,sticky='ew'); footer.columnconfigure(0,weight=1)
        self.status=tk.StringVar(self.window,''); ttk.Label(footer,textvariable=self.status,wraplength=d(580)).grid(row=0,column=0,sticky='ew')
        self.open_button=ttk.Button(footer,text='打开运行目录',command=self.open_record); self.open_button.grid(row=0,column=1,padx=d(5))
        self.stop_button=ttk.Button(footer,text='停止本次运行',command=self.stop); self.stop_button.grid(row=0,column=2)
        self.controls += [self.open_button,self.stop_button]
        self.window.protocol('WM_DELETE_WINDOW',self.close); self.window.after(80,self.drain); self.refresh()

    def table(self, title, columns, labels, widths):
        frame=ttk.Frame(self.book); frame.columnconfigure(0,weight=1); frame.rowconfigure(0,weight=1); self.book.add(frame,text=title)
        table=ttk.Treeview(frame,columns=columns,show='headings',selectmode='browse')
        for key,label,width in zip(columns,labels,widths):table.heading(key,text=label); table.column(key,width=round(width*self.ui.ui_scale),minwidth=80)
        table.grid(row=0,column=0,sticky='nsew'); sy=ttk.Scrollbar(frame,orient='vertical',command=table.yview); sy.grid(row=0,column=1,sticky='ns'); table.configure(yscrollcommand=sy.set)
        sx=ttk.Scrollbar(frame,orient='horizontal',command=table.xview); sx.grid(row=1,column=0,sticky='ew'); table.configure(xscrollcommand=sx.set)
        return table

    def submit(self,fn,callback):
        if self.worker is not None and self.worker.is_alive():return
        for button in self.controls:button.configure(state='disabled')
        self.status.set('处理中…')
        def work():
            try:self.events.put((callback,fn(),None))
            except Exception as exc:self.events.put((callback,None,str(exc)))
        self.worker=threading.Thread(target=work,name='AutoZZZ-Schedules'); self.worker.start()

    def drain(self):
        if self.closed:return
        if self.worker is None or not self.worker.is_alive():
            try:
                while True:
                    callback,value,error=self.events.get_nowait()
                    if not self.closing:
                        for button in self.controls:button.configure(state='normal')
                        if error:
                            if self.editor is not None and self.editor.winfo_exists():self.save_button.configure(state='normal')
                            self.status.set(error); messagebox.showerror('定时任务',error,parent=self.window)
                        else:callback(value)
            except queue.Empty:pass
            if self.closing:self.closed=True;self.window.destroy();return
        self.window.after(80,self.drain)

    def refresh(self):self.submit(lambda:(self.manager.catalog(),history(self.store)),self.loaded)

    def loaded(self,value):
        self.catalog,self.history=value
        for table in (self.plans,self.records):table.delete(*table.get_children())
        for row in self.catalog['plans']:
            p=row['plan']; label=next(k for k,v in TRIGGERS.items() if v==p['trigger'])
            if p['trigger']!='logon':label+=' '+p['time']
            state='已启用' if p['enabled'] else '已停用'
            if not row['bound']:state='系统定义缺失或冲突'
            if p['binding_error']:state=p['binding_error']
            self.plans.insert('', 'end',iid=p['id'],values=(p['name'],label,next(k for k,v in MODES.items() if v==p['mode']),state))
        for i,row in enumerate(self.history):self.records.insert('','end',iid=str(i),values=(row['name'],row['started_at'],STATES.get(row['state'],row['state'])))
        self.status.set('系统计划有 '+str(len(self.catalog['orphaned']))+' 项未在本地登记。' if self.catalog['orphaned'] else '关闭助手后计划仍有效；停用不会终止正在运行的任务。')

    def selected(self):
        ids=self.plans.selection()
        return None if not ids or self.catalog is None else next(Schedule.load(r['plan']) for r in self.catalog['plans'] if r['plan']['id']==ids[0])

    def edit_selected(self):
        plan=self.selected()
        if plan:self.edit(plan)

    def edit(self,plan=None):
        if self.worker is not None and self.worker.is_alive():return
        if self.editor is not None and self.editor.winfo_exists():self.editor.lift();return
        self.original=plan or Schedule(server='global' if self.ui.server.get()=='国际服' else 'cn', date=date.today().isoformat())
        p=self.original; self.editor=tk.Toplevel(self.window); self.editor.title('编辑计划'); self.editor.transient(self.window)
        self.editor.minsize(round(620*self.ui.ui_scale),round(360*self.ui.ui_scale))
        self.choice_profile=None;self.account_choices={};self.app_choices={}
        self.editor.columnconfigure(0,weight=1); self.editor.rowconfigure(0,weight=1)
        # A scrollable form keeps fields available at small window sizes and high DPI.
        canvas=tk.Canvas(self.editor,highlightthickness=0,width=round(640*self.ui.ui_scale),height=round(620*self.ui.ui_scale)); canvas.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(self.editor,orient='vertical',command=canvas.yview);scroll.grid(row=0,column=1,sticky='ns');canvas.configure(yscrollcommand=scroll.set)
        form=ttk.Frame(canvas,padding=16);slot=canvas.create_window(0,0,anchor='nw',window=form);form.columnconfigure(1,weight=1)
        form.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')));canvas.bind('<Configure>',lambda e:canvas.itemconfigure(slot,width=e.width))
        self.values={};self.fields={}
        specs=[('name','名称',p.name,None),('trigger','触发方式',next(k for k,v in TRIGGERS.items() if v==p.trigger),TRIGGERS),
            ('time','时间 · HH:MM',p.time,None),('date','日期 · YYYY-MM-DD',p.date,None),
            ('server','区服',next(k for k,v in SERVERS.items() if v==p.server),SERVERS),
            ('mode','执行内容',next(k for k,v in MODES.items() if v==p.mode),MODES),('instance','账号配置',p.instance or 1,()),
            ('app_id','应用',p.app_id,()),('timeout','原生运行时限（秒，至少 30）',p.timeout,None),
            ('allowance','每次触发总电量',p.allowance or '',None),('battles','每次触发次数',p.battles or '',None),('maximum','单次电量上限 · 20—100',p.maximum or '',None)]
        for i,(key,label,value,choices) in enumerate(specs):
            ttk.Label(form,text=label).grid(row=i,column=0,sticky='w',padx=(0,16),pady=5)
            var=tk.StringVar(self.editor,str(value));self.values[key]=var
            entry=ttk.Combobox(form,textvariable=var,values=list(choices),state='readonly') if choices is not None else ttk.Entry(form,textvariable=var)
            entry.grid(row=i,column=1,sticky='ew',pady=5);self.fields[key]=entry
        days=ttk.Frame(form);days.grid(row=12,column=0,columnspan=2,sticky='w',pady=8);self.days=[]
        for i,label in enumerate(('一','二','三','四','五','六','日')):
            var=tk.BooleanVar(self.editor,i in p.weekdays);self.days.append(var);ttk.Checkbutton(days,text='周'+label,variable=var).grid(row=0,column=i)
        self.flags={}
        for i,(key,label) in enumerate((('highest','以管理员权限运行'),('missed','错过后补跑'),('wake','唤醒电脑'))):
            var=tk.BooleanVar(self.editor,getattr(p,key));self.flags[key]=var;ttk.Checkbutton(form,text=label,variable=var).grid(row=13+i,column=0,columnspan=2,sticky='w',pady=4)
        ttk.Label(form,text='资源额度留空表示不使用电量；原生应用须先在“应用配置”中保存。',wraplength=round(550*self.ui.ui_scale)).grid(row=16,column=0,columnspan=2,sticky='w',pady=10)
        self.enable_after=tk.BooleanVar(self.editor,p.enabled);ttk.Checkbutton(form,text='核对后启用',variable=self.enable_after).grid(row=17,column=0,sticky='w')
        self.save_button=ttk.Button(form,text='核对并保存',command=self.review);self.save_button.grid(row=17,column=1,sticky='e')
        self.choices_button=ttk.Button(form,text='刷新账号与应用',command=lambda:self.load_choices(force=True));self.choices_button.grid(row=18,column=1,sticky='e',pady=8)
        self.fields['mode'].bind('<<ComboboxSelected>>',lambda _:self.field_states());self.fields['trigger'].bind('<<ComboboxSelected>>',lambda _:self.field_states())
        self.fields['server'].bind('<<ComboboxSelected>>',lambda _:self.field_states());self.field_states()
        self.editor_original=self.editor_snapshot()
        self.editor.protocol('WM_DELETE_WINDOW',self.close_editor)

    def field_states(self):
        mode=MODES[self.values['mode'].get()];trigger=TRIGGERS[self.values['trigger'].get()]
        for key in ('instance','app_id'):self.fields[key].configure(state='readonly' if mode=='native' else 'disabled')
        self.fields['timeout'].configure(state='disabled' if mode=='daily' else 'normal')
        for key in ('allowance','battles','maximum'):self.fields[key].configure(state='normal' if mode!='daily' else 'disabled')
        self.fields['time'].configure(state='disabled' if trigger=='logon' else 'normal')
        self.fields['date'].configure(state='normal' if trigger=='once' else 'disabled')
        self.choices_button.configure(state='normal' if mode=='native' else 'disabled')
        if mode=='native':self.load_choices()

    def load_choices(self,force=False):
        profile=SERVERS[self.values['server'].get()]
        if MODES[self.values['mode'].get()]!='native' or self.worker is not None and self.worker.is_alive():return
        if not force and self.choice_profile==profile:return
        self.save_button.configure(state='disabled')
        plan=replace(self.original,server=profile)
        self.submit(lambda:native_query(plan,directory=True),lambda data:self.choices_loaded(profile,data))

    def choices_loaded(self,profile,data):
        if self.editor is None or not self.editor.winfo_exists():return
        self.save_button.configure(state='normal')
        if profile!=SERVERS[self.values['server'].get()]:self.load_choices();return
        self.choice_profile=profile
        self.account_choices={f"{row['name']} · {row['idx']}":row['idx'] for row in data['accounts']['instances']}
        self.app_choices={f"{row['name']} · {row['id']}":row['id'] for row in data['catalog']['applications'] if row['executable'] and not row['internal']}
        for key,choices,current in (('instance',self.account_choices,self.original.instance or data['accounts']['current']),('app_id',self.app_choices,self.original.app_id)):
            old=self.values[key].get();selected=next((label for label,value in choices.items() if value==current),'')
            self.fields[key].configure(values=list(choices));self.values[key].set(old if old in choices else selected)
        # Only directory labels changed; no execution configuration has been edited.
        for key in ('instance','app_id'):self.editor_original[0][key]=self.values[key].get()

    def editor_snapshot(self):
        return ({key:var.get() for key,var in self.values.items()}, {key:var.get() for key,var in self.flags.items()},
                [var.get() for var in self.days], self.enable_after.get())

    def confirm_close(self):
        if self.editor is None or not self.editor.winfo_exists():return True
        if self.editor_snapshot()==self.editor_original:return True
        return messagebox.askokcancel('放弃计划修改','计划有未保存修改，关闭并放弃？',parent=self.editor)

    def close_editor(self,force=False):
        if self.worker is not None and self.worker.is_alive():return
        if not force and not self.confirm_close():return
        self.editor.destroy();self.editor=None

    def review(self):
        try:
            values={k:v.get() for k,v in self.values.items()}
            if MODES[values['mode']]=='native':
                if self.choice_profile!=SERVERS[values['server']]:raise ValueError('请先刷新账号与应用。')
                values['instance']=str(self.account_choices[values['instance']]);values['app_id']=self.app_choices[values['app_id']]
            plan=editor_plan(self.original,values,{k:v.get() for k,v in self.flags.items()},[i for i,v in enumerate(self.days) if v.get()])
        except (ValueError,KeyError) as exc:messagebox.showerror('计划输入',str(exc),parent=self.editor);return
        enabled=self.enable_after.get(); self.save_button.configure(state='disabled')
        self.submit(lambda:daily_review(plan,self.ui.config_path) if plan.mode=='daily' else native_query(plan),lambda review:self.reviewed(plan,enabled,review))

    def reviewed(self,plan,enabled,review):
        self.save_button.configure(state='normal')
        if not review.get('configuration_valid'):raise_message='\n'.join(str(x) for x in review.get('issues',[]));messagebox.showerror('配置未通过核对',raise_message or '请先完善应用配置。',parent=self.editor);return
        dialog=tk.Toplevel(self.editor);dialog.title('核对执行清单');dialog.transient(self.editor);dialog.columnconfigure(0,weight=1);dialog.rowconfigure(0,weight=1)
        content=tk.Text(dialog,wrap='word',width=78,height=24);content.grid(row=0,column=0,sticky='nsew');content.insert('1.0',review_text(plan,review));content.configure(state='disabled')
        sy=ttk.Scrollbar(dialog,orient='vertical',command=content.yview);sy.grid(row=0,column=1,sticky='ns');content.configure(yscrollcommand=sy.set)
        accepted=tk.BooleanVar(dialog,False);resource=requires_resources(review)
        if resource:ttk.Checkbutton(dialog,text='允许本计划每次触发执行上述资源操作',variable=accepted).grid(row=1,column=0,sticky='w',padx=12,pady=8)
        def save():
            try:authorization=authorize(plan,review,resources_accepted=accepted.get())
            except ValueError as exc:messagebox.showerror('执行清单',str(exc),parent=dialog);return
            dialog.destroy();self.submit(lambda:self.manager.save(replace(plan,enabled=enabled,authorization=authorization),self.catalog['revision']),self.saved)
        ttk.Button(dialog,text='确认并保存',command=save).grid(row=2,column=0,sticky='e',padx=12,pady=12)
        dialog.grab_set()

    def saved(self,_):
        self.close_editor(force=True);self.refresh()

    def enable(self):
        plan=self.selected()
        if plan:self.edit(plan);self.enable_after.set(True)

    def disable(self):
        plan=self.selected()
        if plan:self.submit(lambda:self.manager.disable(plan.id,self.catalog['revision']),lambda _:self.refresh())

    def run(self):
        plan=self.selected()
        if plan and messagebox.askokcancel('立即运行','立即执行“'+plan.name+'”？使用已核对的账号、资源清单和本次额度。',parent=self.window):
            self.submit(lambda:self.manager.run(plan.id,self.catalog['revision']),lambda _:self.refresh())

    def remove(self):
        plan=self.selected()
        if plan and messagebox.askokcancel('删除计划','删除“'+plan.name+'”？运行记录会保留。',parent=self.window):
            self.submit(lambda:self.manager.remove(plan.id,self.catalog['revision']),lambda _:self.refresh())

    def record(self):
        selected=self.records.selection()
        if not selected:return None
        row=self.history[int(selected[0])]
        return None if row.get('invalid') else row

    def open_record(self):
        row=self.record()
        if row:
            try:os.startfile(record_path(self.store,row['schedule_id'],row['instance']).parent)
            except (ValueError,OSError) as exc:messagebox.showerror('运行记录',str(exc),parent=self.window)

    def stop(self):
        row=self.record()
        if row:self.submit(lambda:request_stop(self.store,row['schedule_id'],row['instance']),lambda _:self.status.set('已请求停止，等待原执行器松键并保存结果；点击刷新核实。'))

    def close(self,confirmed=False):
        if not confirmed and not self.confirm_close():return
        self.closing=True;self.window.withdraw()
        if self.worker is None or not self.worker.is_alive():self.closed=True;self.window.destroy()


def open_schedules(ui):
    existing=getattr(ui,'schedules_window',None)
    if existing is not None and not existing.closed:
        existing.window.deiconify();existing.window.lift();return
    ui.schedules_window=SchedulesWindow(ui)
