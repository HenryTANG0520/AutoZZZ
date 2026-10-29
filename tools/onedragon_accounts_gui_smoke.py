"""账号原生窗口与实际工作进程的交互回归，不启动游戏。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_accounts_ui import AccountsWindow
from autozzz.onedragon_runtime import RuntimePaths
from onedragon_configuration_gui_smoke import layout,pump


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--preview',type=Path)
    args=parser.parse_args()
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1])
    cases=[]
    with tempfile.TemporaryDirectory(prefix='autozzz-account-ui-') as folder:
        config=Path(folder)/'daily.json'
        Settings().save(config)
        original=config.read_bytes()
        paths=RuntimePaths(defaults.source,defaults.python,Path(folder)/'profile')
        with patch('autozzz.onedragon_accounts_ui.RuntimePaths.discover',return_value=paths),patch('autozzz.gui.AutoZZZWindow._discover_report'):
            for scale in (1.,1.5,2.):
                root=tk.Tk()
                destroyed=threading.Event()
                root.bind('<Destroy>',lambda event:destroyed.set() if event.widget is root else None,add='+')
                root.tk.call('tk','scaling',scale*96/72)
                root.tk.eval('set autozzz_bgerrors {}; proc bgerror {message} {lappend ::autozzz_bgerrors $message}')
                errors=[]
                root.report_callback_exception=lambda kind,value,tb:errors.append(value)
                ui=AutoZZZWindow(root,config,background_helpers=False)
                ui.overlay.set_enabled(False)
                window=AccountsWindow(ui,'cn')
                ui.account_window=window
                try:
                    pump(root,lambda:window.description is not None and not window.busy)
                    assert window.form.controls['password'].cget('show')=='●'
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                        root.update()
                        layout(window.window)
                        cases.append([scale,size])
                    with patch('autozzz.onedragon_accounts_ui.simpledialog.askstring',return_value='界面账号 '+str(scale)):
                        window.create()
                    pump(root,lambda:window.description is not None and window.description['idx']!=1 and not window.busy)
                    idx=window.description['idx']
                    password=window.form.controls['password']
                    password.insert(0,'TEST-UI-PASSWORD')
                    assert window.dirty
                    with patch('autozzz.onedragon_accounts_ui.messagebox.askokcancel',return_value=False):
                        assert not window.close() and not window.closing
                    window.save_button.invoke()
                    pump(root,lambda:not window.busy and not window.dirty)
                    assert window.description['password_set']
                    assert window.form.controls['password'].get()==''
                    window.action('activate')
                    pump(root,lambda:not window.busy and window.snapshot.get('current')==idx and window.description is not None)
                    window.toggle_enabled()
                    pump(root,lambda:not window.busy and window.row()['enabled'])
                    if scale==1 and args.preview:
                        from PIL import ImageGrab
                        args.preview.parent.mkdir(parents=True,exist_ok=True)
                        ImageGrab.grab(window=window.window.winfo_id()).save(args.preview)
                    with patch('autozzz.onedragon_accounts_ui.messagebox.askokcancel',return_value=True):window.archive()
                    pump(root,lambda:not window.busy and len(window.snapshot.get('instances',[]))==1 and window.description is not None)
                    assert window.snapshot['current']==1
                    if scale==2.:
                        window.reload()
                        ui._close()
                        assert ui.closing and not destroyed.is_set()
                        deadline=time.monotonic()+8
                        while not destroyed.is_set() and time.monotonic()<deadline:
                            root.update()
                            time.sleep(.02)
                        assert destroyed.is_set()
                    else:
                        window.close()
                        pump(root,lambda:window.closed)
                    window.worker.join(2)
                    assert not window.worker.is_alive() and not errors,errors
                    assert config.read_bytes()==original
                finally:
                    window.dirty=False
                    window.close()
                    window.worker.join(5)
                    ui.closing=True
                    if not destroyed.is_set():
                        ui.overlay.close()
                        ui._destroy_root()
                    assert not root.tk.splitlist(root.tk.eval('set autozzz_bgerrors'))
    print(json.dumps(dict(layout_cases=cases,real_password_save_without_echo=True,activate_and_archive=True,
                          dirty_close_cancelled=True,parent_close_waits_worker=True,legacy_settings_unchanged=True,game_started=False)))


if __name__=='__main__':main()
