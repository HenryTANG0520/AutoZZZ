"""条件战斗监视面板及运行窗口入口，不操作游戏。"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_run_ui import ExecutionWindow
from onedragon_run_gui_smoke import Session
from onedragon_configuration_gui_smoke import layout,pump


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--screenshots',type=Path,required=True);args=parser.parse_args()
    args.screenshots.mkdir(parents=True,exist_ok=True);cases=[]
    data=dict(available=True,running=True,policy='全配队通用',task=dict(trigger='闪避识别',conditions='[闪避识别-黄光] ← [前台-角色]',priority='99',elapsed=1.25),
              states=[dict(name='前台-角色',age=.5,value=1),dict(name='闪避识别-黄光',age=.1,value=1)])
    with tempfile.TemporaryDirectory(prefix='autozzz-battle-state-ui-') as folder:
        config=Path(folder)/'config.json';Settings().save(config)
        for scale in (1.,1.5,2.):
            root=tk.Tk();root.tk.call('tk','scaling',scale*96/72);errors=[]
            root.report_callback_exception=lambda kind,value,tb:errors.append(value)
            ui=AutoZZZWindow(root,config);ui.overlay.set_enabled(False)
            with patch('autozzz.onedragon_run_ui.RuntimeSession',Session):window=ExecutionWindow(ui,'auto_battle','自动战斗','cn')
            ui.execution_window=window
            try:
                pump(root,lambda:window.connected and not window.loading_account)
                before=len(window.session.commands);window.battle_button.invoke();root.update();monitor=window.battle_monitor
                assert '尚未加载' in monitor.status.get() and len(window.session.commands)==before
                window.session.state['battle']=copy.deepcopy(data);window.session.emit(dict(type='state',state=copy.deepcopy(window.session.state)))
                pump(root,lambda:monitor.table.get_children()!=())
                assert '运行中' in monitor.status.get() and monitor.conditions.get()==data['task']['conditions']
                monitor.query.set('黄光');root.update();assert monitor.table.get_children()[0]=='闪避识别-黄光'
                for size in ('default','minimum'):
                    if size=='minimum':monitor.window.geometry('%dx%d' % monitor.window.minsize())
                    root.update();layout(monitor.window);cases.append([scale,size])
                    from PIL import ImageGrab
                    ImageGrab.grab(window=monitor.window.winfo_id()).save(args.screenshots/f'battle-{scale}-{size}.png')
                long=copy.deepcopy(data);long['task']['conditions']='[条件表达式] ← '*100
                monitor.update(long);root.update();layout(monitor.window)
                assert monitor.condition_text.get('1.0','end-1c')==long['task']['conditions']
                window.session.state['battle']=dict(available=True,running=False,policy='全配队通用',states=[],task=None)
                window.session.emit(dict(type='state',state=copy.deepcopy(window.session.state)));pump(root,lambda:not monitor.table.get_children())
                assert '未运行' in monitor.status.get() and monitor.trigger.get()=='—'
                monitor.window.destroy();window.battle_button.invoke();root.update();assert window.battle_monitor is not monitor
                window.close();pump(root,lambda:window.closed);window.worker.join(1)
                assert not window.battle_monitor.window.winfo_exists() and not errors,errors
            finally:
                window.session.close();window.worker.join(2);ui.overlay.close()
                for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
                root.destroy()
    print(json.dumps(dict(layout_cases=cases,actual_window_entry=True,state_and_task_updates=True,keyword_priority=True,stop_clears_display=True,long_expression_scrolls=True,close_with_parent=True,game_input_sent=False),ensure_ascii=False))


if __name__=='__main__':main()
