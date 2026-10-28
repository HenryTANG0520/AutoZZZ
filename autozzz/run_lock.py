"""旧每日与一条龙共用的 Windows 任务互斥量。"""
from __future__ import annotations

import ctypes
import hashlib
from pathlib import Path

RUN_MUTEX_NAME = "Local\\AutoZZZ.DailyRunner"


def profile_lock(data: Path) -> RunLock:
    identity = hashlib.sha256(str(Path(data).resolve()).casefold().encode("utf-8")).hexdigest()[:32]
    return RunLock("Local\\AutoZZZ.OneDragon.Profile." + identity)


class RunLock:
    def __init__(self, name: str = RUN_MUTEX_NAME):
        self.name = name
        self.handle = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        self.kernel.CreateMutexW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]

    def acquire(self) -> None:
        if self.handle is not None:
            raise RuntimeError("本运行已经持有任务锁。")
        ctypes.set_last_error(0)
        handle = self.kernel.CreateMutexW(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise RuntimeError("无法创建 AutoZZZ 任务锁。")
        if error == 183:
            self.kernel.CloseHandle(handle)
            raise RuntimeError("另一个 AutoZZZ 任务正在运行，请先停止后重试。")
        self.handle = handle

    def close(self) -> None:
        if self.handle is not None:
            if not self.kernel.CloseHandle(self.handle):
                raise RuntimeError("无法释放 AutoZZZ 任务锁。")
            self.handle = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_args):
        self.close()
