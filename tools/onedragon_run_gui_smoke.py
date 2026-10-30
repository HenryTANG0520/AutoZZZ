"""运行窗口的布局与命令检查；真实初始化仅准备邮件，不启动游戏。"""
from __future__ import annotations

import copy
import argparse
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
            if params["method"]=="set_assistant_mode":
                assert params['params']['job_id']==self.state['job_id']
                self.state['assistant']['mode']=params['params']['mode']
                result=copy.deepcopy(self.state)
            else:
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
    parser=argparse.ArgumentParser()
    parser.add_argument('--screenshots',type=Path)
    output=parser.parse_args().screenshots
    if output is not None:output.mkdir(parents=True,exist_ok=True)
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
            ui=AutoZZZWindow(root,config,background_helpers=False)
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
                    window.allowance.set('2400')
                    window.rounds.set('120')
                    window.per_battle.set('100')
                    window.timeout.set('86400')
                    window.auto_launch.set(False)
                    window.start_button.invoke()
                    pump(root,lambda:window.last_state.get('state')=='running')
                    command,params=window.session.commands[-1]
                    assert command=='start' and params['budget']['allowance']==2400 and params['budget']['battle_limit']==120
                    assert params['budget']['max_per_battle']==100 and params['timeout_seconds']==86400
                    window.session.state['input']=dict(background=True,requires_foreground=False)
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:'后台' in window.status.get())
                    window.session.state['input']['foreground_borrowing']=True
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:'临时前台操作' in window.status.get())
                    window.session.state['input']['foreground_borrowing']=False
                    window.session.state['control']['focus_waiting']=True
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.status.get()=='等待游戏恢复焦点')
                    window.pause_button.invoke()
                    pump(root,lambda:window.paused)
                    assert window.status.get()=='已暂停'
                    window.session.state['control']['focus_waiting']=False
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
                    route_debug=dict(key='former_employee_community_1',revision='saved-route-revision',start_index=1)
                    window=ExecutionWindow(ui,'world_patrol','路线调试','global',route_debug=route_debug)
                    ui.execution_window=window
                    pump(root,lambda:window.connected and not window.loading_account)
                    assert not window.auto_launch.get()
                    window.session.state.update(state='prepared',busy=False,prepared_app='world_patrol',uses_charge=False)
                    window.prepare_button.invoke();pump(root,lambda:window.prepared)
                    assert window.session.commands[-1][1]['route_debug']==route_debug
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                        root.update();layout(window.window);cases.append([scale,size,'route-debug'])
                    window.start_button.invoke();pump(root,lambda:window.busy)
                    assert window.session.commands[-1][1]['budget'] is None
                    window.stop();pump(root,lambda:not window.busy)
                    window.close();pump(root,lambda:window.closed);window.worker.join(1)
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
                    window=ExecutionWindow(ui,'notorious_hunt','恶名狩猎','cn')
                    ui.execution_window=window
                    pump(root,lambda:window.connected and not window.loading_account)
                    assert window.auto_launch.get()
                    window.session.state.update(state='prepared',busy=False,prepared_app='notorious_hunt',uses_charge=False,hunt={})
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.prepared)
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                        root.update()
                        layout(window.window)
                        cases.append([scale,size,'weekly-hunt'])
                    assert not window.budget_frame.winfo_ismapped()
                    window.auto_launch.set(False)
                    window.start_button.invoke()
                    pump(root,lambda:window.busy)
                    assert window.session.commands[-1][1]['budget'] is None
                    window.session.state.update(state='success',busy=False,hunt=dict(free_completed=3,paid_completed=0))
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:'免费 3 场' in window.status.get())
                    assert not errors,errors
                    window.close()
                    pump(root,lambda:window.closed)
                    window.worker.join(1)
                    window=ExecutionWindow(ui,'notify','通知','global')
                    ui.execution_window=window
                    pump(root,lambda:window.connected and not window.loading_account)
                    window.session.state.update(state='prepared',busy=False,prepared_app='notify',uses_charge=False)
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.prepared)
                    assert not window.budget_frame.winfo_ismapped()
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                        root.update()
                        layout(window.window)
                        cases.append([scale,size,'notify'])
                    window.start_button.invoke()
                    pump(root,lambda:window.last_state.get('state')=='running')
                    assert window.session.commands[-1][1]['budget'] is None
                    window.session.state.update(state='success',busy=True,notifications=dict(enabled=True,pending=1,sent=0,failed=0))
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:'等待通知发送' in window.status.get())
                    assert window.busy
                    window.session.state.update(state='failed',busy=False,notifications=dict(enabled=True,pending=0,sent=1,failed=1))
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:'失败 1' in window.status.get())
                    window.session.state.update(state='success',notifications=dict(enabled=False,pending=0,sent=0,failed=0))
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.status.get()=='通知已关闭')
                    window.close()
                    pump(root,lambda:window.closed)
                    window.worker.join(1)
                    window=ExecutionWindow(ui,'one_dragon','一条龙资源确认','cn')
                    ui.execution_window=window
                    pump(root,lambda:window.connected and not window.loading_account)
                    actions=[dict(id='manual',text='随便观：派遣游历、制造和售卖，消耗经营材料及邦布续航。',requires_confirmation=True),
                             dict(id='procurement',text='饮茶仙：提交材料，缺料时尝试制造或派遣；定期采办刷新开启。',requires_confirmation=True),
                             dict(id='goods',text='好物铺：使用云纹徽批量购买邦布能源插件，可能用完对应资源。',requires_confirmation=True),
                             dict(id='bangboo',text='邦巢：刷新并使用云纹徽批量聘用 S 级邦布；最低价格游历25000／制造25000／售卖25000，可能用完云纹徽。请先关闭游戏内“快速选择”。',requires_confirmation=True),
                             dict(id='disc',text='驱动盘：拆解A及以下；额外包含全部已弃置驱动盘。拆解后无法恢复。',requires_confirmation=True)]
                    for item in actions:item['app_id']='drive_disc_dismantle' if item['id']=='disc' else 'suibian_temple'
                    window.session.state.update(state='prepared',busy=False,prepared_app='one_dragon',uses_charge=True,
                                                resources=dict(actions=actions,confirmation_required=True,confirmation_id='prepared-resources-1',confirmed=[]))
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.prepared)
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                        root.update()
                        layout(window.window)
                        cases.append([scale,size,'resources'])
                        if output is not None and scale==1. and size=='default':
                            from PIL import ImageGrab
                            ImageGrab.grab(window=window.window.winfo_id()).save(output/'resource-confirmation.png')
                    assert window.start_button.instate(['disabled'])
                    assert '拆解无法恢复' in window.resource_check.cget('text')
                    before=len(window.session.commands)
                    window.start()
                    assert len(window.session.commands)==before
                    window.resource_check.invoke()
                    assert not window.start_button.instate(['disabled'])
                    window.auto_launch.set(False)
                    window.start_button.invoke()
                    pump(root,lambda:window.busy)
                    assert window.session.commands[-1][1]['resource_confirmation']=='prepared-resources-1'
                    assert window.resource_check.instate(['disabled'])
                    window.stop_button.invoke()
                    pump(root,lambda:not window.busy)
                    window.session.state.update(state='prepared',busy=False)
                    window.session.state['resources']['confirmation_id']='prepared-resources-2'
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.prepared)
                    assert not window.resource_accepted.get() and window.start_button.instate(['disabled'])
                    window.session.state.update(state='success',busy=False,resource_results=[dict(app_id='drive_disc_dismantle',reward_verified=False)])
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:'拆解结果未核实' in window.status.get())
                    from autozzz.onedragon_restore import restore_action,BOTH
                    window.session.state.update(state='prepared',busy=False,resource_results=[],resources=dict(actions=[restore_action(BOTH)],confirmation_required=True,confirmation_id='restore-1',confirmed=[]))
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.prepared and window.resource_confirmation_id=='restore-1')
                    assert not window.budget_note.winfo_ismapped() and window.start_button.instate(['disabled'])
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                        root.update();layout(window.window);cases.append([scale,size,'restore'])
                    window.resource_check.invoke();window.start_button.invoke();pump(root,lambda:window.busy)
                    assert window.session.commands[-1][1]['resource_confirmation']=='restore-1'
                    window.stop_button.invoke();pump(root,lambda:not window.busy)
                    window.session.state.update(state='success',busy=False,resource_results=[dict(action='restore_charge',reward_verified=False)])
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:'电量恢复结果未核实' in window.status.get())
                    window.session.state.update(state='stopped',budget=dict(limited=True))
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:'已达到本轮预算' in window.status.get() and '电量恢复结果未核实' in window.status.get())
                    from autozzz.onedragon_synthesis import synthesis_action
                    from types import SimpleNamespace
                    action=synthesis_action([SimpleNamespace(category_name='合成电池',plan_times=2)],False)
                    window.session.state.update(state='prepared',busy=False,budget={},resource_results=[],resources=dict(actions=[action],confirmation_required=True,confirmation_id='synthesize-1',confirmed=[]))
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.prepared and window.resource_confirmation_id=='synthesize-1')
                    assert '储值电卡和丁尼' in window.resource_check.cget('text')
                    assert window.budget_note.winfo_ismapped() and window.start_button.instate(['disabled'])
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                        root.update();layout(window.window);cases.append([scale,size,'synthesize'])
                    window.resource_check.invoke();window.start_button.invoke();pump(root,lambda:window.busy)
                    assert window.session.commands[-1][1]['resource_confirmation']=='synthesize-1'
                    window.stop_button.invoke();pump(root,lambda:not window.busy)
                    window.session.state.update(state='stopped',budget=dict(limited=True),resource_results=[dict(action='synthesize_battery',reward_verified=False)])
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:'已达到本轮预算' in window.status.get() and '电池合成结果未核实' in window.status.get())
                    assert not errors,errors
                    window.close()
                    pump(root,lambda:window.closed)
                    window.worker.join(1)
                    window=ExecutionWindow(ui,'commission_assistant','委托助手','cn')
                    ui.execution_window=window
                    pump(root,lambda:window.connected and not window.loading_account)
                    assert not window.auto_launch.get()
                    assert window.mode_selector.instate(['disabled'])
                    window.session.state.update(job_id='same-assistant-job',state='running',busy=True,
                                                uses_charge=False,assistant=dict(mode=0,keys=dict(dodge='5',battle='6')))
                    window.events.put(dict(type='state',state=copy.deepcopy(window.session.state)))
                    pump(root,lambda:window.busy)
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                        root.update()
                        layout(window.window)
                        cases.append([scale,size,'commission'])
                        if output is not None and scale==1. and size=='default':
                            from PIL import ImageGrab
                            ImageGrab.grab(window=window.window.winfo_id()).save(output/'commission-run.png')
                    assert not window.mode_selector.instate(['disabled'])
                    assert window.mode_labels==['对话','闪避（5）','战斗（6）']
                    window.mode.set('战斗（6）')
                    window.mode_selector.event_generate('<<ComboboxSelected>>')
                    pump(root,lambda:window.last_state['assistant']['mode']==2)
                    window.pause_button.invoke()
                    pump(root,lambda:window.paused)
                    window.mode.set('闪避（5）')
                    window.mode_selector.event_generate('<<ComboboxSelected>>')
                    pump(root,lambda:window.last_state['assistant']['mode']==1)
                    assert window.paused
                    window.stop_button.invoke()
                    pump(root,lambda:not window.busy)
                    assert window.mode_selector.instate(['disabled'])
                    assert not errors,errors
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
        ui=AutoZZZWindow(root,config,background_helpers=False)
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
        ui=AutoZZZWindow(root,config,background_helpers=False)
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
