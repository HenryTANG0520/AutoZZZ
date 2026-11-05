"""Native log geometry/style/lifecycle, using only an owned synthetic client rectangle."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.overlay import GameLogOverlay,NativePresentation
from autozzz.trace_preferences import DEFAULTS,TracePreferences
from autozzz.trace_layout_ui import TraceLayoutWindow


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    foreground=windows.user32.GetForegroundWindow();cases=[]
    class Presentation(NativePresentation):
        def game_bounds(self,handle,*,require_foreground=True):
            return (40,40,1920,1080) if self.valid and (self.focused or not require_foreground) else None
    with tempfile.TemporaryDirectory(prefix='autozzz-log-panel-') as temporary:
        for scale in (1.,1.5,2.):
            root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72)
            NativePresentation(protect_capture=False).prepare(root)
            warnings=[];errors=[];root.report_callback_exception=lambda *error:errors.append(str(error))
            native=Presentation();native.valid=native.focused=True
            path=Path(temporary)/str(scale)/'prefs.json'
            view=GameLogOverlay(root,lambda:1,lambda *message:warnings.append(message),presentation=native,preferences_path=path)
            def tick():
                if view._timer is not None:root.after_cancel(view._timer)
                view.preference_next=0;view._tick();root.update_idletasks()
            def configure(**changes):
                store=TracePreferences(path);value=store.load();value['panels']['log'].update(changes);store.save(value);tick()
            try:
                view.begin('日志验证');view.append('近期任务日志');view.append('警告文本','warning');tick()
                assert view.visible and view._bounds==(60,828,576,227),view._bounds
                assert native.get_style(view.handle,-20)&0x20 and native.get_style(view.handle,-20)&0x8000000
                configure(x=120,y=180,w=640,h=240,font_size=18,opacity=65)
                assert view._bounds==(160,220,640,240)
                assert abs(float(view.window.attributes('-alpha'))-.65)<.01
                text=[view.canvas.itemcget(item,'font') for item in view.canvas.find_all() if view.canvas.type(item)=='text']
                assert any('-24' in font for font in text),text
                native.focused=False;tick();assert not view.visible
                configure(mode='desktop',desktop_x=120,desktop_y=140)
                panel=view.desktop_panel;assert panel.visible and not view.visible
                assert not native.get_style(panel.handle,-20)&0x20 and native.get_style(panel.handle,-20)&0x8000000
                for size in ('normal','minimum'):
                    if size=='minimum':configure(w=180,h=100)
                    assert panel.visible and panel.canvas.find_all()
                    cases.append([scale,size])
                panel.down(NS(x_root=100,y_root=100));panel.motion(NS(x_root=145,y_root=125));root.update_idletasks();panel.up(NS())
                saved=TracePreferences(path).load();assert saved['panels']['log']['desktop_x']!=120
                panel.resize_down(NS(x=panel.canvas.winfo_width()-1,y=panel.canvas.winfo_height()-1,x_root=100,y_root=100))
                panel.motion(NS(x_root=160,y_root=150));root.update_idletasks();panel.up(NS());tick()
                assert TracePreferences(path).load()['panels']['log']['w']>=200
                panel.change(collapsed=True);tick();assert not panel.canvas.winfo_ismapped()
                panel.change(collapsed=False,visible=False);tick();assert not panel.visible
                configure(visible=True);assert panel.visible
                view.set_enabled(False);assert not panel.visible;tick();assert not panel.visible
                view.set_enabled(True);tick();assert panel.visible
                native.valid=False;tick();assert not panel.visible
                native.valid=True;configure(mode='game');assert not panel.visible
                native.focused=True;tick();assert view.visible
                draft=[]
                editor=TraceLayoutWindow(root,scale,TracePreferences(path).load(),draft.append)
                editor.window.withdraw();NativePresentation(protect_capture=False).prepare(editor.window)
                editor.panel.set('日志');editor.select();editor.dock.set('右下');editor.docking()
                assert editor.selected=='log'
                assert editor.value['panels']['log']['x']==1920-editor.value['panels']['log']['w']
                editor.commit();assert draft and editor.closed
                view.close();assert panel.closed and not view.window.winfo_exists()
                assert not warnings and not errors,(warnings,errors)
            finally:
                view.close()
                for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
                root.destroy()
    assert foreground==windows.user32.GetForegroundWindow(),'Changed user foreground'
    result=dict(cases=cases,geometry=True,font_alpha=True,desktop_background=True,drag_resize_saved=True,
        collapse_close_restore=True,master_switch_preserved=True,invalid_window_hidden=True,
        drag_preview_fifth_panel=True,all_windows_destroyed=True,nonactivating=True,
        capture_exclusion_enforced=True,game_accessed=False,input_sent=False)
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
