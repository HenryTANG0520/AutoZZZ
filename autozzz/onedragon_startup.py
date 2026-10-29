"""登录前仅启动客户端并等待窗口；不点击、不激活窗口、不重复启动。"""
from __future__ import annotations

from pathlib import Path
import subprocess
import time

from .models import AutomationError


def game_processes():
    """使用上游已有 psutil 依赖识别尚未创建窗口的游戏进程。"""
    import psutil
    result=[]
    for process in psutil.process_iter(['name']):
        try:
            if (process.info.get('name') or '').casefold()!='zenlesszonezero.exe':continue
            result.append(dict(pid=process.pid,path=str(Path(process.exe()).resolve()),created=process.create_time()))
        except psutil.NoSuchProcess:continue
        except psutil.AccessDenied:raise AutomationError('无法核实游戏进程身份，请检查助手权限。') from None
    return result


def launch_client(path):
    executable=Path(path)
    try:subprocess.Popen([str(executable)],cwd=str(executable.parent))
    except OSError as exc:
        if getattr(exc,'winerror',None)==740:
            raise AutomationError('游戏需要管理员权限，请以管理员身份启动 AutoZZZ 后重试。') from None
        raise AutomationError('启动游戏失败，请检查游戏路径和助手权限。') from None


class GameStartup:
    def __init__(self,path,*,cancelled,paused,poll_keys,elapsed,phase,inventory=None,find_window=None,launch=None,clock=None,attempt=None):
        self.path=Path(path).resolve()
        self.cancelled,self.paused,self.poll_keys=cancelled,paused,poll_keys
        self.elapsed,self.phase=elapsed,phase
        if find_window is None:
            from .windows import find_game
            find_window=find_game
        self.inventory=inventory or game_processes
        self.find_window=find_window
        self.launch=launch or launch_client
        self.clock=clock or time.monotonic
        self.attempt=attempt or self._attempt
        self.launched=False
        self.previous_process=None

    def _attempt(self,action):
        if self.cancelled.is_set() or self.paused.is_set():return False
        action();return True

    def run(self,auto_launch):
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
            if any(str(Path(row['path']).resolve()).casefold()!=str(self.path).casefold() for row in processes):
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
                    self.launch(str(self.path))
                if not self.attempt(launch):continue
                self.phase('已启动客户端，等待游戏窗口')
            try:window=self.find_window(str(self.path))
            except AutomationError:window=None
            if self.cancelled.is_set():return None
            if self.paused.is_set():continue
            if window is not None and identity is not None:
                return dict(window_handle=window.hwnd,process_id=identity[0])
            self.phase('等待游戏窗口')
            self.cancelled.wait(.05)
        return None
