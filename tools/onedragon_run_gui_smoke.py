"""运行窗口的布局与命令检查；真实初始化仅准备邮件，不启动游戏。"""
from __future__ import annotations

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
from autozzz.gui import AutoZZZWindow
from autozzz.config import Settings
from autozzz.onedragon_run_ui import ExecutionWindow
from autozzz.onedragon_runtime import RuntimePaths
from autozzz.battle_budget import BattleBudget
from autozzz.budget_ui import BudgetWindow
from onedragon_configuration_gui_smoke import layout,pump


class Session:
    def __init__(self,paths,emit):
        self.emit=emit
        self.commands=[]
        self.finished=threading.Event()
        self.state=dict(state="prepared",busy=False,prepared_app="charge_plan",uses_charge=True,control={},error="")
        self.worker=threading.Thread(target=self.work,daemon=False)
        self.worker.start()
        self.emit(dict(type="connected"))
    def work(self):
        self.finished.wait()
        time.sleep(.1)
        self.emit(dict(type="closed"))
    def send(self,command,**params):
        self.commands.append((command,params))
        if command=="rpc":
            result=dict(current=1,instances=[dict(idx=1,name="离线账号"*20)]) if params["method"]=="account_list" else dict(path_configured=False,values=dict(game_region="cn"))
            self.emit(dict(type="rpc_result",tag=params["tag"],result=result))
            return
        if command=="prepare":self.state.update(state="prepared",busy=False)
        elif command=="start":self.state.update(state="running",busy=True)
        self.emit(dict(type="state",state=copy.deepcopy(self.state)))
    def pause(self,paused):
        self.state['control']['manual_paused']=paused
        self.emit(dict(type="state",state=copy.deepcopy(self.state)))
    def stop(self):
        self.state.update(state="stopped",busy=False)
        self.emit(dict(type="state",state=copy.deepcopy(self.state)))
    def close(self):self.finished.set()


