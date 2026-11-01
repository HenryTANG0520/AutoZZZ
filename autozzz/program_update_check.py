"""Metadata-only startup check, retaining original RPCs and process through cancellation."""
from __future__ import annotations
import threading
import time

from .onedragon_runtime import OneDragonClient, RequestPending


def result_message(catalog):
    if (type(catalog) is not dict or type(catalog.get('current')) is not str or type(catalog.get('releases')) is not list
        or any(type(row) is not dict or type(row.get('version')) is not str for row in catalog['releases'])):
        raise ValueError('版本服务返回无效结果。')
    rows=catalog['releases'];current=catalog['current']
    if not rows:return catalog.get('unavailable_reason') or '暂未找到可安装的完整发布包。'
    latest=rows[0]['version']
    if latest==current:return '当前版本与本通道最新发布一致：'+current
    # A dev build or a different tag is not necessarily older; do not invent version ordering.
    return '本通道最新发布：'+latest+'；当前版本：'+current+'。可打开版本与归档查看。'


class MetadataCheck:
    def __init__(self, paths, channel, emit, *, client_factory=OneDragonClient):
        if channel not in ('stable','beta'):raise ValueError('更新通道无效。')
        self.paths=paths;self.channel=channel;self.emit=emit;self.client_factory=client_factory
        self.cancelled=threading.Event();self.catalog=None;self.job_id=None;self.process_exited=False
        self.worker=threading.Thread(target=self.work,name='AutoZZZ-Startup-Update',daemon=False);self.worker.start()

    @property
    def alive(self):return self.worker.is_alive()

    def cancel(self):self.cancelled.set()
    close=cancel

    def work(self):
        client=self.client_factory(self.paths);failure=None;outcome='cancelled';cancel_sent=False
        def status(message):self.emit(dict(type='update_status',message=message))
        def call(method,**params):
            request=client.submit(method,**params)
            while True:
                try:return client.result(request,timeout=.2)
                except RequestPending:status('等待原更新请求响应…')
        try:
            status('正在连接版本服务…')
            try:client.start(timeout=.2)
            except RequestPending:
                while True:
                    try:client.finish_start(timeout=.2);break
                    except RequestPending:status('等待原更新进程连接…')
            if not self.cancelled.is_set():
                job=call('program_check',channel=self.channel,page=1);self.job_id=job['job_id']
                while True:
                    if job.get('job_id')!=self.job_id:raise ValueError('版本任务编号发生变化，未启动其他任务。')
                    if self.cancelled.is_set() and not cancel_sent:
                        cancel_sent=True;job=call('program_cancel',job_id=self.job_id)
                    if not job['busy']:
                        outcome=job['state']
                        if outcome=='success':self.catalog=job['result']
                        elif outcome!='cancelled':failure=job.get('detail') or '检查更新失败。'
                        break
                    status('正在取消并等待版本请求结束…' if cancel_sent else job['detail'])
                    self.cancelled.wait(.4) if not cancel_sent else time.sleep(.1)
                    job=call('program_status',job_id=self.job_id)
        except Exception as exc:failure=str(exc);outcome='failed'
        finally:
            status('正在等待原更新进程退出…')
            while True:
                try:client.close(timeout=.2);self.process_exited=True;break
                except RequestPending:continue
                except Exception as exc:
                    failure=failure or str(exc);outcome='failed'
                    try:
                        if not client.alive:self.process_exited=True;break
                    except Exception:pass
                    status('尚未核实原更新进程退出，继续等待…');time.sleep(.2)
            if self.cancelled.is_set() and outcome=='success':outcome='cancelled'
            try:message=('已取消检查更新。' if outcome=='cancelled' else failure or result_message(self.catalog))
            except (ValueError,TypeError,KeyError) as exc:outcome='failed';message=str(exc)
            self.emit(dict(type='update_finished',state=outcome,message=message,catalog=self.catalog,
                           process_exited=self.process_exited))
