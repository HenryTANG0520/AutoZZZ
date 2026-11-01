"""共用既有 SDK 连接的追踪订阅；迟到的数据不会撤销新的显示意图。"""
from __future__ import annotations

import threading
import time


class TraceSubscription:
    def __init__(self,emit):
        self.emit=emit;self.lock=threading.RLock();self.enabled=False;self.version=0
        self.applied=None;self.next_poll=0.;self.clear_version=0;self.error=''
        self.latest=None;self.notification_pending=False;self.capture_version=None;self.capture_deadline=0.

    def _deliver(self,snapshot,version=None):
        with self.lock:
            if snapshot is not None and self.latest is not None and self.latest.get('version')==version:
                previous=self.latest.get('snapshot') or {}
                if 'capture' in previous and 'capture' not in snapshot:snapshot=dict(snapshot,capture=previous['capture'],capture_snapshot=previous.get('capture_snapshot'))
            self.latest=dict(type='trace',snapshot=snapshot,version=version)
            if not self.notification_pending:
                self.notification_pending=True;self.emit(dict(type='trace'))

    def take(self):
        with self.lock:
            result=self.latest;self.latest=None;self.notification_pending=False;return result

    def configure(self,enabled,clear=False,*,capture=False):
        if type(capture)is not bool:raise ValueError('截图请求须为布尔值。')
        if type(enabled)is not bool or type(clear)is not bool:raise ValueError('追踪开关须为布尔值。')
        with self.lock:
            self.enabled=enabled;self.version+=1
            self.capture_version=self.version if capture and enabled else None
            self.capture_deadline=time.monotonic()+30 if capture and enabled else 0.
            if clear:self.clear_version=self.version
            return self.version

    def disconnected(self):
        with self.lock:self.applied=None;self.next_poll=0.;self.capture_version=None
        self._deliver(None)

    def pump(self,client,call):
        with self.lock:
            enabled,version,clear_version=self.enabled,self.version,self.clear_version
        if not version:return  # 默认不创建调试请求，也不初始化上下文。
        try:
            key=(client,version)
            if self.applied!=key:
                call('trace_configure',enabled=enabled,clear=clear_version==version)
                self.applied=key;self.next_poll=0.
                if not enabled:
                    with self.lock:
                        if version==self.version:self._deliver(None,version)
            if not enabled:return
            if time.monotonic()<self.next_poll:return
            self.next_poll=time.monotonic()+.2
            snapshot=call('trace_snapshot') if enabled else None
            with self.lock:capture=version==self.version==self.capture_version
            if capture:
                if time.monotonic()>self.capture_deadline:
                    with self.lock:self.capture_version=None
                    self.emit(dict(type='trace_error',message='截图等待超时，请回到游戏后重新请求。'))
                elif snapshot and snapshot.get('control',{}).get('state') in ('running','paused','focus_wait'):
                    try:result=call('trace_capture',epoch=snapshot['epoch'],generation=snapshot['generation'])
                    except Exception:
                        with self.lock:self.capture_version=None
                        raise
                    if not result.get('waiting'):
                        captured_snapshot=result['snapshot'];snapshot=dict(captured_snapshot,capture=result['capture'],capture_snapshot=captured_snapshot)
                        with self.lock:
                            if self.capture_version==version:self.capture_version=None
            with self.lock:
                if version!=self.version:return
                self._deliver(snapshot,version)
            self.error=''
        except Exception as exc:
            with self.lock:
                if version!=self.version:return
            message=str(exc)
            if message!=self.error:self.emit(dict(type='trace_error',message=message))
            self.error=message;self.next_poll=time.monotonic()+.5
