"""实际安装的自建插件经源工厂、Operation 和 RunSlot 执行；设备均为记录器。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

FACTORY='''from one_dragon.base.operation.application.application_factory import ApplicationFactory
from one_dragon.base.operation.application_base import Application
from one_dragon.base.operation.operation_node import operation_node
from . import demo_const
class DemoApp(Application):
    def __init__(self,ctx):super().__init__(ctx,demo_const.APP_ID,op_name='插件实际操作图回放');self.began=False
    @operation_node(name='插件操作节点',is_start_node=True)
    def step(self):
        fixture=self.ctx.fixture
        if fixture.mode=='blocked':
            if not self.began:
                self.began=True;self.ctx.controller.keyboard_controller.press('j');fixture.started.set()
            if not fixture.finish.is_set():return self.round_wait(wait=.02)
        if fixture.mode=='failed':return self.round_fail('fixture failure')
        self.ctx.controller.keyboard_controller.tap('j');return self.round_success('fixture completed')
class DemoFactory(ApplicationFactory):
    def __init__(self,ctx):super().__init__(demo_const);self.ctx=ctx
    def create_application(self,instance_idx,group_id):return DemoApp(self.ctx)
    def create_run_record(self,instance_idx):return None
'''


def wait(predicate,timeout=12):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=predicate()
        if value:return value
        time.sleep(.01)
    raise AssertionError('插件执行回放未在时限内结束')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',required=True,type=Path);args=parser.parse_args();base=args.data.resolve()
    if base.exists():raise ValueError('请使用新的隔离验证目录。')
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);root=base/'profile';RuntimePaths(defaults.source,defaults.python,root).prepare();sys.path.insert(0,str(defaults.source/'src'));platform.uname()
    import numpy as np
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_execution import OneDragonExecution
    from autozzz.onedragon_control import WindowState
    from autozzz.onedragon_host import ControlledDevice
    from onedragon_plugins_smoke import write_plugin
    source=base/'source/demo';write_plugin(source);(source/'demo_factory.py').write_text(FACTORY,encoding='utf-8')
    constants=(source/'demo_const.py').read_text(encoding='utf-8').replace('DEFAULT_GROUP = False','DEFAULT_GROUP = True');(source/'demo_const.py').write_text(constants,encoding='utf-8')
    service=None;checks=[];packets=[];down=set();window=[WindowState(True,True,(0,0,1920,1080))]
    def forbidden(*_,**__):raise AssertionError('插件执行回放不得访问游戏、网络或物理输入。')
    with ExitStack() as scope:
        for target in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                       'autozzz.windows.find_game','autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow','autozzz.windows.user32.ShowWindow',
                       'pynput.keyboard.Controller.press','pynput.mouse.Controller.press','pynput.keyboard.Controller.release','pynput.mouse.Controller.release'):
            scope.enter_context(patch(target,side_effect=forbidden))
        scope.enter_context(patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0))
        try:
            service=OneDragonService(defaults.source,root)
            def plugin_job(start):
                state=wait(lambda:(state if not state['busy'] else None) if (state:=service.plugin_status())['job_id']==start['job_id'] else None)
                assert state['state']=='done',state;return state['result']
            review=plugin_job(service.plugin_preview(str(source),'directory'));plugin_job(service.plugin_install(review['token'],['demo'],[],review['revision']))
            catalog=service.plugin_catalog();service.plugin_enabled('demo',True,catalog['revision']);service.close();service=OneDragonService(defaults.source,root)
            assert next(row for row in service.catalog()['applications'] if row['id']=='fixture_plugin')['executable']
            ctx=service.ctx;manager=OneDragonExecution(ctx);service._execution=manager;control=manager.control
            class Raw:
                def press(self,key):packets.append(['down',str(key)]);down.add(str(key))
                def release(self,key):packets.append(['up',str(key)]);down.discard(str(key))
            class Keys:
                def __init__(self):self.keyboard=ControlledDevice(Raw(),control);self.mouse=ControlledDevice(Raw(),control)
                def press(self,key):self.keyboard.press(key)
                def release(self,key):self.keyboard.release(key)
                def tap(self,key):
                    self.press(key)
                    try:control.checkpoint(require_frame=True)
                    finally:self.release(key)
                def reset(self):self.keyboard.reset();self.mouse.reset()
            class Controller:
                is_game_window_ready=True
                screenshot_method='auto'
                def __init__(self):
                    self.keyboard_controller=Keys();self.screenshot_controller=SimpleNamespace(init_screenshot=lambda *_:None)
                def bind(self,_):pass
                def probe(self):return window[0]
                def init_before_context_run(self):return True
                def screenshot(self):return control.observe(lambda:(time.time(),np.zeros((1080,1920,3),np.uint8)))
                def release_only(self,*_,release=False,**__):
                    assert release;self.keyboard_controller.reset()
                dodge=normal_attack=special_attack=move_w=move_s=move_a=move_d=switch_next=switch_prev=switch_backup=ultimate=chain_left=chain_right=chain_cancel=lock=release_only
                def sync_game_config(self,*_):pass
                def set_window_title(self,*_):pass
                def cleanup_after_app_shutdown(self):self.keyboard_controller.reset()
            ctx.controller=Controller();ctx.ready_for_application=True;ctx.fixture=SimpleNamespace(mode='success',started=threading.Event(),finish=threading.Event());ctx.screen_loader.reload()
            def execution_job(start):
                state=wait(lambda:(state if not state['busy'] else None) if (state:=manager.status())['job_id']==start['job_id'] else None)
                return state
            def prepare(app='fixture_plugin'):
                state=execution_job(manager.prepare_application(app));assert state['state']=='prepared',state
                assert state['resources']['confirmation_required'] and state['prepared_requirements']['plugins'][0]['app_id']=='fixture_plugin'
                return state['resources']['confirmation_id']
            token=prepare()
            try:manager.start_application('fixture_plugin');raise AssertionError('unconfirmed plugin started')
            except ValueError:pass
            state=execution_job(manager.start_application('fixture_plugin',resource_confirmation=token));assert state['state']=='success' and packets==[['down','j'],['up','j']],state
            checks.append('actual_installed_plugin_factory_operation_and_run_slot_success_with_explicit_confirmation')
            ctx.fixture.mode='failed';token=prepare();state=execution_job(manager.start_application('fixture_plugin',resource_confirmation=token));assert state['state']=='failed' and 'fixture failure' in state['error'],state
            checks.append('actual_plugin_failure_preserves_terminal_status')
            ctx.fixture.mode='blocked';token=prepare();started=manager.start_application('fixture_plugin',resource_confirmation=token);wait(ctx.fixture.started.is_set);manager.pause(True);wait(lambda:manager.control.status()['manual_paused']);assert not down
            window[0]=WindowState(True,False,(0,0,1920,1080));control.poll();manager.pause(False);count=len(packets);time.sleep(.08);assert len(packets)==count and control.status()['focus_waiting']
            window[0]=WindowState(True,True,(0,0,1920,1080));wait(lambda:not control.status()['focus_waiting']);ctx.fixture.finish.set();state=execution_job(started);assert state['state']=='success' and not down,state
            checks.append('actual_plugin_hold_release_manual_pause_focus_wait_and_resume')
            ctx.fixture.started.clear();ctx.fixture.finish.clear();token=prepare();started=manager.start_application('fixture_plugin',resource_confirmation=token);wait(ctx.fixture.started.is_set);manager.stop();state=execution_job(started);assert state['state']=='stopped' and not down,state
            checks.append('actual_plugin_stop_releases_recorded_inputs_and_waits_run_slot')
            ctx.fixture.mode='success';group=service.describe_configuration('_group');rows=next(row['value'] for row in group['fields'] if row['name']=='app_list')
            assert any(row['app_id']=='fixture_plugin' for row in rows)
            for row in rows:row['enabled']=row['app_id']=='fixture_plugin'
            service.save_configuration('_group',dict(app_list=rows),group['revision']);token=prepare('one_dragon');state=execution_job(manager.start_application('one_dragon',fixed_entry=True,resource_confirmation=token));assert state['state']=='success',state
            assert state['applications']==[dict(id='fixture_plugin',name='插件回放',state='success',detail='fixture completed')],state['applications']
            checks.append('actual_default_group_plugin_execution_order_and_per_application_result')
            token=prepare();path=root/'plugins/demo/demo_factory.py';path.write_text(path.read_text(encoding='utf-8')+'\n# changed after preparation\n',encoding='utf-8');count=len(packets)
            state=execution_job(manager.start_application('fixture_plugin',resource_confirmation=token));assert state['state']=='failed' and '插件已变化' in state['error'] and len(packets)==count,state
            checks.append('plugin_code_change_rejects_stale_loaded_context_before_any_input')
        finally:
            if service is not None:service.close()
    result=dict(checks=checks,actual_source_factory_operation_group_and_run_slot=True,fixture_plugins_only=True,packets=packets,game_accessed=False,game_input_sent=False)
    (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
