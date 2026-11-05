"""Revocable read-only access to a known legacy task mutex; never grants input."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
import re
import threading
import uuid

from .run_lock import RunLock,RUN_MUTEX_NAME
from .task_lease import _created,_is_ancestor


def kernel_api():
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=W.HANDLE
    kernel.DuplicateHandle.argtypes=[W.HANDLE,W.HANDLE,W.HANDLE,C.POINTER(W.HANDLE),W.DWORD,W.BOOL,W.DWORD]
    kernel.DuplicateHandle.restype=W.BOOL
    kernel.CreateEventW.argtypes=[C.c_void_p,W.BOOL,W.BOOL,W.LPCWSTR];kernel.CreateEventW.restype=W.HANDLE
    kernel.OpenEventW.argtypes=[W.DWORD,W.BOOL,W.LPCWSTR];kernel.OpenEventW.restype=W.HANDLE
    kernel.OpenMutexW.argtypes=[W.DWORD,W.BOOL,W.LPCWSTR];kernel.OpenMutexW.restype=W.HANDLE
    kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];kernel.OpenProcess.restype=W.HANDLE
    kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];kernel.WaitForSingleObject.restype=W.DWORD
    kernel.SetEvent.argtypes=[W.HANDLE];kernel.SetEvent.restype=W.BOOL
    kernel.CloseHandle.argtypes=[W.HANDLE];kernel.CloseHandle.restype=W.BOOL
    return kernel


class CaptureLeaseOwner:
    def __init__(self,known_task_handle,epoch):
        self.kernel=kernel_api();self.task=self.event=None;self.lock=threading.RLock();self.closed=False
        self.capability=RunLock('Local\\AutoZZZ.Capture.'+uuid.uuid4().hex);self.epoch=epoch
        if not known_task_handle or type(epoch)is not int or epoch<0:raise ValueError('旧任务截图租约来源无效。')
        try:
            duplicate=W.HANDLE();process=self.kernel.GetCurrentProcess()
            if not self.kernel.DuplicateHandle(process,known_task_handle,process,C.byref(duplicate),0,False,2):
                raise RuntimeError('无法保留旧任务总锁。')
            self.task=duplicate.value;self.capability.acquire()
            C.set_last_error(0)
            self.event=self.kernel.CreateEventW(None,True,False,self.capability.name+'.Cancel')
            if not self.event or C.get_last_error()==183:raise RuntimeError('无法创建唯一的只读截图撤销信号。')
        except BaseException:self.close();raise

    def export(self):
        with self.lock:
            if self.closed:raise RuntimeError('旧任务截图能力已撤销。')
            return json.dumps(dict(version=1,parent_pid=os.getpid(),parent_created=_created(self.kernel,self.kernel.GetCurrentProcess()),
                capability=self.capability.name,epoch=self.epoch),separators=(',',':'))

    def close(self):
        with self.lock:
            self.closed=True
            if self.event:
                if not self.kernel.SetEvent(self.event):raise RuntimeError('无法撤销只读截图能力。')
                self.kernel.CloseHandle(self.event);self.event=None
            self.capability.close()
            if self.task:self.kernel.CloseHandle(self.task);self.task=None


class JoinedCaptureLease:
    def __init__(self,descriptor):
        self.kernel=kernel_api();self.parent=self.task=self.capability=self.event=None
        try:
            value=json.loads(descriptor)
            if (type(value)is not dict or set(value)!={'version','parent_pid','parent_created','capability','epoch'}
                    or type(value['version'])is not int or value['version']!=1 or type(value['parent_pid'])is not int
                    or value['parent_pid']<=0 or type(value['parent_created'])is not int or type(value['epoch'])is not int
                    or value['epoch']<0 or type(value['capability'])is not str
                    or re.fullmatch(r'Local\\AutoZZZ\.Capture\.[0-9a-f]{32}',value['capability']) is None):
                raise ValueError()
        except (ValueError,TypeError,KeyError):raise ValueError('只读截图能力格式无效。') from None
        try:
            self.parent=self.kernel.OpenProcess(0x101000,False,value['parent_pid'])
            if (not self.parent or _created(self.kernel,self.parent)!=value['parent_created']
                    or not _is_ancestor(self.kernel,value['parent_pid'])):raise RuntimeError('只读截图父进程身份不符。')
            self.task=self.kernel.OpenMutexW(0x100000,False,RUN_MUTEX_NAME)
            self.capability=self.kernel.OpenMutexW(0x100000,False,value['capability'])
            self.event=self.kernel.OpenEventW(0x100000,False,value['capability']+'.Cancel')
            self.check()
        except BaseException:self.close();raise

    def check(self):
        if not all((self.parent,self.task,self.capability,self.event)):raise RuntimeError('只读截图租约已关闭。')
        if self.kernel.WaitForSingleObject(self.parent,0)!=258:raise RuntimeError('原任务父进程已退出或状态未知。')
        if self.kernel.WaitForSingleObject(self.event,0)!=258:raise RuntimeError('原任务观察代次已变化或截图已取消。')
        return True

    def close(self):
        for name in ('event','capability','task','parent'):
            handle=getattr(self,name)
            if handle:self.kernel.CloseHandle(handle);setattr(self,name,None)

    def __enter__(self):self.check();return self
    def __exit__(self,*_):self.close()
