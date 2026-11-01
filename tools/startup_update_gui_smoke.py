"""Actual Tk startup-check settings/feedback/close lifecycle; SDK client is a recorder."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.program_update_check import MetadataCheck
from autozzz.program_update_preferences import UpdatePreferences,UpdatePreferencesStore
from tests.test_program_update_check import Client
from ui_layout_smoke import check_layout


def pump(root,predicate):
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        root.update()
        if predicate():return
        time.sleep(.02)
    raise AssertionError('Startup update UI timed out')


class Recorder(Client):
    def __init__(self):super().__init__(None);self.hold=False;self.cancelled=False
    def result(self,key,timeout):
        result=super().result(key,timeout)
        method=self.calls[int(key)-1][0]
        if method=='program_cancel':self.cancelled=True
        if self.hold and method in ('program_status','program_cancel'):
            return dict(job_id='original',busy=True,state='working',detail='等待原检查结束')
        if self.cancelled and method=='program_status':return dict(job_id='original',busy=False,state='cancelled',detail='已取消')
        return result


def main():
    cases=[];clients=[]
    with tempfile.TemporaryDirectory(prefix='autozzz-startup-ui-') as tmp:
        folder=Path(tmp);config=folder/'config.json';Settings().save(config);original=config.read_bytes()
        for scale in (1.,1.5,2.):
            preferences=UpdatePreferencesStore(folder,folder/f'{scale}.json');preferences.save(UpdatePreferences(True,'beta'),preferences.load())
            def make(*args,**kwargs):
                client=Recorder();clients.append(client);return MetadataCheck(*args,**kwargs,client_factory=lambda _:client)
            errors=[];root=tk.Tk();root.tk.call('tk','scaling',scale*96/72);root.report_callback_exception=lambda kind,error,tb:errors.append(str(error))
            with patch('autozzz.program_update_check_ui.UpdatePreferencesStore',return_value=preferences),patch('autozzz.program_update_check_ui.MetadataCheck',side_effect=make),patch('autozzz.gui.AutoZZZWindow._discover_report'),patch('autozzz.onedragon_pip_ui.PipController',return_value=None):
                ui=AutoZZZWindow(root,config,background_helpers=True);ui.overlay.set_enabled(False)
                ui._show_page('settings');ui.show_settings_section('updates')
                for size in ('default','minimum'):
                    if size=='minimum':root.geometry('%dx%d'%root.minsize())
                    root.update();check_layout(ui.pages['settings']);cases.append(f'{scale}-{size}')
                pump(root,lambda:ui.startup_updates.checker is not None and not ui.startup_updates.worker_alive)
                pump(root,lambda:'没有完整发布包' in ui.startup_updates.status.get())
                assert clients[-1].calls[0]==('program_check',dict(channel='beta',page=1))
                previous=len(clients);ui.program_updates_window=SimpleNamespace(closed=False)
                ui.startup_updates.check();assert len(clients)==previous
                ui.program_updates_window=None
                previous=len(clients);ui.startup_updates.check();held=clients[-1];held.hold=True
                ui.startup_updates.check();assert len(clients)==previous+1
                pump(root,lambda:ui.startup_updates.checker.job_id is not None)
                ui.startup_updates.cancel();pump(root,lambda:held.cancelled)
                assert ui.startup_updates.worker_alive
                held.hold=False;pump(root,lambda:not ui.startup_updates.worker_alive)
                ui.startup_updates.enabled.set(False);assert ui.startup_updates.save()
                assert not preferences.load().enabled and preferences.load().channel=='beta'
                # Main-window close keeps Tk alive until the original check and process actually finish.
                ui.startup_updates.check();held=clients[-1];held.hold=True
                pump(root,lambda:ui.startup_updates.checker.job_id is not None)
                ui._close();pump(root,lambda:held.cancelled)
                assert ui.startup_updates.worker_alive and ui.root.winfo_exists()
                held.hold=False
                pump(root,lambda:not ui.startup_updates.worker_alive)
                for token in root.tk.splitlist(root.tk.call('after','info')):root.after_cancel(token)
                try:root.destroy()
                except tk.TclError:pass
                assert not errors,errors;cases.append(f'{scale}-startup-single-request-cancel-preferences-cooperative-close')
        assert config.read_bytes()==original
    assert all(method in ('program_check','program_status','program_cancel') for client in clients for method,_ in client.calls)
    print(json.dumps(dict(cases=cases,game_started=False,network_fixture=True,download_started=False,installation_started=False,daily_config_unchanged=True)))


if __name__=='__main__':main()
