"""Main-window screenshot routing: original native session, legacy lease, or read-only SDK."""
import queue
import threading
import time
import uuid

from .config import ROOT
from .global_capture_hotkeys import GlobalCaptureHotkeys
from .onedragon_runtime import OneDragonClient,RuntimePaths,RequestPending


class GlobalCaptureController:
    def __init__(self,ui,*,hotkeys=True):
        self.ui=ui;self.closed=self.closing=False;self.events=queue.Queue();self.commands=queue.Queue()
        self.requests={};self.client=None;self.last_error=''
        self.worker=threading.Thread(target=self.work,name='AutoZZZ-GlobalCapture',daemon=False);self.worker.start()
        self.hotkeys=None
        if hotkeys:
            from .windows import user32
            self.hotkeys=GlobalCaptureHotkeys(ui.root,user32.GetAsyncKeyState,user32.GetForegroundWindow,self.request,self.report)

    def report(self,message):
        if not self.closing:self.ui._log(message)

    @staticmethod
    def current(paths):
        generation=getattr(paths,'generation',None)
        if generation is None:return True
        from .runtime_generation import selection_generation
        return selection_generation(paths.stable_data or paths.data)==generation

    def request(self,method,params,done):
        if self.closing:done(error='全局截图服务正在关闭。');return
        if method not in ('global_capture_status','global_capture_capture'):raise ValueError('不允许的全局截图请求。')
        profile='global' if self.ui.server.get()=='国际服' else 'cn'
        target=self.ui.game_path.get();tag='global-capture-'+uuid.uuid4().hex
        window=getattr(self.ui,'execution_window',None);lease=None
        source=(window if window is not None and not window.closed and not window.closing and window.connected
            and window.worker.is_alive() else None)
        borrowed=None;runner=getattr(self.ui,'runner',None) if getattr(self.ui,'running',False) else None
        # A completed native window can remain open while the main daily Runner
        # owns the game. Capture must borrow the active Runner's capability.
        if runner is not None:source=None
        if source is not None:
            profile=source.profile;target=source.game_path.get()
            if source.app_id=='_rotation':
                borrowed=source.session.client if source.session.bound else None
                if borrowed is None or not borrowed.alive:
                    done(error='轮换正在切换账号或连接，等待原任务完成窗口绑定。');return
                target=None  # The active account's own controller is the authoritative target.
            bootstrap=getattr(source.session,'runner',None)
            if bootstrap is not None:runner=bootstrap;source=None
            elif not getattr(source,'busy',False) and not self.current(getattr(source.session,'paths',None)):
                source=None  # An idle stale window must not pin global capture to an obsolete SDK.
        if runner is not None and source is None:
            profile=runner.settings.server;target=runner.settings.game_path
        params={**params,'game_path':target}
        try:
            if source is None and method=='global_capture_capture':
                if runner is not None:
                    lease=runner.capture_lease();params['legacy_lease']=lease.export()
            self.requests[tag]=(done,lease,source)
            if source is not None and borrowed is None:source.session.send('rpc',method=method,params=params,tag=tag)
            else:self.commands.put((profile,method,params,tag,borrowed))
        except Exception as exc:
            self.complete(tag,error=str(exc))

    def complete(self,tag,result=None,error=None):
        item=self.requests.pop(tag,None)
        if item is None:return
        done,lease,_=item
        try:
            if lease is not None:lease.close()
        except Exception as exc:error=error or str(exc)
        done(result=result,error=error)

    def receive(self,event):
        tag=event.get('tag','')
        if not str(tag).startswith('global-capture-'):return False
        self.complete(tag,result=event.get('result'),error=event.get('message') if event['type']!='rpc_result' else None)
        return True

    @staticmethod
    def call(client,method,**params):
        key=client.submit(method,**params)
        while True:
            try:return client.result(key,timeout=.5)
            except RequestPending:continue

    @staticmethod
    def wait_close(client):
        while True:
            try:client.close(timeout=1);return
            except RequestPending:continue

    def work(self):
        client=None;profile=None
        try:
            while True:
                desired,method,params,tag,borrowed=self.commands.get()
                if method=='close' or self.closing:break
                try:
                    if borrowed is not None:
                        value=self.call(borrowed,method,**params)
                        self.events.put(dict(type='rpc_result',tag=tag,result=value));continue
                    if client is None or profile!=desired or not self.current(client.paths):
                        if client is not None:self.wait_close(client)
                        client=OneDragonClient(RuntimePaths.discover(ROOT,desired));self.client=client;profile=desired
                        try:client.start()
                        except RequestPending:
                            while True:
                                try:client.finish_start(timeout=.5);break
                                except RequestPending:continue
                    if self.closing:break
                    value=self.call(client,method,**params)
                    self.events.put(dict(type='rpc_result',tag=tag,result=value))
                except Exception as exc:
                    self.events.put(dict(type='error',tag=tag,message=str(exc)))
                    if borrowed is None and client is not None and not client.alive:
                        self.wait_close(client);client=None;self.client=None;profile=None
        finally:
            if client is not None:
                while True:
                    try:self.wait_close(client);break
                    except Exception as exc:
                        # An unconfirmed exit must retain this one worker, never spawn a replacement.
                        self.events.put(dict(type='close_error',message=str(exc)));time.sleep(.2)
            self.events.put(dict(type='closed'))

    def poll(self):
        while True:
            try:event=self.events.get_nowait()
            except queue.Empty:break
            if event['type']=='closed':self.closed=True
            elif event['type']=='close_error':
                if event['message']!=self.last_error:self.report(event['message']);self.last_error=event['message']
            else:self.receive(event)
        for tag,(_,_,source) in list(self.requests.items()):
            if source is not None and (source.closed or not source.worker.is_alive()):
                self.complete(tag,error='原运行连接已关闭，截图请求已取消。')

    def close(self):
        if self.closing:return
        self.closing=True
        if self.hotkeys is not None:self.hotkeys.close()
        for tag in list(self.requests):self.complete(tag,error='全局截图服务已关闭。')
        self.commands.put((None,'close',{},None,None))
