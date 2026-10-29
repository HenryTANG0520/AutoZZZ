"""插件执行专用 SDK 夹具；只替换模型、窗口和设备，保留应用执行与协议。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack,redirect_stdout
import json
from pathlib import Path
import platform
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--data',type=Path,required=True);parser.add_argument('--fixture-bridge',type=Path,required=True);args=parser.parse_args()
    import autozzz
    assert Path(autozzz.__file__).resolve().is_relative_to(args.fixture_bridge.resolve())
    root=args.data.resolve();assert (root/'.autozzz-plugin-execution-test').read_text()=='offline fixture only'
    platform.uname();sys.path.insert(0,str(args.source/'src'))
    from one_dragon.utils import os_utils
    os_utils.set_work_dir(root)
    state=root/'autozzz';state.mkdir(exist_ok=True);lock=threading.RLock()
    def save(name,value):
        with lock:
            path=state/name;temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value),encoding='utf-8');temporary.replace(path)
    def forbidden(*_,**__):raise AssertionError('插件执行管道不得访问游戏、网络或物理输入。')
    with ExitStack() as scope:
        with redirect_stdout(sys.stderr):
            for target in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                           'autozzz.windows.find_game','autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow','autozzz.windows.user32.ShowWindow',
                           'pynput.keyboard.Controller.press','pynput.mouse.Controller.press','pynput.keyboard.Controller.release','pynput.mouse.Controller.release'):
                scope.enter_context(patch(target,side_effect=forbidden))
            scope.enter_context(patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0))
            import numpy as np
            from autozzz.onedragon_host import ManagedZContext,ControlledDevice
            from autozzz.onedragon_control import WindowState
            class Fixture:
                def __init__(self):self.started=threading.Event();self.finish=threading.Event()
                @property
                def mode(self):return json.loads((state/'fixture.json').read_text(encoding='utf-8'))['mode']
            class Raw:
                def __init__(self):self.packets=[]
                def press(self,key):self.packets.append(['down',str(key)]);save('packets.json',self.packets)
                def release(self,key):self.packets.append(['up',str(key)]);save('packets.json',self.packets)
            class Keys:
                def __init__(self,control):self.control=control;self.keyboard=ControlledDevice(Raw(),control);self.mouse=ControlledDevice(Raw(),control)
                def press(self,key):self.keyboard.press(key)
                def release(self,key):self.keyboard.release(key)
                def tap(self,key):
                    self.press(key)
                    try:self.control.checkpoint(require_frame=True)
                    finally:self.release(key)
                def reset(self):self.keyboard.reset();self.mouse.reset()
            class Controller:
                is_game_window_ready=True;screenshot_method='auto'
                def __init__(self,ctx):self.ctx=ctx;self.keyboard_controller=Keys(ctx.execution_control);self.screenshot_controller=SimpleNamespace(init_screenshot=lambda *_:None)
                def bind(self,_):pass
                def probe(self):return WindowState(True,True,(0,0,1920,1080))
                def init_before_context_run(self):return True
                def screenshot(self):return self.ctx.execution_control.observe(lambda:(time.time(),np.zeros((1080,1920,3),np.uint8)))
                def sync_game_config(self,*_):pass
                def set_window_title(self,*_):pass
                def release_only(self,*_,release=False,**__):assert release;self.keyboard_controller.reset()
                dodge=normal_attack=special_attack=move_w=move_s=move_a=move_d=switch_next=switch_prev=switch_backup=ultimate=chain_left=chain_right=chain_cancel=lock=release_only
                def cleanup_after_app_shutdown(self):self.keyboard_controller.reset()
            def initialize(ctx):
                ctx.register_application_factory();ctx.screen_loader.reload();ctx.fixture=Fixture();ctx.controller=Controller(ctx);ctx.ocr._model=object();ctx.ready_for_application=True
                path=state/'initializations.json';save('initializations.json',(json.loads(path.read_text()) if path.exists() else 0)+1)
            scope.enter_context(patch.object(ManagedZContext,'init',initialize))
            from autozzz.onedragon_worker import main as worker
        sys.argv=[sys.argv[0],'--source',str(args.source),'--data',str(root)];return worker()


if __name__=='__main__':main()
