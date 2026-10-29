"""实际 RuntimeSession 与 CLI 执行自建插件；保留 SDK 协议、工厂与报告。"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import queue
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending
from autozzz.onedragon_session import RuntimeSession
from autozzz.onedragon_cli import run_application
from onedragon_plugins_smoke import write_plugin
from onedragon_plugin_execution_smoke import FACTORY


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--package',type=Path);parser.add_argument('--gui',action='store_true');args=parser.parse_args();base=args.data.resolve()
    if base.exists():raise ValueError('请使用新的隔离验证目录。')
    root=Path(__file__).resolve().parents[1];paths=replace(RuntimePaths.discover(args.package or root),data=base/'profile');paths.prepare();bridge=paths.bundle/'bridge' if paths.bundle is not None else root
    (paths.data/'.autozzz-plugin-execution-test').write_text('offline fixture only');state=paths.data/'autozzz';state.mkdir(exist_ok=True);game=base/'game/ZenlessZoneZero.exe';game.parent.mkdir();game.write_bytes(b'fixture only')
    source=base/'source/demo';write_plugin(source);(source/'demo_factory.py').write_text(FACTORY,encoding='utf-8')
    original=subprocess.Popen;checks=[]
    def mode(value):
        temporary=state/'fixture.tmp';temporary.write_text(json.dumps(dict(mode=value)),encoding='utf-8');temporary.replace(state/'fixture.json')
    def spawn(command,**kwargs):
        if command[:3]==['git','-C',str(paths.source)] and command[3]in ('rev-parse','diff','ls-files'):return original(command,**kwargs)
        assert str(command[0])==str(paths.python) and '-m' in command;index=command.index('-m');assert command[index+1]=='autozzz.onedragon_worker'
        return original(command[:index]+[str(Path(__file__).with_name('plugin_execution_fixture_worker.py'))]+command[index+2:]+['--fixture-bridge',str(bridge)],**kwargs)
    def close(client):
        while True:
            try:client.close(timeout=5);return
            except RequestPending:continue
    def call(client,method,**params):
        key=client.submit(method,**params)
        while True:
            try:return client.result(key,timeout=.5)
            except RequestPending:continue
    def job(client,method,**params):
        key=call(client,method,**params)['job_id']
        while True:
            result=call(client,'plugin_status');assert result['job_id']==key
            if not result['busy']:assert result['state']=='done',result;return result['result']
    with patch('autozzz.onedragon_runtime.subprocess.Popen',side_effect=spawn):
        setup=OneDragonClient(paths)
        try:
            setup.start();review=job(setup,'plugin_preview',path=str(source),kind='directory');job(setup,'plugin_install',token=review['token'],names=['demo'],overwrites=[],revision=review['revision'])
            current=call(setup,'plugin_catalog');call(setup,'plugin_enabled',name='demo',enabled=True,revision=current['revision'])
        finally:close(setup)
        for expected in ('success','failed'):
            mode(expected);events=queue.Queue();session=RuntimeSession(paths,events.put,report_root=base/'reports')
            try:
                def wait(predicate):
                    while True:
                        event=events.get(timeout=60)
                        if event['type']=='error':raise AssertionError(event)
                        if predicate(event):return event
                wait(lambda event:event['type']=='connected');session.send('prepare',app_id='fixture_plugin',server='cn',game_path=str(game),region='cn')
                prepared=wait(lambda event:event['type']=='state' and event['state']['state']=='prepared')['state'];assert prepared['resources']['confirmation_required']
                session.send('start',app_id='fixture_plugin',resource_confirmation=prepared['resources']['confirmation_id'],timeout_seconds=30,auto_launch=False,budget=None)
                result=wait(lambda event:event['type']=='state' and event['state']['state']in ('success','failed'))['state'];assert result['state']==expected,result
                report=json.loads((session.report_dir/'report.json').read_text(encoding='utf-8'));assert report['status']==expected and report['execution']['prepared_requirements']['plugins'][0]['app_id']=='fixture_plugin'
                checks.append('actual_sdk_runtime_session_plugin_'+expected+'_and_report')
            finally:session.close();session.worker.join(30);assert not session.worker.is_alive()
        mode('success')
        with patch('autozzz.onedragon_cli.RuntimePaths.discover',return_value=paths):
            factory=lambda paths,emit:RuntimeSession(paths,emit,report_root=base/'cli-reports')
            try:run_application('fixture_plugin',server='cn',game_path=str(game),region='cn',auto_launch=False,session_factory=factory);raise AssertionError('CLI silently confirmed plugin')
            except ValueError as exc:assert '确认' in str(exc)
            result=run_application('fixture_plugin',server='cn',game_path=str(game),region='cn',auto_launch=False,confirm_resources=True,session_factory=factory);assert result['state']['state']=='success',result
            before=(state/'initializations.json').read_bytes()
            try:run_application('not_registered',server='cn',game_path=str(game),region='cn',auto_launch=False,confirm_resources=True,session_factory=factory);raise AssertionError('unknown plugin accepted')
            except RuntimeError as exc:assert '未注册' in str(exc)
            assert (state/'initializations.json').read_bytes()==before
            checks.append('actual_cli_plugin_confirmation_success_and_unknown_id_before_initialization')
        if args.gui:
            import copy,tkinter as tk
            from autozzz.config import Settings
            from autozzz.gui import AutoZZZWindow
            from autozzz.onedragon_pip_ui import PipPresentation
            from autozzz.windows import user32
            from onedragon_configuration_gui_smoke import layout,pump
            config=base/'gui.json';Settings(game_path=str(game)).save(config);saved=config.read_bytes();snapshot=None;layouts=[];foreground=user32.GetForegroundWindow()
            with patch('autozzz.onedragon_run_ui.RuntimeSession',lambda paths,emit:RuntimeSession(paths,emit,report_root=base/'gui-reports')),patch('autozzz.onedragon_apps_ui.RuntimePaths.discover',return_value=paths):
                for scale in (1.,1.5,2.):
                    mode('success');window=tk.Tk();window.withdraw();window.tk.call('tk','scaling',scale*96/72);errors=[];window.report_callback_exception=lambda kind,value,tb:errors.append(value)
                    ui=AutoZZZWindow(window,config,background_helpers=False);ui.overlay.set_enabled(False);presentation=PipPresentation(protect_capture=False);presentation.prepare(window);ui._show_page('applications');window.deiconify();panel=ui.onedragon_panel;run=None
                    try:
                        if snapshot is None:
                            panel.load_button.invoke();pump(window,lambda:not panel.busy and bool(panel.cache));snapshot=dict(apps=list(panel.apps.values()),descriptions=copy.deepcopy(panel.cache),options=copy.deepcopy(panel.options))
                        else:panel._loaded(copy.deepcopy(snapshot))
                        panel.tree.selection_set('fixture_plugin');panel.select();window.update();assert not panel.run_button.instate(['disabled'])
                        panel.run_button.invoke();run=ui.execution_window;run.window.withdraw();presentation.prepare(run.window);run.window.deiconify();pump(window,lambda:run.connected and not run.loading_account)
                        assert user32.GetForegroundWindow()==foreground,'测试窗口初始化改变了用户焦点'
                        # 只屏蔽夹具窗口的任务收尾显示动作；运行、协议、表单和报告保持实际实现。
                        window.iconify=lambda:None;window.deiconify=lambda:None;run.window.deiconify=lambda:None
                        run.auto_launch.set(False);run.prepare_button.invoke();pump(window,lambda:run.prepared and not run.busy);assert run.resource_required and not run.resource_accepted.get() and run.start_button.instate(['disabled'])
                        assert '不计入内置刷本预算' in run.resource_text.get('1.0','end')
                        for size in ('default','minimum'):
                            if size=='minimum':run.window.geometry('%dx%d' % run.window.minsize())
                            window.update();layout(run.window);layouts.append([scale,size])
                            if scale==1. and size=='default':
                                from PIL import ImageGrab
                                ImageGrab.grab(window=run.window.winfo_id()).save(base/'plugin-run.png')
                        run.resource_check.invoke();assert not run.start_button.instate(['disabled']);run.start_button.invoke();pump(window,lambda:not run.busy and run.last_state.get('state')=='success')
                        assert run.last_state['prepared_requirements']['plugins'][0]['app_id']=='fixture_plugin'
                        run.close();pump(window,lambda:run.closed);run.worker.join(2)
                        assert not errors,errors
                        assert config.read_bytes()==saved,'原每日配置发生变化'
                        assert user32.GetForegroundWindow()==foreground,'测试窗口结束时改变了用户焦点'
                    finally:
                        if run is not None:run.session.close();run.worker.join(10)
                        ui.overlay.close()
                        for token in window.tk.splitlist(window.tk.call('after','info')):window.tk.call('after','cancel',token)
                        window.destroy()
            checks.append(dict(actual_native_applications_entry_resource_confirmation_run_and_report=True,layouts=layouts,window_visibility_fixture=True))
    result=dict(checks=checks,actual_sdk_protocol_session_and_cli=True,package=str(args.package.resolve()) if args.package else None,fixture_plugins_only=True,game_accessed=False,game_input_sent=False)
    (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
