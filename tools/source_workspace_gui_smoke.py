"""Source workspace window layout and close lifecycle without an SDK or network."""
import json
from pathlib import Path
import sys
import tkinter as tk
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.source_workspace_ui import SourceBranchesWindow
from autozzz.overlay import NativePresentation
from autozzz import windows
from onedragon_configuration_gui_smoke import layout

class Worker:
    def __init__(self,*args,**kwargs):pass
    def start(self):pass

foreground=windows.user32.GetForegroundWindow();checks=[]
for scale in (1.,1.5,2.):
    root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72)
    native=NativePresentation(protect_capture=False);native.prepare(root);root.deiconify()
    try:
        with patch('autozzz.source_workspace_ui.threading.Thread',Worker):view=SourceBranchesWindow(NS(root=root,ui_scale=scale),'cn')
        native.prepare(view.window)
        view.events.put(('catalog',dict(path='D:/LocalAppData/AutoZZZ/OneDragon/cn/autozzz/upstream-source',
            branch='main',head='a'*40,repository='https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon.git',
            branches=['main','feature/example'],dirty=True,changes=' M src/example.py',diff='src/example.py | 1 +')))
        view.poll();root.update()
        for size in ('default','minimum'):
            if size=='minimum':view.window.geometry('%dx%d'%view.window.minsize())
            root.update();layout(view.window);checks.append([scale,size])
        view.branch.set('feature/example');view.request('select')
        assert view.commands.get_nowait()==('select','feature/example') and view.busy
        view.close();assert view.closing and not view.closed
        view.events.put(('closed',None));view.poll();assert view.closed
    finally:root.destroy()
assert windows.user32.GetForegroundWindow()==foreground
print(json.dumps(dict(layout=checks,request_branch=True,close_waits_for_worker=True,game=False,network=False)))
