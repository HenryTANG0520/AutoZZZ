"""一次正常关闭请求后观察原进程句柄；窗口消失不能替代进程退出证据。"""
from __future__ import annotations

from .models import FocusLost,InputInterrupted
from .onedragon_control import WindowState,ObservationExpired,ExecutionStopped


class ClientExit:
    def __init__(self,controller,control):
        self.controller,self.control=controller,control
        window=controller.game_win
        if window.bound is None:raise ValueError('尚未绑定待关闭的游戏窗口。')
        self.progress=dict(requested=False,process_exited=False,window_handle=window.bound.hwnd,process_id=window.process_id)

    def probe(self):
        if self.progress['requested']:
            return WindowState(True,True,self.control.geometry)
        return self.controller.probe()

    def input_guard(self):
        if self.progress['requested']:raise ExecutionStopped('游戏关闭请求已发送，禁止后续游戏输入。')

    def submit(self,action):
        def send():
            result=action()
            if result:self.progress['requested']=True
            return result
        return self.control.input(send)

    def run(self,cancelled,wait):
        watch=None
        try:
            while True:
                if watch is not None and watch.exited():
                    self.progress['process_exited']=True
                    return True
                if cancelled.is_set() or self.control.stopped:return False
                state=self.control.poll()
                if self.control.stopped:return False
                if watch is None and state['state']=='running':
                    try:
                        self.control.observe(lambda:None)
                        with self.control.scope():
                            watch=self.controller.game_win.bound.request_close(
                                lambda:self.control.checkpoint(require_frame=True),submit=self.submit)
                    except (FocusLost,InputInterrupted,ObservationExpired):
                        if self.progress['requested']:raise RuntimeError('关闭请求已尝试，但未取得原进程退出监测句柄。') from None
                        continue
                wait(.02)
        finally:
            if watch is not None:watch.close()

    def status(self):return dict(self.progress)
