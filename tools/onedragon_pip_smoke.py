"""实际 PipConfig、PipCaptureWorker 与自建原生窗口；不访问游戏或实际输入。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import GAME_PATHS
from autozzz.onedragon_runtime import RuntimePaths
from onedragon_background_capture_smoke import SOURCE


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);RuntimePaths(defaults.source,defaults.python,data).prepare()
    sys.path.insert(0,str(defaults.source/'src'))
    from autozzz.onedragon_service import OneDragonService
    from autozzz.windows import GameWindow,user32,process_path
    from one_dragon.base.operation import one_dragon_context  # 在输入拦截之前完成普通依赖导入。
    import numpy as np
    from PySide6.QtCore import Qt
    checks=[];service=OneDragonService(defaults.source,data);process=None
    foreground=user32.GetForegroundWindow()
    try:
        settings=service.pip_settings();assert settings['values']==dict(enabled=False,width=480,x=-1,y=-1)
        updated=service.pip_save(dict(width=640,x=24,y=32),settings['revision'])
        assert updated['values']['width']==640
        try:service.pip_save(dict(width=700),settings['revision'])
        except ValueError:pass
        else:raise AssertionError('stale PiP preferences accepted')
        assert service._ctx is None and service._execution is None
        checks.append('actual_pip_config_save_and_conflict_without_context_or_input_controller')
        from autozzz.run_lock import profile_lock
        from autozzz.rotation_snapshot import snapshot,RotationSnapshot
        from autozzz.onedragon_rotation import rotation_revision
        from autozzz.onedragon_execution import OneDragonExecution
        from types import SimpleNamespace as NS
        before=snapshot(data);rotation=rotation_revision(data);digest=OneDragonExecution.configuration_digest(NS(data=data))
        guard=RotationSnapshot(data,before);guard.install()
        try:
            with profile_lock(data):
                current=service.pip_settings();service.pip_save(dict(width=700),current['revision'])
                guard.check()
                assert snapshot(data)==before and rotation_revision(data)==rotation
                assert OneDragonExecution.configuration_digest(NS(data=data))==digest
        finally:guard.close()
        checks.append('real_presentation_save_during_profile_lock_preserves_execution_and_rotation_guard')
        fixture=data/'owned-capture';fixture.mkdir();source=fixture/'Capture.cs';executable=fixture/'CaptureFixture.exe'
        source.write_text(SOURCE,encoding='utf-8')
        subprocess.run(['C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe','/nologo','/target:exe','/platform:x64','/out:'+str(executable),str(source)],
                       check=True,capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        process=subprocess.Popen([str(executable)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        info=json.loads(process.stdout.readline());assert info['pid']==process.pid
        assert process_path(info['hwnd']).casefold()==str(executable).casefold();time.sleep(.3)
        def forbidden(*_,**__):raise AssertionError('PiP 回放不得启动游戏、访问网络或发送输入。')
        with ExitStack() as scope:
            for name in ('requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                         'autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow','autozzz.windows.user32.ShowWindow',
                         'pynput.mouse.Controller.press','pynput.keyboard.Controller.press'):
                scope.enter_context(patch(name,side_effect=forbidden))
            scope.enter_context(patch('autozzz.windows.find_game',return_value=GameWindow(info['hwnd'])))
            state=service.pip_start('cn',GAME_PATHS['cn']);assert state['state']=='loading'
            worker=service._pip.worker;assert worker is not None
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                state=service.pip_status()
                if state['visible']:break
                time.sleep(.03)
            assert state['visible'] and state['identity']==dict(hwnd=info['hwnd'],pid=process.pid),state
            import cv2,base64
            image=cv2.imdecode(np.frombuffer(base64.b64decode(state['image']['data']),dtype=np.uint8),cv2.IMREAD_COLOR)
            assert image is not None and image[90,80,2]>image[90,80,0]+80 and image[90,240,0]>image[90,240,2]+80
            assert service._ctx is None and service._execution is None and user32.GetForegroundWindow()==foreground
            checks.append('actual_source_qthread_printwindow_identity_rgb_and_jpeg_without_game_context')
            assert 'image' not in service.pip_status(after_sequence=state['sequence']) or service._pip.sequence>state['sequence']
            service.pip_dismiss();assert service.pip_status()['state']=='dismissed'
            assert service._pip.frame is None
            checks.append('dismiss_stops_frame_delivery_without_stopping_game')
            service.pip_stop();assert not worker.isRunning() and service._pip.window is None and service._pip.capture is None
            checks.append('source_capture_thread_finishes_before_gdi_and_process_binding_release')
    finally:
        service.close()
        if process is not None:
            if process.poll() is None:
                if 'info' in locals() and user32.IsWindow(info['hwnd']) and process_path(info['hwnd']).casefold()==str(executable).casefold():
                    user32.PostMessageW(info['hwnd'],16,0,0)
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)  # 仅本工具保有句柄的自建测试进程。
            (data/'fixture-stderr.log').write_text(process.stderr.read(),encoding='utf-8')
        assert user32.GetForegroundWindow()==foreground
    result=dict(checks=checks,actual_source_qthread_and_config=True,actual_owned_window_capture=True,game_accessed=False,game_input_sent=False)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