def main():
    cases=[]
    with tempfile.TemporaryDirectory(prefix="autozzz-run-ui-") as folder:
        root_path=Path(folder)
        config=root_path/'config.json'
        Settings().save(config)
        original=config.read_bytes()
        for scale in (1.,1.5,2.):
            root=tk.Tk()
            root.tk.call('tk','scaling',scale*96/72)
            errors=[]
            root.report_callback_exception=lambda kind,value,tb:errors.append(value)
            ui=AutoZZZWindow(root,config)
            ui.overlay.set_enabled(False)
            with patch('autozzz.onedragon_run_ui.RuntimeSession',Session):
                window=ExecutionWindow(ui,'charge_plan','体力刷本','cn')
                ui.execution_window=window
                try:
                    pump(root,lambda:window.connected and not window.loading_account)
                    assert window.prepare_button.cget('style')=='Accent.TButton'
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                        root.update()
                        layout(window.window)
                        cases.append([scale,size])
                    window.prepare_button.invoke()
                    pump(root,lambda:window.prepared)
                    assert window.start_button.cget('style')=='Accent.TButton'
                    assert window.prepare_button.cget('style')=='TButton'
                    window.allowance.set('40')
                    window.rounds.set('2')
                    window.auto_launch.set(False)
                    window.start_button.invoke()
                    pump(root,lambda:window.last_state.get('state')=='running')
                    command,params=window.session.commands[-1]
                    assert command=='start' and params['budget']['allowance']==40 and params['budget']['battle_limit']==2
                    window.pause_button.invoke()
                    pump(root,lambda:window.paused)
                    window.pause_button.invoke()
                    pump(root,lambda:not window.paused)
                    window.stop_button.invoke()
                    pump(root,lambda:not window.busy)
                    # 预算核对只处理本次临时记录；上限不退款，也不改成获胜。
                    ledger_path=root_path/('budget-'+str(scale))
                    ledger=BattleBudget(ledger_path,'ui-stopped','cn',40,2)
                    ledger.reserve(20,'fixture')
                    with patch('autozzz.budget_ui.budget_directory',return_value=ledger_path):
                        budget=BudgetWindow(window.window,'cn',ui.ui_scale)
                    root.update()
                    layout(budget.window)
                    budget.window.geometry('%dx%d' % budget.window.minsize())
                    root.update()
                    layout(budget.window)
                    budget.table.selection_set('0')
                    root.update()
                    budget.account.invoke()
                    assert not budget.items and '20' in budget.status.get()
                    checked=BattleBudget(ledger_path,'ui-stopped','cn',40,2)
                    assert checked.reserved==20 and not checked.pending
                    budget.window.destroy()
                    assert not errors,errors
                    window.close()
                    assert not window.closed and window.worker.is_alive()
                    pump(root,lambda:window.closed)
                    window.worker.join(1)
                    # 国际服增加了时区选择，收拢时限说明后也必须在最小窗口完整显示。
                    window=ExecutionWindow(ui,'charge_plan','体力刷本','global')
                    ui.execution_window=window
                    pump(root,lambda:window.connected and not window.loading_account)
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                        root.update()
                        layout(window.window)
                        cases.append([scale,size,'global'])
                    window.close()
                    pump(root,lambda:window.closed)
                    window.worker.join(1)
                finally:
                    window.session.close()
                    window.worker.join(2)
                    ui.overlay.close()
                    for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
                    root.destroy()
        # Real parent/worker connection, OCR initialization, application preparation, and close.
        defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1])
        data=root_path/'real-profile'
        RuntimePaths(defaults.source,defaults.python,data).prepare()
        cached=Path(__file__).resolve().parents[1]/'runs/onedragon-20261028/initialize-cn/assets/models'
        if cached.is_dir():shutil.copytree(cached,data/'assets/models',dirs_exist_ok=True)
        root=tk.Tk()
        root.tk.eval('set autozzz_bgerrors {}; proc bgerror {message} {lappend ::autozzz_bgerrors $message}')
        ui=AutoZZZWindow(root,config)
        ui.overlay.set_enabled(False)
        with patch('autozzz.onedragon_run_ui.RuntimePaths.discover',return_value=RuntimePaths(defaults.source,defaults.python,data)):
            window=ExecutionWindow(ui,'email','邮件','cn')
            ui.execution_window=window
        try:
            pump(root,lambda:window.connected and not window.loading_account,timeout=45)
            window.prepare_button.invoke()
            pump(root,lambda:window.prepared or (not window.busy and '失败' in window.status.get()),timeout=90)
            assert window.prepared,window.status.get()
            assert not window.uses_charge
            window.close()
            pump(root,lambda:window.closed)
            window.worker.join(2)
            assert not window.worker.is_alive()
            assert config.read_bytes()==original
        finally:
            window.close()
            window.worker.join(5)
            ui.overlay.close()
            for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
            root.destroy()
        # 退出主窗口也须等运行进程结束，不能只隐藏窗口后留下输入线程。
        root=tk.Tk()
        root.tk.eval('set autozzz_bgerrors {}; proc bgerror {message} {lappend ::autozzz_bgerrors $message}')
        ui=AutoZZZWindow(root,config)
        ui.overlay.set_enabled(False)
        destroyed=threading.Event()
        root.bind('<Destroy>',lambda event:destroyed.set() if event.widget is root else None,add='+')
        with patch('autozzz.onedragon_run_ui.RuntimeSession',Session):
            window=ExecutionWindow(ui,'charge_plan','体力刷本','cn')
        ui.execution_window=window
        pump(root,lambda:window.connected and not window.loading_account)
        window.prepare_button.invoke()
        pump(root,lambda:window.prepared)
        window.auto_launch.set(False)
        window.start_button.invoke()
        pump(root,lambda:window.busy)
        ui._close()
        assert not destroyed.is_set() and window.worker.is_alive()
        deadline=time.monotonic()+5
        while not destroyed.is_set() and time.monotonic()<deadline:
            root.update()
            time.sleep(.02)
        assert destroyed.is_set()
        window.worker.join(1)
        assert not window.worker.is_alive()
        time.sleep(.12)
        root.tk.call('update')
        assert not root.tk.splitlist(root.tk.eval('set autozzz_bgerrors'))
    print(json.dumps(dict(layout_cases=cases,prepare_start_pause_stop=True,real_prepare_without_input=True,close_waits_worker=True,parent_close_waits_worker=True,game_started=False),ensure_ascii=False))


if __name__=='__main__':main()
