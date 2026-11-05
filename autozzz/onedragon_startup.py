"""启动客户端、核实窗口，并尝试让本次启动的客户端取得焦点。"""
from __future__ import annotations

from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from pathlib import Path
from threading import get_native_id
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
    def _window_owner(api,hwnd):
        if not hwnd:
            return None
        pid=wintypes.DWORD()
        thread=api.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        return (thread,pid.value) if thread and pid.value else None

    def _focus_new_client(self,window,identity):
        process=self._launched_process
        pid,created=identity
        poll_process=getattr(process,'poll',None)
        if self._focus_checked or getattr(process,'pid',None)!=pid or not callable(poll_process):
            return

        def interrupted():
            return self.cancelled.is_set() or self.paused.is_set()

        def verified_process():
            self.poll_keys()
            if interrupted():
                return False
            self.launch_session.guard()
            rows=self.inventory()
            if len(rows)!=1:
                return False
            row=rows[0]
            return row['pid']==pid and row['created']==created and self._matches_path(row)

        def focus():
            if not verified_process():
                return
            api=self._window_api
            if api is None:
                from .windows import user32
                api=user32
            hwnd=window.hwnd
            foreground=api.GetForegroundWindow()
            foreground_owner=self._window_owner(api,foreground)
            if foreground==hwnd or (foreground_owner is not None and foreground_owner[1]==pid):
                self._focus_checked=True
                return
            target_owner=self._window_owner(api,hwnd)
            if target_owner is None or target_owner[1]!=pid or poll_process() is not None:
                return
            if interrupted():
                return
            self._focus_checked=True
            if api.GetForegroundWindow()!=foreground or interrupted():
                return
            if api.SetForegroundWindow(hwnd):
                return
            if foreground_owner is None or api.GetForegroundWindow()!=foreground or interrupted():
                return
            if not verified_process():
                return

            def can_continue():
                return (self._window_owner(api,foreground)==foreground_owner
                        and self._window_owner(api,hwnd)==target_owner
                        and poll_process() is None and api.GetForegroundWindow()==foreground
                        and not interrupted())

            # Join only the input queues involved in this attempt. If the user
            # changes windows, stop before making another foreground request.
            current=get_native_id()
            with ExitStack() as attached:
                for thread in dict.fromkeys((foreground_owner[0],target_owner[0])):
                    if thread==current:
                        continue
                    if not can_continue() or not api.AttachThreadInput(current,thread,True):
                        return
                    attached.callback(api.AttachThreadInput,current,thread,False)
                if can_continue():
                    api.BringWindowToTop(hwnd)
                    if can_continue():
                        api.SetForegroundWindow(hwnd)
            # If Windows still declines, the normal login gate waits for focus.

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
