"""启动客户端、核实窗口，并尝试让本次启动的客户端取得焦点。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import time

from .models import AutomationError


def game_processes():
    """Both the frozen launcher and the standalone SDK use the same inventory."""
    from .process_inventory import process_inventory
    return process_inventory('ZenlessZoneZero.exe')


def launch_client(path):
    executable=Path(path)
    from .game_launch import LaunchSession
    session=LaunchSession(executable)
    try:session.spawn()
    except OSError as exc:
        if getattr(exc,'winerror',None)==740:
            raise AutomationError('游戏需要管理员权限，请以管理员身份启动 AutoZZZ 后重试。') from None
        raise AutomationError('启动游戏失败，请检查游戏路径和助手权限。') from None
    finally:session.close()


class GameStartup:
    def __init__(self,path,*,cancelled,paused,poll_keys,elapsed,phase,inventory=None,find_window=None,launch=None,clock=None,attempt=None,options=None,guard=None,hdr_lease=None,window_api=None):
        self.path=Path(path).resolve()
        self.cancelled,self.paused,self.poll_keys=cancelled,paused,poll_keys
        self.elapsed,self.phase=elapsed,phase
        if find_window is None:
            from .windows import find_game
            find_window=find_game
        self.inventory=inventory or game_processes
        self.find_window=find_window
        from .game_launch import LaunchSession
        self.launch_session=LaunchSession(self.path,options,guard=guard,hdr_lease=hdr_lease)
        self.launch=launch or (lambda path:self.launch_session.spawn())
        self.clock=clock or time.monotonic
        self.attempt=attempt or self._attempt
        self.launched=False
        self.previous_process=None
        self._launched_process=None
        self._focus_checked=False
        self._window_api=window_api

    def _attempt(self,action):
        if self.cancelled.is_set() or self.paused.is_set():return False
        action();return True

    def _matches_path(self,process):
        return str(Path(process['path']).resolve()).casefold()==str(self.path).casefold()

    @staticmethod
    def _window_pid(api,hwnd):
        pid=wintypes.DWORD()
        if hwnd and api.GetWindowThreadProcessId(hwnd,ctypes.byref(pid)):
            return pid.value
        return None

    def _focus_new_client(self,window,identity):
        process=self._launched_process
        pid,created=identity
        poll_process=getattr(process,'poll',None)
        if self._focus_checked or getattr(process,'pid',None)!=pid or not callable(poll_process):
            return

        def focus():
            self.poll_keys()
            if self.cancelled.is_set() or self.paused.is_set():
                return
            self.launch_session.guard()
            rows=self.inventory()
            if len(rows)!=1:
                return
            row=rows[0]
            if row['pid']!=pid or row['created']!=created or not self._matches_path(row):
                return
            api=self._window_api
            if api is None:
                from .windows import user32
                api=user32
            hwnd=window.hwnd
            foreground=api.GetForegroundWindow()
            if foreground==hwnd or self._window_pid(api,foreground)==pid:
                self._focus_checked=True
                return
            if self._window_pid(api,hwnd)!=pid or poll_process() is not None:
                return
            if self.cancelled.is_set() or self.paused.is_set():
                return
            self._focus_checked=True
            # Windows may decline. The normal login gate waits for foreground.
            api.SetForegroundWindow(hwnd)

        self.attempt(focus)

    def run(self,auto_launch):
        try:return self._run(auto_launch)
        finally:self.launch_session.close()

    def _run(self,auto_launch):
        if self.path.name.casefold()!='zenlesszonezero.exe' or not self.path.is_file():
            raise ValueError('请选择有效的 ZenlessZoneZero.exe。')
        previous=self.clock()
        while not self.cancelled.is_set():
            self.poll_keys()
            if self.cancelled.is_set():return None
            now=self.clock()
            if not self.paused.is_set():self.elapsed(now-previous)
            previous=now
            if self.cancelled.is_set():return None
            if self.paused.is_set():
                self.phase('已暂停');self.cancelled.wait(.05);previous=self.clock();continue
            processes=self.inventory()
            if self.cancelled.is_set():return None
            if any(not self._matches_path(row) for row in processes):
                raise AutomationError('另一绝区零客户端仍在运行，请先退出，避免 multi game detected。')
            if len(processes)>1:raise AutomationError('检测到多个绝区零进程，请保留要登录的一个客户端。')
            identity=None if not processes else (processes[0]['pid'],processes[0]['created'])
            if self.previous_process is not None and identity!=self.previous_process:
                raise AutomationError('等待期间游戏进程已退出或变化，请重新准备登录。')
            if identity is not None:self.previous_process=identity
            if not processes and not self.launched:
                if not auto_launch:raise AutomationError('游戏尚未启动；请打开游戏或勾选“未运行时启动游戏”。')
                if self.paused.is_set():continue
                def launch():
                    self.launched=True  # 启动结果未知也不自动再次启动。
                    self._launched_process=self.launch(str(self.path))
                if not self.attempt(launch):continue
                self.phase('已启动客户端，等待游戏窗口')
            try:window=self.find_window(str(self.path))
            except AutomationError:window=None
            if self.cancelled.is_set():return None
            if self.paused.is_set():continue
            if window is not None and identity is not None:
                if auto_launch:self._focus_new_client(window,identity)
                if self.cancelled.is_set():return None
                if self.paused.is_set():continue
                return dict(window_handle=window.hwnd,process_id=identity[0])
            self.phase('等待游戏窗口')
            self.cancelled.wait(.05)
        return None
