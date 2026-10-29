"""轮换运行页的原生界面回放；不启动工作进程或游戏。"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows  # DPI awareness before Tk.
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_run_ui import ExecutionWindow
from onedragon_configuration_gui_smoke import layout,pump


class Session:
    def __init__(self,paths,emit):
        self.emit=emit;self.commands=[];self.finished=threading.Event();self.counter=0
        self.state=dict(state='idle',busy=False,uses_charge=True,control={},error='',rotation=dict(accounts=[],current=None))
        self.worker=threading.Thread(target=self.work,daemon=False);self.worker.start();emit(dict(type='connected'))
    def work(self):
        self.finished.wait();time.sleep(.1);self.emit(dict(type='closed'))
    def publish(self):self.emit(dict(type='state',state=copy.deepcopy(self.state)))
    def send(self,command,**params):
        self.commands.append((command,params))
        if command=='prepare':
            self.counter+=1
            self.state.update(state='prepared',busy=False,rotation=dict(current=None,returning=False,accounts=[dict(idx=i,name='轮换账号'+str(i)+'（离线资料）',state='pending') for i in range(1,6)]),
                              resources=dict(actions=[dict(id='1:coffee',text='轮换账号1：咖啡，3000 丁尼',requires_confirmation=True)],confirmation_id=str(self.counter),confirmation_required=True))
        elif command=='start':self.state.update(state='running',busy=True)
        else:raise AssertionError('轮换界面提交了单账号命令：'+command)
        self.publish()
    def pause(self,paused):self.state['control']['manual_paused']=paused;self.publish()
    def stop(self):self.state.update(state='stopped',busy=False);self.publish()
    def close(self):self.finished.set()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--screenshots',type=Path,required=True);args=parser.parse_args()
    args.screenshots.mkdir(parents=True,exist_ok=True);cases=[]
    with tempfile.TemporaryDirectory(prefix='autozzz-rotation-ui-') as folder:
        config=Path(folder)/'config.json';Settings().save(config);original=config.read_bytes()
        for scale in (1.,1.5,2.):
            root=tk.Tk();root.tk.call('tk','scaling',scale*96/72)
            errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(value)
            ui=AutoZZZWindow(root,config,background_helpers=False);ui.overlay.set_enabled(False)
            with patch('autozzz.onedragon_rotation_session.RotationSession',Session):window=ExecutionWindow(ui,'_rotation','连续轮换','global')
            ui.execution_window=window
            try:
                pump(root,lambda:window.connected and not window.loading_account)
                assert not window.session.commands and window.prepare_button.cget('text')=='轮换预检'
                window.prepare_button.invoke();pump(root,lambda:window.prepared)
                assert window.session.commands[-1]==('prepare',dict(server='global'))
                assert len(window.rotation_table.get_children())==5
                assert window.budget_frame.cget('text')=='整轮共享战斗预算'
                assert window.start_button.instate(['disabled'])
                window.start();assert len(window.session.commands)==1 and '确认' in window.status.get()
                window.resource_accepted.set(True);window.update_buttons()
                window.prepare_button.invoke();pump(root,lambda:window.resource_confirmation_id=='2')
                assert not window.resource_accepted.get() and window.start_button.instate(['disabled'])
                for size in ('default','minimum'):
                    if size=='minimum':window.window.geometry('%dx%d' % window.window.minsize())
                    root.update();layout(window.window);cases.append([scale,size])
                    from PIL import ImageGrab
                    ImageGrab.grab(window=window.window.winfo_id()).save(args.screenshots/f'rotation-{scale}-{size}.png')
                window.timeout.set('29');window.resource_accepted.set(True);window.update_buttons();window.start()
                assert len(window.session.commands)==2 and '30' in window.status.get()
                window.timeout.set('600');window.allowance.set('40');window.rounds.set('2');window.per_battle.set('20')
                window.start_button.invoke();pump(root,lambda:window.busy)
                command,params=window.session.commands[-1]
                assert command=='start' and set(params)=={'budget','timeout_seconds','resource_confirmation'}
                assert params['budget']['allowance']==40 and params['budget']['battle_limit']==2 and params['resource_confirmation']=='2'
                window.pause_button.invoke();pump(root,lambda:window.paused)
                window.pause_button.invoke();pump(root,lambda:not window.paused)
                window.session.state['control']['focus_waiting']=True;window.session.publish();pump(root,lambda:'焦点' in window.status.get())
                window.session.state['control']['focus_waiting']=False
                window.session.state['rotation']['current']=1;window.session.state['rotation']['returning']=True;window.session.publish()
                pump(root,lambda:'返回起点' in window.account_badge.cget('text'))
                window.stop_button.invoke();pump(root,lambda:not window.busy)
                assert not window.start_button.instate(['!disabled'])
                window.close();assert not window.closed and window.worker.is_alive()
                pump(root,lambda:window.closed);window.worker.join(1);assert not window.worker.is_alive()
                assert not errors,errors
            finally:
                window.session.close();window.worker.join(2);ui.overlay.close()
                for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
                root.destroy()
        assert config.read_bytes()==original
    print(json.dumps(dict(layout_cases=cases,shared_budget=True,consent_and_reprepare=True,account_progress=True,focus_pause_feedback=True,close_waits_worker=True,game_started=False),ensure_ascii=False))


if __name__=='__main__':main()
