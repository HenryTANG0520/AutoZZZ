"""上游画中画的只读截图与偏好；预览帧不进入执行控制器。"""
from __future__ import annotations

import base64
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from .config_transaction import atomic_apply,config_revision
from .run_lock import profile_lock


class PipService:
    def __init__(self,root):
        self.root=Path(root);self.enabled=False;self.closed=False;self.dismissed=False
        self.window=self.capture=self.worker=None
        self.epoch=0;self.sequence=0;self.capturing=False;self.frame=None;self.encoded=None
        self.frame_lock=threading.RLock();self.next_bind=0.;self.path='';self.error=''
        self._capture_epoch=-1

    def _config(self):
        from one_dragon.base.config.pip_config import PipConfig
        from one_dragon.base.config.yaml_operator import cached_yaml_data
        cached_yaml_data.clear()
        return PipConfig()

    def settings(self):
        config=self._config()
        values=dict(enabled=config.enabled,width=config.width,x=config.x,y=config.y)
        self._validate(values)
        return dict(values=values,revision=config_revision(config))

    @staticmethod
    def _validate(values):
        if not isinstance(values,dict) or set(values)-{'enabled','width','x','y'}:raise ValueError('画中画设置字段无效。')
        for name,value in values.items():
            if name=='enabled':
                if type(value)is not bool:raise ValueError('画中画开关须为布尔值。')
            elif type(value)is not int or not ((320<=value<=1920) if name=='width' else (-2**31<=value<2**31)):
                raise ValueError('画中画尺寸或位置超出范围。')

    def save(self,values,revision):
        self._validate(values)
        with profile_lock(self.root/'.pip-presentation'):
            config=self._config()
            atomic_apply(config,revision,lambda:[setattr(config,key,value) for key,value in values.items()],self.root)
        return self.settings()

    def start(self,server,game_path):
        if self.closed:raise RuntimeError('画中画服务已关闭。')
        from .config import Settings
        Settings(server=server,game_path=game_path).validate('check')
        if self.enabled and self.path==game_path:return self.status()
        self.stop();self.path=game_path;self.enabled=True;self.dismissed=False;self.next_bind=0.;self.error=''
        return self.status()

    def _clear_frame(self):
        with self.frame_lock:
            self.epoch+=1;self.frame=self.encoded=None

    def _release(self):
        self.capturing=False;self._clear_frame()
        if self.worker is not None:
            self.worker.stop();self.worker=None  # 上游 stop 等原截图线程退出后才释放 GDI。
        if self.capture is not None:self.capture.cleanup();self.capture=None
        if self.window is not None:self.window.clear_binding();self.window=None

    def stop(self):
        self.enabled=False;self.dismissed=False;self._release()
        return self.status()

    def close(self):
        self.stop();self.closed=True

    def dismiss(self):
        self.dismissed=True;self.capturing=False;self._clear_frame()
        if self.worker is not None:self.worker.pause()
        return self.status()

    def _bind(self):
        from .windows import find_game,user32,kernel32,W,C
        from .onedragon_host import BoundGameWindow,WindowCapture
        bound=find_game(self.path)
        window=BoundGameWindow(1920,1080);window.bound=bound
        pid=W.DWORD();user32.GetWindowThreadProcessId(bound.hwnd,C.byref(pid))
        window.process_id=pid.value;window.process_handle=kernel32.OpenProcess(0x101000,False,pid.value)
        capture=None
        try:
            if not window.identity_valid():raise RuntimeError('画中画绑定期间游戏身份已变化。')
            window.init_win()
            capture=WindowCapture(SimpleNamespace(using_background=True),window,1920,1080)
            if capture.init_screenshot('auto') is None:
                capture.cleanup();raise RuntimeError('画中画窗口截图器初始化失败。')
            self.window,self.capture=window,capture;self._clear_frame();self.error=''
        except Exception:
            try:
                if capture is not None:capture.cleanup()
            finally:window.clear_binding()
            raise

    def _valid_for_capture(self):
        from .windows import user32
        window=self.window
        return (self.enabled and self.capturing and not self.dismissed and window is not None
                and window.identity_valid() and not user32.IsIconic(window.bound.hwnd)
                and user32.GetForegroundWindow()!=window.bound.hwnd)

    def _capture(self):
        if not self._valid_for_capture():return None
        epoch=self.epoch
        try:image=self.capture.get_screenshot(resize=False)
        except Exception as exc:
            with self.frame_lock:self.frame=self.encoded=None
            self.error='窗口截图失败：'+str(exc);return None
        if (image is None or image.ndim!=3 or image.shape[2]!=3 or not image.size
                or image.shape[0]*image.shape[1]>36_000_000 or image[::16,::16].std()<2):
            with self.frame_lock:self.frame=self.encoded=None
            self.error='游戏画面尚未就绪。';return None
        if epoch!=self.epoch or not self._valid_for_capture():return None
        self._capture_epoch=epoch;return image

    def _on_frame(self,image):
        if self._capture_epoch!=self.epoch or not self._valid_for_capture():return
        with self.frame_lock:
            self.sequence+=1;self.frame=(self.sequence,self.epoch,time.monotonic(),image)
            self.encoded=None;self.error=''

    def _resume(self):
        self.capturing=True
        if self.worker is None:
            from one_dragon_qt.services.pip.pip_capture_worker import PipCaptureWorker
            from PySide6.QtCore import Qt
            self.worker=PipCaptureWorker(self._capture)
            self.worker.frame_ready.connect(self._on_frame,Qt.ConnectionType.DirectConnection)
            self.worker.start()
        else:self.worker.resume()

    def status(self,after_sequence=-1):
        if type(after_sequence)is not int or after_sequence< -1:raise ValueError('画中画帧编号无效。')
        result=dict(enabled=self.enabled,state='off',visible=False,sequence=self.sequence,epoch=self.epoch,error=self.error)
        if not self.enabled:return result
        if self.window is not None and not self.window.identity_valid():self._release()
        if self.window is None:
            if time.monotonic()>=self.next_bind:
                self.next_bind=time.monotonic()+.5
                try:self._bind()
                except Exception as exc:self.error=str(exc)
            if self.window is None:
                result.update(state='waiting',error=self.error);return result
        from .windows import user32
        hwnd=self.window.bound.hwnd
        result['identity']=dict(hwnd=hwnd,pid=self.window.process_id)
        foreground=user32.GetForegroundWindow()==hwnd;minimized=bool(user32.IsIconic(hwnd))
        if foreground or minimized or self.dismissed:
            if self.capturing:self.capturing=False;self._clear_frame()
            if self.worker is not None:self.worker.pause()
            if foreground:self.dismissed=False
            result.update(state='foreground' if foreground else 'minimized' if minimized else 'dismissed',epoch=self.epoch)
            return result
        self._resume()
        with self.frame_lock:frame=self.frame
        if frame is None or frame[1]!=self.epoch or time.monotonic()-frame[2]>1.5:
            result.update(state='loading',error=self.error,epoch=self.epoch);return result
        result.update(state='visible',visible=True,sequence=frame[0],epoch=self.epoch,error=self.error)
        if frame[0]!=after_sequence:
            cached=self.encoded
            payload=cached[1] if cached is not None and cached[0]==frame[0] else self._encode(frame[3])
            with self.frame_lock:
                if self.frame is frame:self.encoded=(frame[0],payload)
            result['image']=payload
        return result

    @staticmethod
    def _encode(image):
        import cv2
        height,width=image.shape[:2];ratio=min(1.,1920/width,1080/height)
        if ratio<1:image=cv2.resize(image,(max(1,round(width*ratio)),max(1,round(height*ratio))))
        bgr=cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
        for quality in (85,75,60,40):
            success,png=cv2.imencode('.jpg',bgr,[cv2.IMWRITE_JPEG_QUALITY,quality])
            if success and png.size<=1_250_000:
                return dict(width=image.shape[1],height=image.shape[0],format='jpeg',data=base64.b64encode(png).decode('ascii'))
        raise RuntimeError('画中画帧过大，未发送。')

    def activate(self,epoch):
        if type(epoch)is not int or epoch!=self.epoch or not self.enabled or self.window is None or not self.window.identity_valid():
            raise ValueError('画中画游戏身份已变化，未切换窗口。')
        # 仅响应用户点击预览；自动显示和截图永不激活游戏。
        from .windows import user32
        hwnd=self.window.bound.hwnd
        if user32.IsIconic(hwnd):user32.ShowWindow(hwnd,9)
        if not self.window.identity_valid():raise ValueError('游戏窗口身份已变化。')
        user32.SetForegroundWindow(hwnd)
        return dict(requested=True,foreground=user32.GetForegroundWindow()==hwnd)
