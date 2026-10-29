"""固定版本一条龙的独立运行环境和本机进程通信。"""
from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any, Callable

UPSTREAM_REVISION = "f80b68456306d2afb97be72436dcda27f2ed6b59"
PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 2 * 1024 * 1024
EXECUTABLE_APPLICATIONS = frozenset({"daily_signin","scratch_card","hou_hou_bakery","random_play","engagement_reward","ridu_weekly","city_fund",
    "email","coffee","trigrams_collection","predefined_team_checker","mouse_sensitivity_checker","auto_battle","dodge_assistant","screenshot_helper","charge_plan","one_dragon",
    "intel_board","lost_void","withered_domain","shiyu_defense","world_patrol",
    "redemption_code","commission_assistant","operation_debug","life_on_line","suibian_temple","drive_disc_dismantle","notorious_hunt","notify"})


class RuntimeUnavailable(RuntimeError):
    pass


class RemoteError(RuntimeError):
    pass


class RequestPending(TimeoutError):
    """等待超时；原请求仍保留，必须用编号继续查询。"""
    def __init__(self, request_id: str):
        self.request_id = request_id
        super().__init__(f"一条龙请求仍在等待：{request_id}；请查询原请求，勿重复执行。")


@dataclass(frozen=True)
class RuntimePaths:
    source: Path
    python: Path
    data: Path

    @classmethod
    def discover(cls, root: Path, profile: str = "cn") -> RuntimePaths:
        if profile not in ("cn", "global"):
            raise ValueError("区服必须为 cn 或 global。")
        source = Path(root).resolve() / "vendor" / "onedragon"
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
        return cls(source, source / ".venv" / "Scripts" / "python.exe",
                   base / "AutoZZZ" / "OneDragon" / profile)

    def verify(self) -> None:
        if not (self.source / "src" / "zzz_od" / "context" / "zzz_context.py").is_file():
            raise RuntimeUnavailable("缺少一条龙源码，请运行 tools/setup_onedragon.ps1。")
        if not self.python.is_file():
            raise RuntimeUnavailable("缺少一条龙 Python 3.11 环境，请运行 tools/setup_onedragon.ps1。")
        try:
            result = subprocess.run(["git", "-C", str(self.source), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, timeout=10, check=True,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeUnavailable("无法核实一条龙源码版本。") from exc
        if result.stdout.strip() != UPSTREAM_REVISION:
            raise RuntimeUnavailable("一条龙源码版本与 AutoZZZ 固定版本不一致，请恢复子模块版本。")
        for extra in ([], ["--cached"]):
            clean = subprocess.run(["git", "-C", str(self.source), "diff", "--quiet", *extra,
                                    "--", "src", "config", "assets"], timeout=10,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if clean.returncode:
                raise RuntimeUnavailable("一条龙源码或内置资源存在本地修改；自定义策略请保存在 AutoZZZ 数据目录。")
        source, data = self.source.resolve(), self.data.resolve()
        if source == data or source in data.parents or data in source.parents:
            raise RuntimeUnavailable("一条龙数据目录必须独立于源码目录。")

    def prepare(self) -> None:
        """只复制版本控制中的资源；已有用户文件不覆盖。"""
        self.verify()
        self.data.mkdir(parents=True, exist_ok=True)
        marker = self.data / "autozzz-runtime.json"
        if marker.exists():
            saved = json.loads(marker.read_text(encoding="utf-8"))
            if saved.get("revision") != UPSTREAM_REVISION:
                raise RuntimeUnavailable("数据目录来自其他一条龙版本，需要先迁移配置与资源。")
            return
        result = subprocess.run(["git", "-C", str(self.source), "ls-files", "-z", "config", "assets"],
                                capture_output=True, timeout=15, check=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for name in result.stdout.decode("utf-8").split("\0"):
            if not name:
                continue
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeUnavailable("上游资源路径无效。")
            origin, target = self.source / relative, self.data / relative
            if not origin.is_file() or target.exists():
                continue
            if not target.resolve().is_relative_to(self.data.resolve()):
                raise RuntimeUnavailable("数据目录中的资源路径指向目录外。")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                shutil.copyfile(origin, temporary)
                try:
                    os.link(temporary, target)  # 已有文件不会被另一并发初始化覆盖。
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        temporary = marker.with_name(marker.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps({"revision": UPSTREAM_REVISION, "protocol": PROTOCOL_VERSION}),
                                 encoding="utf-8")
            temporary.replace(marker)
        finally:
            temporary.unlink(missing_ok=True)


@dataclass
class _Reply:
    ready: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: str | None = None


class OneDragonClient:
    """保留原请求的客户端；超时不重启、不重发。"""
    def __init__(self, paths: RuntimePaths, log: Callable[[str], None] | None = None,run_lease=None):
        self.paths, self.log = paths, log
        self.run_lease=run_lease
        self.process: subprocess.Popen | None = None
        self._replies: dict[str, _Reply] = {}
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._closed = False
        self._terminal_error: str | None = None
        self._reader: threading.Thread | None = None
        self._logger: threading.Thread | None = None
        self.worker_pid: int | None = None
        self.worker_exit_code: int | None = None
        self._worker_handle = None
        self._kernel = None

    @property
    def alive(self) -> bool:
        if self._worker_handle is not None:
            state = self._kernel.WaitForSingleObject(self._worker_handle, 0)
            if state not in (0, 258):
                raise RuntimeUnavailable("暂时无法核实实际工作进程状态；请保留原进程，不要重复启动。")
            return state == 258
        return self.process is not None and self.process.poll() is None

    def start(self, timeout: float = 30) -> dict:
        with self._lock:
            if self.process is not None:
                raise RuntimeError("该一条龙客户端已经启动过；请查询现有进程状态。")
            self.paths.prepare()
            environment = os.environ.copy()
            from .task_lease import LEASE_ENV
            environment.pop(LEASE_ENV,None)
            if self.run_lease is not None:environment[LEASE_ENV]=self.run_lease.export()
            environment["PYTHONUTF8"] = "1"
            # 独立解释器只通过明确的模块路径加载桥接代码。
            package_root = Path(__file__).resolve().parent.parent
            environment["PYTHONPATH"] = str(package_root)
            self.process = subprocess.Popen(
                [str(self.paths.python), "-u", "-m", "autozzz.onedragon_worker",
                 "--source", str(self.paths.source.resolve()), "--data", str(self.paths.data.resolve())],
                cwd=package_root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._reader = threading.Thread(target=self._read, name="AutoZZZ-OneDragon-Responses", daemon=True)
            self._logger = threading.Thread(target=self._logs, name="AutoZZZ-OneDragon-Logs", daemon=True)
            self._reader.start()
            self._logger.start()
        hello = self.call("hello", timeout=timeout)
        if hello.get("protocol") != PROTOCOL_VERSION or hello.get("revision") != UPSTREAM_REVISION:
            self.close()
            raise RuntimeUnavailable("一条龙通信协议或源码版本不一致。")
        if self.run_lease is not None and hello.get("task_lease") is not True:
            self.close()
            raise RuntimeUnavailable("工作进程未确认轮换任务锁，不能开始执行。")
        self.worker_pid = hello.get("pid")
        if os.name == "nt":
            if type(self.worker_pid) is not int or self.worker_pid <= 0:
                self.close()
                raise RuntimeUnavailable("一条龙未提供可监测的实际工作进程。")
            self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            self._kernel.OpenProcess.restype = ctypes.c_void_p
            self._kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            self._kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            self._kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            self._worker_handle = self._kernel.OpenProcess(0x101000, False, self.worker_pid)
            if not self._worker_handle:
                self.close()
                raise RuntimeUnavailable("无法监测一条龙实际工作进程。")
        return hello

    def submit(self, method: str, **params: Any) -> str:
        request_id = uuid.uuid4().hex
        payload = (json.dumps({"id": request_id, "method": method, "params": params},
                              ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ValueError("一条龙请求过大。")
        with self._write_lock:
            with self._lock:
                if self._closed or self._terminal_error or not self.alive:
                    raise RuntimeUnavailable(self._terminal_error or "一条龙进程未运行。")
                self._replies[request_id] = _Reply()
            try:
                self.process.stdin.write(payload)
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                self._fail_all(f"一条龙通信中断：{exc}")
                raise RuntimeUnavailable("无法发送请求；不能假定原任务已终止。") from exc
        return request_id

    def result(self, request_id: str, timeout: float = 30) -> Any:
        with self._lock:
            if request_id not in self._replies:
                raise KeyError(f"未知一条龙请求：{request_id}")
            reply = self._replies[request_id]
        if not reply.ready.wait(timeout):
            raise RequestPending(request_id)
        if reply.error:
            raise RemoteError(reply.error)
        return reply.value

    def call(self, method: str, timeout: float = 30, **params: Any) -> Any:
        return self.result(self.submit(method, **params), timeout)

    def forget(self, request_id: str) -> None:
        with self._lock:
            reply = self._replies[request_id]
            if not reply.ready.is_set():
                raise RuntimeError("不能丢弃仍在等待的请求。")
            del self._replies[request_id]

    def _fail_all(self, error: str) -> None:
        with self._lock:
            self._terminal_error = error
            for reply in self._replies.values():
                if not reply.ready.is_set():
                    reply.error = error
                    reply.ready.set()

    def _read(self) -> None:
        try:
            while True:
                line = self.process.stdout.readline(MAX_MESSAGE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                    raise ValueError("响应过大或未完整结束。")
                response = json.loads(line)
                if not isinstance(response, dict) or set(response) not in ({"id", "result"}, {"id", "error"}):
                    raise ValueError("响应结构无效。")
                with self._lock:
                    reply = self._replies.get(response.get("id"))
                    if reply is None or reply.ready.is_set():
                        raise ValueError("响应编号无效或重复。")
                    reply.value = response.get("result")
                    if "error" in response:
                        reply.error = str(response["error"])
                    reply.ready.set()
        except (OSError, ValueError, TypeError) as exc:
            self._fail_all(f"一条龙响应无效：{exc}")
        finally:
            self._fail_all("一条龙通信已结束；请检查进程状态和日志。")

    def _logs(self) -> None:
        for line in iter(self.process.stderr.readline, b""):
            if self.log:
                try:
                    self.log(line.decode("utf-8", errors="replace").rstrip())
                except Exception:
                    pass  # 日志接收器异常不能阻塞进程排空 stderr。

    def close(self, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        with self._write_lock:
            if self.process is None:
                return
            if not self._closed:
                self._closed = True
                try:
                    self.process.stdin.close()
                except (OSError, ValueError):
                    pass
        try:
            self.process.wait(timeout)
        except subprocess.TimeoutExpired as exc:
            raise RequestPending("process-shutdown") from exc
        if self._worker_handle is not None:
            waited = self._kernel.WaitForSingleObject(self._worker_handle, max(0, round((deadline-time.monotonic())*1000)))
            if waited == 258:
                raise RequestPending("worker-shutdown")
            if waited != 0:
                raise RuntimeUnavailable("无法核实一条龙实际工作进程退出状态。")
            exit_code = ctypes.c_ulong()
            known_exit = self._kernel.GetExitCodeProcess(self._worker_handle, ctypes.byref(exit_code))
            self._kernel.CloseHandle(self._worker_handle)
            self._worker_handle = None
            if not known_exit:
                raise RuntimeUnavailable("无法读取一条龙实际工作进程的退出码。")
            self.worker_exit_code = exit_code.value
        for thread in (self._reader, self._logger):
            if thread:
                thread.join(timeout=1)
        for stream in (self.process.stdout, self.process.stderr):
            stream.close()
        if self.worker_exit_code or self.process.returncode:
            raise RemoteError(f"一条龙进程异常退出，退出码 {self.worker_exit_code or self.process.returncode}。")

    def __enter__(self) -> OneDragonClient:
        self.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
