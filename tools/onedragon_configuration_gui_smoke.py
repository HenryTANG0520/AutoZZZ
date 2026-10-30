"""用真实工作进程验证原生配置页面、记录编辑及三种 DPI；不访问游戏。"""
from __future__ import annotations

import argparse
import copy
import gc
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows  # DPI awareness before Tk.
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_runtime import RuntimePaths


def pump(root,predicate,timeout=45):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        root.update()
        if predicate():return
        time.sleep(.02)
    raise AssertionError("配置界面等待超时")


def layout(widget):
    # A Canvas is the explicit scrolling viewport: its content may exceed its height.
    for child in widget.winfo_children():
        if not child.winfo_ismapped():continue
        x,y,w,h = child.winfo_x(),child.winfo_y(),child.winfo_width(),child.winfo_height()
        if not isinstance(widget,tk.Canvas):
            assert x>=-2 and y>=-2,(str(child),"position",x,y)
            assert x+w<=widget.winfo_width()+2,(str(child),"width",x,w,widget.winfo_width())
            assert y+h<=widget.winfo_height()+2,(str(child),"height",y,h,widget.winfo_height())
        if isinstance(child,(ttk.Label,ttk.Button,ttk.Checkbutton)):
            assert w>=child.winfo_reqwidth(),(str(child),"text width",w,child.winfo_reqwidth())
            assert h>=child.winfo_reqheight(),(str(child),"text height",h,child.winfo_reqheight())
        layout(child)


