"""一条龙初始化和运行状态；后台任务结束前始终保留运行句柄和互斥量。"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from .onedragon_control import ExecutionControl, UpstreamControlAdapter, WindowState
from .run_lock import RunLock, profile_lock


class OneDragonExecution:
    def __init__(self, ctx: Any):
        from zzz_od.backend.backend_context import ZzzBackendContext
        self.ctx = ctx
        from one_dragon.utils import os_utils
        self.data = Path(os_utils.get_work_dir())
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
        from .onedragon_battle import BattleInputControl
        self.battle_control = BattleInputControl(ctx,self.control)
        self.battle_control.install()
        self.prepared_app = self.prepared_hash = ""
        self.uses_charge = False
        self.charge_control = None
        self.active_seconds,self.time_limit,self.time_limited = 0.,600,False
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
                    with profile_lock(self.data):
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
            self.prepared_app=self.prepared_hash=""
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

    def configuration_digest(self) -> str:
        digest=hashlib.sha256()
        for path in sorted((self.data/"config").rglob("*.yml")):
            digest.update(str(path.relative_to(self.data)).encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def prepare_application(self,app_id:str) -> dict:
        if not self.ctx.ready_for_application:raise RuntimeError("请先初始化运行环境。")
        from .onedragon_runtime import EXECUTABLE_APPLICATIONS
        if app_id not in EXECUTABLE_APPLICATIONS:raise ValueError("该应用的执行适配尚未完成。")
        def prepare():
            self.prepared_app=self.prepared_hash=""
            self.backend._refresh_runtime_config()
            from .onedragon_config import ConfigurationStore
            from types import SimpleNamespace
            store=ConfigurationStore(SimpleNamespace(ctx=self.ctx,data=self.data))
            charge=store.get("charge_plan")
            self.uses_charge=app_id=="charge_plan" or (app_id=="coffee" and store.get("coffee").run_charge_plan_afterwards)
            policies=[]
            if self.uses_charge:
                from .onedragon_charge import ChargeBudgetControl
                if charge.restore_charge!="不使用":raise ValueError("本轮仅使用现有电量，请将补充电量方式设为“不使用”。")
                plans=charge.plan_list+([charge.combat_simulation_double_reward_config] if charge.double_reward else [])
                if not plans:raise ValueError("请先添加刷本计划。")
                for plan in plans:
                    if plan.category_name not in ChargeBudgetControl.CATEGORIES:raise ValueError("该训练模式的预算适配尚未完成。")
                    from zzz_od.application.charge_plan.charge_plan_config import ChargePlanConfig
                    self.ctx.compendium_service.reload()
                    error=ChargePlanConfig.validate_item(self.ctx,plan)
                    if error:raise ValueError(error)
                    policy=plan.auto_battle_config if plan.predefined_team_idx==-1 else self.ctx.team_config.team_list[plan.predefined_team_idx].auto_battle
                    policies.append(("auto_battle",policy))
            if app_id=="auto_battle":policies.append(("auto_battle",self.ctx.battle_assistant_config.auto_battle_config))
            if app_id=="dodge_assistant":policies.append(("dodge",self.ctx.battle_assistant_config.dodge_assistant_config))
            from one_dragon.base.conditional_operation.loader import ConditionalOperatorLoader
            for directory,name in sorted(set(policies)):
                path=Path(ConditionalOperatorLoader.get_yaml_file_path([directory],name,self.ctx.battle_assistant_config.use_merged_file))
                if not path.is_file():raise ValueError("战斗策略不存在："+name)
                self.ctx.auto_battle_context.init_auto_op(name,directory)
                if self.cancelled.is_set():
                    self.state="stopped"
                    return
            self.prepared_hash=self.configuration_digest()
            self.prepared_app=app_id
            self.state="prepared"
        return self._submit("preparing",prepare)

    def start_application(self, app_id: str, *, fixed_entry: bool = False, budget:dict|None=None,timeout_seconds:int=600) -> dict:
        """通过已准备的配置启动实际上游应用，保留同一进程与任务锁。"""
        if not self.ctx.ready_for_application:
            raise RuntimeError("请先初始化一条龙运行环境。")
        if not self.ctx.run_context.is_app_registered(app_id):
            raise ValueError("应用未注册。")
        if self.ctx.run_context.is_app_internal(app_id) and not fixed_entry:
            raise ValueError("该应用必须通过其固定入口启动。")
        if app_id!=self.prepared_app:raise ValueError("请先准备所选应用。")
        if type(timeout_seconds)is not int or not 30<=timeout_seconds<=7200:raise ValueError("运行时限须为 30—7200 秒。")
        if self.uses_charge:
            if not isinstance(budget,dict) or set(budget)!={"id","allowance","battle_limit","max_per_battle"}:
                raise ValueError("刷本须指定预算编号、电量上限、场次和每场上限。")
            for name,maximum in (("allowance",300),("battle_limit",15),("max_per_battle",100)):
                if type(budget[name])is not int or not (20 if name=="max_per_battle" else 1)<=budget[name]<=maximum:raise ValueError("战斗预算超出允许范围。")
        elif budget is not None:raise ValueError("当前应用不需要战斗预算。")
        def run():
            with RunLock():
                if self.prepared_hash!=self.configuration_digest():raise ValueError("配置已变化，请重新准备应用。")
                self.charge_control=None
                if self.uses_charge:
                    from .battle_budget import BattleBudget,budget_directory
                    from .onedragon_charge import ChargeBudgetControl
                    server="global" if self.ctx.game_account_config.game_region!="cn" else "cn"
                    ledger=BattleBudget(budget_directory(),budget["id"],server,budget["allowance"],budget["battle_limit"])
                    self.charge_control=ChargeBudgetControl(self.ctx,self.control,ledger,budget["max_per_battle"])
                    self.charge_control.install()
                try:
                    self.active_seconds,self.time_limit,self.time_limited=0.,timeout_seconds,False
                    self._run_bound_app(app_id)
                finally:
                    if self.charge_control is not None:self.charge_control.close()
        return self._submit("running", run)

    def _run_bound_app(self,app_id:str) -> None:
        self.ctx.controller.bind(self.ctx.game_account_config.game_path)
        self.control.reset()
        self.battle_control.allowed=False
        watch_stop = threading.Event()
        watcher = threading.Thread(target=self._watch,args=(watch_stop,),name="AutoZZZ-OneDragon-Hotkeys",daemon=True)
        watcher.start()
        try:
            while (not self.cancelled.is_set() and not self.control.stopped
                   and self.control.status()["state"]!="running"):
                self.control.poll()
                watch_stop.wait(.02)
            if self.cancelled.is_set() or self.control.stopped:
                self.state="stopped"
                return
            ok,future=self.backend._start_app("autozzz",app_id,"one_dragon")
            if not ok or future is None:raise RuntimeError("一条龙拒绝并发运行。")
            future.result()
            status=self.backend.query_status()
            self.state="stopped" if self.cancelled.is_set() or self.control.stopped else status.state
            if self.state=="failed":self.error=status.last_status or "一条龙应用执行失败。"
            if self.control.error:
                self.error=self.control.error
                self.state="failed"
        finally:
            watch_stop.set()
            watcher.join(timeout=2)
            self.control.stop()

    def _watch(self, watch_stop: threading.Event) -> None:
        from .windows import user32
        f9 = False
        previous=time.monotonic()
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
                now=time.monotonic()
                if self.control.status()["state"]=="running":self.active_seconds+=now-previous
                previous=now
                if self.active_seconds>=self.time_limit:
                    self.time_limited=True
                    self.control.stop()
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
            result.update(prepared_app=self.prepared_app,uses_charge=self.uses_charge)
            result.update(active_seconds=round(self.active_seconds,2),time_limited=self.time_limited)
            bound=getattr(getattr(self.ctx.controller,"game_win",None),"bound",None)
            result["window_handle"]=None if bound is None else bound.hwnd
            if self.charge_control is not None:result["budget"]=self.charge_control.status()
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
        self.battle_control.close()
