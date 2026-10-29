"""画中画专用只读工作连接；只保留最新帧，不写运行报告或发通知。"""
from __future__ import annotations

import queue
import threading
import time

from .onedragon_runtime import OneDragonClient,RequestPending


class PipSession:
    def __init__(self,paths,server,game_path):
        self.paths=paths;self.server=server;self.game_path=game_path
        self.commands=queue.Queue();self.events=queue.Queue();self.closing=False
        self.lock=threading.RLock();self.latest=None;self.ready=False;self.client=None
        self.worker=threading.Thread(target=self._work,name='AutoZZZ-PictureInPicture',daemon=False);self.worker.start()

    def send(self,command,**params):
        if not self.closing:self.commands.put((command,params))

    def take_state(self):
        with self.lock:result=self.latest;self.latest=None;return result

    def close(self):
        if not self.closing:
            self.closing=True;self.commands.put(('close',{}))

    def allow_user_focus(self):
        if self.closing:return False
        client=self.client
        if client is None or not client.alive or client.worker_pid is None:return False
        from .windows import user32,W
        user32.AllowSetForegroundWindow.argtypes=[W.DWORD];user32.AllowSetForegroundWindow.restype=W.BOOL
        return bool(user32.AllowSetForegroundWindow(client.worker_pid))

    @staticmethod
    def _call(client,method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=.5)
            except RequestPending:continue

    def _work(self):
        client=OneDragonClient(self.paths);self.client=client;settings=None;enabled=False;sequence=-1
        try:
            if self.closing:return
            try:client.start()
            except RequestPending:
                while True:
                    try:client.finish_start(timeout=.5);break
                    except RequestPending:continue
            if self.closing:return
            settings=self._call(client,'pip_settings');enabled=settings['values']['enabled'];self.ready=True
            self.events.put(dict(type='settings',settings=settings))
            target=(self.server,self.game_path);active_target=None
            while True:
                try:command,params=self.commands.get(timeout=.03 if enabled else .2)
                except queue.Empty:command=None;params={}
                if command=='close':break
                if self.closing and command not in ('enabled','geometry'):continue
                try:
                    if command=='target':target=(params['server'],params['game_path'])
                    elif command in ('enabled','geometry'):
                        values=dict(enabled=params['enabled']) if command=='enabled' else params['values']
                        settings=self._call(client,'pip_save',values=values,revision=settings['revision'])
                        enabled=settings['values']['enabled']
                        self.events.put(dict(type='settings',settings=settings,version=params.get('version')))
                    elif command=='dismiss':self._call(client,'pip_dismiss')
                    elif command=='activate':
                        result=self._call(client,'pip_activate',epoch=params['epoch'])
                        self.events.put(dict(type='activated',**result))
                    if self.closing:continue
                    if enabled and active_target!=target and not self.closing:
                        active_target=target;sequence=-1
                        self._call(client,'pip_start',server=target[0],game_path=target[1])
                    elif not enabled and active_target is not None:
                        self._call(client,'pip_stop');active_target=None;sequence=-1
                    state=self._call(client,'pip_status',after_sequence=sequence)
                    if 'image' in state:sequence=state['sequence']
                    state['target']=target[1]
                    with self.lock:self.latest=state
                except Exception as exc:
                    self.events.put(dict(type='error',message=str(exc),settings=settings,version=params.get('version')))
                    with self.lock:self.latest=dict(enabled=enabled,state='error',visible=False,sequence=-1,epoch=-1,error=str(exc),target=target[1])
                if not getattr(client,'protocol_available',True) or not client.alive:break
        except Exception as exc:self.events.put(dict(type='error',message=str(exc)))
        finally:
            self.ready=False
            last_error=None
            while True:
                try:client.close(timeout=5);break
                except RequestPending:continue
                except Exception as exc:
                    if str(exc)!=last_error:self.events.put(dict(type='error',message=str(exc)));last_error=str(exc)
                    time.sleep(.2)  # 继续等待同一原进程，不能因退出结果暂时未知而另开连接。
            self.events.put(dict(type='closed',exited=True))
