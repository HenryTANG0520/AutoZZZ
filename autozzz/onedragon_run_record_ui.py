"""Display and reset the current account's upstream exploration progress."""
from __future__ import annotations

from tkinter import messagebox,ttk

from . import theme
from .localization import tr
from .localization_tk import PresentationVar


def progress_text(app_id, progress):
    if app_id=='world_patrol':
        return tr('今日巡逻：{done:g} / {goal} 轮',done=progress['daily_progress'],goal=progress['daily_plan_rounds'])
    return tr('今日：{daily} / {daily_goal} 次 · 本周：{weekly} / {weekly_goal} 次',
              daily=progress['daily_run_times'],daily_goal=progress['daily_plan_times'],
              weekly=progress['weekly_run_times'],weekly_goal=progress['weekly_plan_times'])


class RunRecordControls:
    def __init__(self,panel,app_id):
        self.panel,self.app_id=panel,app_id
        self.record=None
        d=lambda n:round(n*panel.scale)
        self.frame=ttk.LabelFrame(panel.content,text='运行进度',padding=d(12))
        self.frame.columnconfigure(0,weight=1)
        self.details=PresentationVar(panel.root)
        self.details_label=theme.wrapped_label(self.frame,textvariable=self.details,style='Hint.TLabel')
        self.details_label.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(d(8),0))
        self.details_label.grid_remove()
        self.read_button=ttk.Button(self.frame,text='查看进度',command=self.read)
        self.read_button.grid(row=0,column=0,sticky='w')
        self.reset_button=ttk.Button(self.frame,text='重置进度…',command=self.reset,style='Danger.TButton')
        self.reset_button.grid(row=0,column=1,sticky='e')
        self.update_controls()

    def update_controls(self):
        idle=not (self.panel.busy or self.panel.closing or self.panel.dirty)
        self.read_button.state(['!disabled' if idle else 'disabled'])
        self.reset_button.state(['!disabled' if idle and self.record and self.record['exists'] else 'disabled'])

    def show(self,record):
        if self.panel.run_record is not self:return
        self.record=record
        account=record['instance_name'] or str(record['instance_idx'])
        self.details.set(account+' · '+progress_text(self.app_id,record['progress']))
        self.details_label.grid()
        self.read_button.configure(text='刷新')
        if record.get('archive'):
            self.panel.status.set('进度已重置。原记录已备份。')
            self.panel.ui._log(tr('运行记录备份：{path}',path=record['archive']))
        else:self.panel.status.set('已读取运行进度')
        self.update_controls()

    def read(self):
        if self.panel.busy or self.panel.closing or self.panel.dirty:return
        self.record=None
        self.details_label.grid_remove()
        self.panel._run(lambda client:self.panel.call(client,'run_record_status',app_id=self.app_id),self.show)

    def reset(self):
        record=self.record
        if self.panel.busy or self.panel.closing or self.panel.dirty or not record or not record['exists']:return
        account=record['instance_name'] or str(record['instance_idx'])
        prompt=('重置「{account}」的今日巡逻进度？之后会重新计算本地执行进度。' if self.app_id=='world_patrol'
                else '重置「{account}」的今日及本周挑战进度？之后会重新计算本地执行进度。')
        if not messagebox.askokcancel(tr('重置运行进度'),tr(prompt,account=account),parent=self.panel.root,default='cancel'):return
        if self.panel.run_record is not self or self.record is not record or self.panel.busy or self.panel.closing or self.panel.dirty:return
        self.record=None
        self.details_label.grid_remove()
        self.panel._run(lambda client:self.panel.call(client,'run_record_reset',app_id=self.app_id,
            revision=record['revision'],instance_idx=record['instance_idx']),self.show)
