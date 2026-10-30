"""实际 Qt 管理器、两类自建设置、独立进程与关闭协议；硬拦截游戏和网络。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import platform
import sys
import time
from unittest.mock import patch


FACTORY='''from one_dragon.base.operation.application.application_factory import ApplicationFactory
from one_dragon.base.config.yaml_config import YamlConfig
from . import demo_const
class DemoConfig(YamlConfig):
    def __init__(self,idx,group):super().__init__(demo_const.APP_ID,instance_idx=idx,sub_dir=[group])
    @property
    def custom_text(self):return self.get('custom_text','初始值')
    @custom_text.setter
    def custom_text(self,value):self.update('custom_text',value)
class Factory(ApplicationFactory):
    def __init__(self,ctx):super().__init__(demo_const);self.ctx=ctx
    def create_application(self,instance_idx,group_id):raise AssertionError('settings may not run the game')
    def create_config(self,instance_idx,group_id):return DemoConfig(instance_idx,group_id)
'''
PROVIDER='''from one_dragon_qt.services.app_setting.app_setting_provider import AppSettingProvider,SettingType
from .demo_const import APP_ID
class Provider(AppSettingProvider):
    app_id=APP_ID
    setting_type=SettingType.{kind}
    @staticmethod
    def get_setting_cls():
        from .demo_view import View
        return View
'''
VIEW='''from PySide6.QtWidgets import QVBoxLayout
from qfluentwidgets import PushButton,LineEdit
from .demo_const import APP_ID
from one_dragon_qt.widgets.base_interface import BaseInterface
from one_dragon_qt.widgets.app_setting.app_setting_flyout import AppSettingFlyout
from one_dragon_qt.services.app_setting.app_setting_provider import GroupIdMixin
class View({base}):
    def __init__(self,ctx{args}):
        {super_call}
        self.ctx=ctx;ctx.fixture_view=self
        {layout}
    def _setup_ui(self,layout):
        self.edit=LineEdit(self);self.edit.setMinimumWidth(280);layout.addWidget(self.edit)
        self.save_button=PushButton('保存',self);layout.addWidget(self.save_button)
        self.save_button.clicked.connect(self.save)
    def init_config(self):
        self.edit.setText(self.config().custom_text)
    def config(self):return self.ctx.run_context.get_config(APP_ID,self.ctx.current_instance_idx,self.group_id)
    def save(self):self.config().custom_text=self.edit.text()
    def on_interface_shown(self):self.init_config()
'''


def write_plugin(root,name,kind):
    folder=root/'plugins'/name;folder.mkdir(parents=True)
    (folder/'demo_const.py').write_text(f"APP_ID='{name}'\nAPP_NAME='专用设置 {kind}'\nDEFAULT_GROUP=False\nNEED_NOTIFY=False\n",encoding='utf-8')
    (folder/'demo_factory.py').write_text(FACTORY,encoding='utf-8')
    (folder/'demo_app_setting.py').write_text(PROVIDER.format(kind=kind),encoding='utf-8')
    if kind=='INTERFACE':
        view=VIEW.format(base='BaseInterface,GroupIdMixin',args='',super_call="BaseInterface.__init__(self,'fixture_settings','专用设置','')",layout='layout=QVBoxLayout(self);self._setup_ui(layout)')
    else:view=VIEW.format(base='AppSettingFlyout',args=',group_id,parent=None',super_call='AppSettingFlyout.__init__(self,ctx,group_id,parent)',layout='pass')
    (folder/'demo_view.py').write_text(view,encoding='utf-8')


def fixture_host():
    parser=argparse.ArgumentParser();parser.add_argument('--fixture-host',action='store_true');parser.add_argument('--source',type=Path);parser.add_argument('--data',type=Path);parser.add_argument('--target');parser.add_argument('--bridge',type=Path);parser.add_argument('--output',type=Path);parser.add_argument('--catalog-views',action='store_true')
    args=parser.parse_args();sys.path.insert(0,str(args.bridge));sys.path.insert(0,str(args.source/'src'));platform.uname()
    import autozzz
    assert Path(autozzz.__file__).resolve().is_relative_to(args.bridge.resolve())
    from autozzz.run_lock import RunLock,profile_lock
    from autozzz.onedragon_settings_host import run,main as host_main
    def forbidden(*_,**__):raise AssertionError('settings fixture cannot access game, network or physical input')
    with ExitStack() as scope:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                     'autozzz.windows.find_game','autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow','autozzz.windows.user32.ShowWindow',
                     'pynput.keyboard.Controller.press','pynput.mouse.Controller.press','pynput.keyboard.Controller.release','pynput.mouse.Controller.release'):
            scope.enter_context(patch(name,side_effect=forbidden))
        def verify(app,window,ctx):
            if args.catalog_views:
                opened=[];args.output.mkdir(parents=True,exist_ok=True)
                for app_id in sorted(window.manager.settable_app_ids):
                    if ctx.factory_manager.get_plugin_info(app_id).is_third_party:continue
                    window.navigator.pop_setting_interface()
                    if window.manager.active_flyout is not None:
                        try:window.manager.active_flyout.close()
                        except RuntimeError:pass
                        window.manager.active_flyout=None
                    window.manager.show_app_setting(app_id,window.navigator,'one_dragon',window.navigator.home.open_button)
                    app.processEvents()
                    assert window.back.isEnabled() or window.manager.active_flyout is not None,app_id
                    if app_id=='charge_plan':
                        from one_dragon_qt.widgets.fast_scroll_area import FastScrollArea
                        areas=[area for view in window.manager._interface_cache.values() for area in view.findChildren(FastScrollArea)]
                        assert any(area.horizontalScrollBar().maximum()>0 for area in areas),'wide native cards must be horizontally reachable'
                    opened.append(app_id)
                    if app_id in ('charge_plan','random_play','world_patrol'):
                        widget=window.manager.active_flyout or window
                        widget.grab().save(str(args.output/(app_id+'.png')))
                assert len(opened)==13,opened
                (args.output/'result.json').write_text(json.dumps(dict(opened_builtin_settings=opened,actual_sdk=True,actual_qt=True,game_accessed=False),ensure_ascii=False,indent=2),encoding='utf-8')
                emitter[0](dict(type='ready',fixture_saved=True));return
            view=ctx.fixture_view;assert view.group_id=='one_dragon'
            assert view.edit.text() in ('初始值','自定义路线与参数')
            view.edit.setText('自定义路线与参数');view.save_button.click()
            assert view.config().custom_text=='自定义路线与参数'
            for lock in (RunLock(),profile_lock(args.data)):
                try:lock.acquire();raise AssertionError('settings lock missing')
                except RuntimeError:pass
            if window.back.isEnabled():window.back.click()
            window.open_setting();view=ctx.fixture_view
            assert view.edit.text()=='自定义路线与参数'
            args.output.mkdir(parents=True,exist_ok=True)
            app.processEvents();window.grab().save(str(args.output/'window.png'))
            tip=window.manager.active_flyout
            if tip is not None:tip.grab().save(str(args.output/'flyout.png'))
            (args.output/'result.json').write_text(json.dumps(dict(actual_manager=True,actual_provider=True,actual_widgets=True,
                saved_and_reopened=True,group=view.group_id,account=ctx.current_instance_idx,locks_held=True,
                game_accessed=False,game_input_sent=False),ensure_ascii=False,indent=2),encoding='utf-8')
            emitter[0](dict(type='ready',fixture_saved=True))
        emitter=[]
        def fixture_run(source,data,target,emit,lease):
            emitter.append(emit)
            return run(source,data,target,emit,lease,verify)
        sys.argv=[sys.argv[0],'--source',str(args.source),'--data',str(args.data),'--target',args.target]
        with patch('autozzz.onedragon_settings_host.run',side_effect=fixture_run):return host_main()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--package',type=Path)
    args=parser.parse_args();base=args.data.resolve()
    if base.exists():raise ValueError('请使用新的隔离验证目录。')
    project=Path(__file__).resolve().parents[1];sys.path.insert(0,str(project))
    from autozzz.onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending
    from autozzz.onedragon_plugins import PluginStore
    from autozzz.onedragon_settings import NativeSettingsSession
    from autozzz.run_lock import RunLock
    paths=replace(RuntimePaths.discover(args.package or project),data=base/'profile');paths.prepare()
    write_plugin(paths.data,'native_interface','INTERFACE');write_plugin(paths.data,'native_flyout','FLYOUT')
    write_plugin(paths.data,'native_disabled','INTERFACE');PluginStore(paths.data).save(['native_interface','native_flyout'])
    original_spawn=__import__('subprocess').Popen;spawns=[];current={}
    bridge=paths.bundle/'bridge' if paths.bundle is not None else project
    def spawn(command,**kwargs):
        if command[:3]==['git','-C',str(paths.source)] and command[3]in ('rev-parse','diff','ls-files'):return original_spawn(command,**kwargs)
        assert str(command[0])==str(paths.python) and '-m'in command
        index=command.index('-m');module=command[index+1]
        if module=='autozzz.onedragon_worker':
            fixture=project/'tools/onedragon_plugins_protocol_smoke.py'
            command=command[:index]+[str(fixture),'--fixture-worker']+command[index+2:]+['--fixture-bridge',str(bridge)]
        else:
            assert module=='autozzz.onedragon_settings_host';spawns.append(list(command))
            command=command[:index]+[str(Path(__file__).resolve()),'--fixture-host']+command[index+2:]+['--bridge',str(bridge),'--output',str(current['output'])]
            if current.get('catalog_views'):command.append('--catalog-views')
        kwargs['env']['QT_QPA_PLATFORM']='offscreen'
        kwargs['env']['QT_SCALE_FACTOR']=str(current.get('scale',1))
        return original_spawn(command,**kwargs)
    def call(client,method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=.5)
            except RequestPending:continue
    def catalog():
        client=OneDragonClient(paths)
        try:
            try:client.start()
            except RequestPending:
                while True:
                    try:client.finish_start(.5);break
                    except RequestPending:continue
            return call(client,'catalog')
        finally:
            while True:
                try:client.close(timeout=5);break
                except RequestPending:continue
    checks=[]
    with patch('subprocess.Popen',side_effect=spawn):
        listing=catalog();assert not any(row['id']=='native_disabled' for row in listing['applications'])
        assert not listing['plugin_failures'],listing['plugin_failures']
        for scale in (1,1.5,2):
            current['scale']=scale
            for name in ('native_interface','native_flyout'):
                row=next(row for row in listing['applications'] if row['id']==name);assert row['native_settings']
                current['output']=base/f'dpi{scale}'/name;events=[]
                def event(value):
                    events.append(value)
                    if value.get('fixture_saved'):session.close()
                session=NativeSettingsSession(paths,dict(app_id=name,instance_idx=listing['instance'],revision=listing['settings_revision'],group_id='one_dragon'),event)
                end=time.monotonic()+60
                while session.worker.is_alive() and time.monotonic()<end:time.sleep(.05)
                if session.worker.is_alive():session.close();raise AssertionError('native settings did not close')
                assert not any(e['type']=='error' for e in events),events[-10:]
                assert json.loads((current['output']/'result.json').read_text(encoding='utf-8'))['saved_and_reopened']
                with RunLock():pass
                checks.append(f'{name}_dpi{scale}_actual_provider_widget_save_reopen_and_parent_close')
        current.update(catalog_views=True,output=base/'builtin-settings',scale=1);events=[]
        session=NativeSettingsSession(paths,dict(app_id='native_interface',instance_idx=listing['instance'],revision=listing['settings_revision'],group_id='one_dragon'),event)
        session.worker.join(60)
        if session.worker.is_alive():session.close();raise AssertionError('builtin settings did not close')
        assert not any(e['type']=='error' for e in events),events[-20:]
        assert len(json.loads((current['output']/'result.json').read_text(encoding='utf-8'))['opened_builtin_settings'])==13
        checks.append('all_13_actual_builtin_setting_providers_construct_and_show')
        current['catalog_views']=False
        for change,message in ((dict(instance_idx=999),'账号'),(dict(revision='stale'),'插件')):
            current['output']=base/('rejected-'+message);events=[]
            target=dict(app_id='native_interface',instance_idx=listing['instance'],revision=listing['settings_revision'],group_id='one_dragon');target.update(change)
            session=NativeSettingsSession(paths,target,events.append)
            session.worker.join(45)
            errors=[e for e in events if e['type']=='error']
            assert not session.worker.is_alive() and len(errors)==1 and message in errors[0]['message'],events[-10:]
            assert not current['output'].exists()
            with RunLock():pass
            checks.append('stale_target_'+message+'_rejected_before_widget_creation')
        latest=catalog();assert latest['settings_revision']==listing['settings_revision']
        checks.append('fresh_rpc_catalog_after_settings_close_and_disabled_provider_remains_unloaded')
    result=dict(checks=checks,spawned_settings_processes=len(spawns),actual_sdk=True,actual_qt=True,
                package=str(args.package.resolve()) if args.package else None,game_accessed=False,game_input_sent=False)
    (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    if '--fixture-host'in sys.argv:raise SystemExit(fixture_host())
    main()
