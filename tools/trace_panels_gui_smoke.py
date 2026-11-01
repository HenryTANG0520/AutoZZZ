"""Actual native panel windows/alpha/drag/close, with owned synthetic identity only."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.overlay import NativePresentation
from autozzz.onedragon_trace_ui import TraceWindow
from autozzz.trace_preferences import DEFAULTS,TracePreferences
from onedragon_configuration_gui_smoke import layout
from onedragon_trace_gui_smoke import sample


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    before=windows.user32.GetForegroundWindow();cases=[];original=tk.Toplevel
    def hidden(*args,**kwargs):window=original(*args,**kwargs);window.withdraw();return window
    with tempfile.TemporaryDirectory(prefix='autozzz-floating-panels-') as folder:
        for scale in (1.,1.5,2.):
            root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72);root.geometry('960x540+40+40')
            native=NativePresentation(protect_capture=False);handle=native.prepare(root);root.deiconify()
            errors=[];root.report_callback_exception=lambda *error:errors.append(str(error))
            try:
                path=Path(folder)/str(scale)/'prefs.json'
                with patch('tkinter.Toplevel',hidden):view=TraceWindow(root,scale,lambda *_:1,presentation=native,preferences_path=path)
                native.prepare(view.window);view.window.deiconify();root.update()
                value=deepcopy(DEFAULTS);value['separate_panels']=True
                value['panels']['decision'].update(mode='desktop',opacity=35,desktop_x=80,desktop_y=100)
                value['panels']['timeline'].update(mode='desktop',opacity=90,desktop_x=550,desktop_y=100)
                view.save_preferences(value);view.cover.set(True);view.persist_overlay_options();data=sample(handle);view.update(data,view.version)
                with patch('tkinter.Toplevel',hidden):view.overlay.paint(960,540,(40,40,960,540))
                root.update();panel=view.overlay.panels['decision'];other=view.overlay.panels['timeline']
                assert panel.visible and other.visible
                assert abs(float(panel.window.attributes('-alpha'))-.35)<.01 and abs(float(other.window.attributes('-alpha'))-.90)<.01
                assert not native.get_style(panel.handle,-20)&0x20 and native.get_style(panel.handle,-20)&0x8000000,(panel.mode,hex(native.get_style(panel.handle,-20)),panel.visible)
                game_panel=view.overlay.panels['perf'];assert native.get_style(game_panel.handle,-20)&0x20
                for size in ('default','small'):
                    if size=='small':panel.show(['条件测试'],dict(panel.config,w=180,h=100),(*panel.bounds[:2],round(180*scale),round(100*scale)),value['text_color'])
                    root.update();layout(panel.window);cases.append([scale,size])
                panel.down(NS(x_root=100,y_root=100));panel.motion(NS(x_root=160,y_root=130));root.update();panel.up(NS())
                saved=TracePreferences(path).load();assert saved['panels']['decision']['desktop_x']!=80
                panel.change(collapsed=True);view.overlay.paint(960,540,(40,40,960,540));root.update();assert not panel.canvas.winfo_ismapped()
                panel.change(visible=False);view.overlay.paint(960,540,(40,40,960,540));root.update();assert not panel.visible
                view.update_state(dict(control=dict(observation_epoch=data['epoch']+1)));view.overlay.draw();root.update()
                assert not any(p.visible for p in view.overlay.panels.values()) and all(not p.canvas.find_all() for p in view.overlay.panels.values())
                view.close();assert all(p.closed for p in view.overlay.panels.values()) and not errors,errors
            finally:
                for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
                root.destroy()
    assert windows.user32.GetForegroundWindow()==before,'Changed user foreground'
    result=dict(layout_cases=cases,actual_independent_windows=True,per_panel_alpha=True,desktop_accepts_only_own_ui=True,game_panels_click_through=True,drag_persisted=True,collapse_close=True,epoch_clears_all=True,close_destroys_all=True,game_accessed=False,game_input_sent=False)
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
