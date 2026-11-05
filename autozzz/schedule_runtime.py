"""Windows 计划实例入口；复用原每日和原生轮换，合作停止并保留同一执行记录。"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import signal
import threading
import time
import uuid

from .config import ROOT,Settings
from .schedule_authorization import daily_review,validate_authorization
from .schedule_manager import ScheduleManager
from .schedules import ScheduleStore


def desktop_available() -> bool:
    user=C.WinDLL('user32',use_last_error=True)
    user.OpenInputDesktop.argtypes=[W.DWORD,W.BOOL,W.DWORD];user.OpenInputDesktop.restype=W.HANDLE
    user.GetUserObjectInformationW.argtypes=[W.HANDLE,C.c_int,W.LPVOID,W.DWORD,C.POINTER(W.DWORD)];user.GetUserObjectInformationW.restype=W.BOOL
    user.CloseDesktop.argtypes=[W.HANDLE]
    handle=user.OpenInputDesktop(0,False,1)
    if not handle:return False
    try:
        name=C.create_unicode_buffer(256);needed=W.DWORD()
        if not user.GetUserObjectInformationW(handle,2,name,C.sizeof(name),C.byref(needed)) or name.value!='Default':
            return False
    finally:user.CloseDesktop(handle)

    # OpenInputDesktop can still return Default for a disconnected session.
    wts=C.WinDLL('wtsapi32',use_last_error=True)
    state_pointer=C.POINTER(C.c_int)
    query=wts.WTSQuerySessionInformationW
    query.argtypes=[W.HANDLE,W.DWORD,C.c_int,C.POINTER(state_pointer),C.POINTER(W.DWORD)]
    query.restype=W.BOOL
    wts.WTSFreeMemory.argtypes=[C.c_void_p]
    wts.WTSFreeMemory.restype=None
    state,size=state_pointer(),W.DWORD()
    current_session,connect_state,active=0xffffffff,8,0
    if not query(None,current_session,connect_state,C.byref(state),C.byref(size)):
        return False
    try:
        return bool(state and size.value==C.sizeof(C.c_int) and state.contents.value==active)
    finally:
        if state:wts.WTSFreeMemory(state)


def process_identity() -> dict:
    from .task_lease import _created
    kernel=C.WinDLL('kernel32',use_last_error=True);kernel.GetCurrentProcess.restype=W.HANDLE
    return dict(pid=os.getpid(),created=_created(kernel,kernel.GetCurrentProcess()))


def write_record(path: Path,value: dict) -> None:
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def execute(plan,review,stop,emit,report_root):
    if plan.mode=='daily':
        from .app import Runner
        runner=Runner(Settings(**review['settings']),emit)
        finished=threading.Event()
        def watch():
            while not finished.wait(.1):
                if stop.is_set():runner.stop();return
        watcher=threading.Thread(target=watch,name='AutoZZZ-Scheduled-Daily-Stop');watcher.start()
        try:
            if stop.is_set():runner.stop()
            result=runner.run().to_dict()
            return dict(state=result['status'],report=result)
        finally:finished.set();watcher.join()
    from .onedragon_rotation_session import RotationSession
    from .onedragon_runtime import RuntimePaths
    from .onedragon_cli import combat_budget
    events=queue.Queue();session=RotationSession(RuntimePaths.discover(ROOT,plan.server),events.put,report_root=report_root,stop_event=stop)
    started=False
    try:
        while True:
            try:event=events.get(timeout=.1)
            except queue.Empty:continue
            emit(event)
            if event['type']=='connected':
                if stop.is_set():return dict(state='stopped',report=None)
                selection=dict(instance_idx=plan.instance,app_id=plan.app_id) if plan.mode=='native' else {}
                session.send('prepare',server=plan.server,expected_intent=review['intent'],**selection)
            elif event['type']=='state' and not event['state']['busy']:
                state=event['state']
                if state['state']=='prepared' and not started:
                    if stop.is_set():return dict(state='stopped',report=None)
                    if bool(state.get('uses_charge'))!=review['uses_charge']:raise ValueError('定时计划的电量需求已变化。')
                    actual=[action for row in session.plan['instances'] for action in row['resources']]
                    expected=[action for row in review['instances'] for action in row['resources']]
                    if actual!=expected:raise ValueError('定时计划的资源清单已变化。')
                    budget=combat_budget(plan.allowance,plan.battles,plan.maximum)
                    started=True;session.send('start',budget=budget,timeout_seconds=plan.timeout,
                        resource_confirmation=state['resources']['confirmation_id'] if state['resources']['confirmation_required'] else None)
                elif state['state']in ('success','failed','stopped','interrupted'):
                    return dict(state=state['state'],report=None if session.report_dir is None else str(session.report_dir/'report.json'),error=state.get('error',''))
            elif event['type']=='closed':raise RuntimeError('原定时执行会话在返回结果前退出。')
    finally:session.close();session.worker.join()


def run_scheduled(key: str,store_path: Path,expected_revision: str,*,root: Path=ROOT,
                  manager_factory=ScheduleManager,execute_task=execute,check_desktop=desktop_available) -> dict:
    store=ScheduleStore(root,store_path);manager=manager_factory(store);plan=store.get(key)
    if not plan.enabled or plan.fingerprint!=expected_revision:raise ValueError('计划已停用或版本变化，未执行。')
    review=validate_authorization(plan)
    entry=next((row for row in manager.backend.call('list')['tasks'] if row['id']==key),None)
    if entry is None or not manager.matches(plan,entry):raise ValueError('系统计划定义已变化，未执行。')
    instances=manager.backend.call('instances',key=key)['instances']
    if len(instances)!=1:raise ValueError('无法唯一核实当前 Windows 计划实例，未执行。')
    occurrence=uuid.UUID(instances[0]['instance']).hex
    folder=store.path.parent/'runs'/plan.id/occurrence
    if folder!=folder.resolve():raise ValueError('定时运行记录目录被重定向。')
    folder.parent.mkdir(parents=True,exist_ok=True)
    try:folder.mkdir()
    except FileExistsError:
        if not (folder/'record.json').is_file():raise ValueError('本实例已有未核实记录，未重复运行。')
        return dict(state='duplicate',record=str(folder/'record.json'))
    record=dict(format=1,owner=store.owner,schedule_id=plan.id,name=plan.name,instance=occurrence,revision=expected_revision,
                process=process_identity(),state='starting',started_at=datetime.now().astimezone().isoformat(),phase='',result=None,error='')
    path=folder/'record.json';stop=threading.Event();finished=threading.Event();previous={}
    write_record(path,record)

    def check_stop():
        if (folder/'stop').exists():stop.set()
        return stop.is_set()

    def watch():
        while not finished.wait(.1):
            if check_stop():return
    watcher=threading.Thread(target=watch,name='AutoZZZ-Scheduled-Stop');watcher.start()
    try:
        for signum in (signal.SIGINT,getattr(signal,'SIGBREAK',None)):
            if signum is not None:previous[signum]=signal.signal(signum,lambda *_:stop.set())
        if not check_desktop():record.update(state='skipped',error='当前交互桌面不可用，未启动游戏。');return dict(state='skipped',record=str(path))
        if plan.mode=='daily' and daily_review(plan,Path(review['config_path']))!=review:raise ValueError('每日配置已变化，请重新核对计划。')
        log_path=folder/'run.log'
        legacy_paused = legacy_focus_waiting = False
        phase_lock = threading.Lock()
        def emit(event):
            nonlocal legacy_paused, legacy_focus_waiting
            kind = event.get('type')
            if kind == 'log':
                with log_path.open('a',encoding='utf-8') as stream:stream.write(str(event.get('message',''))+'\n')
                return
            with phase_lock:
                phase = None
                if kind == 'phase':
                    phase = str(event.get('phase') or '')
                elif kind in ('focus_wait', 'paused'):
                    if kind == 'focus_wait':
                        legacy_focus_waiting = bool(event.get('waiting'))
                    else:
                        legacy_paused = bool(event.get('paused'))
                    phase = '已暂停' if legacy_paused else '等待游戏恢复焦点' if legacy_focus_waiting else ''
                elif kind == 'state' and event['state'].get('busy'):
                    state = event['state']
                    control = state.get('control', {})
                    if control.get('manual_paused'):
                        phase = '已暂停'
                    elif control.get('focus_waiting'):
                        phase = '等待游戏恢复焦点'
                    else:
                        phase = str(state.get('preparation_step') or '')
                if phase is not None and phase != record['phase']:
                    record['phase'] = phase
                    write_record(path,record)
        record['state']='running';write_record(path,record)
        if check_stop():
            record.update(state='stopped',result=dict(state='stopped',report=None))
            return dict(state='stopped',record=str(path))
        result=execute_task(plan,review,stop,emit,folder/'reports')
        state=result.get('state')
        if state not in ('success','partial','failed','stopped','interrupted'):raise ValueError('原执行器返回无效状态。')
        record.update(state=state,result=result)
        return dict(state=state,record=str(path))
    except Exception as exc:
        record.update(state='failed',error=str(exc));raise
    finally:
        finished.set();watcher.join()
        for signum,handler in previous.items():signal.signal(signum,handler)
        record['finished_at']=datetime.now().astimezone().isoformat();write_record(path,record)
