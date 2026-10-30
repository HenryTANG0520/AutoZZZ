"""供脚本调用的完整包更新入口；返回交接就绪，安装结果由独立助手记录。"""
from __future__ import annotations

from pathlib import Path
import signal
import threading
import time

from .config import ROOT
from .onedragon_bundle import digest
from .onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending
from .program_package import read_manifest
from .program_update import ProgramUpdate,write_json
from .program_update_handoff import UpdateHandoff


def run_update(package,report,*,server='cn',restart=True,client_factory=OneDragonClient,handoff_factory=UpdateHandoff):
    read_manifest(ROOT)
    if (ROOT/'.git').exists():raise ValueError('源码目录不能原地替换为便携包。')
    report=Path(report).resolve();report.parent.mkdir(parents=True,exist_ok=True)
    with report.open('x',encoding='utf-8') as stream:stream.write('{}')
    stop=threading.Event();client=client_factory(RuntimePaths.discover(ROOT,server));handoff=None;ready=False
    previous=signal.signal(signal.SIGINT,lambda *_:stop.set())
    def save(**value):write_json(report,value);return value
    def call(method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=.5)
            except RequestPending:continue
    try:
        save(state='preparing',package=str(Path(package).resolve()))
        try:
            try:client.start()
            except RequestPending:
                while True:
                    try:client.finish_start(.5);break
                    except RequestPending:continue
            job=call('program_import',path=str(Path(package).resolve()))['job_id']
            while True:
                state=call('program_cancel' if stop.is_set() else 'program_status',job_id=job)
                if state['job_id']!=job:raise RuntimeError('更新任务编号已变化，请保留原进程。')
                save(state=state['state'],detail=state['detail'],job_id=job)
                if not state['busy']:break
                time.sleep(.1)
            if stop.is_set() or state['state']=='cancelled':raise InterruptedError(state['detail'])
            if state['state']!='success':raise RuntimeError(state['detail'])
            plan=state['result']
        finally:
            while True:
                try:client.close(timeout=5);break
                except RequestPending:continue
        transaction=ProgramUpdate(plan['transaction'])
        if transaction.target!=ROOT or digest(transaction.journal)!=plan['journal_hash']:raise ValueError('更新事务已变化，未替换当前程序。')
        handoff=handoff_factory(transaction,restart=restart);handoff.start()
        while not handoff.poll():
            if stop.is_set():raise InterruptedError('已取消更新交接。')
            time.sleep(.1)
        if stop.is_set():raise InterruptedError('已取消更新交接。')
        ready=True
        return save(state='handoff_ready',transaction=str(transaction.job),version=plan['version'],
                    result_file=str(transaction.job/'helper-result.json'))
    except Exception as exc:
        save(state='cancelled' if isinstance(exc,InterruptedError) else 'failed',error=str(exc));raise
    finally:
        try:
            if not ready and handoff is not None:
                while not handoff.cancel():time.sleep(.1)
        finally:signal.signal(signal.SIGINT,previous)
