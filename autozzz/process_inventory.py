"""Read Win32 executable identities, including processes without windows."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from pathlib import Path

from .models import AutomationError
from .windows import kernel32, user32


class _ProcessEntry(C.Structure):
    _fields_=[('size',W.DWORD),('usage',W.DWORD),('pid',W.DWORD),('heap',C.c_size_t),('module',W.DWORD),
              ('threads',W.DWORD),('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),('exe',W.WCHAR*260)]


kernel32.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD]
kernel32.CreateToolhelp32Snapshot.restype=W.HANDLE
for _next in (kernel32.Process32FirstW,kernel32.Process32NextW):
    _next.argtypes=[W.HANDLE,C.POINTER(_ProcessEntry)]
    _next.restype=W.BOOL
kernel32.GetProcessTimes.argtypes=[W.HANDLE,*([C.POINTER(W.FILETIME)]*4)]
kernel32.GetProcessTimes.restype=W.BOOL


def _identity(pid,exe_name):
    handle=kernel32.OpenProcess(0x101000,False,pid)  # synchronize and limited query
    if not handle:
        if C.get_last_error()==87:return None  # no longer exists
        raise AutomationError('无法核实游戏进程身份，请检查助手权限。')
    try:
        state=kernel32.WaitForSingleObject(handle,0)
        if state==0:return None
        if state!=258:raise AutomationError('无法核实游戏进程状态。')
        size=W.DWORD(32768);buf=C.create_unicode_buffer(size.value)
        times=[W.FILETIME() for _ in range(4)]
        if (not kernel32.QueryFullProcessImageNameW(handle,0,buf,C.byref(size))
            or not kernel32.GetProcessTimes(handle,*(C.byref(value) for value in times))):
            if kernel32.WaitForSingleObject(handle,0)==0:return None
            raise AutomationError('无法核实游戏进程的路径或创建时间。')
        path=Path(buf.value)
        if path.name.casefold()!=exe_name:return None  # snapshot PID was replaced
        state=kernel32.WaitForSingleObject(handle,0)
        if state==0:return None
        if state!=258:raise AutomationError('无法核实游戏进程状态。')
        created=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
        return dict(pid=pid,path=str(path.resolve()),created=created)
    finally:kernel32.CloseHandle(handle)


def window_process_identity(hwnd):
    """Read the current ZZZ owner of one HWND without scanning other processes."""
    pid = W.DWORD()
    thread = user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
    if not thread or not pid.value:
        return None
    identity = _identity(pid.value, 'zenlesszonezero.exe')
    observed = W.DWORD()
    if user32.GetWindowThreadProcessId(hwnd, C.byref(observed)) != thread or observed.value != pid.value:
        return None
    return identity


def process_inventory(executable_name,*,only_pid=None):
    name=executable_name.casefold()
    snapshot=kernel32.CreateToolhelp32Snapshot(2,0)
    if not snapshot or snapshot==C.c_void_p(-1).value:raise AutomationError('无法读取 Windows 进程列表。')
    result=[]
    try:
        item=_ProcessEntry();item.size=C.sizeof(item)
        next_entry=kernel32.Process32FirstW
        while True:
            C.set_last_error(0)
            if not next_entry(snapshot,C.byref(item)):
                if C.get_last_error()!=18:raise AutomationError('Windows 进程列表读取中断。')
                return result
            next_entry=kernel32.Process32NextW
            if item.exe.casefold()==name and (only_pid is None or item.pid==only_pid):
                row=_identity(item.pid,name)
                if row is not None:result.append(row)
    finally:kernel32.CloseHandle(snapshot)
