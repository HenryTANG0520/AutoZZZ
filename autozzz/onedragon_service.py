"""在上游解释器内管理应用配置、条件策略和受控执行。"""
from __future__ import annotations

import inspect
import math
import os
from pathlib import Path
import sys
from enum import Enum
from typing import Any
import uuid


def plain(value: Any) -> Any:
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, Enum):
        return plain(value.value)
    if isinstance(value, (list, tuple, set)):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {key: plain(item) for key, item in vars(value).items() if not key.startswith("_")}
    raise ValueError(f"不支持的配置类型：{type(value).__name__}")


class OneDragonService:
    def __init__(self, source: Path, data: Path,run_lease=None):
        self.source, self.data = source.resolve(), data.resolve()
        sys.path.insert(0, str(self.source / "src"))
        from one_dragon.utils import os_utils
        os_utils.set_work_dir(self.data)
        self._ctx: Any = None
        self._execution: Any = None
        self._environment_changed = False
        self._binding = None
        self._route_capture = None
        self._maps = None
        self._map_capture = None
        self._downloads = None
        self._rotation = None
        self._run_lease=run_lease
        self._rotation_guard=None
        self._rotation_sealed=False

    @property
    def ctx(self) -> Any:
        if self._ctx is None:
            from .run_lock import profile_lock
            with profile_lock(self.data):
                from .onedragon_host import ManagedZContext
                ctx = ManagedZContext()
                ctx.register_application_factory()
                self._ctx = ctx
        return self._ctx

    def catalog(self) -> dict:
        from one_dragon.base.operation.application.application_factory import ApplicationFactory
        ctx = self.ctx
        factories = ctx.run_context._application_factory_map
        return {
            "instance": ctx.current_instance_idx,
            "applications": [dict(id=key, name=factory.app_name,
                                  default_group=factory.default_group, internal=factory.internal_app,
                                  configurable=key == "notify" or type(factory).create_config is not ApplicationFactory.create_config)
                             for key, factory in factories.items()],
            "failures": [dict(path=str(path), error=error) for path, error in ctx.factory_manager.scan_failures],
            "ready_for_game": bool(ctx.ready_for_application),
        }

    def _config(self, app_id: str) -> Any:
        if app_id == "notify":
            return self.ctx.notify_config
        if app_id == "battle_assistant":
            return self.ctx.battle_assistant_config
        if app_id == "teams":
            return self.ctx.team_config
        if app_id not in self.ctx.run_context._application_factory_map:
            raise ValueError(f"应用未注册：{app_id}")
        from one_dragon.base.operation.application.application_const import DEFAULT_GROUP_ID
        return self.ctx.run_context.get_config(app_id, self.ctx.current_instance_idx, DEFAULT_GROUP_ID)

    def configuration(self, app_id: str) -> dict:
        self._assert_idle()
        self.ctx
        from .run_lock import profile_lock
        with profile_lock(self.data):
            return self._configuration(app_id)

    def _configuration(self, app_id: str) -> dict:
        config = self._config(app_id)
        values, failures = {}, {}
        for name, prop in inspect.getmembers(type(config), lambda p: isinstance(p, property)):
            if name.startswith("_") or prop.fset is None:
                continue
            try:
                values[name] = plain(getattr(config, name))
            except Exception as exc:
                failures[name] = f"{type(exc).__name__}: {exc}"
        return dict(app_id=app_id, fields=values, failures=failures)

    def describe_configuration(self, app_id: str) -> dict:
        from .onedragon_config import ConfigurationStore
        return ConfigurationStore(self).describe(app_id)

    def save_configuration(self, app_id: str, values: dict, revision: str) -> dict:
        from .onedragon_config import ConfigurationStore
        return ConfigurationStore(self).save(app_id, values, revision)

    def notification_channels(self):
        from .onedragon_notify import NotificationStore
        return NotificationStore(self).channels()

    def notification_channel(self, channel_id: str):
        from .onedragon_notify import NotificationStore
        return NotificationStore(self).describe_channel(channel_id)

    def notification_channel_save(self, channel_id: str, values: dict, revision: str):
        from .onedragon_notify import NotificationStore
        return NotificationStore(self).save_channel(channel_id, values, revision)

    def notification_test(self, channel_id: str, revision: str):
        from .onedragon_notify import NotificationStore
        return NotificationStore(self).test(channel_id, revision)

    def qq_bind_start(self, is_group: bool, revision: str):
        if self._binding is None:
            from .onedragon_qq_binding import QQBinding
            self._binding = QQBinding(self)
        return self._binding.start(is_group, revision)

    def _download_service(self):
        if self._downloads is None:
            from .onedragon_downloads import ModelDownloads
            self._downloads = ModelDownloads(self)
        return self._downloads

    def _rotation_plans(self):
        if self._rotation is None:
            from .onedragon_rotation import RotationPlans
            self._rotation=RotationPlans(self)
        return self._rotation

    def rotation_preview(self,server:str):return self._rotation_plans().preview(server)
    def rotation_validate(self,plan_id:str):return self._rotation_plans().validate(plan_id)

    def rotation_snapshot(self,plan_id:str):
        self.rotation_validate(plan_id)
        from .rotation_snapshot import snapshot
        result=snapshot(self.data)
        self.rotation_validate(plan_id)
        return result

    def rotation_bind(self,manifest:dict):
        self._assert_idle()
        if self._run_lease is None:raise RuntimeError('轮换资料交接需要总任务锁。')
        if self._ctx is not None or self._rotation_guard is not None:raise RuntimeError('请使用新的工作进程接续轮换。')
        self._run_lease.check()
        from .rotation_snapshot import RotationSnapshot
        guard=RotationSnapshot(self.data,manifest);guard.install();self._rotation_guard=guard
        return dict(bound=True)

    def rotation_select(self,idx:int,expected_current:int):
        self._assert_idle()
        guard=self._rotation_guard
        if guard is None:raise RuntimeError('请先交接轮换资料。')
        from .onedragon_accounts import AccountStore
        from .config_transaction import atomic_apply,config_revision
        from .run_lock import profile_lock
        store=AccountStore(self)
        with profile_lock(self.data):
            guard.check();registry=store._registry();store._instance(registry,idx)
            if type(expected_current)is not int or registry.current_active_instance.idx!=expected_current:raise ValueError('当前账号已变化，请重新预检。')
            def activate():
                rows=[dict(row,active=row['idx']==idx) for row in registry.dict_instance_list]
                registry.dict_instance_list=rows
            target=registry._get_write_path()
            guard.authorized(target,lambda:atomic_apply(registry,config_revision(registry),activate,self.data))
            registry._init_instance_list();store._refresh(registry)
        return dict(current=idx)

    def rotation_checkpoint(self):
        self._assert_idle()
        if self._rotation_guard is None:raise RuntimeError('当前没有轮换资料交接。')
        return self._rotation_guard.checkpoint()

    def rotation_control(self,paused:bool,f9_down:bool):
        self._assert_idle()
        if self._rotation_guard is None or self._execution is None:raise RuntimeError('当前没有可交接的轮换执行器。')
        if type(paused)is not bool or type(f9_down)is not bool:raise ValueError('暂停交接格式无效。')
        self._execution.handoff=(paused,f9_down)
        return dict(accepted=True)

    def rotation_window(self):
        self._assert_idle()
        if self._rotation_guard is None:raise RuntimeError('请先交接轮换资料。')
        from .windows import find_game,user32,W,C
        from .models import AutomationError
        path=self.ctx.game_account_config.game_path
        if not path:return None
        try:window=find_game(path)
        except AutomationError:return None
        pid=W.DWORD();user32.GetWindowThreadProcessId(window.hwnd,C.byref(pid))
        return dict(window_handle=window.hwnd,process_id=pid.value)

    def rotation_release(self):
        self._assert_idle()
        guard=self._rotation_guard
        if guard is None:raise RuntimeError('当前没有轮换资料交接。')
        try:
            self._close_resources()
            return guard.checkpoint()
        finally:
            guard.close();self._rotation_guard=None;self._rotation_sealed=True

    def model_catalog(self): return self._download_service().catalog()
    def model_download_start(self, resource_id: str, revision: str): return self._download_service().start(resource_id,revision)
    def model_download_status(self, job_id: str): return self._download_service().status(job_id)
    def model_download_cancel(self, job_id: str): return self._download_service().cancel(job_id)
    def model_install_recover(self): return self._download_service().recover()

    def qq_bind_status(self, job_id: str):
        if self._binding is None: raise ValueError("QQ 绑定尚未开始。")
        return self._binding.status(job_id)

    def qq_bind_cancel(self, job_id: str):
        if self._binding is None: raise ValueError("QQ 绑定尚未开始。")
        return self._binding.cancel(job_id)

    def library_catalog(self):
        from .onedragon_library import LibraryStore
        result=LibraryStore(self).catalog()
        maps=self._map_store();result['items'].extend(maps.catalog());result['map_pending']=maps.tx.pending.exists()
        return result

    def _map_store(self):
        if self._maps is None:
            from .onedragon_maps import MapStore
            self._maps=MapStore(self)
        return self._maps

    def map_edit(self,data:dict,action:str,value=None):return self._map_store().edit(data,action,value)
    def map_archives(self,key:str):return self._map_store().archives(key)
    def map_restore(self,key:str,identifier:str,revision:str):return self._map_store().restore(key,identifier,revision)
    def map_recover(self):return self._map_store().recover()

    def map_capture_start(self,data:dict,game_path:str,rotate:bool=False):
        self._assert_idle()
        from .onedragon_map_capture import MapCaptureJob
        if self._map_capture is None:self._map_capture=MapCaptureJob(self)
        return self._map_capture.start_map(data,game_path,rotate)

    def map_capture_status(self,job_id:str):
        if self._map_capture is None:raise ValueError('地图取景尚未开始。')
        return self._map_capture.status(job_id)

    def map_capture_pause(self,job_id:str,paused:bool):
        if self._map_capture is None:raise ValueError('地图取景尚未开始。')
        return self._map_capture.pause(job_id,paused)

    def map_capture_cancel(self,job_id:str):
        if self._map_capture is None:raise ValueError('地图取景尚未开始。')
        return self._map_capture.cancel(job_id)

    def route_locate(self, area_id: str, anchor: list, image: str):
        self._assert_idle()
        from .onedragon_route_recorder import decode_client_image, locate_position, route_map
        from .run_lock import profile_lock
        frame = decode_client_image(image)
        large_map, point = route_map(self, area_id, anchor)
        with profile_lock(self.data): return locate_position(self.ctx, large_map, point, frame)

    def route_capture_start(self, area_id: str, anchor: list, game_path: str):
        self._assert_idle()
        from .onedragon_route_recorder import RouteCaptureJob
        if self._route_capture is None: self._route_capture = RouteCaptureJob(self)
        return self._route_capture.start(area_id, anchor, game_path)

    def route_capture_status(self, job_id: str):
        if self._route_capture is None: raise ValueError("截图任务尚未开始。")
        return self._route_capture.status(job_id)

    def route_capture_cancel(self, job_id: str):
        if self._route_capture is None: raise ValueError("截图任务尚未开始。")
        return self._route_capture.cancel(job_id)

    def route_capture_pause(self, job_id: str, paused: bool):
        if self._route_capture is None: raise ValueError("截图任务尚未开始。")
        return self._route_capture.pause(job_id, paused)

    def library_read(self, kind: str, key: str):
        if kind=='world_patrol_map':return self._map_store().read(key)
        from .onedragon_library import LibraryStore
        return LibraryStore(self).read(kind, key)

    def library_draft(self, kind: str, key: str = "", source_key: str = "", source_revision: str = "", area_id: str = "", text: str | None = None, data: dict | None = None):
        from .onedragon_library import LibraryStore
        return LibraryStore(self).draft(kind, key, source_key, source_revision, area_id, text, data)

    def library_save(self, kind: str, key: str, data: dict, revision: str, source_key: str = "", source_revision: str = ""):
        if kind=='world_patrol_map':return self._map_store().save(key,data,revision)
        from .onedragon_library import LibraryStore
        return LibraryStore(self).save(kind, key, data, revision, source_key, source_revision)

    def library_remove(self, kind: str, key: str, revision: str):
        if kind=='world_patrol_map':return self._map_store().remove(key,revision)
        from .onedragon_library import LibraryStore
        return LibraryStore(self).remove(kind, key, revision)

    def library_rename(self, kind: str, key: str, name: str, revision: str):
        from .onedragon_library import LibraryStore
        return LibraryStore(self).rename(kind, key, name, revision)

    def library_map(self, area_id: str):
        from .onedragon_library import LibraryStore
        return LibraryStore(self).map_preview(area_id)

    def library_export(self, kind: str, key: str, revision: str):
        from .onedragon_library import LibraryStore
        return LibraryStore(self).export(kind, key, revision)

    def library_archive_read(self, identifier: str):
        from .onedragon_library import LibraryStore
        return LibraryStore(self).archive_read(identifier)

    def training_options(self) -> dict:
        from .onedragon_config import ConfigurationStore
        return ConfigurationStore(self).compendium()

    def configuration_options(self) -> dict:
        self._assert_idle()
        from .onedragon_config import ConfigurationStore
        from .run_lock import profile_lock
        store = ConfigurationStore(self)
        with profile_lock(self.data):
            return store.options()

    def account_list(self) -> dict:
        from .onedragon_accounts import AccountStore
        return AccountStore(self).list()

    def account_action(self,action:str,revision:str,idx:int|None=None,name:str|None=None,enabled:bool|None=None,force_login:bool|None=None) -> dict:
        from .onedragon_accounts import AccountStore
        return AccountStore(self).action(action,revision,idx,name,enabled,force_login)

    def account_describe(self,idx:int,server:str) -> dict:
        from .onedragon_accounts import AccountStore
        return AccountStore(self).describe(idx,server)

    def account_save(self,idx:int,server:str,revision:str,values:dict) -> dict:
        from .onedragon_accounts import AccountStore
        return AccountStore(self).save(idx,server,revision,values)

    def set_battle_options(self, values: dict) -> dict:
        self._assert_idle()
        allowed = {"auto_battle_config": str, "use_merged_file": bool, "auto_ultimate_enabled": bool,
                   "screenshot_interval": float, "background_mode": bool, "dodge_assistant_config": str}
        if not isinstance(values, dict) or set(values) - set(allowed):
            raise ValueError("战斗设置包含未知字段。")
        for key, value in values.items():
            expected = allowed[key]
            if expected is float:
                if type(value) not in (float, int) or not math.isfinite(value) or not .01 <= value <= 1:
                    raise ValueError("截图间隔必须在 0.01—1 秒之间。")
            elif type(value) is not expected:
                raise ValueError(f"{key} 的配置类型错误。")
        from .onedragon_config import ConfigurationStore
        store = ConfigurationStore(self)
        description = store.describe("battle_assistant")
        store.save("battle_assistant", values, description["revision"])
        return self.configuration("battle_assistant")

    @staticmethod
    def _name(name: str) -> str:
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError("请输入策略名称。")
        if name in (".", "..") or any(char in name for char in '/\\\0<>:"|?*') or name.endswith((".", " ")):
            raise ValueError("策略名称不能包含路径或文件名保留字符。")
        if name.endswith((".sample", ".merged")):
            raise ValueError("策略名称不能以 .sample 或 .merged 结尾。")
        if len(name) > 100 or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
            raise ValueError("策略名称无效。")
        return name

    @staticmethod
    def _policy_kind(kind: str) -> str:
        if kind not in ('auto_battle','dodge'):raise ValueError('策略类型须为自动战斗或闪避。')
        return kind

    def _policy_path(self, name: str, merged: bool = True, kind: str = 'auto_battle') -> Path:
        self._name(name)
        kind=self._policy_kind(kind)
        from one_dragon.base.conditional_operation.loader import ConditionalOperatorLoader
        path = Path(ConditionalOperatorLoader.get_yaml_file_path([kind], name, merged)).resolve()
        directory = (self.data / "config" / kind).resolve()
        if path.parent != directory or not path.is_file():
            raise ValueError(f"条件策略不存在：{name}")
        return path

    def policies(self, kind: str = 'auto_battle') -> list[dict]:
        kind=self._policy_kind(kind)
        directory = self.data / "config" / kind
        names = set()
        for path in directory.glob("*.yml"):
            name = path.name.removesuffix(".yml").removesuffix(".merged").removesuffix(".sample")
            if not name.startswith("autozzz-check-"):
                names.add(name)
        return [dict(name=name, format=self._policy_path(name,kind=kind).name,
                     custom=not (directory / (name + ".merged.yml")).exists()
                            and not (directory / (name + ".sample.yml")).exists())
                for name in sorted(names)]

    def policy_read(self, name: str, merged: bool = True, kind: str = 'auto_battle') -> dict:
        path = self._policy_path(name, merged,kind)
        return dict(name=name, filename=path.name, content=path.read_text(encoding="utf-8"))

    def policy_validate(self, name: str, merged: bool = True, kind: str = 'auto_battle') -> dict:
        self._assert_idle()
        self.ctx
        from .run_lock import profile_lock
        with profile_lock(self.data):
            return self._policy_validate(name,merged,kind)

    def _policy_validate(self, name: str, merged: bool = True, kind: str = 'auto_battle') -> dict:
        self._assert_idle()
        self._policy_path(name, merged,kind)  # 上游会在缺失时回退；桥接层必须先拒绝错误名称。
        from zzz_od.auto_battle.auto_battle_operator import AutoBattleOperator
        from one_dragon.base.conditional_operation.operator import ConditionalOperator
        operator = AutoBattleOperator(self.ctx.auto_battle_context, kind, name, merged)
        try:
            ConditionalOperator.init(operator)  # 真正构建状态树与原子操作，不启动它们。
            scenes = []
            for scene in operator.scenes:
                states = set()
                for handler in scene.handlers:
                    states.update(handler.usage_states)
                scenes.append(dict(triggers=scene.triggers, priority=scene.priority, states=sorted(states)))
            if not scenes:
                raise ValueError("条件策略至少需要一个场景。")
            return dict(name=name, author=operator.author, version=operator.version,
                        introduction=operator.introduction, team_list=operator.team_list,
                        engine=type(operator).__module__ + "." + type(operator).__name__,
                        scenes=scenes, game_input_sent=False)
        finally:
            operator.dispose()

    def policy_save(self, name: str, content: str, kind: str = 'auto_battle') -> dict:
        self._assert_idle()
        self.ctx
        from .run_lock import profile_lock
        with profile_lock(self.data):
            return self._policy_save(name, content,kind)

    def _policy_save(self, name: str, content: str, kind: str = 'auto_battle') -> dict:
        self._assert_idle()
        name = self._name(name)
        if not isinstance(content, str) or len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("策略必须为不超过 1 MiB 的 YAML 文本。")
        directory = self.data / "config" / self._policy_kind(kind)
        if any((directory / (name + suffix)).exists() for suffix in (".sample.yml", ".merged.yml")):
            raise ValueError("内置策略请使用新名称另存，保留原策略及作者信息。")
        temporary_name = "autozzz-check-" + uuid.uuid4().hex
        temporary = directory / (temporary_name + ".yml")
        target = directory / (name + ".yml")
        if target.resolve().parent != directory.resolve():
            raise ValueError("策略文件路径指向数据目录外。")
        temporary.write_text(content, encoding="utf-8")
        try:
            validation = self._policy_validate(temporary_name, merged=False,kind=kind)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        validation["name"] = name
        return validation

    def policy_check(self, content: str, kind: str = 'auto_battle') -> dict:
        self._assert_idle()
        self.ctx
        from .run_lock import profile_lock
        with profile_lock(self.data):
            return self._policy_check(content,kind)

    def _policy_check(self, content: str, kind: str = 'auto_battle') -> dict:
        self._assert_idle()
        if not isinstance(content, str) or len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("策略必须为不超过 1 MiB 的 YAML 文本。")
        directory = self.data / "config" / self._policy_kind(kind)
        name = "autozzz-check-" + uuid.uuid4().hex
        temporary = directory / (name + ".yml")
        temporary.write_text(content, encoding="utf-8")
        try:
            result = self._policy_validate(name, merged=False,kind=kind)
            result["name"] = "草稿"
            return result
        finally:
            temporary.unlink(missing_ok=True)

    def _assert_idle(self) -> None:
        if self._downloads is not None and self._downloads.busy:
            raise RuntimeError("模型下载仍在处理，请等待或取消原任务。")
        if self._map_capture is not None and self._map_capture.busy:
            raise RuntimeError("地图取景仍在处理，请等待或取消原任务。")
        if self._route_capture is not None and self._route_capture.busy:
            raise RuntimeError("路线截图仍在处理，请等待或取消原任务。")
        if self._binding is not None and self._binding.busy:
            raise RuntimeError("QQ 绑定仍在处理中，请等待原连接退出。")
        if self._execution is not None and self._execution.busy:
            raise RuntimeError("一条龙任务仍在处理，暂时不能修改或重载配置。")

    def initialize_execution(self, server: str, game_path: str, region: str | None = None,instance_idx:int|None=None) -> dict:
        self._assert_idle()
        from .map_transaction import MapTransaction
        MapTransaction(self.data).assert_clear()
        from .model_install import ModelInstall
        ModelInstall(self.data).assert_clear()
        if self._environment_changed:
            raise RuntimeError("环境或模型已修改，请重新连接工作进程后准备。")
        from .onedragon_environment import EnvironmentStore, validate_environment
        validate_environment(self.ctx.env_config, self.ctx.repo_config)
        EnvironmentStore(self).validate_models(self.ctx.model_config)
        if self._execution is None:
            from .onedragon_execution import OneDragonExecution
            self._execution = OneDragonExecution(self.ctx,run_lease=self._run_lease,rotation_guard=self._rotation_guard) if self._run_lease is not None else OneDragonExecution(self.ctx)
        return self._execution.initialize(server, game_path, region,instance_idx)

    def execution_status(self) -> dict:
        if self._execution is None:
            return dict(state="idle", busy=False, ready=False, job_id="", error="")
        return self._execution.status()

    def prepare_application(self,app_id:str,route_debug:dict|None=None) -> dict:
        self._assert_idle()
        if self._environment_changed: raise RuntimeError("环境或模型已修改，请重新连接工作进程后准备。")
        if self._execution is None:raise RuntimeError("请先初始化运行环境。")
        if route_debug is not None:
            if app_id != "world_patrol" or not isinstance(route_debug, dict) or set(route_debug) != {"key", "revision", "start_index"}:
                raise ValueError("路线调试参数无效。")
            from .onedragon_library import LibraryStore
            from .run_lock import profile_lock
            store = LibraryStore(self)
            with profile_lock(self.data):
                document = store._document("world_patrol_route", route_debug["key"])
                if route_debug["revision"] != document["revision"]: raise ValueError("路线已变化，请重新读取后调试。")
                data, _, _ = store._validate("world_patrol_route", document["key"], document["data"])
                index = route_debug["start_index"]
                if type(index) is not int or not 0 <= index < len(data["op_list"]): raise ValueError("开始下标须为 0 到移动点数量减 1。")
                descriptor = dict(route_debug, data=data, name=document["name"])
                # 首次读取补齐安全默认值，先完成这个已知初始化，再固定准备快照。
                self.ctx.push_service.reload_config()
                digest = self._execution.configuration_digest()
            return self._execution.prepare_route_debug(descriptor, digest)
        return self._execution.prepare_application(app_id)

    def prepare_login(self,mode:str) -> dict:
        self._assert_idle()
        if self._environment_changed:raise RuntimeError("环境或模型已修改，请重新连接工作进程后准备。")
        if self._execution is None:raise RuntimeError("请先初始化运行环境。")
        return self._execution.prepare_login(mode)

    def start_login(self,mode:str,timeout_seconds:int=600,auto_launch:bool=False) -> dict:
        self._assert_idle()
        if self._environment_changed:raise RuntimeError("环境或模型已修改，请重新连接工作进程后准备。")
        if self._execution is None:raise RuntimeError("请先初始化运行环境。")
        return self._execution.start_login(mode,timeout_seconds,auto_launch=auto_launch)

    def prepare_client_close(self,expected_window:dict) -> dict:
        self._assert_idle()
        if self._environment_changed:raise RuntimeError("环境或模型已修改，请重新连接工作进程后准备。")
        if self._execution is None:raise RuntimeError("请先初始化运行环境。")
        return self._execution.prepare_client_close(expected_window)

    def start_client_close(self,timeout_seconds:int=60) -> dict:
        self._assert_idle()
        if self._environment_changed:raise RuntimeError("环境或模型已修改，请重新连接工作进程后准备。")
        if self._execution is None:raise RuntimeError("请先初始化运行环境。")
        return self._execution.start_client_close(timeout_seconds)

    def start_application(self,app_id:str,budget:dict|None=None,timeout_seconds:int=600,resource_confirmation:str|None=None) -> dict:
        self._assert_idle()
        if self._environment_changed: raise RuntimeError("环境或模型已修改，请重新连接工作进程后准备。")
        if self._execution is None:raise RuntimeError("请先初始化运行环境。")
        return self._execution.start_application(app_id,fixed_entry=True,budget=budget,timeout_seconds=timeout_seconds,resource_confirmation=resource_confirmation)

    def pause_execution(self, paused: bool) -> dict:
        if self._execution is None:
            raise RuntimeError("当前没有一条龙执行任务。")
        return self._execution.pause(paused)

    def set_assistant_mode(self,mode:int,job_id:str) -> dict:
        if self._execution is None:raise RuntimeError("委托助手尚未开始。")
        return self._execution.set_assistant_mode(mode,job_id)

    def stop_execution(self) -> dict:
        if self._execution is None:
            return self.execution_status()
        return self._execution.stop()

    def runtime_probe(self) -> dict:
        self._assert_idle()
        if self._execution is None or not self.ctx.ready_for_application:
            raise RuntimeError("一条龙环境尚未就绪。")
        import cv2
        import numpy as np
        image = np.full((100, 320, 3), 255, dtype=np.uint8)
        cv2.putText(image, "400", (25, 72), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3, cv2.LINE_AA)
        text = self.ctx.ocr.run_ocr_single_line(image)
        model=self.ctx.auto_battle_context.dodge_context._flash_model
        flash=None if model is None else dict(name=model.model_name,providers=model.session.get_providers(),
                                              class_idx=int(model.run(image).class_idx))
        detectors={}
        lost=self.ctx.__dict__.get("lost_void")
        withered=self.ctx.__dict__.get("withered_domain")
        for key,detector in (("lost_void_det",None if lost is None else lost.detector),
                             ("hollow_zero_event",None if withered is None else withered.map_service.event_model)):
            if detector is not None:
                result=detector.run(np.zeros((1080,1920,3),dtype=np.uint8))
                detectors[key]=dict(name=detector.model_name,providers=detector.session.get_providers(),objects=len(result.results))
        controller = self.ctx.controller
        capture = controller.screenshot_controller
        return dict(ocr_text=text, ocr_model=self.ctx.ocr._ocr_param.ocr_model_name,
                    screenshot_method=controller.screenshot_method, controller=type(controller).__name__,
                    screenshot_window_bound=capture.game_win is controller.game_win and
                        all(strategy.game_win is controller.game_win for strategy in capture.strategies.values()),
                    window_bound=self.ctx.controller.game_win.bound is not None,
                    upstream_hotkeys_started=self.ctx._btn_listener_started,game_input_sent=False,flash_model=flash,detectors=detectors)

    def close(self) -> None:
        try:self._close_resources()
        finally:
            if self._rotation_guard is not None:self._rotation_guard.close();self._rotation_guard=None

    def _close_resources(self) -> None:
        if self._downloads is not None:self._downloads.close()
        if self._map_capture is not None:self._map_capture.close()
        if self._route_capture is not None: self._route_capture.close()
        if self._binding is not None:
            self._binding.close()
        if self._execution is not None:
            self._execution.close()
            self._execution=None
        if self._ctx is not None:
            ctx = self._ctx
            try:
                if ctx.controller is not None:
                    ctx.after_app_shutdown()
                else:
                    # 只读模式没有控制器。上游完整退出会释放游戏按键，
                    # 也会懒加载未使用的遥测/空洞上下文；此处只清理已创建的服务。
                    from one_dragon.base.operation.one_dragon_context import OneDragonContext
                    from zzz_od.auto_battle.auto_battle_operator import AutoBattleOperator
                    battle = ctx.auto_battle_context
                    for operator in battle._op_cache.values():
                        operator.dispose()
                    battle.agent_context.after_app_shutdown()
                    battle.dodge_context.after_app_shutdown()
                    battle.target_context.after_app_shutdown()
                    AutoBattleOperator.after_app_shutdown()
                    OneDragonContext.after_app_shutdown(ctx)
            finally:
                self._ctx = None


