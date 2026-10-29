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
    def __init__(self, ctx: Any,run_lease=None,rotation_guard=None):
        from zzz_od.backend.backend_context import ZzzBackendContext
        self.ctx = ctx
        from one_dragon.utils import os_utils
        self.data = Path(os_utils.get_work_dir())
        self.backend = ZzzBackendContext(ctx)
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self.state, self.error, self.job_id = "idle", "", ""
        self.cancelled = threading.Event()
        self.manual_pause=threading.Event()
        self._input_ready=False
        self._f9_down=False
        self.startup=None
        self.client_exit=None
        self.client_exit_result={}
        self.close_target=None
        self.handoff=None
        self.control = ExecutionControl(self._probe, self._release, self._pause_backend,
                                        self._resume_backend, self._stop_backend)
        self.run_lease=run_lease
        self.rotation_guard=rotation_guard
        self.control.input_validator=self._validate_task_input
        self.ctx.execution_control = self.control
        self.adapter = UpstreamControlAdapter(self.control)
        self.adapter.install()
        from .onedragon_battle import BattleInputControl
        self.battle_control = BattleInputControl(ctx,self.control)
        self.battle_control.install()
        from .onedragon_scripts import ScriptPlaybackControl
        from .onedragon_assistant import AssistantModeControl
        from .onedragon_redemption import RedemptionControl
        self.script_control=ScriptPlaybackControl(self.control)
        self.assistant_control=AssistantModeControl(ctx,self.control)
        self.redemption_control=RedemptionControl(self.control)
        from .onedragon_login import LoginControl
        self.login_control=LoginControl(ctx,self.control)
        for adapter in (self.script_control,self.assistant_control,self.redemption_control,self.login_control):adapter.install()
        from .onedragon_push import NotificationControl
        self.notification_control = NotificationControl(ctx)
        self.notification_control.install()
        from .onedragon_patrol import PatrolOrderControl
        self.patrol_control = PatrolOrderControl(ctx)
        self.patrol_control.install()
        from .onedragon_resources import ResourceConfirmation
        self.resource_confirmation = ResourceConfirmation()
        self.resource_control = None
        self.hunt_control = None
        self.prepared_app = self.prepared_hash = ""
        self.route_debug = None
        self.login_mode = ""
        self.prepared_applications=[]
        self.prepared_requirements={}
        self.preparation_step=""
        self.group_control=None
        self.connection=None
        self.uses_charge = False
        self.charge_control = None
        self.active_seconds,self.time_limit,self.time_limited = 0.,600,False
        self.closed = False

    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _run_scope(self):
        return RunLock() if self.run_lease is None else self.run_lease.scope()

    def _validate_task_input(self):
        if self.run_lease is not None:self.run_lease.check()
        if self.client_exit is not None:self.client_exit.input_guard()

    def _probe(self) -> WindowState:
        if self.client_exit is not None:return self.client_exit.probe()
        if self.ctx.controller is None:
            return WindowState(False, False, reason="控制器尚未初始化。")
        return self.ctx.controller.probe()

    def _release(self) -> None:
        if self.ctx.controller is not None:
            from .onedragon_gamepad import release_controller_inputs
            release_controller_inputs(self.ctx.controller)

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
            if state=="running":
                paused,down=self.handoff or (False,False);self.handoff=None
                if paused:self.manual_pause.set()
                else:self.manual_pause.clear()
                self._input_ready=False;self._f9_down=down;self.client_exit_result={}
            self.ctx.download_cancel.clear()
            self.state, self.error, self.job_id = state, "", uuid.uuid4().hex
            def work():
                try:
                    with profile_lock(self.data):
                        from .map_transaction import MapTransaction
                        MapTransaction(self.data).assert_clear()
                        from .model_install import ModelInstall
                        ModelInstall(self.data).assert_clear()
                        if self.rotation_guard is not None:self.rotation_guard.check()
                        operation()
                        if self.rotation_guard is not None:self.rotation_guard.check()
                        with self.lock:
                            if self.cancelled.is_set():
                                if self.state!="failed":self.state="stopped"
                                if state in ("initializing","preparing"):
                                    self.prepared_app=self.prepared_hash=""
                                    self.prepared_requirements={}
                except Exception as exc:
                    with self.lock:
                        self.error = f"{type(exc).__name__}: {exc}"
                        self.state = "stopped" if self.cancelled.is_set() else "failed"
            self.worker = threading.Thread(target=work, name="AutoZZZ-OneDragon-"+state, daemon=False)
            self.worker.start()
            return self.status()

    def initialize(self, server: str, game_path: str, region: str | None = None,instance_idx:int|None=None) -> dict:
        if server not in ("cn", "global"):
            raise ValueError("区服必须为 cn 或 global。")
        from .config import Settings
        Settings(server=server, game_path=game_path).validate("check")
        game = Path(game_path)
        if game.name.casefold() != "zenlesszonezero.exe" or not game.is_file():
            raise ValueError("请选择有效的 ZenlessZoneZero.exe。")
        region = region or ("cn" if server == "cn" else "asia")
        if region not in (("cn","cn_b") if server == "cn" else ("us", "eu", "asia", "twhkmo")):
            raise ValueError("客户端与游戏服务器不匹配。")
        if instance_idx is not None and type(instance_idx) is not int:raise ValueError("账号配置编号无效。")
        def initialize():
            self.route_debug = None
            self.prepared_app=self.prepared_hash=""
            self.prepared_requirements={}
            self.preparation_step=""
            self.prepared_applications=[]
            self.uses_charge=False
            self.charge_control=self.group_control=None
            self.script_control.reset()
            self.redemption_control.reset()
            self.login_control.reset()
            self.resource_confirmation.prepare([], "")
            self.resource_control = None
            self.hunt_control = None
            self.ctx.ready_for_application = False
            self.backend._refresh_runtime_config()
            if instance_idx is not None and self.ctx.current_instance_idx!=instance_idx:
                raise ValueError("当前账号配置已变化，请重新打开运行窗口。")
            config = self.ctx.game_account_config
            config.data.update(game_path=str(game.resolve()), game_region=region)
            config.data.setdefault("game_language","cn")
            config.save()
            self.connection=(self.ctx.current_instance_idx,config.game_path,config.game_region,config.game_language)
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
        paths = list((self.data/"config").rglob("*.yml"))
        maps = self.data/"assets/game_data/world_patrol"
        paths.extend(path for path in maps.rglob("*") if path.is_file() and path.suffix in (".png", ".yml"))
        for path in sorted(paths):
            digest.update(str(path.relative_to(self.data)).encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def prepare_route_debug(self, descriptor: dict, expected_digest: str) -> dict:
        if not self.ctx.ready_for_application: raise RuntimeError("请先初始化运行环境。")
        def prepare():
            self.ctx.push_service.reset_results()
            self.route_debug = None
            self.prepared_app = self.prepared_hash = ""
            self.prepared_requirements = {}; self.prepared_applications = []
            self.uses_charge = False
            self.charge_control = self.group_control = self.hunt_control = self.resource_control = None
            self.resource_confirmation.prepare([], "")
            self.backend._refresh_runtime_config()
            account = self.ctx.game_account_config
            if self.connection != (self.ctx.current_instance_idx, account.game_path, account.game_region, account.game_language):
                raise ValueError("游戏登录设置已变化，请重新准备运行环境。")
            if expected_digest != self.configuration_digest(): raise ValueError("路线或配置已变化，请重新读取后调试。")
            self.ctx.on_switch_instance()
            self.ctx.world_patrol_service.load_data()
            from .onedragon_requirements import ApplicationRequirements
            requirements = ApplicationRequirements(self.ctx, self.data, None)
            requirements.collect([])
            config = self.ctx.run_context.get_config("world_patrol", self.ctx.current_instance_idx, "one_dragon")
            requirements.policy(config.auto_battle)
            requirements.routes = [descriptor["key"]]
            requirements.load(self.cancelled.is_set, lambda label: setattr(self, "preparation_step", label))
            if expected_digest != self.configuration_digest(): raise ValueError("准备期间资料已变化，请重新准备。")
            self.prepared_requirements = dict(requirements.summary(), route_debug={key: descriptor[key] for key in ("key", "name", "start_index")})
            self.prepared_hash = expected_digest
            self.resource_confirmation.prepare([], expected_digest)
            self.route_debug = descriptor
            self.prepared_app, self.state = "world_patrol", "prepared"
        return self._submit("preparing", prepare)

    def prepare_application(self,app_id:str) -> dict:
        if not self.ctx.ready_for_application:raise RuntimeError("请先初始化运行环境。")
        from .onedragon_runtime import EXECUTABLE_APPLICATIONS
        if app_id not in EXECUTABLE_APPLICATIONS:raise ValueError("该应用的执行适配尚未完成。")
        def prepare():
            self.route_debug = None
            self.ctx.push_service.reset_results()
            self.prepared_app=self.prepared_hash=""
            self.prepared_requirements={}
            self.preparation_step=""
            self.prepared_applications=[]
            self.uses_charge=False
            self.charge_control=self.group_control=None
            self.resource_confirmation.prepare([], "")
            self.resource_control = None
            self.hunt_control = None
            self.backend._refresh_runtime_config()
            current=self.ctx.game_account_config
            if self.connection is not None and self.connection!=(self.ctx.current_instance_idx,current.game_path,current.game_region,current.game_language):
                self.ctx.ready_for_application=False
                raise ValueError("游戏登录设置已变化，请重新准备运行环境。")
            self.ctx.on_switch_instance()
            from .onedragon_config import ConfigurationStore
            from .onedragon_service import OneDragonService
            from types import SimpleNamespace
            store=ConfigurationStore(SimpleNamespace(ctx=self.ctx,data=self.data,_name=OneDragonService._name))
            applications=[app_id]
            if app_id=="one_dragon":
                applications=[item.app_id for item in store.get("_group").app_list if item.enabled]
                if not applications:raise ValueError("请先在执行顺序中启用应用。")
                unsupported=[self.ctx.run_context.get_application_name(key) for key in applications if key not in EXECUTABLE_APPLICATIONS]
                if unsupported:raise ValueError("以下应用的执行适配尚未完成："+"、".join(unsupported))
            self.prepared_applications=applications
            from .onedragon_requirements import ApplicationRequirements
            requirements=ApplicationRequirements(self.ctx,self.data,store)
            requirements.collect(applications)
            self.uses_charge=requirements.uses_charge
            digest=self.configuration_digest()
            requirements.load(self.cancelled.is_set,lambda label:setattr(self,"preparation_step",label))
            if digest!=self.configuration_digest():raise ValueError("准备期间配置发生变化，请重新准备。")
            self.prepared_requirements=requirements.summary()
            self.prepared_hash=digest
            self.resource_confirmation.prepare(requirements.resource_actions, digest)
            self.prepared_app=app_id
            self.state="prepared"
        return self._submit("preparing",prepare)

    def prepare_login(self,mode:str) -> dict:
        if mode not in ("enter","switch","forced_enter","account"):raise ValueError("登录方式无效。")
        if not self.ctx.ready_for_application:raise RuntimeError("请先初始化运行环境。")
        def prepare():
            self.ctx.push_service.reset_results()
            self.prepared_app=self.prepared_hash=self.login_mode=""
            self.login_control.reset(mode)
            self.prepared_applications=[];self.prepared_requirements={};self.route_debug=None
            self.uses_charge=False;self.charge_control=self.group_control=self.hunt_control=self.resource_control=None
            self.resource_confirmation.prepare([],"")
            self.backend._refresh_runtime_config()
            config=self.ctx.game_account_config
            if self.connection!=(self.ctx.current_instance_idx,config.game_path,config.game_region,config.game_language):
                raise ValueError("账号或登录设置已变化，请重新准备运行环境。")
            if mode!="enter":
                if not config.has_login_info:raise ValueError("自动换号需要完整登录资料。")
                if config.game_region!="cn_b" and min(len(config.account.strip()),len(config.password.strip()))<=3:
                    raise ValueError("登录账号与密码均须超过 3 个字符。")
            self.ctx.push_service.protect_transient(config.account,config.password,config.bilibili_account_name)
            self.ctx.on_switch_instance()
            digest=self.configuration_digest()
            self.login_control.reset(mode)
            self.prepared_requirements=dict(login=dict(mode=mode),models=["onnx_ocr"],policies=[],resource_actions=[])
            self.resource_confirmation.prepare([],digest)
            self.login_mode=mode;self.prepared_hash=digest;self.prepared_app="_login";self.state="prepared"
        return self._submit("preparing",prepare)

    def start_login(self,mode:str,timeout_seconds:int=600,auto_launch:bool=False) -> dict:
        if not self.ctx.ready_for_application or self.prepared_app!="_login" or mode!=self.login_mode:
            raise ValueError("请先准备所选登录方式。")
        if type(timeout_seconds)is not int or not 30<=timeout_seconds<=7200:raise ValueError("运行时限须为 30—7200 秒。")
        if type(auto_launch)is not bool:raise ValueError("启动游戏选项必须为布尔值。")
        def run():
            try:
                with self._run_scope():
                    if self.prepared_hash!=self.configuration_digest():raise ValueError("登录配置已变化，请重新准备。")
                    from zzz_od.operation.enter_game.enter_game import EnterGame
                    from zzz_od.operation.enter_game.switch_account import SwitchAccount
                    self.login_control.reset(mode)
                    self.active_seconds,self.time_limit,self.time_limited=0.,timeout_seconds,False
                    identity=None
                    if auto_launch:
                        from .onedragon_startup import GameStartup
                        def elapsed(seconds):
                            self.active_seconds+=seconds
                            if self.active_seconds>=self.time_limit:
                                self.time_limited=True;self.cancelled.set()
                        def attempt(action):
                            with self.lock:
                                if self.cancelled.is_set() or self.manual_pause.is_set():return False
                                if self.run_lease is not None:self.run_lease.check()
                                action();return True
                        self.startup=GameStartup(self.ctx.game_account_config.game_path,cancelled=self.cancelled,
                                                paused=self.manual_pause,poll_keys=self._poll_task_keys,elapsed=elapsed,
                                                phase=lambda text:setattr(self,"preparation_step",text),attempt=attempt)
                        try:identity=self.startup.run(auto_launch=True)
                        finally:self.startup=None;self.preparation_step=""
                        if identity is None:self.state="stopped";return
                    def factory(ctx):
                        if mode=='account':
                            from .onedragon_login_account import LoginAccount
                            return LoginAccount(ctx)
                        return SwitchAccount(ctx) if mode=="switch" else EnterGame(ctx,switch=mode=="forced_enter")
                    extra={} if identity is None else dict(expected_window=identity)
                    self._run_bound_app("one_dragon",factory,display_name="账号登录",**extra)
                    if self.state not in ("failed","stopped") and not self.login_control.status()["world_verified"]:
                        self.state="failed";self.error="登录流程未核实进入大世界。"
            finally:self.prepared_app=self.prepared_hash=self.login_mode=""
        return self._submit("running",run)

    def prepare_client_close(self,expected_window:dict) -> dict:
        if not isinstance(expected_window,dict) or set(expected_window)!={"window_handle","process_id"} or any(type(value)is not int or value<=0 for value in expected_window.values()):
            raise ValueError("请提供已核实的游戏窗口和进程编号。")
        if not self.ctx.ready_for_application:raise RuntimeError("请先初始化运行环境。")
        target=dict(expected_window)
        def prepare():
            self.prepared_app=self.prepared_hash=self.login_mode="";self.close_target=None
            self.client_exit_result={};self.uses_charge=False
            self.charge_control=self.group_control=self.hunt_control=self.resource_control=None
            self.prepared_applications=[];self.prepared_requirements={};self.route_debug=None
            self.resource_confirmation.prepare([],"")
            self.backend._refresh_runtime_config()
            config=self.ctx.game_account_config
            if self.connection!=(self.ctx.current_instance_idx,config.game_path,config.game_region,config.game_language):
                raise ValueError("账号或客户端配置已变化，请重新准备运行环境。")
            self.close_target=target;self.prepared_hash=self.configuration_digest()
            self.prepared_requirements=dict(client_close=target,models=[],policies=[],resource_actions=[])
            self.prepared_app="_client_close";self.state="prepared"
        return self._submit("preparing",prepare)

    def start_client_close(self,timeout_seconds:int=60) -> dict:
        if self.prepared_app!="_client_close" or self.close_target is None:raise ValueError("请先准备关闭已核实的客户端。")
        if type(timeout_seconds)is not int or not 5<=timeout_seconds<=300:raise ValueError("退出等待时限须为 5—300 秒。")
        target=dict(self.close_target)
        def run():
            watch_stop=threading.Event();watcher=None;transition=None
            try:
                with self._run_scope():
                    try:
                        if self.prepared_hash!=self.configuration_digest():raise ValueError("客户端配置已变化，请重新准备。")
                        self.ctx.controller.bind(self.ctx.game_account_config.game_path)
                        window=self.ctx.controller.game_win
                        if window.bound is None or window.bound.hwnd!=target['window_handle'] or window.process_id!=target['process_id']:
                            raise RuntimeError("待关闭窗口身份已变化，未发送关闭请求。")
                        from .onedragon_client_exit import ClientExit
                        transition=ClientExit(self.ctx.controller,self.control)
                        with self.lock:
                            self.client_exit=transition;self.control.reset();self._input_ready=True
                            self.control.set_manual_pause(self.manual_pause.is_set())
                        self.active_seconds,self.time_limit,self.time_limited=0.,timeout_seconds,False
                        watcher=threading.Thread(target=self._watch,args=(watch_stop,),name="AutoZZZ-Client-Exit",daemon=True)
                        watcher.start();self.preparation_step="等待原游戏进程退出"
                        exited=transition.run(self.cancelled,watch_stop.wait)
                        if self.control.error:self.state="failed";self.error=self.control.error
                        elif self.cancelled.is_set() or self.control.stopped:self.state="stopped"
                        elif exited:self.state="success"
                        else:self.state="failed";self.error="尚未核实原游戏进程退出。"
                    finally:
                        watch_stop.set()
                        if watcher is not None:watcher.join()
                        if transition is not None:
                            self.client_exit_result=transition.status()
                            self.control.stop()
                            if self.client_exit_result['process_exited']:self.ctx.controller.game_win.clear_binding()
                        with self.lock:self.client_exit=None;self._input_ready=False
                        self.preparation_step=""
            finally:self.prepared_app=self.prepared_hash="";self.close_target=None
        return self._submit("running",run)

    def start_application(self, app_id: str, *, fixed_entry: bool = False, budget:dict|None=None,timeout_seconds:int=600,
                          resource_confirmation: str | None = None) -> dict:
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
            from .battle_budget import validate_runtime_budget
            validate_runtime_budget(budget)
        elif budget is not None:raise ValueError("当前应用不需要战斗预算。")
        self.resource_confirmation.verify(resource_confirmation, self.prepared_hash)
        def run():
            with self._run_scope():
                if self.prepared_hash!=self.configuration_digest():raise ValueError("配置已变化，请重新准备应用。")
                self.resource_confirmation.consume(resource_confirmation, self.prepared_hash)
                self.script_control.reset()
                self.redemption_control.reset()
                self.login_control.reset()
                self.charge_control=None
                self.group_control=None
                self.hunt_control=None
                try:
                    fingerprints = self.prepared_requirements.get("resource_settings", {})
                    if fingerprints:
                        from .onedragon_resources import ResourceActionControl
                        self.resource_control = ResourceActionControl(self.ctx, self.control, fingerprints)
                        self.resource_control.install()
                    if app_id=="one_dragon":
                        from .onedragon_group import GroupRunControl
                        self.group_control=GroupRunControl(self.ctx,self.control,self.prepared_applications)
                        self.group_control.install()
                    if self.uses_charge:
                        from .battle_budget import BattleBudget,budget_directory
                        from .onedragon_charge import ChargeBudgetControl
                        server="cn" if self.ctx.game_account_config.game_region in ("cn","cn_b") else "global"
                        ledger=BattleBudget(budget_directory(),budget["id"],server,budget["allowance"],budget["battle_limit"])
                        self.charge_control=ChargeBudgetControl(self.ctx,self.control,ledger,budget["max_per_battle"])
                        self.charge_control.install()
                    if self.prepared_requirements.get("uses_hunt"):
                        from .onedragon_hunt import NotoriousHuntControl
                        self.hunt_control=NotoriousHuntControl(self.ctx,self.control)
                        self.hunt_control.install()
                    self.active_seconds,self.time_limit,self.time_limited=0.,timeout_seconds,False
                    factory = None
                    if self.route_debug is not None:
                        from zzz_od.application.world_patrol.operation.world_patrol_run_route import WorldPatrolRunRoute
                        from zzz_od.application.world_patrol.world_patrol_route import WorldPatrolRoute
                        descriptor = self.route_debug
                        area = next(area for area in self.ctx.world_patrol_service.area_list if area.full_id == descriptor["data"]["tp_area_id"])
                        route = WorldPatrolRoute.from_dict(descriptor["data"], area)
                        def factory(ctx):
                            config = ctx.run_context.get_config("world_patrol", ctx.current_instance_idx, "one_dragon")
                            ctx.auto_battle_context.init_auto_op(config.auto_battle)
                            return WorldPatrolRunRoute(ctx, route, start_idx=descriptor["start_index"])
                    if factory is None: self._run_bound_app(app_id)
                    else: self._run_bound_app(app_id, factory)
                finally:
                    if self.hunt_control is not None:self.hunt_control.close()
                    if self.charge_control is not None:self.charge_control.close()
                    if self.group_control is not None:self.group_control.close()
                    if self.resource_control is not None:self.resource_control.close()
        return self._submit("running", run)

    def _run_bound_app(self,app_id:str,op_factory=None,display_name:str="路线调试",expected_window=None) -> None:
        self.ctx.push_service.reset_results()
        self.ctx.controller.bind(self.ctx.game_account_config.game_path)
        if expected_window is not None:
            window=self.ctx.controller.game_win
            if window.bound is None or window.bound.hwnd!=expected_window["window_handle"] or window.process_id!=expected_window["process_id"]:
                raise RuntimeError("启动后游戏窗口身份已变化，请重新准备登录。")
        with self.lock:
            self.control.reset()
            self._input_ready=True
            self.control.set_manual_pause(self.manual_pause.is_set())
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
            if op_factory is None: ok,future=self.backend._start_app("autozzz",app_id,"one_dragon")
            else: ok,future=self.backend.run_slot._start("autozzz",op_factory=op_factory,instance_idx=self.ctx.current_instance_idx,display_name=display_name)
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
            with self.lock:self._input_ready=False
            self.preparation_step = "等待通知发送" if self.ctx.push_service.pending else ""
            try: self.ctx.push_service.flush()
            finally: self.preparation_step = ""

    def _poll_task_keys(self):
        from .windows import user32
        if self.run_lease is not None:self.run_lease.check()
        if user32.GetAsyncKeyState(0x79) & 0x8000:
            self.cancelled.set()
            if self._input_ready:self.control.stop()
        pressed=bool(user32.GetAsyncKeyState(0x78)&0x8000)
        if pressed and not self._f9_down:self._set_manual_pause(not self.manual_pause.is_set())
        self._f9_down=pressed

    def _set_manual_pause(self,paused):
        with self.lock:
            if paused:self.manual_pause.set()
            else:self.manual_pause.clear()
            if self._input_ready:self.control.set_manual_pause(paused)

    def _watch(self, watch_stop: threading.Event) -> None:
        from .windows import user32
        previous=time.monotonic()
        while not watch_stop.is_set():
            try:
                self._poll_task_keys()
                self.control.poll()
                self.assistant_control.poll_keys(user32.GetAsyncKeyState)
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
            result.update(prepared_requirements=self.prepared_requirements,preparation_step=self.preparation_step)
            result.update(script=dict(self.script_control.progress),assistant=self.assistant_control.status(),redemption=list(self.redemption_control.attempts),login=self.login_control.status())
            from .onedragon_battle_state import battle_snapshot
            result['battle']=battle_snapshot(self.ctx)
            result['client_exit']=self.client_exit.status() if self.client_exit is not None else dict(self.client_exit_result)
            if self.rotation_guard is not None:result['handoff_control']=dict(paused=self.manual_pause.is_set(),f9_down=self._f9_down)
            result["resources"] = self.resource_confirmation.status()
            result["resource_results"] = [] if self.resource_control is None else [dict(item) for item in self.resource_control.results]
            result["hunt"] = {} if self.hunt_control is None else self.hunt_control.status()
            result["notifications"] = self.ctx.push_service.status()
            result["prepared_applications"]=[dict(id=key,name=self.ctx.run_context.get_application_name(key)) for key in self.prepared_applications]
            if self.group_control is not None:result["applications"]=self.group_control.status()
            result.update(active_seconds=round(self.active_seconds,2),time_limited=self.time_limited)
            bound=getattr(getattr(self.ctx.controller,"game_win",None),"bound",None)
            result["window_handle"]=None if bound is None else bound.hwnd
            pid=getattr(getattr(self.ctx.controller,'game_win',None),'process_id',None)
            result['window_identity']=None if bound is None or pid is None else dict(window_handle=bound.hwnd,process_id=pid)
            if self.charge_control is not None:result["budget"]=self.charge_control.status()
            if self.state in ("running", "stopping"):
                result["control"] = self.control.status() if self._input_ready else dict(state="paused" if self.manual_pause.is_set() else "starting",manual_paused=self.manual_pause.is_set(),focus_waiting=False)
                result["run"] = asdict(self.backend.query_status())
            return result

    def pause(self, paused: bool) -> dict:
        if type(paused) is not bool:
            raise ValueError("暂停状态必须为布尔值。")
        if self.state != "running" or not self.busy:
            raise RuntimeError("当前没有正在运行的游戏任务。")
        self._set_manual_pause(paused)
        return self.status()

    def set_assistant_mode(self,mode:int,job_id:str) -> dict:
        if job_id!=self.job_id or self.state!="running" or not self.busy:raise RuntimeError("委托助手任务已结束或已变化。")
        self.assistant_control.set_mode(mode)
        return self.status()

    def stop(self) -> dict:
        with self.lock:
            active=self.busy and self.state in ("initializing","preparing","running","stopping")
            if active:
                self.cancelled.set()
                self.ctx.download_cancel.set()
                self.state = "stopping"
        if active and self.ctx.controller is not None:self.control.stop()
        return self.status()

    def close(self) -> None:
        self.closed = True
        self.stop()
        if self.worker is not None:
            self.worker.join()  # 初始化下载或运行仍在退出时，保留同一线程与进程。
        for adapter in (self.login_control,self.redemption_control,self.assistant_control,self.script_control):adapter.close()
        self.notification_control.close()
        self.patrol_control.close()
        self.adapter.close()
        self.battle_control.close()
