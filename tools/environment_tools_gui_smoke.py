"""Actual management window layout, requests, error recovery and delayed close; no network."""
import argparse
import json
from pathlib import Path
import sys
import threading
import tkinter as tk
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.overlay import NativePresentation
from autozzz.environment_tools_ui import EnvironmentToolsWindow
from autozzz.environment_tools import INDEXES
from onedragon_configuration_gui_smoke import layout


class Session:
    def __init__(self,paths,emit):
        self.emit=emit;self.sent=[];self.closing=threading.Event();self.allowed=threading.Event()
        def work():self.closing.wait();self.allowed.wait();emit(dict(type='closed'))
        self.worker=threading.Thread(target=work);self.worker.start();emit(dict(type='connected'))
    def send(self,command,**params):self.sent.append((command,params))
    def close(self):self.closing.set()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True);before=windows.user32.GetForegroundWindow();cases=[]
    original=tk.Toplevel
    def hidden(*args,**kwargs):window=original(*args,**kwargs);window.withdraw();return window
    for scale in (1.,1.5,2.):
        root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72)
        native=NativePresentation(protect_capture=False);native.prepare(root)
        errors=[];root.report_callback_exception=lambda *error:errors.append(str(error))
        with patch('autozzz.environment_tools_ui.RuntimeSession',Session),patch('tkinter.Toplevel',hidden):
            view=EnvironmentToolsWindow(NS(root=root,ui_scale=scale),'cn')
        native.prepare(view.window);view.window.deiconify();root.update()
        try:
            view.poll();view.events.put(dict(type='rpc_result',tag='environment_tools_catalog',result=dict(revision='fixture',pip_source=INDEXES[0],options=[],proxy_configured=False)));view.poll()
            for size in ('default','minimum'):
                if size=='minimum':view.window.geometry('%dx%d'%view.window.minsize())
                root.update();layout(view.window);cases.append([scale,size])
            view.start('pip_test');assert view.busy
            method=view.session.sent[-1][1];assert method['method']=='environment_tools_start' and method['params']['action']=='pip_test'
            view.events.put(dict(type='error',tag='environment_tools_start',message='fixture rejected'));view.poll()
            assert not view.busy and view.callback is None
            view.start('pip_test')
            result=dict(job_id='fixture',busy=False,state='success',revision='fixture',detail='检查完成',candidate=INDEXES[1],
                results=[dict(label='清华大学',value=INDEXES[1],elapsed_ms=25,error='')])
            view.events.put(dict(type='rpc_result',tag='environment_tools_start',result=result));view.poll()
            assert not view.busy and view.candidates and not view.actions[1].instate(['disabled'])
            view.apply();assert view.session.sent[-1][1]['method']=='environment_tools_apply'
            assert view.session.sent[-1][1]['params']['value']==INDEXES[1]
            view.close();view.poll();assert view.closing and not view.closed and view.worker.is_alive()
            view.session.allowed.set();view.worker.join(3);view.poll();assert view.closed
            assert not errors,errors
        finally:
            view.session.allowed.set();view.session.close();view.worker.join(3)
            for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
            root.destroy()
    assert before==windows.user32.GetForegroundWindow(),'Changed foreground'
    result=dict(layout_cases=cases,actual_rpc_actions=True,error_releases_busy=True,explicit_apply=True,
        delayed_close_waits_original_worker=True,foreground_unchanged=True,game_accessed=False,network_accessed=False)
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
