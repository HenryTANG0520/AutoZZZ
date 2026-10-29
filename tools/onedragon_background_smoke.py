"""实际源控制器、截图选择与运行槽的后台回放；窗口、画面、设备均为替身。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from onedragon_gamepad_smoke import Constants,Pad


class Desktop:
    def __init__(self):self.foreground=10;self.calls=[];self.ids={10:(10,100),20:(20,200),30:(30,300)}
    def current(self):return self.foreground
    def identity(self,hwnd):return self.ids.get(hwnd)
    def activate(self,hwnd):self.calls.append(hwnd);self.foreground=hwnd


class Mouse:
    def __init__(self):self.events=[];self._position=(0,0)
    def press(self,key):self.events.append(('down',str(key)))
    def release(self,key):self.events.append(('up',str(key)))
    def type(self,text):self.events.append(('type',text))
    def move(self,x,y):self.events.append(('move',x,y))
    def scroll(self,x,y):self.events.append(('scroll',x,y))
    @property
    def position(self):return self._position
    @position.setter
    def position(self,value):self.events.append(('position',value));self._position=value


def wait(manager,job):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        result=manager.status();assert result['job_id']==job
        if not result['busy']:return result
        time.sleep(.02)
    raise AssertionError('原后台验证任务未完成，不能创建替代任务。')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    RuntimePaths(defaults.source,defaults.python,data).prepare();sys.path.insert(0,str(defaults.source/'src'));platform.uname()
    import numpy as np
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_execution import OneDragonExecution
    from autozzz.onedragon_control import ObservationExpired
    from autozzz.onedragon_host import ManagedController
    from autozzz.windows import Geometry
    from one_dragon.base.geometry.point import Point
    from one_dragon.base.operation.application_base import Application
    from one_dragon.base.operation.operation_node import operation_node
    from one_dragon.envs.env_config import ScreenshotMethodEnum
    image=np.tile(np.arange(1920,dtype=np.uint8),(1080,1));image=np.stack([image,image,image],axis=2)
    desktop=Desktop();messages=[];valid=[True];iconic=[False];captures=[];witness=[]
    fake=NS(VX360Gamepad=Pad,VDS4Gamepad=Pad,XUSB_BUTTON=Constants(),DS4_BUTTONS=Constants(),DS4_DPAD_DIRECTIONS=Constants(),DS4_SPECIAL_BUTTONS=Constants())
    def forbidden(*_,**__):raise AssertionError('后台回放不得访问实际游戏、设备、前台或网络。')
    checks=[]
    with ExitStack() as scope:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                     'pynput.keyboard.Controller.press','pynput.mouse.Controller.press','autozzz.windows.find_game','autozzz.windows.user32.SendInput',
                     'autozzz.windows.user32.SetForegroundWindow','win32gui.SetForegroundWindow','win32gui.PostMessage','win32gui.SendMessage'):
            scope.enter_context(patch(name,side_effect=forbidden))
        scope.enter_context(patch.dict(sys.modules,{'vgamepad':fake}))
        scope.enter_context(patch('one_dragon.base.controller.pc_button.pc_button_utils.is_vgamepad_installed',return_value=True))
        scope.enter_context(patch('autozzz.windows.user32.GetForegroundWindow',side_effect=desktop.current))
        scope.enter_context(patch('autozzz.windows.user32.IsWindow',side_effect=lambda _:valid[0]))
        scope.enter_context(patch('autozzz.windows.user32.IsIconic',side_effect=lambda _:iconic[0]))
        scope.enter_context(patch('autozzz.windows.geometry',return_value=Geometry(0,0,1920,1080)))
        scope.enter_context(patch('autozzz.windows.user32.SendMessageTimeoutW',side_effect=lambda *a:messages.append(('activate',a[0])) or True))
        scope.enter_context(patch('autozzz.windows.user32.PostMessageW',side_effect=lambda *a:messages.append(('post',a[0],a[1])) or True))
        service=OneDragonService(defaults.source,data);manager=None
        try:
            ctx=service.ctx;manager=OneDragonExecution(ctx);service._execution=manager
            controller=ManagedController(ctx,manager.control);ctx.controller=controller;ctx.ready_for_application=True
            controller.desktop=desktop;control=manager.control
            raw=Mouse();controller.keyboard_controller.mouse.raw=raw;controller.keyboard_controller.keyboard.raw=Mouse()
            def bind(_path=None):
                controller.game_win.bound=NS(hwnd=20,path='fixture',guard=lambda **_:Geometry(0,0,1920,1080))
                controller.game_win.process_id=20;controller.game_win._hWnd=20;controller.game_win._win=NS()
            controller.bind=bind;controller.game_win.init_win=bind;controller.game_win.identity_valid=lambda:valid[0];bind()
            scope.enter_context(patch.object(type(controller.game_win),'is_win_valid',new=property(lambda _:valid[0])))
            printing=ScreenshotMethodEnum.PRINT_WINDOW.value.value
            for name,strategy in controller.screenshot_controller.strategies.items():
                strategy.init=lambda:True;strategy.cleanup=lambda:None
                def capture(rect,independent=False,name=name):
                    captures.append(name)
                    if name!=printing:raise AssertionError('后台截图回退到了其他来源')
                    return image
                strategy.capture=capture
            ctx.game_config.background_mode=True;ctx.game_config.background_gamepad_type='xbox'
            controller.game_config=ctx.game_config;controller.begin_input_policy('email');control.reset()
            assert controller.probe().focused and desktop.current()==10
            assert controller.init_before_context_run();controller.screenshot()
            controller.keyboard_controller.mouse.before_press=lambda _:witness.append(True)
            controller.normal_attack();assert desktop.current()==10 and not raw.events
            assert controller.background_mode and controller.btn_controller is controller.xbox_controller
            checks.append('actual_source_background_gamepad_without_foreground_change')
            before=len(messages);ctx.game_config.gamepad_turn_speed=0;controller.move_mouse_relative(100,0)
            assert len(messages)==before;ctx.game_config.gamepad_turn_speed=1000
            checks.append('zero_camera_speed_does_not_pulse_gamepad')
            def revoked():raise RuntimeError('fixture parent lease revoked')
            manager.run_lease=NS(check=revoked);before=len(messages)
            try:controller.normal_attack()
            except RuntimeError:pass
            else:raise AssertionError('battle adapter overwrote parent task guard')
            assert len(messages)==before;manager.run_lease=None
            checks.append('battle_guard_preserves_parent_task_input_validator')
            controller.screenshot_controller.active_strategy_name=ScreenshotMethodEnum.PIL.value.value
            controller.screenshot();assert set(captures)=={printing};checks.append('window_only_capture_even_with_cached_pil')
            previous=controller.screenshot_controller.strategies[printing].capture
            controller.screenshot_controller.strategies[printing].capture=lambda *_:None
            old=control.epoch
            try:controller.screenshot()
            except ValueError:pass
            else:raise AssertionError('failed capture accepted')
            assert control.epoch>old and not control.status()['fresh_frame'] and set(captures)=={printing}
            controller.screenshot_controller.strategies[printing].capture=previous;controller.screenshot()
            checks.append('capture_failure_discards_frame_without_desktop_fallback')
            before=len(messages)
            try:controller.click(Point(20,20))
            except ObservationExpired:pass
            else:raise AssertionError('default background mouse did not wait')
            assert control.focus_waiting and not desktop.calls and len(messages)==before
            desktop.foreground=20;control.poll();controller.screenshot();controller.click(Point(20,20))
            assert [entry[2] for entry in messages if entry[0]=='post'][-2:]==[0x201,0x202]
            assert not controller._foreground_required and len(witness)==1
            desktop.foreground=10;control.poll();controller.screenshot()
            checks.append('default_mouse_waits_then_resumes_background')
            ctx.game_config.update('autozzz_background_mouse','flash');controller._game_input_mode='gamepad';before=len(witness)
            controller.click(Point(30,30));assert desktop.current()==10 and desktop.calls==[20,10] and len(witness)==before+1
            checks.append('explicit_mouse_flash_restores_original_foreground')
            before=len(witness);controller.click(gamepad_key='compendium');assert len(witness)==before+1
            assert desktop.current()==10;checks.append('menu_gamepad_substitution_preserves_click_witness')
            control.set_manual_pause(True);before=len(messages)
            try:controller.normal_attack()
            except ObservationExpired:pass
            else:raise AssertionError('manual pause allowed input')
            assert len(messages)==before and not controller.xbox_controller.pressed
            control.set_manual_pause(False);controller.screenshot()
            checks.append('manual_pause_releases_and_blocks_background_input')
            ctx.battle_assistant_config.background_mode=False;controller.begin_input_policy('auto_battle')
            controller.enable_foreground_mode();assert not controller.probe().focused
            ctx.battle_assistant_config.background_mode=True;controller.begin_input_policy('auto_battle');control.poll();controller.screenshot()
            controller.game_config.background_gamepad_type='ds4';controller.init_before_context_run();controller.screenshot();controller.normal_attack()
            assert controller.btn_controller is controller.ds4_controller and controller._get_switch_gamepad_key()=='ds4_rs_up'
            controller.game_config.background_gamepad_type='xbox';controller.init_before_context_run();controller.screenshot()
            checks.append('standalone_background_override_and_ds4_selection')
            iconic[0]=True;control.poll();assert control.focus_waiting
            iconic[0]=False;control.poll();controller.screenshot();checks.append('minimized_window_waits_for_restore')
            try:controller.keyboard_controller.keyboard.type('fixture')
            except ObservationExpired:pass
            else:raise AssertionError('physical typing sent to unrelated foreground')
            assert not controller.keyboard_controller.keyboard.raw.events
            desktop.foreground=20;control.poll();controller.screenshot();controller.keyboard_controller.keyboard.type('fixture')
            assert controller.keyboard_controller.keyboard.raw.events
            checks.append('physical_keyboard_still_requires_actual_foreground')
            # 真正的条件执行器与源原子动作；仅 HUD 判定和设备替换。
            controller._foreground_required=False;desktop.foreground=10;controller.screenshot()
            from zzz_od.auto_battle.auto_battle_operator import AutoBattleOperator
            from one_dragon.base.conditional_operation.operator import ConditionalOperator
            from one_dragon.base.conditional_operation.state_recorder import StateRecord
            content='''author: AutoZZZ offline test
auto_lock_interval: -1
auto_turn_interval: -1
scenes:
  - priority: 1
    interval: 0.02
    handlers:
      - states: "[自定义-后台普攻,0,60]"
        operations:
          - op_name: 按键-普通攻击
            way: 按下
            press: 0.01
'''
            service.policy_save('后台条件回放',content)
            battle=ctx.auto_battle_context;operator=AutoBattleOperator(battle,'auto_battle','后台条件回放',False)
            ConditionalOperator.init(operator);battle.auto_op=operator
            manager.battle_control.allowed=True;manager.battle_control.allowed_epoch=control.epoch
            before=len(controller.xbox_controller.raw.pad.events)
            try:
                assert operator.start_running_async()
                battle.state_record_service.update_state(StateRecord('自定义-后台普攻',time.time()))
                deadline=time.monotonic()+2
                while len(controller.xbox_controller.raw.pad.events)==before and time.monotonic()<deadline:time.sleep(.01)
                assert len(controller.xbox_controller.raw.pad.events)>before and desktop.current()==10
                operator.stop_running();time.sleep(.05)
                before=len(controller.xbox_controller.raw.pad.events)
                battle.state_record_service.update_state(StateRecord('自定义-后台普攻',time.time()));time.sleep(.05)
                assert len(controller.xbox_controller.raw.pad.events)==before
                checks.append('actual_conditional_engine_and_source_atomic_input_in_background')
            finally:operator.dispose();battle.auto_op=None
            controller._foreground_required=False;desktop.foreground=10;controller.end_input_policy();control.reset()
            # 最后运行实际应用槽；邮件内容替换为受控输入回放，不领取实际奖励。
            class FixtureApp(Application):
                def __init__(self,context):super().__init__(context,'email',op_name='后台运行槽回放')
                @operation_node(name='源控制器后台动作',is_start_node=True)
                def action(self):
                    assert self.ctx.controller.background_mode and desktop.current()==10
                    self.ctx.controller.normal_attack();return self.round_success('offline fixture')
            factory=ctx.run_context._application_factory_map['email'];factory.create_application=lambda instance_idx,group_id:FixtureApp(ctx)
            prepared=manager.prepare_application('email');state=wait(manager,prepared['job_id']);assert state['state']=='prepared',state
            started=manager.start_application('email',timeout_seconds=30);state=wait(manager,started['job_id']);assert state['state']=='success',state
            assert desktop.current()==10 and not controller.background_mode and not controller._task_policy_active
            checks.append('actual_run_slot_hands_off_and_releases_background_policy')
        finally:service.close()
    result=dict(checks=checks,actual_source_controller_and_run_slot=True,fixture_models_screens_and_devices=True,game_accessed=False,real_virtual_device_created=False,game_input_sent=False)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
