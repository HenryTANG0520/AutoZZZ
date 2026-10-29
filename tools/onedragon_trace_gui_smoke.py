"""追踪窗口、过滤、冻结、导出与覆盖；用自建窗口和合成追踪，不接触游戏。"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_pip_ui import PipPresentation
from autozzz.onedragon_run_ui import ExecutionWindow
from autozzz.overlay import NativePresentation
from autozzz.windows import user32
from onedragon_configuration_gui_smoke import layout,pump
from onedragon_run_gui_smoke import Session


class TraceSession(Session):
    def __init__(self,*args,**kwargs):self.trace_calls=[];self.trace_version=0;super().__init__(*args,**kwargs)
    def trace(self,enabled,clear=False):self.trace_version+=1;self.trace_calls.append((enabled,clear));return self.trace_version


def sample(hwnd):
    now=time.time();common=dict(created=now,meta=dict(source='synthetic'))
    return dict(enabled=True,generation=2,epoch=0,created=now,discarded=0,counts={},truncated={},standard_size=[1920,1080],
                window_identity=dict(hwnd=hwnd,pid=os.getpid()),control={},items=dict(
                    vision=[dict(common,source='ocr',label='400',x1=150,y1=180,x2=520,y2=260,score=.99),dict(common,source='yolo',label='fixture target',x1=600,y1=300,x2=800,y2=580,score=.9)],
                    decision=[dict(common,source='AutoBattleOperator',trigger='黄光',expression='[闪避识别-黄光]',operation='按键-闪避',status='执行')],
                    timeline=[dict(common,category='operation',title='测试节点',detail='合成画面',level='INFO')],
                    perf=[dict(common,metric='ocr_ms',value=12.5,unit='ms'),dict(common,metric='custom_ms',value=2.3,unit='ms')] ))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    cases=[];foreground=user32.GetForegroundWindow()
    for scale in (1.,1.5,2.):
        with tempfile.TemporaryDirectory(prefix='autozzz-trace-ui-') as directory:
            config=Path(directory)/'config.json';Settings().save(config);original=config.read_bytes();root=tk.Tk();root.withdraw()
            root.tk.call('tk','scaling',scale*96/72);errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(value)
            ui=AutoZZZWindow(root,config,background_helpers=False);ui.overlay.set_enabled(False)
            noactivate=PipPresentation(protect_capture=False);root_handle=noactivate.prepare(root)
            with patch('autozzz.onedragon_run_ui.RuntimeSession',TraceSession):run=ExecutionWindow(ui,'auto_battle','自动战斗','cn')
            ui.execution_window=run;run.window.withdraw();noactivate.prepare(run.window)
            root.deiconify();run.window.deiconify()
            try:
                pump(root,lambda:run.connected and not run.loading_account)
                with patch('autozzz.onedragon_trace_ui.NativePresentation',lambda:NativePresentation(protect_capture=False)):
                    run.trace_button.invoke();view=run.trace_window;view.window.withdraw();noactivate.prepare(view.window);view.window.deiconify()
                assert run.session.trace_calls==[(True,False)]
                data=sample(root_handle);run.events.put(dict(type='trace',snapshot=data,version=view.version))
                pump(root,lambda:len(view.tables['vision'].get_children())==2)
                view.query.set('黄光');root.update();assert not view.tables['vision'].get_children() and view.tables['decision'].get_children()
                view.query.set('');view.sources['ocr'].set(False);view.refresh();assert len(view.tables['vision'].get_children())==1
                view.sources['ocr'].set(True);view.refresh()
                view.frozen.set(True);changed=copy.deepcopy(data);changed['items']['vision'][0]['label']='new';view.update(changed,view.version)
                assert view.records['vision']['1']['label']=='400' and view.overlay.snapshot['items']['vision'][0]['label']=='new'
                view.frozen.set(False);view.refresh();assert view.records['vision']['1']['label']=='new'
                target=Path(directory)/'trace.json'
                with patch('autozzz.onedragon_trace_ui.filedialog.asksaveasfilename',return_value=str(target)):view.export()
                assert json.loads(target.read_text(encoding='utf-8'))['items']['vision'][0]['label']=='new'
                with patch('autozzz.onedragon_trace_ui.filedialog.asksaveasfilename',return_value=''):view.export()
                view.tables['decision'].selection_set('0');view.detail('decision');assert '[闪避识别-黄光]' in view.details['decision'].get('1.0','end')
                for size in ('default','minimum'):
                    if size=='minimum':view.window.geometry('%dx%d' % view.window.minsize())
                    for kind,page in zip(('vision','decision','timeline','perf'),view.book.tabs()):
                        view.book.select(page);root.update();layout(view.window);cases.append([scale,size,kind])
                        if scale==1. and size=='default' and kind=='decision':
                            from PIL import ImageGrab
                            ImageGrab.grab(window=view.window.winfo_id()).save(args.output/'trace.png')
                view.cover.set(True);view.overlay_options();view.overlay.presentation.game_bounds=lambda hwnd:(30,30,900,510) if hwnd==root_handle else None
                fresh=sample(root_handle);view.update(fresh,view.version);view.overlay.tick();root.update()
                assert view.overlay.visible and view.overlay.canvas.find_all()
                with patch.object(view.overlay.presentation,'show',side_effect=OSError('fixture native presentation failure')):
                    view.overlay.tick()
                assert 'fixture native presentation failure' in view.status.get() and not view.overlay.visible
                view.update(sample(root_handle),view.version);view.overlay.tick()
                view.font_size.set('invalid');view.cover.set(False);view.overlay_options();assert not view.overlay.enabled and not view.overlay.visible
                view.font_size.set('11');view.cover.set(True);view.overlay_options()
                view.update_state(dict(control=dict(observation_epoch=1,manual_paused=True)));view.overlay.tick();assert not view.overlay.visible
                old_version=view.version;view.configure(clear=True);view.update(data,old_version);assert not view.tables['vision'].get_children()
                assert run.session.trace_calls[-1]==(True,True)
                run.events.put(dict(type='trace_error',message='fixture query failed'));pump(root,lambda:'fixture query' in view.status.get())
                assert not run.busy and not view.overlay.visible
                view.frozen.set(True);view.update(None);assert not view.frozen.get() and not view.tables['decision'].get_children()
                run.trace_window.close();assert run.session.trace_calls[-1]==(False,False) and view.overlay.closed
                with patch('autozzz.onedragon_trace_ui.NativePresentation',lambda:NativePresentation(protect_capture=False)):
                    run.show_trace();reopened=run.trace_window;reopened.window.withdraw();noactivate.prepare(reopened.window);reopened.window.deiconify()
                reopened.window.destroy();root.update();assert reopened.closed and reopened.overlay.closed
                with patch('autozzz.onedragon_trace_ui.NativePresentation',lambda:NativePresentation(protect_capture=False)):
                    run.show_trace();reopened=run.trace_window;reopened.window.withdraw();noactivate.prepare(reopened.window);reopened.window.deiconify()
                run.close();pump(root,lambda:run.closed);run.worker.join(2)
                assert reopened.closed and reopened.overlay.closed and run.session.trace_calls[-1]==(False,False)
                assert config.read_bytes()==original and not errors,errors
                assert user32.GetForegroundWindow()==foreground,'测试窗口不能改变前台焦点'
            finally:
                if run.trace_window is not None:run.trace_window.close()
                run.session.close();run.worker.join(2);ui.overlay.close()
                for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
                root.destroy()
    result=dict(layout_cases=cases,actual_execution_entry=True,filter_and_sources=True,freeze_does_not_freeze_overlay=True,export_explicit=True,
                old_version_rejected=True,disconnect_clears_frozen_history=True,overlay_follows_owned_window=True,pause_hides_overlay=True,
                close_unsubscribes=True,game_accessed=False,game_input_sent=False)
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
