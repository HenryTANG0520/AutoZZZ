"""轮换父进程和工作进程共同保留总任务锁；不靠超时或文件推断退出。"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import json
import os
import re
import threading
import uuid

from .run_lock import RunLock,RUN_MUTEX_NAME

LEASE_ENV='AUTOZZZ_INTERNAL_RUN_LEASE'


def _created(kernel,handle):
    kernel.GetProcessTimes.argtypes=[W.HANDLE,C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME)]
    values=[W.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle,*(C.byref(value) for value in values)):
        raise RuntimeError('无法核实轮换进程创建时间。')
    return values[0].dwHighDateTime<<32|values[0].dwLowDateTime


def _is_ancestor(kernel,pid):
    # Windows venv 的 python.exe 可能只是转发器，真实工作进程多一层父进程。
    class Entry(C.Structure):
        _fields_=[('size',W.DWORD),('usage',W.DWORD),('pid',W.DWORD),('heap',C.c_size_t),('module',W.DWORD),
                  ('threads',W.DWORD),('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),('exe',W.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD];kernel.CreateToolhelp32Snapshot.restype=W.HANDLE
    kernel.Process32FirstW.argtypes=[W.HANDLE,C.POINTER(Entry)];kernel.Process32FirstW.restype=W.BOOL
    kernel.Process32NextW.argtypes=[W.HANDLE,C.POINTER(Entry)];kernel.Process32NextW.restype=W.BOOL
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if not snapshot or snapshot==C.c_void_p(-1).value:raise RuntimeError('无法核实工作进程的父进程链。')
    parents={};entry=Entry();entry.size=C.sizeof(entry)
    try:
        valid=kernel.Process32FirstW(snapshot,C.byref(entry))
        while valid:
            parents[entry.pid]=entry.parent
            valid=kernel.Process32NextW(snapshot,C.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    current=os.getpid();seen={current}
    for _ in range(12):
        current=parents.get(current)
        if current is None or current in seen:return False
        if current==pid:return True
        seen.add(current)
    return False


class RunLeaseOwner:
    def __init__(self):
        self.task=RunLock()
        self.capability=RunLock('Local\\AutoZZZ.Rotation.'+uuid.uuid4().hex)
        self.lock=threading.RLock()
        self.active=False
        self.used=False

    def acquire(self):
        with self.lock:
            if self.used:raise RuntimeError('每次轮换须建立新的任务锁。')
            self.used=True
            self.task.acquire()
            try:self.capability.acquire()
            except BaseException:self.task.close();raise
            self.active=True
        return self

    def export(self):
        with self.lock:
            if not self.active:raise RuntimeError('轮换任务锁已释放，不能建立后续工作进程。')
            self.task.kernel.GetCurrentProcess.restype=W.HANDLE
            created=_created(self.task.kernel,self.task.kernel.GetCurrentProcess())
            return json.dumps(dict(version=1,parent_pid=os.getpid(),parent_created=created,capability=self.capability.name),separators=(',',':'))

    def close(self):
        with self.lock:
            self.active=False
            # 能打开能力对象的进程必须同时持有总锁；关闭顺序不能反过来。
            self.capability.close()
            self.task.close()

    def __enter__(self):return self.acquire()
    def __exit__(self,*_):self.close()


class JoinedRunLease:
    def __init__(self,descriptor):
        self.parent=self.task=self.capability=None
        self.kernel=C.WinDLL('kernel32',use_last_error=True)
        self.kernel.OpenMutexW.argtypes=[W.DWORD,W.BOOL,W.LPCWSTR];self.kernel.OpenMutexW.restype=W.HANDLE
        self.kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];self.kernel.OpenProcess.restype=W.HANDLE
        self.kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];self.kernel.WaitForSingleObject.restype=W.DWORD
        self.kernel.CloseHandle.argtypes=[W.HANDLE];self.kernel.CloseHandle.restype=W.BOOL
        try:
            value=json.loads(descriptor)
            if not isinstance(value,dict) or set(value)!= {'version','parent_pid','parent_created','capability'} or type(value['version'])is not int or value['version']!=1:
                raise ValueError()
            if type(value['parent_pid'])is not int or value['parent_pid']<=0 or type(value['parent_created'])is not int:raise ValueError()
            if not isinstance(value['capability'],str) or re.fullmatch(r'Local\\AutoZZZ\.Rotation\.[0-9a-f]{32}',value['capability']) is None:raise ValueError()
        except (ValueError,TypeError,KeyError):raise RuntimeError('轮换任务锁交接信息无效。') from None
        try:
            self.parent=self.kernel.OpenProcess(0x101000,False,value['parent_pid'])
            if not self.parent:raise RuntimeError('无法核实轮换父进程。')
            if _created(self.kernel,self.parent)!=value['parent_created'] or not _is_ancestor(self.kernel,value['parent_pid']):
                raise RuntimeError('轮换父进程身份不符。')
            # 总锁先打开；旧能力对象仍存在时，其持有者必然也保留同一总锁。
            self.task=self.kernel.OpenMutexW(0x100000,False,RUN_MUTEX_NAME)
            if not self.task:raise RuntimeError('原轮换总任务锁已不存在。')
            self.capability=self.kernel.OpenMutexW(0x100000,False,value['capability'])
            if not self.capability:raise RuntimeError('原轮换任务锁交接已结束。')
            self.execution_name=value['capability']+'.Input'
            self.check()
        except BaseException:self.close();raise

    @classmethod
    def from_environment(cls):
        descriptor=os.environ.pop(LEASE_ENV,None)
        return None if descriptor is None else cls(descriptor)

    def check(self):
        if not self.parent or not self.task or not self.capability:
            raise RuntimeError('轮换任务锁连接已关闭。')
        result=self.kernel.WaitForSingleObject(self.parent,0)
        if result==0:raise RuntimeError('轮换父进程已退出，停止当前任务。')
        if result!=258:raise RuntimeError('无法核实轮换父进程状态，拒绝后续操作。')

    @contextmanager
    def scope(self):
        self.check()
        with RunLock(self.execution_name):
            self.check()
            yield self

    def close(self):
        for name in ('capability','task','parent'):
            handle=getattr(self,name)
            if handle:
                if not self.kernel.CloseHandle(handle):raise RuntimeError('无法释放轮换进程句柄。')
                setattr(self,name,None)
