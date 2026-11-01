"""Read local scheduled instances and request cooperative stop; never terminate a process."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path

from .schedules import identifier

TERMINAL = {'success', 'partial', 'failed', 'stopped', 'interrupted', 'skipped'}


def record_path(store, schedule_id: str, instance: str) -> Path:
    identifier(schedule_id); identifier(instance)
    path = store.path.parent / 'runs' / schedule_id / instance / 'record.json'
    if path != path.resolve() or (path.exists() and path.stat().st_nlink != 1):
        raise ValueError('运行记录路径被重定向。')
    return path


def read_record(store, schedule_id: str, instance: str) -> dict:
    path = record_path(store, schedule_id, instance)
    if path.stat().st_size > 4 * 1024 ** 2:
        raise ValueError('运行记录过大。')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (type(value) is not dict or type(value.get('format')) is not int or value.get('format') != 1 or value.get('owner') != store.owner
        or value.get('schedule_id') != schedule_id or value.get('instance') != instance
        or type(value.get('name')) is not str or type(value.get('started_at')) is not str or type(value.get('state')) is not str
        or value.get('state') not in TERMINAL | {'starting', 'running'}):
        raise ValueError('运行记录身份不符。')
    return value


def history(store) -> list[dict]:
    root = store.path.parent / 'runs'
    if root != root.resolve(): raise ValueError('历史目录被重定向。')
    rows = []
    if not root.exists(): return rows
    for plan in root.iterdir():
        if not plan.is_dir(): continue
        try: identifier(plan.name)
        except ValueError: continue
        for instance in plan.iterdir():
            if not instance.is_dir(): continue
            try:
                value = read_record(store, plan.name, instance.name)
            except (ValueError, OSError):
                # Keep corrupt history visible without granting it stop/open capability.
                rows.append(dict(name=plan.name, started_at='', state='unreadable', error='记录无法核实', invalid=True))
            else: rows.append(value)
    return sorted(rows, key=lambda row: row.get('started_at', ''), reverse=True)


def process_alive(process: dict) -> bool:
    from .task_lease import _created
    if (type(process) is not dict or type(process.get('pid')) is not int or process['pid'] <= 0
        or type(process.get('created')) is not int): raise ValueError('运行进程身份无效。')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; kernel.OpenProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]; kernel.WaitForSingleObject.restype = W.DWORD
    handle = kernel.OpenProcess(0x101000, False, process['pid'])
    if not handle:
        if C.get_last_error() == 87: return False
        raise RuntimeError('无法核实原运行进程。')
    try:
        if _created(kernel, handle) != process['created']: return False
        state = kernel.WaitForSingleObject(handle, 0)
        if state not in (0, 258): raise RuntimeError('无法核实原运行进程状态。')
        return state == 258
    finally: kernel.CloseHandle(handle)


def request_stop(store, schedule_id: str, instance: str, *, alive=process_alive) -> None:
    value = read_record(store, schedule_id, instance)
    if value.get('state') in TERMINAL: raise ValueError('此运行已经结束。')
    if not alive(value.get('process')): raise ValueError('原运行进程已退出，结果尚未核实。')
    path = record_path(store, schedule_id, instance).parent / 'stop'
    if path != path.resolve() or (path.exists() and path.stat().st_nlink != 1):
        raise ValueError('停止请求路径被重定向。')
    # Exclusive creation avoids following an existing link. Repeated stop stays idempotent.
    try:
        with path.open('x', encoding='utf-8') as stream: stream.write('stop\n')
    except FileExistsError: pass
