"""完整 PiP 管道回放：真实 SDK 子进程、源截图线程、GDI、JPEG、Tk；只截自建窗口。"""
from __future__ import annotations

import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time

if '--fixture-worker' not in sys.argv:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def fixture_worker():
    from contextlib import ExitStack,redirect_stdout
    from unittest.mock import patch
    parser=argparse.ArgumentParser();parser.add_argument('--fixture-worker',action='store_true')
    parser.add_argument('--fixture-hwnd',type=int);parser.add_argument('--fixture-pid',type=int);parser.add_argument('--fixture-path',type=Path)
    parser.add_argument('--source',type=Path);parser.add_argument('--data',type=Path);parser.add_argument('--fixture-bridge',type=Path);args=parser.parse_args()
    import autozzz
    assert Path(autozzz.__file__).resolve().is_relative_to(args.fixture_bridge.resolve()),'工作进程未使用指定桥接源码'
    sys.path.insert(0,str(args.source/'src'))
    with redirect_stdout(sys.stderr):
        from autozzz.windows import GameWindow,user32,W,C,process_path
        from autozzz import onedragon_host
        import platform
        platform.uname()
    def owned_window(*_):
        pid=W.DWORD();user32.GetWindowThreadProcessId(args.fixture_hwnd,C.byref(pid))
        if pid.value!=args.fixture_pid or process_path(args.fixture_hwnd).casefold()!=str(args.fixture_path).casefold():
            raise RuntimeError('原自建截图窗口已不存在，不能访问其他窗口。')
        return GameWindow(args.fixture_hwnd)
    def forbidden(*_,**__):raise AssertionError('端到端 PiP 不得访问游戏、网络或输入设备。')
    with ExitStack() as scope:
        scope.enter_context(patch('autozzz.windows.find_game',side_effect=owned_window))
        for name in ('requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                     'autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow','autozzz.windows.user32.ShowWindow',
                     'pynput.mouse.Controller.press','pynput.keyboard.Controller.press','subprocess.Popen'):
            scope.enter_context(patch(name,side_effect=forbidden))
        sys.argv=[sys.argv[0],'--source',str(args.source),'--data',str(args.data)]
        from autozzz.onedragon_worker import main
        return main()


def main():
    from dataclasses import replace
    from unittest.mock import patch
    from types import SimpleNamespace as NS
    import tkinter as tk
    from tkinter import ttk
    from PIL import ImageGrab
    from autozzz.config import GAME_PATHS
    from autozzz.onedragon_runtime import RuntimePaths
    from autozzz.onedragon_pip_session import PipSession
    from autozzz.onedragon_pip_ui import PipController,PipPresentation
    from autozzz.windows import user32,process_path
    from tools.onedragon_background_capture_smoke import SOURCE
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--package',type=Path);args=parser.parse_args();data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    root_path=Path(__file__).resolve().parents[1];defaults=RuntimePaths.discover(args.package or root_path);paths=replace(defaults,data=data)
    bridge=defaults.bundle/'bridge' if defaults.bundle is not None else root_path
    paths.prepare();(data/'config/pip.yml').write_text('enabled: true\nwidth: 480\nx: 30\ny: 30\n',encoding='utf-8')
    own=data/'capture';own.mkdir();source=own/'Capture.cs';executable=own/'CaptureFixture.exe';source.write_text(SOURCE,encoding='utf-8')
    subprocess.run(['C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe','/nologo','/target:exe','/platform:x64','/out:'+str(executable),str(source)],
                   check=True,capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    process=subprocess.Popen([str(executable)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    info=json.loads(process.stdout.readline());assert info['pid']==process.pid
    assert process_path(info['hwnd']).casefold()==str(executable).casefold();time.sleep(.3)
    original_popen=subprocess.Popen;root=None;controller=None;foreground=user32.GetForegroundWindow();sessions=[]
    def spawn(command,**kwargs):
        if command[:3]==['git','-C',str(defaults.source)] and command[3] in ('rev-parse','diff','ls-files'):
            return original_popen(command,**kwargs)
        assert str(command[0])==str(defaults.python) and '-m' in command
        index=command.index('-m');assert command[index+1]=='autozzz.onedragon_worker'
        replacement=command[:index]+[str(Path(__file__).resolve()),'--fixture-worker']+command[index+2:]+[
            '--fixture-hwnd',str(info['hwnd']),'--fixture-pid',str(process.pid),'--fixture-path',str(executable),'--fixture-bridge',str(bridge)]
        return original_popen(replacement,**kwargs)
    try:
        with patch('autozzz.onedragon_runtime.subprocess.Popen',side_effect=spawn):
            root=tk.Tk();root.withdraw();messages=[]
            ui=NS(root=root,ui_scale=1.,server=tk.StringVar(root,'国服'),game_path=tk.StringVar(root,GAME_PATHS['cn']),
                  pip_enabled=tk.BooleanVar(root,False),pip_button=ttk.Checkbutton(root),_log=messages.append)
            def factory(_,server,path):
                session=PipSession(paths,server,path);sessions.append(session);return session
            controller=PipController(ui,session_factory=factory,presentation=PipPresentation(protect_capture=False))
            deadline=time.monotonic()+12
            while time.monotonic()<deadline and not controller.view.visible:
                root.update();time.sleep(.02)
            assert controller.view.visible,messages
            root.update();time.sleep(.1);frame=ImageGrab.grab(window=controller.view.handle).convert('RGB')
            left=frame.getpixel((frame.width//4,frame.height//2));right=frame.getpixel((frame.width*3//4,frame.height//2))
            assert left[0]>left[2]+80 and right[2]>right[0]+80,(left,right)
            frame.save(data/'native-pip-flow.png')
            assert user32.GetForegroundWindow()==foreground
            controller.close();deadline=time.monotonic()+8
            while controller.worker_alive and time.monotonic()<deadline:root.update();time.sleep(.02)
            assert not controller.worker_alive and not sessions[0].client.alive
            result=dict(actual_sdk_child=True,actual_source_capture_worker=True,actual_owned_window_printwindow=True,verified_worker_bridge=str(bridge),
                        actual_json_pipe_jpeg_tk_pixels=True,original_worker_exited=True,foreground_unchanged=True,game_accessed=False,game_input_sent=False)
            (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
    finally:
        if controller is not None:controller.close()
        for session in sessions:session.close();session.worker.join(8)
        if root is not None:
            try:root.destroy()
            except tk.TclError:pass
        if process.poll() is None:
            if user32.IsWindow(info['hwnd']) and process_path(info['hwnd']).casefold()==str(executable).casefold():user32.PostMessageW(info['hwnd'],16,0,0)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
        (data/'fixture-stderr.log').write_text(process.stderr.read(),encoding='utf-8')


if __name__=='__main__':
    raise SystemExit(fixture_worker() if '--fixture-worker' in sys.argv else main())