METHODS = {"catalog", "configuration", "policies", "policy_read", "policy_validate",
           "policy_save", "policy_check", "set_battle_options", "initialize_execution",
           "execution_status", "pause_execution", "stop_execution", "runtime_probe","set_assistant_mode",
           "describe_configuration", "save_configuration", "training_options", "configuration_options",
           "prepare_application", "start_application","account_list","account_action","account_describe","account_save"}
METHODS.update({"notification_channels", "notification_channel", "notification_channel_save", "notification_test"})
METHODS.update({"qq_bind_start", "qq_bind_status", "qq_bind_cancel"})
METHODS.update({"library_catalog", "library_read", "library_draft", "library_save", "library_remove", "library_rename", "library_map", "library_export", "library_archive_read"})
METHODS.update({"model_catalog", "model_download_start", "model_download_status", "model_download_cancel", "model_install_recover"})
METHODS.update({"rotation_preview", "rotation_validate"})
METHODS.update({"rotation_snapshot", "rotation_bind", "rotation_select", "rotation_checkpoint", "rotation_release"})
METHODS.update({"rotation_control", "rotation_window"})
METHODS.update({"prepare_login", "start_login"})
METHODS.update({"prepare_client_close", "start_client_close"})
METHODS.update({"route_locate", "route_capture_start", "route_capture_status", "route_capture_cancel", "route_capture_pause"})
METHODS.update({"map_edit", "map_archives", "map_restore", "map_recover"})
METHODS.update({"map_capture_start", "map_capture_status", "map_capture_pause", "map_capture_cancel"})
