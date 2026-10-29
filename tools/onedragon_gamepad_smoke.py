"""源 Xbox/DS4 控制器及托管设备回放，底层虚拟设备为记录器。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


class Constants:
    def __getattr__(self,name):return name


class Pad:
    def __init__(self):self.events=[];self.on_update=None
    def __getattr__(self,name):
        def record(*args,**kwargs):self.events.append((name,args,kwargs))
        return record
    def update(self):
        self.events.append(('update',(),{}))
        if self.on_update is not None:
            callback,self.on_update=self.on_update,None;callback()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    RuntimePaths(defaults.source,defaults.python,data).prepare();sys.path.insert(0,str(defaults.source/'src'));platform.uname()
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_host import ManagedController
    from autozzz.onedragon_control import ExecutionControl,WindowState,ObservationExpired
    from autozzz.onedragon_game_input import GameInputStore
    from autozzz.onedragon_gamepad import release_controller_inputs
    from one_dragon.base.controller.pc_button.xbox_button_controller import XboxButtonController
    from one_dragon.base.controller.pc_button.ds4_button_controller import Ds4ButtonController
    fake=NS(VX360Gamepad=Pad,VDS4Gamepad=Pad,XUSB_BUTTON=Constants(),DS4_BUTTONS=Constants(),DS4_DPAD_DIRECTIONS=Constants(),DS4_SPECIAL_BUTTONS=Constants())
    def forbidden(*_,**__):raise AssertionError('离线手柄回放不能访问真实设备、游戏或网络。')
    result=dict(checks=[],devices=[],game_accessed=False,real_virtual_device_created=False,game_input_sent=False)
    with ExitStack() as scope:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                     'pynput.keyboard.Controller.press','pynput.mouse.Controller.press','autozzz.windows.find_game','autozzz.windows.user32.SendInput'):
            scope.enter_context(patch(name,side_effect=forbidden))
        scope.enter_context(patch.dict(sys.modules,{'vgamepad':fake}))
        scope.enter_context(patch('one_dragon.base.controller.pc_button.pc_button_utils.is_vgamepad_installed',return_value=True))
        service=OneDragonService(defaults.source,data)
        try:
            config=service.ctx.game_config
            controller=ManagedController.__new__(ManagedController)
            controller.game_config=config;controller.background_mode=False;controller.xbox_controller=controller.ds4_controller=None
            controller.keyboard_controller=NS(reset=lambda:None);controller.btn_controller=controller.keyboard_controller
            control=ExecutionControl(lambda:WindowState(True,True,(0,0,1920,1080)),lambda:release_controller_inputs(controller),lambda:True,lambda:None,lambda:None)
            controller.control=control;control.observe(lambda:None)
            for kind,raw_type in (('xbox',XboxButtonController),('ds4',Ds4ButtonController)):
                getattr(controller,'enable_'+kind)();device=controller.btn_controller
                assert type(device.raw)is raw_type
                device.set_key_press_time(.001)
                for key in device.raw._key_bindings:
                    before=len(device.raw.pad.events);device.tap(key)
                    assert len(device.raw.pad.events)>before and not device.pressed
                assert controller._get_switch_gamepad_key()==kind+'_rs_up'
                before=len(device.raw.pad.events);controller.normal_attack()
                assert len(device.raw.pad.events)>before
                device.turn(2,-1,1000)
                assert ('right_joystick_float',(1.,.5),{}) in device.raw.pad.events
                assert ('right_joystick_float',(0,0),{}) in device.raw.pad.events
                first,second=list(device.raw._key_bindings)[:2]
                device.raw.pad.on_update=lambda:control.set_manual_pause(True)
                device.tap_combo([first,second]);assert not device.pressed
                with_scope_old=control.epoch
                control.set_manual_pause(False);control.observe(lambda:None)
                with control.scope(with_scope_old):
                    try:device.press(first)
                    except ObservationExpired:pass
                    else:raise AssertionError('old epoch accepted')
                result['devices'].append(dict(kind=kind,actual_controller=type(device.raw).__module__+'.'+type(device.raw).__name__,keys=len(device.raw._key_bindings)))
            result['checks']+=['all_actual_source_key_bindings','actual_combat_action_mapping','camera_normalized_and_released','combo_pause_stops_next_key','old_observation_rejected']
            store=GameInputStore(service);description=store.describe();store.save(dict(key_normal_attack='j',xbox_key_normal_attack='xbox_b',ds4_key_normal_attack='ds4_circle'),description['revision'])
            controller.game_config=service.ctx.game_config;controller.enable_xbox()
            assert controller.action_keys['normal_attack']=='xbox_b'
            controller.enable_ds4();assert controller.action_keys['normal_attack']=='ds4_circle'
            controller.enable_keyboard();assert controller.action_keys['normal_attack']=='j'
            result['checks'].append('saved_game_config_used_by_actual_controller')
            with patch('one_dragon.base.controller.pc_button.pc_button_utils.is_vgamepad_installed',return_value=False):
                try:controller.enable_xbox()
                except ValueError:pass
                else:raise AssertionError('missing dependency accepted')
            result['checks'].append('missing_dependency_rejected')
            release_controller_inputs(controller);assert not controller.xbox_controller.pressed and not controller.ds4_controller.pressed
            assert service.ctx.controller is None
        finally:service.close()
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
