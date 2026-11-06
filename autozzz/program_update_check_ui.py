"""Main-window feedback for optional startup metadata checks."""
from __future__ import annotations
import queue
import tkinter as tk
from tkinter import messagebox

from .localization_tk import PresentationVar
from .config import ROOT
from .program_update_check import MetadataCheck
from .program_update_preferences import UpdatePreferences, UpdatePreferencesStore
from .onedragon_runtime import RuntimePaths


class StartupUpdates:
    def __init__(self,ui,background=True):
        self.ui=ui;self.store=UpdatePreferencesStore(ROOT);self.checker=None;self.events=queue.Queue();self.closing=False;self.timer=None;self.open_when_finished=False
        try:self.preferences=self.store.load();error=None
        except (ValueError,OSError) as exc:self.preferences=UpdatePreferences();error=str(exc)
        self.enabled=tk.BooleanVar(ui.root,self.preferences.enabled)
        self.channel=tk.StringVar(ui.root,'测试版' if self.preferences.channel=='beta' else '稳定版')
        self.status=PresentationVar(ui.root,error or ('将在启动后检查版本信息。' if self.preferences.enabled else '启动时不自动检查。'))
        if background and self.preferences.enabled and not error:self.timer=ui.root.after(1500,self.check)

    @property
    def worker_alive(self):return self.checker is not None and self.checker.alive

    def save(self):
        value=UpdatePreferences(self.enabled.get(),'beta' if self.channel.get()=='测试版' else 'stable')
        try:self.store.save(value,self.preferences)
        except (ValueError,OSError) as exc:
            self.enabled.set(self.preferences.enabled);self.channel.set('测试版' if self.preferences.channel=='beta' else '稳定版')
            messagebox.showerror('检查更新设置',str(exc),parent=self.ui.root);return False
        self.preferences=value
        if not value.enabled:
            if self.timer is not None:self.ui.root.after_cancel(self.timer);self.timer=None
            if self.worker_alive:self.cancel()
        elif not self.worker_alive:self.status.set('已保存；下次启动时只检查版本信息。')
        return True

    def check(self):
        self.timer=None
        if self.closing or self.worker_alive:return
        view=getattr(self.ui,'program_updates_window',None)
        if view is not None and not view.closed:
            self.status.set('版本窗口已打开；请在该窗口检查。');return
        try:
            profile='global' if self.ui.server.get()=='国际服' else 'cn'
            self.checker=MetadataCheck(RuntimePaths.discover_program_control(ROOT,profile),self.preferences.channel,self.events.put)
            self.status.set('正在检查版本信息…')
        except Exception as exc:self.status.set('检查更新不可用：'+str(exc))
        self.buttons()

    def cancel(self):
        if self.worker_alive:self.checker.cancel();self.status.set('正在取消；等待原请求与进程退出…')

    def buttons(self):
        button=getattr(self.ui,'startup_update_cancel',None)
        if button is not None:button.state(['!disabled'] if self.worker_alive and not self.closing else ['disabled'])
        button=getattr(self.ui,'startup_update_check',None)
        if button is not None:button.state(['disabled'] if self.worker_alive or self.closing else ['!disabled'])

    def poll(self):
        try:
            while True:
                event=self.events.get_nowait();self.status.set(event['message'])
        except queue.Empty:pass
        self.buttons()
        if self.open_when_finished and not self.worker_alive and not self.closing:
            self.open_when_finished=False
            from .program_updates_ui import open_program_updates
            open_program_updates(self.ui)

    def close(self):
        self.closing=True
        if self.timer is not None:self.ui.root.after_cancel(self.timer);self.timer=None
        self.cancel();self.buttons()
