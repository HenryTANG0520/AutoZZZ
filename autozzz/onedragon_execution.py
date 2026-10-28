"""一条龙初始化和运行状态；后台任务结束前始终保留运行句柄和互斥量。"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import threading
from typing import Any
import uuid

from .onedragon_control import ExecutionControl, UpstreamControlAdapter, WindowState
from .run_lock import RunLock


class OneDragonExecution:
    def __init__(self, ctx: Any):
        from zzz_od.backend.backend_context import ZzzBackendContext
        self.ctx = ctx
        self.backend = ZzzBackendContext(ctx)
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self.state, self.error, self.job_id = "idle", "", ""
        self.cancelled = threading.Event()
        self.control = ExecutionControl(self._probe, self._release, self._pause_backend,
                                        self._resume_backend, self._stop_backend)
        self.ctx.execution_control = self.control
        self.adapter = UpstreamControlAdapter(self.control)
        self.adapter.install()
        self.closed = False

    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _probe(self) -> WindowState:
        if self.ctx.controller is None:
            return WindowState(False, False, reason="控制器尚未初始化。")
        return self.ctx.controller.probe()

    def _release(self) -> None:
        if self.ctx.controller is not None:
            self.ctx.controller.keyboard_controller.reset()

    def _pause_backend(self) -> bool:
        if self.ctx.run_context.is_context_running:
            self.ctx.run_context.switch_context_pause_and_run()
        return self.ctx.run_context.is_context_pause

    def _resume_backend(self) -> None:
        if self.ctx.run_context.is_context_pause:
            self.ctx.run_context.switch_context_pause_and_run()

    def _stop_backend(self) -> None:
        try:
            self.ctx.run_context.stop_running()
        except Exception:
            # 上游先 PAUSE 再 STOP；暂停回调异常可能让它停在 PAUSE。
            # 第二次仅完成 STOP 收口，不重启、不重复游戏操作。
            if not self.ctx.run_context.is_context_stop:
                self.ctx.run_context.stop_running()
            raise

    def _submit(self, state: str, operation) -> dict:
        with self.lock:
            if self.closed or self.busy:
                raise RuntimeError("已有一条龙任务仍在处理，请查询原任务状态。")
            self.cancelled.clear()
            self.ctx.download_cancel.clear()
            self.state, self.error, self.job_id = state, "", uuid.uuid4().hex
            def work():
                try:
                    operation()
                except Exception as exc:
                    with self.lock:
                        self.error = f"{type(exc).__name__}: {exc}"
                        self.state = "stopped" if self.cancelled.is_set() else "failed"
            self.worker = threading.Thread(target=work, name="AutoZZZ-OneDragon-"+state, daemon=False)
            self.worker.start()
            return self.status()

    def initialize(self, server: str, game_path: str, region: str | None = None) -> dict:
        if server not in ("cn", "global"):
            raise ValueError("区服必须为 cn 或 global。")
        from .config import Settings
        Settings(server=server, game_path=game_path).validate("check")
        game = Path(game_path)
        if game.name.casefold() != "zenlesszonezero.exe" or not game.is_file():
            raise ValueError("请选择有效的 ZenlessZoneZero.exe。")
        region = region or ("cn" if server == "cn" else "asia")
        if region not in (("cn",) if server == "cn" else ("us", "eu", "asia", "twhkmo")):
            raise ValueError("客户端与游戏服务器不匹配。")
        def initialize():
            self.ctx.ready_for_application = False
            config = self.ctx.game_account_config
            config.data.update(game_path=str(game.resolve()), game_region=region, game_language="cn")
            config.save()
            self.ctx.init()
            if self.cancelled.is_set():
                self.ctx.ready_for_application = False
                self.state = "stopped"
                return
            if not self.ctx.ready_for_application or self.ctx.ocr._model is None or self.ctx.controller is None:
                self.ctx.ready_for_application = False
                raise RuntimeError("一条龙初始化未完成，请查看资源下载与模型加载日志。")
            if not self.ctx.run_context._application_factory_map or self.ctx.factory_manager.scan_failures:
                self.ctx.ready_for_application = False
                raise RuntimeError("一条龙应用注册不完整，请检查应用加载日志。")
            self.state = "ready"
        return self._submit("initializing", initialize)

    def start_application(self, app_id: str, *, fixed_entry: bool = False) -> dict:
        """供后续配置/预算入口调用；传输层尚不直接开放此方法。"""
        if not self.ctx.ready_for_application:
            raise RuntimeError("请先初始化一条龙运行环境。")
        if not self.ctx.run_context.is_app_registered(app_id):
            raise ValueError("应用未注册。")
        if self.ctx.run_context.is_app_internal(app_id) and not fixed_entry:
            raise ValueError("该应用必须通过其固定入口启动。")
        def run():
            with RunLock():
                self.ctx.controller.bind(self.ctx.game_account_config.game_path)
                self.control.reset()
                # 监听会持续到真正运行结束，F10 和失焦无需等待标准输入轮询。
                watch_stop = threading.Event()
                watcher = threading.Thread(target=self._watch, args=(watch_stop,), name="AutoZZZ-OneDragon-Hotkeys", daemon=True)
                watcher.start()
                try:
                    while (not self.cancelled.is_set() and not self.control.stopped
                           and self.control.status()["state"] != "running"):
                        self.control.poll()
                        watch_stop.wait(.02)
                    if self.cancelled.is_set() or self.control.stopped:
                        self.state = "stopped"
                        return
                    ok, future = self.backend._start_app("autozzz", app_id, "one_dragon")
                    if not ok or future is None:
                        raise RuntimeError("一条龙拒绝并发运行。")
                    future.result()
                    status = self.backend.query_status()
                    self.state = "stopped" if self.cancelled.is_set() or self.control.stopped else status.state
                    if self.state == "failed":
                        self.error = status.last_status or "一条龙应用执行失败。"
                    if self.control.error:
                        self.error = self.control.error
                        self.state = "failed"
                finally:
                    watch_stop.set()
                    watcher.join(timeout=2)
                    self.control.stop()
        return self._submit("running", run)

    def _watch(self, watch_stop: threading.Event) -> None:
        from .windows import user32
        f9 = False
        while not watch_stop.is_set():
            try:
                if user32.GetAsyncKeyState(0x79) & 0x8000:
                    self.cancelled.set()
                    self.control.stop()
                pressed = bool(user32.GetAsyncKeyState(0x78) & 0x8000)
                if pressed and not f9:
                    self.control.toggle_pause()
                f9 = pressed
                self.control.poll()
            except Exception as exc:
                self.error = f"执行控制异常：{exc}"
                self.cancelled.set()
                self.control.stop(self.error)
                return
            watch_stop.wait(.01)

    def status(self) -> dict:
        with self.lock:
            result = dict(job_id=self.job_id, state=self.state, busy=self.busy,
                          ready=bool(self.ctx.ready_for_application), error=self.error)
            if self.state in ("running", "stopping"):
                result["control"] = self.control.status()
                result["run"] = asdict(self.backend.query_status())
            return result

    def pause(self, paused: bool) -> dict:
        if type(paused) is not bool:
            raise ValueError("暂停状态必须为布尔值。")
        if self.state != "running" or not self.busy:
            raise RuntimeError("当前没有正在运行的游戏任务。")
        self.control.set_manual_pause(paused)
        return self.status()

    def stop(self) -> dict:
        if self.busy:
            self.cancelled.set()
            self.ctx.download_cancel.set()
            self.state = "stopping"
            if self.ctx.controller is not None:
                self.control.stop()
        return self.status()

    def close(self) -> None:
        self.closed = True
        self.stop()
        if self.worker is not None:
            self.worker.join()  # 初始化下载或运行仍在退出时，保留同一线程与进程。
        self.adapter.close()
