"""仅供轮换集成回放的 SDK 工作进程；设备、识别、模型和游戏状态均为替身。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve()
    if not (data/'.autozzz-rotation-flow-test').is_file() or (data/'.autozzz-rotation-flow-test').read_text()!='offline sdk only':
        raise RuntimeError('Fixture worker requires its owned offline profile')
    protocol_output=sys.stdout;sys.stdout=sys.stderr
    platform.uname();sys.path.insert(0,str(args.source/'src'))
    import numpy as np
    from pynput.keyboard import Key
    from pynput.mouse import Button
    from autozzz.onedragon_host import ManagedZContext,ControlledDevice
    from autozzz.onedragon_control import WindowState
    from one_dragon.base.operation.application_base import Application
    from one_dragon.base.operation.operation_node import operation_node
    from one_dragon.base.operation.operation_base import OperationResult
    from one_dragon.base.operation.one_dragon_context import OneDragonContext
    from zzz_od.operation.enter_game.enter_game import EnterGame
    from zzz_od.operation.enter_game.switch_account import SwitchAccount
    from zzz_od.operation.goto.goto_menu import GotoMenu
    from zzz_od.application.charge_plan.charge_plan_config import ChargePlanItem
    state_file=data/'autozzz/fixture-game.json';state_file.parent.mkdir(exist_ok=True)
    def load():return json.loads(state_file.read_text()) if state_file.is_file() else dict(running=False,world=False,path='',pid=900,hwnd=1900,done=[])
    def save(value):state_file.write_text(json.dumps(value),encoding='utf-8')
    def launch(path):
        value=load();value.update(running=True,world=False,path=path,pid=value['pid']+1,hwnd=value['hwnd']+1);save(value)
    def inventory():
        value=load();return [dict(pid=value['pid'],path=value['path'],created=1.)] if value['running'] else []
    def window(path):
        from autozzz.models import AutomationError
        value=load()
        if not value['running'] or Path(value['path']).resolve()!=Path(path).resolve():raise AutomationError('fixture window absent')
        def close(checkpoint,submit):
            checkpoint()
            def post():value=load();value.update(running=False,world=False);save(value);return True
            submit(post);return SimpleNamespace(exited=lambda:not load()['running'],close=lambda:None)
        return SimpleNamespace(hwnd=value['hwnd'],path=value['path'],request_close=close)
    def window_pid(hwnd,pointer):
        value=load();assert hwnd==value['hwnd'];pointer._obj.value=value['pid'];return 1
    class Raw:
        def press(self,key):pass
        def release(self,key):pass
        def type(self,text):pass
    class Keys:
        def __init__(self,control):self.keyboard=ControlledDevice(Raw(),control);self.mouse=ControlledDevice(Raw(),control);self.control=control
        def press(self,key):self.keyboard.press(Key.ctrl if key=='ctrl' else key)
        def release(self,key):self.keyboard.release(Key.ctrl if key=='ctrl' else key)
        def tap(self,key):
            self.press(key)
            try:self.control.checkpoint(require_frame=True)
            finally:self.release(key)
        def reset(self):self.keyboard.reset();self.mouse.reset()
    class Controller:
        screenshot_method='auto';is_game_window_ready=True
        def __init__(self,ctx):
            self.ctx=ctx;self.keyboard_controller=Keys(ctx.execution_control)
            self.game_win=SimpleNamespace(bound=None,process_id=None,clear_binding=self.clear)
            self.screenshot_controller=SimpleNamespace(init_screenshot=lambda *_:None)
        def clear(self):self.game_win.bound=None;self.game_win.process_id=None
        def bind(self,path):self.game_win.bound=window(path);self.game_win.process_id=load()['pid']
        def probe(self):return WindowState(load()['running'],True,(0,0,1920,1080))
        def screenshot(self):return self.ctx.execution_control.observe(lambda:(time.time(),np.zeros((1080,1920,3),np.uint8)))
        def init_before_context_run(self):return True
        def set_window_title(self,*_):pass
        def sync_game_config(self,*_):pass
        def dodge(self,*_,**__):pass
    original_register=ManagedZContext.register_application_factory
    class FixtureApp(Application):
        def __init__(self,ctx):super().__init__(ctx,'email',op_name='离线邮件回放')
        @operation_node(name='记录本账号进度',is_start_node=True,screenshot_before_round=False)
        def step(self):
            value=load();assert value['world'];value['done'].append(self.ctx.current_instance_idx);save(value)
            config=self.ctx.run_context.get_config('charge_plan',self.ctx.current_instance_idx,'one_dragon')
            if not config.plan_list:config.plan_list=[ChargePlanItem(plan_times=2)];config.save()
            config.add_plan_run_times(config.plan_list[0])
            return self.round_success('fixture completed')
    def register(ctx):
        original_register(ctx)
        factory=ctx.run_context._application_factory_map['email'];factory.create_application=lambda instance_idx,group_id:FixtureApp(ctx)
    def initialize(ctx):
        ctx.register_application_factory();ctx.screen_loader.reload();ctx.controller=Controller(ctx);ctx.ocr._model=object();ctx.ready_for_application=True
    def shutdown(ctx):
        if ctx.controller is not None:ctx.controller.keyboard_controller.reset()
        ctx.controller=None;OneDragonContext.after_app_shutdown(ctx)
    original_enter=EnterGame.__init__;original_switch=SwitchAccount.__init__
    def attach(op):
        def click(screen_name,area_name,**kwargs):
            control=op.ctx.execution_control;control.checkpoint(require_frame=True)
            mouse=op.ctx.controller.keyboard_controller.mouse;mouse.press(Button.left)
            try:
                value=load()
                if area_name=='更多登出确认':value['world']=False
                if '账号密码进入游戏' in area_name:value['world']=True
                save(value)
            finally:mouse.release(Button.left)
            return op.round_success(area_name)
        op.round_by_click_area=click;op.round_by_find_and_click_area=lambda screen,screen_name,area_name,**kwargs:click(screen_name,area_name,**kwargs)
        op.round_by_ocr_and_click=lambda screen,text,**kwargs:click('菜单',text)
        op.round_by_find_area=lambda screen,screen_name,area_name,**kwargs:op.round_success(area_name) if area_name=='点击进入游戏' and not load()['world'] else op.round_retry('fixture absent')
        op.is_in_big_world=lambda screen:op.round_success('大世界') if load()['world'] else None
        op.check_screen_with_can_go=lambda *_,**__:(None,False)
        op.round_by_ocr=lambda *_,**__:op.round_retry('fixture absent')
        op.check_enter_click_status_text=lambda *_,**__:(None,None)
        op.check_login_related=lambda screen:op.round_success('国服-账号密码')
        op.check_screen_to_interact=lambda screen:None
    def enter(op,ctx,switch=False):original_enter(op,ctx,switch);attach(op);op.use_clipboard=False
    def switch(op,ctx):original_switch(op,ctx);attach(op)
    def forbidden(*_,**__):raise AssertionError('offline fixture attempted an external operation')
    with ExitStack() as scope:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo','winreg.SetValueEx','winreg.DeleteValue','pynput.keyboard.Controller.press','pynput.mouse.Controller.press','win32clipboard.OpenClipboard','autozzz.windows.user32.PostMessageW','autozzz.windows.user32.SendInput'):
            scope.enter_context(patch(name,side_effect=forbidden))
        for cls,name,replacement in ((ManagedZContext,'register_application_factory',register),(ManagedZContext,'init',initialize),(ManagedZContext,'after_app_shutdown',shutdown),
                                     (EnterGame,'__init__',enter),(SwitchAccount,'__init__',switch),(GotoMenu,'execute',lambda *_:OperationResult(True))):
            scope.enter_context(patch.object(cls,name,replacement))
        scope.enter_context(patch('autozzz.onedragon_startup.game_processes',side_effect=inventory))
        scope.enter_context(patch('autozzz.onedragon_startup.launch_client',side_effect=launch))
        scope.enter_context(patch('autozzz.windows.find_game',side_effect=window))
        scope.enter_context(patch('autozzz.windows.user32.GetWindowThreadProcessId',side_effect=window_pid))
        scope.enter_context(patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0))
        from autozzz.onedragon_worker import main as worker_main
        sys.stdout=protocol_output
        return worker_main()


if __name__=='__main__':raise SystemExit(main())
