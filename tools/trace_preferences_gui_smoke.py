"""Actual Tk calibration/layout editing with synthetic trace; no game or input."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.onedragon_trace_ui import TraceWindow
from autozzz.overlay import NativePresentation
from autozzz.trace_preferences import DEFAULTS, TracePreferences
from onedragon_configuration_gui_smoke import layout
from onedragon_trace_gui_smoke import sample


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    foreground=windows.user32.GetForegroundWindow();cases=[]
    original_toplevel=tk.Toplevel
    def hidden(*args,**kwargs):
        window=original_toplevel(*args,**kwargs);window.withdraw();return window
    with tempfile.TemporaryDirectory(prefix='autozzz-overlay-layout-') as folder:
        for scale in (1.,1.5,2.):
            root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72)
            errors=[];root.report_callback_exception=lambda *error:errors.append(str(error))
            native=NativePresentation(protect_capture=False);native.prepare(root);root.deiconify()
            path=Path(folder)/str(scale)/'settings.json'
            try:
                with patch('tkinter.Toplevel',hidden):view=TraceWindow(root,scale,lambda *_:1,presentation=native,preferences_path=path)
                native.prepare(view.window);view.window.deiconify()
                with patch('tkinter.Toplevel',hidden):view.open_advanced()
                editor=view.advanced;native.prepare(editor.window);editor.window.deiconify()
                for size in ('default','minimum'):
                    if size=='minimum':editor.window.geometry('%dx%d'%editor.window.minsize())
                    root.update()
                    book=next(child for child in editor.window.winfo_children() if isinstance(child,ttk.Notebook))
                    for page in book.tabs():
                        book.select(page);root.update();layout(editor.window);cases.append([scale,size,book.tab(page,'text')])
                        nested=[child for child in editor.window.nametowidget(page).winfo_children() if isinstance(child,ttk.Notebook)]
                        for tabs in nested:
                            for panel in tabs.tabs():tabs.select(panel);root.update();layout(editor.window);cases.append([scale,size,tabs.tab(panel,'text')])
                editor.variables['scale_x'].set('not a number');assert not editor.apply() and not path.exists()
                editor.defaults();editor.variables['scale_x'].set('1.25');editor.variables['offset_y'].set('12')
                editor.variables['separate_panels'].set(True);editor.variables[('metrics','ocr_ms')].set(False)
                editor.variables[('panels','state','x')].set('40');assert editor.apply()
                assert path.exists() and view.overlay.preferences['scale_x']==1.25
                data=sample(1);view.update(data,view.version);view.cover.set(True);view.overlay_options()
                view.overlay.paint(960,540) # Inspect synchronous paint before the invalid fixture HWND is hidden by the safety timer.
                assert len(view.overlay.panel_widgets)==3 # No active condition; three trace panels.
                performance=view.overlay.panel_widgets['perf']
                text=' '.join(str(performance.itemcget(item,'text')) for item in performance.find_all())
                assert 'ocr_ms' not in text and 'custom_ms' in text
                assert view.tables['perf'].get_children() # Overlay filtering does not drop source trace.
                view.close()
                with patch('tkinter.Toplevel',hidden):reopened=TraceWindow(root,scale,lambda *_:1,presentation=native,preferences_path=path)
                assert reopened.preferences['scale_x']==1.25 and reopened.preferences['panels']['state']['x']==40
                with patch('tkinter.Toplevel',hidden):reopened.open_advanced()
                reopened.advanced.defaults();assert reopened.advanced.apply()
                assert TracePreferences(path).load()==DEFAULTS
                reopened.close();assert reopened.advanced.closed
                assert not errors,errors
            finally:
                for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
                root.destroy()
    assert foreground==windows.user32.GetForegroundWindow(),'Changed user foreground'
    result=dict(layout_cases=cases,invalid_draft_preserved=True,persist_reopen=True,reset_saved=True,metric_filter_applied=True,game_accessed=False,game_input_sent=False)
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