class SettingsSessionFixture:
    def __init__(self,paths,target,emit):
        self.paths,self.target,self.emit=paths,target,emit
        self.closing=threading.Event();self.allowed=threading.Event()
        def work():
            emit(dict(type='ready'));self.closing.wait();self.allowed.wait();emit(dict(type='closed'))
        self.worker=threading.Thread(target=work,daemon=False);self.worker.start()
    def close(self):self.closing.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview",type=Path)
    args = parser.parse_args()
    defaults = RuntimePaths.discover(Path(__file__).resolve().parents[1])
    cases,dialogs = 0,0
    snapshot = None
    with tempfile.TemporaryDirectory(prefix="autozzz-config-gui-") as folder:
        base = Path(folder)
        paths = RuntimePaths(defaults.source,defaults.python,base/"profile")
        config = base/"config.json"
        Settings().save(config)
        original = config.read_bytes()
        with patch("autozzz.onedragon_apps_ui.RuntimePaths.discover",return_value=paths),patch("autozzz.gui.AutoZZZWindow._discover_report"):
            for scale in (1.,1.5,2.):
                root = tk.Tk()
                root.tk.call("tk","scaling",scale*96/72)
                errors = []
                root.report_callback_exception = lambda kind,value,traceback:errors.append(value)
                ui = AutoZZZWindow(root,config,background_helpers=False)
                panel = ui.onedragon_panel
                try:
                    ui.overlay.set_enabled(False)
                    ui._show_page("applications")
                    root.update()
                    if snapshot is None:
                        panel.load_button.invoke()
                        pump(root,lambda:not panel.busy and bool(panel.cache))
                        snapshot = dict(apps=list(panel.apps.values()),options=copy.deepcopy(panel.options),descriptions=copy.deepcopy(panel.cache),settings_target=copy.deepcopy(panel.settings_target))
                    else:
                        panel._loaded(copy.deepcopy(snapshot))
                        root.update()
                    assert len(panel.cache)==33,(panel.status.get(),len(panel.cache))
                    for size in ("default","minimum"):
                        if size=="minimum":root.geometry("%dx%d" % root.minsize())
                        for key,description in panel.cache.items():
                            panel.tree.selection_set(key)
                            panel.select()
                            root.update()
                            assert bool(panel.native_button.winfo_ismapped())==bool(panel.apps[key].get('native_settings'))
                            if panel.apps[key].get('native_settings'):assert panel.native_button.instate(['!disabled'])
                            assert panel.current_id==key
                            if panel.form is not None:
                                try: panel.form.values()
                                except ValueError as exc: raise AssertionError((key,str(exc))) from exc
                            assert bool(panel.note_label.winfo_ismapped())==bool(panel.note.get())
                            layout(root)
                            cases+=1
                        for key,field_name in (("charge_plan","plan_list"),("teams","team_list"),("redemption_code","codes")):
                            panel.tree.selection_set(key)
                            panel.select()
                            root.update()
                            editor = panel.collections[field_name]
                            editor.edit(new=key!="teams")
                            root.update()
                            dialog = editor.dialog
                            assert dialog is not None
                            layout(dialog.window)
                            dialog.form.values()
                            if key=='charge_plan':
                                form=dialog.form;choice=form.selectors['category_name'];previous=choice.variable.get()
                                choice.variable.set(next(item['label'] for item in choice.options if item['value']=='合成电池'))
                                choice.widget.event_generate('<<ComboboxSelected>>');root.update();layout(dialog.window)
                                values=form.values();assert values['mission_type_name']=='' and values['mission_name'] is None
                                assert form.synthesis_note.winfo_ismapped()
                                assert not form.selectors['mission_type_name'].widget.winfo_ismapped()
                                assert not form.form.lines['auto_battle_config'].winfo_ismapped()
                                assert form.form.lines['plan_times'].winfo_ismapped()
                                choice.variable.set(previous);choice.widget.event_generate('<<ComboboxSelected>>');root.update()
                                assert form.selectors['mission_type_name'].widget.winfo_ismapped() and form.form.lines['auto_battle_config'].winfo_ismapped()
                                assert not form.synthesis_note.winfo_ismapped();form.values();layout(dialog.window)
                            dialog.window.destroy()
                            dialogs+=1
                        panel.tree.selection_set("shiyu_defense")
                        panel.select()
                        root.update()
                        assert "team_list" not in panel.collections
                        shortcut=next(child for child in panel.content.winfo_children() if isinstance(child,ttk.Button))
                        shortcut.invoke()
                        root.update()
                        assert panel.current_id=="teams"
                    if scale==1.:
                        panel.tree.selection_set('_game_input');panel.select();root.update()
                        panel.form.controls['key_normal_attack'].variable.set('j');root.update()
                        assert panel.dirty
                        panel.save_button.invoke();pump(root,lambda:not panel.busy)
                        assert not panel.dirty and '已保存' in panel.status.get(),panel.status.get()
                        panel.reload_button.invoke();pump(root,lambda:not panel.busy)
                        assert panel.form.values()['key_normal_attack']=='j'
                        if args.preview:
                            from PIL import ImageGrab
                            args.preview.parent.mkdir(parents=True,exist_ok=True)
                            ImageGrab.grab(window=root.winfo_id()).save(args.preview.with_name('game-input.png'))
                        panel.tree.selection_set("charge_plan")
                        panel.select()
                        root.update()
                        editor = panel.collections["plan_list"]
                        editor.edit(new=True)
                        root.update()
                        dialog = editor.dialog
                        form = dialog.form
                        form.selectors["category_name"].variable.set("实战模拟室")
                        form._refresh_type("代理人晋升","异常演练")
                        form.form.controls["predefined_team_idx"].variable.set(panel.options["teams"][0]["label"])
                        dialog.confirm()
                        root.update()
                        assert panel.dirty and editor.rows[0]["mission_name"]=="异常演练"
                        panel.save_button.invoke()
                        pump(root,lambda:not panel.busy)
                        assert not panel.dirty and "已保存" in panel.status.get(),panel.status.get()
                        panel.reload_button.invoke()
                        pump(root,lambda:not panel.busy)
                        assert panel.collections["plan_list"].rows[0]["mission_name"]=="异常演练"
                        if args.preview:
                            from PIL import ImageGrab
                            args.preview.parent.mkdir(parents=True,exist_ok=True)
                            root.geometry("1280x860")
                            root.update()
                            time.sleep(.1)
                            ImageGrab.grab(window=root.winfo_id()).save(args.preview)
                        # The UI must submit the edited draft, not persist every field's default.
                        panel.tree.selection_set("coffee")
                        panel.select()
                        root.update()
                        control = panel.form.controls["run_charge_plan_afterwards"]
                        assert control.cget("text")==next(f["label"] for f in panel.description["fields"] if f["name"]=="run_charge_plan_afterwards")
                        control.invoke()
                        root.update()
                        assert panel.dirty and panel.save_button.instate(["!disabled"])
                        with patch("autozzz.onedragon_apps_ui.messagebox.askokcancel",return_value=False):
                            panel.tree.selection_set("teams")
                            panel.select()
                            root.update()
                            assert panel.current_id=="coffee" and panel.dirty
                        panel.save_button.invoke()
                        assert panel.busy and control.instate(["disabled"])
                        pump(root,lambda:not panel.busy)
                        assert not panel.dirty and "已保存" in panel.status.get(),panel.status.get()
                        panel.reload_button.invoke()
                        pump(root,lambda:not panel.busy)
                        field = next(f for f in panel.description["fields"] if f["name"]=="run_charge_plan_afterwards")
                        assert field["value"] is True
                        # A worker result cannot discard edits or save them against a stale revision.
                        panel.description["revision"]="stale"
                        panel.form.controls["run_charge_plan_afterwards"].invoke()
                        panel.save_button.invoke()
                        pump(root,lambda:not panel.busy)
                        assert panel.dirty and "其他窗口" in panel.status.get(),panel.status.get()
                        with patch("autozzz.onedragon_apps_ui.messagebox.askokcancel",return_value=True):
                            panel.reload_button.invoke()
                        pump(root,lambda:not panel.busy)
                        assert not panel.dirty
                        # 错误需要字段与原因各出现一次，格式和数值范围仍可追溯。
                        panel.tree.selection_set("battle_assistant")
                        panel.select()
                        root.update()
                        numeric=next(f for f in panel.description["fields"] if f["kind"] in ("int","float"))
                        entry=panel.form.controls[numeric["name"]]
                        entry.delete(0,"end")
                        entry.insert(0,"invalid")
                        try:panel.form.values()
                        except ValueError as exc:
                            assert str(exc).count(numeric["label"])==1,str(exc)
                            assert "请输入" in str(exc)
                        else:raise AssertionError("无效数字必须给出字段错误")
                        panel.dirty=False
                        # 原生专用窗口关闭尚未结束时不能重读、保存或启动另一个任务。
                        panel.tree.selection_set('charge_plan');panel.select();root.update()
                        assert panel.native_button.instate(['!disabled'])
                        with patch('autozzz.onedragon_settings.NativeSettingsSession',SettingsSessionFixture):
                            panel.native_button.invoke();session=panel.native_session
                            assert panel.busy and session.target['instance_idx']==panel.settings_target['instance_idx']
                            assert panel.run_button.instate(['disabled']) and panel.save_button.instate(['disabled'])
                            session.close();root.update();assert panel.busy and session.worker.is_alive()
                            session.allowed.set();pump(root,lambda:not panel.busy and panel.native_session is None)
                        assert panel.current_id=='charge_plan' and panel.settings_target and not panel.dirty
                    assert not errors,errors
                    assert config.read_bytes()==original,"legacy daily settings changed"
                    if scale==2.:
                        panel.reload_button.invoke()
                        assert panel.busy
                        assert panel.close(discard=True) and not panel.closed
                        pump(root,lambda:panel.closed)
                        assert not panel.worker.is_alive()
                finally:
                    panel.close(discard=True)
                    pump(root,lambda:panel.closed)
                    ui.overlay.close()
                    for token in root.tk.splitlist(root.tk.call("after","info")):root.after_cancel(token)
                    root.destroy()
                ui = panel = editor = dialog = form = control = None
                gc.collect()  # The test creates several independent Tcl interpreters in one process.
    print(json.dumps(dict(layout_cases=cases,dialogs=dialogs,dpi=[100,150,200],real_save_reload=True,
                          close_waits_original_worker=True,native_settings_close_wait_and_fresh_reload=True,legacy_config_unchanged=True,game_started=False),ensure_ascii=False))


if __name__ == "__main__":main()
