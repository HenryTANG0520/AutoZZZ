"""在上游解释器内管理应用配置、条件策略和受控执行。"""
from __future__ import annotations

import inspect
import math
import os
from pathlib import Path
import sys
import threading
from enum import Enum
from typing import Any
import uuid

from .onedragon_bundle import filesystem_path
from .runtime_generation import SDK_CONTROL_METHODS


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
    def __init__(self, source: Path, data: Path,run_lease=None,*,runtime_revision=None,stable_data=None,product_root=None,runtime_generation=None,management_only=False,recovery_host=None):
        self.source, self.data = source.resolve(), data.resolve()
        from .onedragon_runtime import UPSTREAM_REVISION
        self.runtime_revision=runtime_revision or UPSTREAM_REVISION
        self.stable_data=Path(stable_data).resolve() if stable_data is not None else self.data
        self.product_root=Path(product_root).resolve() if product_root is not None else self.source.parent.parent
        from .runtime_generation import RuntimeGeneration
        self.runtime_guard=RuntimeGeneration(self.stable_data,runtime_generation,management_only)
        sys.path.insert(0, str(filesystem_path(self.source / "src")))
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
        self._pip = None
        self._plugins = None
        self._program_updates=None
        self._gamepad_dependencies=None
        self._source_workspace=None
        self._sdk_upgrade=None
        self._environment_tools=None
        self._global_capture=None
        self._dispatch_lock=threading.RLock()
        self._mcp_service=None
        self._run_lease=run_lease
        self._recovery_host = recovery_host
        self._rotation_guard=None
        self._rotation_sealed=False

    @property
    def ctx(self) -> Any:
        self.runtime_guard.check_current()
        if self._ctx is None:
            from .run_lock import profile_lock
            with profile_lock(self.data):
                self._create_context()
        else:
            from .onedragon_plugins import verify_context_plugins
            verify_context_plugins(self._ctx)
        return self._ctx

    def _create_context(self):
        """调用方持有 profile_lock；专用设置在整个界面生命周期保留它。"""
        from .gamepad_install import activate_gamepad_support
        try:activate_gamepad_support(self.data)
        except (ValueError,OSError) as exc:
            # 损坏的可选依赖不进入sys.path；保留前台功能与显式修复入口。
            from one_dragon.utils.log_utils import log
            log.warning('虚拟手柄依赖未激活：%s',exc)
        from .onedragon_host import ManagedZContext
        ctx=ManagedZContext();ctx.plugin_run_lease=self._run_lease
        ctx.autozzz_runtime_revision=self.runtime_revision
        ctx.autozzz_runtime_guard=self.runtime_guard.check_execution
        from .onedragon_audio import install_audio_dodge
        install_audio_dodge(ctx,self.runtime_revision)
        ctx.register_application_factory();self._ctx=ctx
        return ctx

    @property
    def plugins(self):
        if self._plugins is None:
            from .onedragon_plugins import PluginManager
            self._plugins=PluginManager(self.data)
        return self._plugins

    def plugin_catalog(self):return self.plugins.catalog()
    def plugin_preview(self,path,kind):self._assert_idle();return self.plugins.preview(path,kind)
    def plugin_status(self):return self.plugins.status()
    def plugin_install(self,token,names,overwrites,revision,allow_downgrade=False):self._assert_idle();return self.plugins.install(token,names,overwrites,revision,allow_downgrade)
    def plugin_enabled(self,name,enabled,revision):self._assert_idle();return self.plugins.enabled(name,enabled,revision)
    def plugin_remove(self,name,revision):self._assert_idle();return self.plugins.remove(name,revision)
    def plugin_archives(self):return self.plugins.archive_list()
    def plugin_directory(self):return self.plugins.directory()
    def plugin_restore(self,identifier,names,revision,overwrites):self._assert_idle();return self.plugins.restore(identifier,names,revision,overwrites)

    @property
    def program_updates(self):
        if self._program_updates is None:
            from .program_updates import ProgramUpdates
            self._program_updates=ProgramUpdates(self)
        return self._program_updates

    def program_catalog(self):return self.program_updates.catalog()
    def program_check(self,channel=None,page=1):return self.program_updates.check(channel,page)
    def program_download(self,release_id,revision):return self.program_updates.download(release_id,revision)
    def program_import(self,path):return self.program_updates.import_package(path)
    def program_restore(self,path):return self.program_updates.restore(path)
    def program_status(self,job_id):return self.program_updates.status(job_id)
    def program_cancel(self,job_id):return self.program_updates.cancel(job_id)

    @property
    def pip_service(self):
        if self._pip is None:
            from .onedragon_pip import PipService
            self._pip=PipService(self.data)
        return self._pip

    def pip_settings(self):return self.pip_service.settings()
    def pip_save(self,values,revision):return self.pip_service.save(values,revision)
    def pip_start(self,server,game_path):return self.pip_service.start(server,game_path)
    def pip_status(self,after_sequence=-1):return self.pip_service.status(after_sequence)
    def pip_dismiss(self):return self.pip_service.dismiss()
    def pip_activate(self,epoch):return self.pip_service.activate(epoch)
    def pip_stop(self):return self.pip_service.stop()

    def trace_configure(self,enabled,clear=False,*,boss_timing=False):
        return self.ctx.debug_trace_runtime.configure(enabled,clear,boss_timing=boss_timing,execution=self._execution)
    def trace_snapshot(self):return self.ctx.debug_trace_runtime.snapshot()
    @property
    def global_capture(self):
        if self._global_capture is None:
            from .global_capture import GlobalCaptureRuntime
            self._global_capture=GlobalCaptureRuntime(self)
        return self._global_capture

    def global_capture_status(self,game_path=None):return self.global_capture.status(game_path)
    def global_capture_capture(self,token,game_path=None,include_overlay=None,legacy_lease=None):
        return self.global_capture.capture(token,game_path,include_overlay,legacy_lease)
    def trace_capture(self,epoch,generation):
        from .trace_capture import capture_source
        if type(epoch)is not int or type(generation)is not int:raise ValueError('截图观察编号无效。')
        return capture_source(self.ctx.debug_trace_runtime,self.data,epoch,generation)

    def _agent_templates(self):
        if getattr(self,'_agent_template_store',None) is None:
            from .agent_templates import AgentTemplates
            self._agent_template_store=AgentTemplates(self)
        return self._agent_template_store

    def agent_template_catalog(self):return self._agent_templates().catalog()
    def agent_template_open(self,agent=None):return self._agent_templates().open(agent)
    def agent_template_import(self,token,index,path,side='left'):return self._agent_templates().import_image(token,index,path,side)
    def agent_template_side(self,token,side):return self._agent_templates().change_side(token,side)
    def agent_template_view(self,token,index):
        self._agent_templates().current(token)
        if type(index)is not int or not 0<=index<6:raise ValueError('模板步骤无效。')
        return self._agent_templates().view(index)
    def agent_template_save(self,token,agent):return self._agent_templates().save(token,agent)
    def agent_template_remove(self,agent,signature):return self._agent_templates().remove(agent,signature)
    def agent_template_restore(self,identifier):return self._agent_templates().restore(identifier)
    def agent_template_recover(self):return self._agent_templates().recover()
    def agent_template_preferences(self):return self._agent_templates().preferences()
    def agent_template_preferences_save(self,values,revision):return self._agent_templates().preferences_save(values,revision)
    def agent_template_capture(self,token,index,game_path,side='left'):
        from .agent_template_capture import capture
        self._assert_idle();self._agent_templates().current(token)
        value=capture(self.data,game_path)
        return dict(capture=value,draft=self._agent_templates().import_image(token,index,value['path'],side))

    def catalog(self) -> dict:
        from one_dragon.base.operation.application.application_factory import ApplicationFactory
        ctx = self.ctx
        factories = ctx.run_context._application_factory_map
        from .onedragon_plugins import executable_application
        from .onedragon_settings import has_settings,settings_revision
        return {
            "instance": ctx.current_instance_idx,
            "settings_revision":settings_revision(ctx),
            "applications": [dict(id=key, name=factory.app_name,
                                  default_group=factory.default_group, internal=factory.internal_app,
                                  third_party=bool(ctx.factory_manager.get_plugin_info(key).is_third_party),
                                  executable=executable_application(ctx,key),
                                  native_settings=has_settings(ctx.factory_manager.get_plugin_info(key)),
                                  configurable=key == "notify" or type(factory).create_config is not ApplicationFactory.create_config)
                             for key, factory in factories.items()],
            "failures": [dict(path=str(path), error=error) for path, error in ctx.factory_manager.scan_failures if not Path(path).resolve().is_relative_to(self.data/'plugins')],
            "plugin_failures": [dict(path=str(path), error=error) for path, error in ctx.factory_manager.scan_failures if Path(path).resolve().is_relative_to(self.data/'plugins')],
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

    def game_launch_options(self):
        from .onedragon_game_input import GameInputStore
        return GameInputStore(self).launch_options()

    def game_hdr_status(self):
        from .onedragon_game_input import GameInputStore
        return GameInputStore(self).hdr_status()

    def game_hdr_set(self,enabled,revision,confirmed=False):
        from .onedragon_game_input import GameInputStore
        return GameInputStore(self).hdr_set(enabled,revision,confirmed)

    def game_hdr_recover(self,journal_digest,confirmed=False):
        from .onedragon_game_input import GameInputStore
        return GameInputStore(self).hdr_recover(journal_digest,confirmed)

    def run_record_status(self,app_id):
        from .onedragon_run_records import RunRecordStore
        return RunRecordStore(self).describe(app_id)

    def run_record_reset(self,app_id,revision,instance_idx):
        from .onedragon_run_records import RunRecordStore
        return RunRecordStore(self).reset(app_id,revision,instance_idx)

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

    def rotation_preview(self,server:str,instance_idx:int|None=None,app_id:str='one_dragon'):return self._rotation_plans().preview(server,instance_idx,app_id)

    def schedule_review(self,server:str,instance_idx:int|None=None,app_id:str='one_dragon'):
        from .onedragon_schedule import review
        return review(self,server,instance_idx,app_id)
    def rotation_validate(self,plan_id:str):return self._rotation_plans().validate(plan_id)

    def rotation_snapshot(self,plan_id:str,expected_intent:str|None=None):
        plan=self.rotation_validate(plan_id)
        if expected_intent is not None:
            import re
            if type(expected_intent)is not str or re.fullmatch('[0-9a-f]{64}',expected_intent)is None:raise ValueError('定时计划配置版本无效。')
        from .rotation_snapshot import snapshot
        result=snapshot(self.data)
        if expected_intent is not None:
            from .onedragon_schedule import intent_revision
            if intent_revision(self.data,[row['idx'] for row in plan['instances']])!=expected_intent:
                raise ValueError('定时计划的配置、策略或资源方式已变化，请重新核对。')
        self.rotation_validate(plan_id)
        if snapshot(self.data)!=result:raise ValueError('资料交接期间配置发生变化，请重新预检。')
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
        job=MapCaptureJob(self)
        result=job.start_map(data,game_path,rotate)
        self._map_capture=job
        return result

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
        large_map, point, validate_map = route_map(self, area_id, anchor)
        with profile_lock(self.data):
            validate_map()
            result = locate_position(self.ctx, large_map, point, frame)
            validate_map()
            return result

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
        if name.casefold().endswith((".sample", ".merged")):
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
        from .policy_archives import checksum
        raw=path.read_bytes()
        return dict(name=name, filename=path.name, content=raw.decode('utf-8'),revision=checksum(raw))

    def policy_remove(self,name:str,revision:str,kind:str='auto_battle'):
        from .policy_archives import PolicyArchives
        return PolicyArchives(self).remove(name,kind,revision)

    def policy_restore(self,archive_id:str):
        from .policy_archives import PolicyArchives
        return PolicyArchives(self).restore(archive_id)

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
        from .policy_archives import checksum
        validation['revision']=checksum(target.read_bytes())
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
        self.runtime_guard.check_current()
        if getattr(self,'_sdk_upgrade',None) is not None and self._sdk_upgrade.busy:raise RuntimeError('SDK 操作仍在运行，请等待或取消原任务。')
        if getattr(self,'_source_workspace',None) is not None and self._source_workspace.busy:raise RuntimeError('源码操作仍在运行，请等待或取消原任务。')
        if getattr(self,'_environment_tools',None) is not None and self._environment_tools.busy:raise RuntimeError('下载源或代理检查仍在运行，请等待或取消原任务。')
        if getattr(self,'_gamepad_dependencies',None) is not None and self._gamepad_dependencies.busy:raise RuntimeError('虚拟手柄安装仍在运行，请等待原任务结束。')
        if getattr(self,'_program_updates',None) is not None and self._program_updates.busy:raise RuntimeError('程序版本操作仍在进行，请查询或取消原任务。')
        if self._plugins is not None and self._plugins.busy:raise RuntimeError('插件操作仍在处理，请等待原任务结束。')
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
        from .agent_templates import TemplateTransaction
        TemplateTransaction(self.data).assert_clear()
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
            if self._run_lease is not None:
                self._execution = OneDragonExecution(self.ctx, run_lease=self._run_lease, rotation_guard=self._rotation_guard)
            elif getattr(self, '_recovery_host', None) is not None:
                self._execution = OneDragonExecution(self.ctx, recovery_host=self._recovery_host)
            else:
                self._execution = OneDragonExecution(self.ctx)
        return self._execution.initialize(server, game_path, region,instance_idx)

    def execution_status(self) -> dict:
        if self._execution is None:
            return dict(state="idle", busy=False, ready=False, job_id="", error="")
        result=self._execution.status()
        manager=getattr(self,'_mcp_service',None)
        if manager is not None and manager.owned_job==result.get('job_id'):
            result['mcp_run']=manager.run_description()
        if manager is not None:result['mcp_events']=manager.actions.events()
        return result

    @property
    def mcp_service(self):
        if self._mcp_service is None:
            from .onedragon_mcp import McpService
            self._mcp_service=McpService(self)
        return self._mcp_service

    def mcp_start(self,port=23001):return self.mcp_service.start(port)
    def mcp_status(self):return self.mcp_service.status()
    def mcp_grant(self,budget=None,timeout_seconds=600,resource_confirmation=None,expires_seconds=900):
        return self.mcp_service.authorize(budget,timeout_seconds,resource_confirmation,expires_seconds)
    def mcp_revoke(self):return self.mcp_service.revoke()
    def mcp_stop(self):return self.mcp_service.stop()
    def mcp_edit_enable(self,scopes):return self.mcp_service.edit_enable(scopes)
    def mcp_edit_disable(self):return self.mcp_service.edit_disable()
    def mcp_edit_restore(self,kind,id):return self.mcp_service.edit_restore(kind,id)
    def mcp_action_prepare(self,descriptor):return self.mcp_service.action_prepare(descriptor)
    def mcp_action_enable(self,scopes,budget=None,resource_confirmation=None,timeout_seconds=600):
        return self.mcp_service.action_enable(scopes,budget,resource_confirmation,timeout_seconds)
    def mcp_action_disable(self):return self.mcp_service.action_disable()
    def mcp_events_ack(self,sequence):return self.mcp_service.events_ack(sequence)

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

    def start_application(self,app_id:str,budget:dict|None=None,timeout_seconds:int=600,resource_confirmation:str|None=None,
                          close_game_on_success:bool=False,finish_after_battle:bool=False,entry_handoff:dict|None=None) -> dict:
        self._assert_idle()
        if self._environment_changed: raise RuntimeError("环境或模型已修改，请重新连接工作进程后准备。")
        if self._execution is None:raise RuntimeError("请先初始化运行环境。")
        extra={'close_game_on_success':close_game_on_success} if close_game_on_success is not False else {}
        if finish_after_battle is not False:extra['finish_after_battle']=finish_after_battle
        if entry_handoff is not None:extra['entry_handoff']=entry_handoff
        return self._execution.start_application(app_id,fixed_entry=True,budget=budget,timeout_seconds=timeout_seconds,resource_confirmation=resource_confirmation,**extra)

    @property
    def gamepad_dependencies(self):
        if self._gamepad_dependencies is None:
            from .gamepad_install import GamepadDependencies
            self._gamepad_dependencies=GamepadDependencies(self)
        return self._gamepad_dependencies

    def gamepad_catalog(self):return self.gamepad_dependencies.catalog()
    def gamepad_install_start(self,action,revision,confirmed=False):
        return self.gamepad_dependencies.start(action,revision,confirmed)
    def gamepad_install_status(self,job_id=None):return self.gamepad_dependencies.status(job_id)
    def gamepad_install_cancel(self,job_id):return self.gamepad_dependencies.cancel(job_id)

    @property
    def source_workspace(self):
        if self._source_workspace is None:
            from .source_workspace import SourceWorkspace
            self._source_workspace=SourceWorkspace(self)
        return self._source_workspace

    def source_catalog(self):return self.source_workspace.catalog()
    def source_start(self,action,branch=None):return self.source_workspace.start(action,branch)
    def source_status(self):return self.source_workspace.status()
    def source_cancel(self):return self.source_workspace.cancel()

    @property
    def sdk_upgrade(self):
        if self._sdk_upgrade is None:
            from .sdk_upgrade import SDKUpgradeManager
            self._sdk_upgrade=SDKUpgradeManager(self)
        return self._sdk_upgrade

    def sdk_catalog(self):return self.sdk_upgrade.catalog()
    def sdk_review(self,groups=None,source_candidate=None):return self.sdk_upgrade.review(groups,source_candidate)
    def sdk_build(self,review_id,review_digest,confirmed=False,offline=True):return self.sdk_upgrade.build(review_id,review_digest,confirmed,offline)
    def sdk_refresh(self,candidate_id,confirmed=False):return self.sdk_upgrade.refresh(candidate_id,confirmed)
    def sdk_activate(self,candidate_id,selection_revision,confirmed=False):return self.sdk_upgrade.activate(candidate_id,selection_revision,confirmed)
    def sdk_rollback(self,selection_revision,confirmed=False):return self.sdk_upgrade.rollback(selection_revision,confirmed)
    def sdk_activation_review(self):return self.sdk_upgrade.activation_review()
    def sdk_recover_activation(self,journal_digest,choice='before',confirmed=False):return self.sdk_upgrade.recover_activation(journal_digest,choice,confirmed)
    def sdk_migrate_stable(self,old_revision,old_defaults_digest,selection_revision,confirmed=False,source_proof=None):
        return self.sdk_upgrade.migrate_stable(old_revision,old_defaults_digest,selection_revision,confirmed,
            source_proof=source_proof)
    def sdk_prepare_stable(self,old_revision,old_defaults_digest,selection_revision,source_proof):
        return self.sdk_upgrade.migrate_stable(old_revision,old_defaults_digest,selection_revision,True,
            source_proof=source_proof,prepare_only=True)
    def sdk_prepare_tool(self):return self.sdk_upgrade.prepare_tool()
    def sdk_status(self,job_id=None):return self.sdk_upgrade.status(job_id)
    def sdk_cancel(self):return self.sdk_upgrade.cancel()

    @property
    def environment_tools(self):
        if self._environment_tools is None:
            from .environment_tools import EnvironmentTools
            self._environment_tools=EnvironmentTools(self)
        return self._environment_tools

    def environment_tools_catalog(self):return self.environment_tools.catalog()
    def environment_tools_start(self,action,revision):return self.environment_tools.start(action,revision)
    def environment_tools_status(self,job_id=None):return self.environment_tools.status(job_id)
    def environment_tools_cancel(self,job_id):return self.environment_tools.cancel(job_id)
    def environment_tools_apply(self,job_id,value,revision):return self.environment_tools.apply(job_id,value,revision)

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
        if getattr(self,'_global_capture',None) is not None:self._global_capture.close()
        if getattr(self,'_mcp_service',None) is not None:self._mcp_service.close()
        if getattr(self,'_environment_tools',None) is not None:self._environment_tools.close()
        if getattr(self,'_source_workspace',None) is not None:self._source_workspace.close()
        if getattr(self,'_sdk_upgrade',None) is not None:self._sdk_upgrade.close()
        if getattr(self,'_gamepad_dependencies',None) is not None:self._gamepad_dependencies.close()
        if getattr(self,'_program_updates',None) is not None:self._program_updates.close()
        if self._plugins is not None:self._plugins.close()
        if self._pip is not None:self._pip.close()
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
            trace=getattr(ctx,'debug_trace_runtime',None)
            if trace is not None:trace.close()
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
METHODS.update({'game_launch_options','game_hdr_status','game_hdr_set','game_hdr_recover'})
METHODS.update({'run_record_status','run_record_reset'})
METHODS.update({"prepare_client_close", "start_client_close"})
METHODS.update({'mcp_action_prepare','mcp_action_enable','mcp_action_disable','mcp_events_ack'})
METHODS.update({"route_locate", "route_capture_start", "route_capture_status", "route_capture_cancel", "route_capture_pause"})
METHODS.update({"map_edit", "map_archives", "map_restore", "map_recover"})
METHODS.update({"map_capture_start", "map_capture_status", "map_capture_pause", "map_capture_cancel"})
METHODS.update({'pip_settings','pip_save','pip_start','pip_status','pip_dismiss','pip_activate','pip_stop'})
METHODS.update({'trace_configure','trace_snapshot','trace_capture'})
METHODS.update({'global_capture_status','global_capture_capture'})
METHODS.update({'plugin_catalog','plugin_preview','plugin_status','plugin_install','plugin_enabled','plugin_remove','plugin_archives','plugin_restore','plugin_directory'})
METHODS.update({'program_catalog','program_check','program_download','program_import','program_restore','program_status','program_cancel'})
METHODS.add('schedule_review')
METHODS.update({'agent_template_catalog','agent_template_open','agent_template_import','agent_template_side','agent_template_save','agent_template_remove',
                'agent_template_restore','agent_template_recover','agent_template_preferences','agent_template_preferences_save','agent_template_capture','agent_template_view'})
METHODS.update({'gamepad_catalog','gamepad_install_start','gamepad_install_status','gamepad_install_cancel'})
METHODS.update(SDK_CONTROL_METHODS)
METHODS.update({'mcp_start','mcp_status','mcp_grant','mcp_revoke','mcp_stop','policy_remove','policy_restore'})
METHODS.update({'mcp_edit_enable','mcp_edit_disable','mcp_edit_restore','environment_tools_catalog','environment_tools_start','environment_tools_status','environment_tools_cancel','environment_tools_apply'})
