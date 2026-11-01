"""Actual Tk schedule CRUD and review with isolated files and a recording Windows backend."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.schedules import ScheduleStore
from autozzz.schedule_manager import ScheduleManager
from autozzz.schedule_runtime import write_record
from autozzz.schedules_ui import open_schedules
from tests.test_schedule_manager import Backend
from ui_layout_smoke import check_layout


def pump(root, predicate):
    end=time.monotonic()+30
    while time.monotonic()<end:
        root.update()
        if predicate():return
        time.sleep(.02)
    raise AssertionError('Schedule UI timed out')


def find(widget, label):
    for child in widget.winfo_children():
        if child.winfo_class()=='TButton' and child.cget('text')==label:return child
        result=find(child,label)
        if result:return result


def main():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='autozzz-schedule-ui-') as tmp:
        folder=Path(tmp);game=folder/'ZenlessZoneZero.exe';game.touch();(folder/'AutoZZZ.exe').touch()
        config=folder/'config.json';Settings(game_path=str(game)).save(config);before=config.read_bytes()
        for scale in (1.,1.5,2.):
            root=tk.Tk();root.tk.call('tk','scaling',scale*96/72)
            errors=[];root.report_callback_exception=lambda kind,error,tb:errors.append(str(error))
            store=ScheduleStore(folder,folder/f'{scale}/plans.json');backend=Backend();manager=ScheduleManager(store,backend);backend.manager=manager
            with patch('autozzz.gui.AutoZZZWindow._discover_report'):
                ui=AutoZZZWindow(root,config,background_helpers=False)
            ui.overlay.set_enabled(False)
            try:
                ui._show_page('settings');ui.show_settings_section('schedules');root.update();check_layout(ui.pages['settings'])
                with patch('autozzz.schedules_ui.ScheduleStore',return_value=store),patch('autozzz.schedules_ui.ScheduleManager',return_value=manager):open_schedules(ui)
                dialog=ui.schedules_window;pump(root,lambda:dialog.catalog is not None)
                for size in ('default','minimum'):
                    if size=='minimum':dialog.window.geometry('%dx%d'%dialog.window.minsize())
                    root.update();check_layout(dialog.window);checks.append(f'{scale}-{size}')
                dialog.edit();root.update();check_layout(dialog.editor)
                dialog.editor.minsize(round(620*scale),round(360*scale))
                dialog.editor.geometry('%dx%d'%dialog.editor.minsize());root.update();check_layout(dialog.editor)
                with patch('autozzz.schedules_ui.native_query',return_value=dict(accounts=dict(current=1,instances=[dict(idx=1,name='测试账号')]),catalog=dict(applications=[dict(id='one_dragon',name='一条龙',executable=True,internal=False)]))):
                    dialog.values['mode'].set('指定账号应用');dialog.field_states();pump(root,lambda:dialog.choice_profile=='cn')
                assert str(dialog.fields['instance']['state'])=='readonly' and dialog.values['instance'].get()=='测试账号 · 1'
                dialog.values['mode'].set('原每日流程');dialog.field_states();assert str(dialog.fields['instance']['state'])=='disabled'
                # Save actual reviewed daily configuration, through the visible confirmation dialog.
                dialog.values['name'].set('中文 每日');dialog.enable_after.set(True);dialog.review()
                pump(root,lambda:find(dialog.editor,'确认并保存') is not None)
                confirm=find(dialog.editor,'确认并保存');root.update();check_layout(confirm.winfo_toplevel());confirm.invoke()
                pump(root,lambda:len(dialog.catalog['plans'])==1 and dialog.editor is None)
                key=dialog.catalog['plans'][0]['plan']['id'];assert dialog.catalog['plans'][0]['bound']
                dialog.plans.selection_set(key)
                with patch('autozzz.schedules_ui.messagebox.askokcancel',return_value=True):dialog.run()
                pump(root,lambda:any(call[0]=='run' for call in backend.calls) and dialog.worker is not None and not dialog.worker.is_alive())
                dialog.disable();pump(root,lambda:not dialog.catalog['plans'][0]['plan']['enabled'])
                occurrence='c'*32;record=store.path.parent/'runs'/key/occurrence/'record.json';record.parent.mkdir(parents=True)
                write_record(record,dict(format=1,owner=store.owner,schedule_id=key,instance=occurrence,name='中文 每日',started_at='2026-11-01',state='running',process=dict(pid=1,created=2)))
                dialog.refresh();pump(root,lambda:len(dialog.history)==1);dialog.book.select(1);dialog.records.selection_set('0')
                with patch('autozzz.schedule_history.process_alive',return_value=True):
                    # Explicit dependency injection for process identity in this no-process fixture.
                    from autozzz.schedule_history import request_stop
                    with patch('autozzz.schedules_ui.request_stop',side_effect=lambda *a:request_stop(*a,alive=lambda p:True)):
                        dialog.stop();pump(root,lambda:(record.parent/'stop').exists())
                dialog.book.select(0);dialog.plans.selection_set(key)
                with patch('autozzz.schedules_ui.messagebox.askokcancel',return_value=True):dialog.remove()
                pump(root,lambda:len(dialog.catalog['plans'])==0)
                assert len(dialog.history)==1 and record.exists()
                dialog.edit();dialog.values['name'].set('未保存')
                with patch('autozzz.schedules_ui.messagebox.askokcancel',return_value=False):dialog.close_editor()
                assert dialog.editor.winfo_exists()
                dialog.close_editor(force=True)
                checks.append(f'{scale}-review-save-run-disable-stop-remove-retain-history')
                dialog.close();pump(root,lambda:dialog.closed)
                assert not errors,errors
            finally:
                ui.closing=True;ui.overlay.close()
                for token in root.tk.splitlist(root.tk.call('after','info')):root.after_cancel(token)
                root.destroy()
        assert config.read_bytes()==before
    print(json.dumps(dict(checks=checks,game_started=False,game_input_sent=False,system_tasks_created=False,config_unchanged=True),ensure_ascii=False))


if __name__=='__main__':main()
