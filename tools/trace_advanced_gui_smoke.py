"""Actual Tk drag/dock, hotkey edges and full-resolution screenshot composition; no game."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.onedragon_trace_ui import TraceWindow
from autozzz.overlay import NativePresentation
from autozzz.trace_capture import capture_directory
from autozzz.trace_preferences import DEFAULTS,TracePreferences
from onedragon_configuration_gui_smoke import layout
from onedragon_trace_gui_smoke import sample


class Session:
    def __init__(self,root):self.paths=NS(data=root);self.version=0;self.calls=[]
    def trace(self,enabled,clear=False,*,capture=False):
        self.calls.append((enabled,clear,capture));self.version+=1;return self.version


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    foreground=windows.user32.GetForegroundWindow();cases=[];original_toplevel=tk.Toplevel
    def hidden(*args,**kwargs):
        window=original_toplevel(*args,**kwargs);window.withdraw();return window
    with tempfile.TemporaryDirectory(prefix='autozzz-trace-advanced-') as folder:
        for scale in (1.,1.5,2.):
            data_root=Path(folder)/str(scale);session=Session(data_root);root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72)
            errors=[];root.report_callback_exception=lambda *error:errors.append(str(error));native=NativePresentation(protect_capture=False);native.prepare(root);root.deiconify()
            try:
                with patch('tkinter.Toplevel',hidden):view=TraceWindow(root,scale,session.trace,presentation=native,preferences_path=data_root/'settings.json')
                native.prepare(view.window);view.window.deiconify();view.update(sample(1),view.version)
                with patch('tkinter.Toplevel',hidden):view.open_advanced()
                editor=view.advanced;native.prepare(editor.window);editor.window.deiconify()
                with patch('tkinter.Toplevel',hidden):editor.open_layout()
                drag=editor.layout_window;native.prepare(drag.window);drag.window.deiconify();root.update()
                for size in ('default','minimum'):
                    if size=='minimum':drag.window.geometry('%dx%d'%drag.window.minsize())
                    root.update();layout(drag.window);cases.append([scale,size,'drag-layout'])
                drag.selected='decision';drag.panel.set('决策');drag.dock.set('左上');drag.docking()
                self_panel=drag.value['panels']['decision'];assert self_panel['x']==self_panel['y']==0
                width,height=drag.canvas.winfo_width(),drag.canvas.winfo_height()
                drag.down(NS(x=5,y=5));drag.motion(NS(x=round(width*.2),y=round(height*.2)))
                assert drag.value['panels']['decision']['x']>0
                drag.commit();assert editor.variables['separate_panels'].get();assert editor.apply()
                assert TracePreferences(data_root/'settings.json').load()['separate_panels']
                from PIL import Image
                directory=capture_directory(data_root);directory.mkdir(parents=True,exist_ok=True)
                raw=directory/('a'*32+'.png');Image.new('RGB',(3840,2160),(180,80,20)).save(raw)
                view.request_capture();assert session.calls[-1]==(True,False,True)
                image=sample(1);image['capture']=dict(path=str(raw),sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),width=3840,height=2160,epoch=image['epoch'],generation=image['generation'])
                view.update(image,view.version);assert raw.with_stem(raw.stem+'_overlay').exists()
                exported=Image.open(raw.with_stem(raw.stem+'_overlay'));assert exported.size==(3840,2160);exported.close()
                # Old epoch must not be composited after a more recent controller state.
                late=directory/('b'*32+'.png');late.write_bytes(raw.read_bytes());view.request_capture()
                view.state=dict(control=dict(observation_epoch=99));image=deepcopy(image);image['capture']['path']=str(late)
                image['capture']['sha256']=hashlib.sha256(late.read_bytes()).hexdigest();view.captured.clear();view.update(image,view.version)
                assert not late.with_stem(late.stem+'_overlay').exists()
                view.close();assert view.hotkey_token is None and not view.hotkeys.bindings
                assert not errors,errors
            finally:
                for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
                root.destroy()
    assert windows.user32.GetForegroundWindow()==foreground,'Changed user foreground'
    result=dict(layout_cases=cases,drag_and_dock_saved=True,full_resolution_png=(3840,2160),late_epoch_rejected=True,close_cleans_hotkeys=True,game_accessed=False,game_input_sent=False)
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
