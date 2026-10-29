"""登录运行窗口及账号入口检查；不启动或操作游戏。"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
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
from autozzz.onedragon_run_ui import ExecutionWindow,LOGIN_MODES
from autozzz.onedragon_accounts_ui import AccountsWindow
from autozzz.onedragon_runtime import RuntimePaths
from onedragon_configuration_gui_smoke import layout,pump
from onedragon_run_gui_smoke import Session


class LoginSession(Session):
    def __init__(self,paths,emit):
        super().__init__(paths,emit)
        self.state.update(prepared_app='_login',uses_charge=False)
    def send(self,command,**params):
        if command=='rpc' and params['method']=='account_describe':
            self.commands.append((command,params))
            self.emit(dict(type='rpc_result',tag=params['tag'],result=dict(path_configured=False,values=dict(game_region='asia'))))
            return
        super().send(command,**params)


def destroy(root,ui,window):
    window.close();window.worker.join(5)
    ui.overlay.close()
    for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
    root.destroy()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--screenshots',type=Path);args=parser.parse_args()
    if args.screenshots:args.screenshots.mkdir(parents=True,exist_ok=True)
    checks=[];cases=0
    with tempfile.TemporaryDirectory(prefix='autozzz-login-ui-') as folder:
        base=Path(folder);config=base/'daily.json';Settings().save(config);original=config.read_bytes()
        with patch('autozzz.gui.AutoZZZWindow._discover_report'):
            for scale in (1.,1.5,2.):
                root=tk.Tk();root.tk.call('tk','scaling',scale*96/72);errors=[]
                root.report_callback_exception=lambda kind,value,tb:errors.append(value)
                ui=AutoZZZWindow(root,config);ui.overlay.set_enabled(False)
                with patch('autozzz.onedragon_run_ui.RuntimeSession',LoginSession):
                    window=ExecutionWindow(ui,'_login','账号登录','global',login_mode='enter');ui.execution_window=window
                try:
                    pump(root,lambda:window.connected and not window.loading_account)
                    assert window.login_mode.get()==LOGIN_MODES[0][0]
                    assert window.budget_frame.winfo_manager()==''
                    for label,mode in LOGIN_MODES:
                        window.login_mode.set(label);root.update()
                        assert not window.prepared and window.start_button.instate(['disabled'])
                        for size in ('default','minimum'):
                            if size=='default':window.window.geometry(f'{round(960*ui.ui_scale)}x{round(740*ui.ui_scale)}')
                            else:window.window.geometry('%dx%d'%window.window.minsize())
                            root.update();layout(window.window);cases+=1
                        window.prepare_button.invoke();pump(root,lambda:window.prepared)
                        prepared=[params for command,params in window.session.commands if command=='prepare'][-1]
                        assert prepared['app_id']=='_login' and prepared['login_mode']==mode
                    assert window.start_button.cget('text')=='开始登录'
                    window.auto_launch.set(True);window.start_button.invoke();pump(root,lambda:window.last_state.get('state')=='running')
                    started=[params for command,params in window.session.commands if command=='start'][-1]
                    assert started['app_id']=='_login' and started['auto_launch'] and started['budget'] is None
                    window.pause_button.invoke();pump(root,lambda:window.paused)
                    window.session.state['control']['focus_waiting']=True
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.last_state.get('control',{}).get('focus_waiting',False))
                    assert window.paused
                    window.session.state['control']['focus_waiting']=False
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.paused and not window.last_state.get('control',{}).get('focus_waiting',True))
                    assert window.pause_button.cget('text')=='继续 F9'
                    window.pause_button.invoke();pump(root,lambda:not window.paused)
                    window.stop_button.invoke();pump(root,lambda:not window.busy)
                    assert not window.prepared and window.start_button.instate(['disabled'])
                    window.session.state.update(state='success',busy=False,login=dict(world_verified=False))
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.status.get()=='登录结果未核实')
                    window.session.state['login']=dict(world_verified=True)
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.status.get()=='已进入游戏')
                    if args.screenshots and scale==1.:
                        from PIL import ImageGrab
                        time.sleep(.35);root.update()
                        ImageGrab.grab(window=window.window.winfo_id()).save(args.screenshots/'login.png')
                    assert not errors,errors
                    checks.append(dict(scale=scale,three_modes=True,pause_stop=True,world_evidence_required=True))
                finally:destroy(root,ui,window)

            # 实际账号进程完成保存后退出，再建立登录进程并准备真实 OCR；不点击开始。
            defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);data=base/'actual-sdk'
            paths=RuntimePaths(defaults.source,defaults.python,data);paths.prepare()
            cached=Path(__file__).resolve().parents[1]/'runs/onedragon-20261028/initialize-cn/assets/models'
            if cached.is_dir():shutil.copytree(cached,data/'assets/models',dirs_exist_ok=True)
            dummy=base/'client/ZenlessZoneZero.exe';dummy.parent.mkdir();dummy.write_bytes(b'fixture only')
            root=tk.Tk();errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(value)
            ui=AutoZZZWindow(root,config);ui.overlay.set_enabled(False);accounts=None;window=None
            with patch('autozzz.onedragon_accounts_ui.RuntimePaths.discover',return_value=paths),patch('autozzz.onedragon_run_ui.RuntimePaths.discover',return_value=paths):
                accounts=AccountsWindow(ui,'cn');ui.account_window=accounts
                try:
                    pump(root,lambda:accounts.description is not None and not accounts.busy)
                    accounts.action('create',name='其他账号')
                    pump(root,lambda:not accounts.busy and accounts.description is not None and accounts.selected()!=accounts.snapshot['current'])
                    accounts.login_button.invoke();root.update()
                    assert '使用此配置' in accounts.status.get() and not accounts.closing and getattr(ui,'execution_window',None) is None
                    accounts.table.selection_set(str(accounts.snapshot['current']));accounts.select()
                    pump(root,lambda:not accounts.busy and accounts.description['idx']==accounts.snapshot['current'])
                    for key,value in dict(game_path=str(dummy),account='fixture-account',password='fixture-password').items():
                        entry=accounts.form.controls[key];entry.delete(0,'end');entry.insert(0,value)
                    root.update();assert accounts.dirty and accounts.login_button.instate(['disabled'])
                    accounts.save_button.invoke();pump(root,lambda:not accounts.busy and not accounts.dirty)
                    root.update();layout(accounts.window);cases+=1
                    accounts.login_button.invoke()
                    pump(root,lambda:accounts.closed and getattr(ui,'execution_window',None) is not None)
                    window=ui.execution_window
                    assert not accounts.worker.is_alive()
                    pump(root,lambda:window.connected and not window.loading_account)
                    window.login_mode.set(dict((value,label) for label,value in LOGIN_MODES)['forced_enter'])
                    window.prepare_button.invoke();pump(root,lambda:window.prepared or (not window.busy and bool(window.last_state)),timeout=90)
                    assert window.prepared,window.status.get()
                    assert window.last_state['prepared_app']=='_login' and not window.uses_charge
                    assert window.last_state.get('window_handle') is None
                    assert window.last_state['prepared_requirements']['login']['mode']=='forced_enter'
                    assert config.read_bytes()==original and not errors,errors
                    root.update();layout(window.window);cases+=1
                    if args.screenshots:
                        from PIL import ImageGrab
                        time.sleep(.35);root.update()
                        ImageGrab.grab(window=window.window.winfo_id()).save(args.screenshots/'actual-prepare.png')
                    checks.append(dict(actual_sdk_account_save_close_then_login_prepare=True,noncurrent_selection_requires_explicit_activation=True,game_started=False))
                finally:
                    if accounts is not None:accounts.dirty=False;accounts.close();accounts.worker.join(5)
                    if window is not None:destroy(root,ui,window)
                    else:ui._close()
    result=dict(layouts=cases,checks=checks,game_accessed=False)
    if args.screenshots:(args.screenshots/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
