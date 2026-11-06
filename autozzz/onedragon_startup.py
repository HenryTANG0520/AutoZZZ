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
        self.launch=launch
        self.clock=clock or time.monotonic
        self.attempt=attempt or self._attempt
        self.launched=False
        self.previous_process=None
        self._launched_process=None
        self._focus_checked=False
        self._window_api=window_api
        self.focus_observation=None

    def _attempt(self,action):
        if self.cancelled.is_set() or self.paused.is_set():return False
        action();return True

    def _matches_path(self,process):
        return str(Path(process['path']).resolve()).casefold()==str(self.path).casefold()

    def _check_focus_authority(self):
        if self.cancelled.is_set() or self.paused.is_set():
            return False
        self.poll_keys()
        self._account_elapsed()
        if self.cancelled.is_set() or self.paused.is_set():
            return False
        self.launch_session.guard()
        self._account_elapsed()
        return not (self.cancelled.is_set() or self.paused.is_set())

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
        if self._focus_checked:
            return
        launched_pid=getattr(process,'pid',None)
        self.focus_observation=dict(window_handle=window.hwnd,process_id=pid,process_created=created,
                                    launched_pid=launched_pid,phase='checking')

        def observe(**fields):
            self.focus_observation = self.focus_observation | fields

        if launched_pid!=pid or not callable(poll_process):
            observe(phase='launch_identity_unverified' if self.launched else 'existing_client')
            return

        def interrupted():
            self._account_elapsed()
            return self.cancelled.is_set() or self.paused.is_set()

        def verified_process():
            if interrupted():
                return False
            self.launch_session.guard()
            rows=self.inventory()
            if interrupted() or len(rows)!=1:
                return False
            row=rows[0]
            return row['pid']==pid and row['created']==created and self._matches_path(row)

        def focus():
            if not verified_process():
                observe(phase='preflight_incomplete')
                return
            api=self._window_api
            if api is None:
                from .windows import user32
                api=user32
            hwnd=window.hwnd
            foreground=api.GetForegroundWindow()
            foreground_owner=self._window_owner(api,foreground)
            observe(foreground_before=foreground or None,
                    foreground_pid=None if foreground_owner is None else foreground_owner[1])
            query_timeout=getattr(api,'SystemParametersInfoW',None)
            if query_timeout is not None:
                timeout=wintypes.DWORD()
                if query_timeout(0x2000,0,ctypes.byref(timeout),0):
                    observe(lock_timeout_ms=timeout.value)
            if foreground==hwnd or (foreground_owner is not None and foreground_owner[1]==pid):
                self._focus_checked=True
                observe(phase='target_foreground' if foreground==hwnd else 'other_client_window_foreground')
                return
            target_owner=self._window_owner(api,hwnd)
            if target_owner is None or target_owner[1]!=pid or poll_process() is not None:
                observe(phase='target_identity_unverified')
                return
            if interrupted():
                observe(phase='interrupted')
                return

            def can_continue():
                # A native focus call can outlive the preceding stop or
                # generation check. Recheck before the next side effect.
                return (api.GetForegroundWindow()==foreground
                        and self._window_owner(api,foreground)==foreground_owner
                        and self._window_owner(api,hwnd)==target_owner
                        and self._check_focus_authority()
                        and poll_process() is None and api.GetForegroundWindow()==foreground
                        and not interrupted())

            self._focus_checked=True
            if not can_continue():
                observe(phase='foreground_changed_or_interrupted')
                return
            observe(phase='primary_pending')
            accepted=bool(api.SetForegroundWindow(hwnd))
            observe(phase='primary_returned',primary_accepted=accepted)
            if accepted:
                return
            if foreground_owner is None or api.GetForegroundWindow()!=foreground or interrupted():
                return
            if not verified_process():
                return

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
                    observe(phase='raise_pending')
                    raised=bool(api.BringWindowToTop(hwnd))
                    observe(phase='raise_returned',raise_accepted=raised)
                    if can_continue():
                        observe(phase='fallback_pending')
                        accepted=bool(api.SetForegroundWindow(hwnd))
                        observe(phase='fallback_returned',fallback_accepted=accepted)
            # If Windows still declines, the normal login gate waits for focus.

        # User32 may wait on another input queue. Keep this call on the
        # original worker without holding the lock used by stop and key release.
        focus()

    def run(self,auto_launch):
        self._previous_tick=self.clock()
        try:
            result=self._run(auto_launch)
        finally:
            self.launch_session.close()
        self._account_elapsed()
        return None if self.cancelled.is_set() else result

    def _account_elapsed(self):
        now=self.clock()
        if not self.cancelled.is_set() and not self.paused.is_set():
            self.elapsed(now-self._previous_tick)
        self._previous_tick=now

    def _before_spawn(self):
        self._account_elapsed()
        if self.cancelled.is_set() or self.paused.is_set():
            return False
        self.launched=True  # An unverified result must not retry a submitted launch.
        return True

    def _run(self,auto_launch):
        if self.path.name.casefold()!='zenlesszonezero.exe' or not self.path.is_file():
            raise ValueError('请选择有效的 ZenlessZoneZero.exe。')
        while not self.cancelled.is_set():
            self.poll_keys()
            self._account_elapsed()
            if self.cancelled.is_set():return None
            if self.paused.is_set():
                self.phase('已暂停');self.cancelled.wait(.05);self._previous_tick=self.clock();continue
            processes=self.inventory()
            self._account_elapsed()
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
                    if self.launch is None:
                        self._launched_process=self.launch_session.spawn(before_spawn=self._before_spawn)
                    elif self._before_spawn():
                        self._launched_process=self.launch(str(self.path))
                if not self.attempt(launch):continue
                self._account_elapsed()
                if self.cancelled.is_set():return None
                if not self.launched:continue
                self.phase('已启动客户端，等待游戏窗口')
            try:window=self.find_window(str(self.path))
            except AutomationError:window=None
            self._account_elapsed()
            if self.cancelled.is_set():return None
            if self.paused.is_set():continue
            if window is not None and identity is not None:
                if auto_launch:self._focus_new_client(window,identity)
                if self.launched and not self._check_focus_authority():continue
                self._account_elapsed()
                if self.cancelled.is_set():return None
                if self.paused.is_set():continue
                return dict(window_handle=window.hwnd,process_id=identity[0])
            self.phase('等待游戏窗口')
            self.cancelled.wait(.05)
        return None
