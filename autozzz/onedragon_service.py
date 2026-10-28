"""在上游解释器内查询应用并管理条件策略；不发送游戏输入。"""
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
    def __init__(self, source: Path, data: Path):
        self.source, self.data = source.resolve(), data.resolve()
        sys.path.insert(0, str(self.source / "src"))
        from one_dragon.utils import os_utils
        os_utils.set_work_dir(self.data)
        self._ctx: Any = None

    @property
    def ctx(self) -> Any:
        if self._ctx is None:
            from zzz_od.context.zzz_context import ZContext
            ctx = ZContext()
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
                                  configurable=type(factory).create_config is not ApplicationFactory.create_config)
                             for key, factory in factories.items()],
            "failures": [dict(path=str(path), error=error) for path, error in ctx.factory_manager.scan_failures],
            "ready_for_game": bool(ctx.ready_for_application),
        }

    def _config(self, app_id: str) -> Any:
        if app_id == "battle_assistant":
            return self.ctx.battle_assistant_config
        if app_id == "teams":
            return self.ctx.team_config
        if app_id not in self.ctx.run_context._application_factory_map:
            raise ValueError(f"应用未注册：{app_id}")
        from one_dragon.base.operation.application.application_const import DEFAULT_GROUP_ID
        return self.ctx.run_context.get_config(app_id, self.ctx.current_instance_idx, DEFAULT_GROUP_ID)

    def configuration(self, app_id: str) -> dict:
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

    def set_battle_options(self, values: dict) -> dict:
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
        config = self.ctx.battle_assistant_config
        if {"auto_battle_config", "use_merged_file"} & set(values):
            name = values.get("auto_battle_config", config.auto_battle_config)
            merged = values.get("use_merged_file", config.use_merged_file)
            self._policy_path(name, merged)
            self.policy_validate(name, merged)
        # 先校验整批字段，再一次写入；避免前半部分成功而后半部分失败。
        old = config.data.copy()
        try:
            config.data.update(values)
            config.save()
        except Exception:
            config.data = old
            raise
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

    def _policy_path(self, name: str, merged: bool = True) -> Path:
        self._name(name)
        from one_dragon.base.conditional_operation.loader import ConditionalOperatorLoader
        path = Path(ConditionalOperatorLoader.get_yaml_file_path(["auto_battle"], name, merged)).resolve()
        directory = (self.data / "config" / "auto_battle").resolve()
        if path.parent != directory or not path.is_file():
            raise ValueError(f"条件策略不存在：{name}")
        return path

    def policies(self) -> list[dict]:
        directory = self.data / "config" / "auto_battle"
        names = set()
        for path in directory.glob("*.yml"):
            name = path.name.removesuffix(".yml").removesuffix(".merged").removesuffix(".sample")
            if not name.startswith("autozzz-check-"):
                names.add(name)
        return [dict(name=name, format=self._policy_path(name).name,
                     custom=not (directory / (name + ".merged.yml")).exists()
                            and not (directory / (name + ".sample.yml")).exists())
                for name in sorted(names)]

    def policy_read(self, name: str, merged: bool = True) -> dict:
        path = self._policy_path(name, merged)
        return dict(name=name, filename=path.name, content=path.read_text(encoding="utf-8"))

    def policy_validate(self, name: str, merged: bool = True) -> dict:
        self._policy_path(name, merged)  # 上游会在缺失时回退；桥接层必须先拒绝错误名称。
        from zzz_od.auto_battle.auto_battle_operator import AutoBattleOperator
        from one_dragon.base.conditional_operation.operator import ConditionalOperator
        operator = AutoBattleOperator(self.ctx.auto_battle_context, "auto_battle", name, merged)
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

    def policy_save(self, name: str, content: str) -> dict:
        name = self._name(name)
        if not isinstance(content, str) or len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("策略必须为不超过 1 MiB 的 YAML 文本。")
        directory = self.data / "config" / "auto_battle"
        if any((directory / (name + suffix)).exists() for suffix in (".sample.yml", ".merged.yml")):
            raise ValueError("内置策略请使用新名称另存，保留原策略及作者信息。")
        temporary_name = "autozzz-check-" + uuid.uuid4().hex
        temporary = directory / (temporary_name + ".yml")
        target = directory / (name + ".yml")
        if target.resolve().parent != directory.resolve():
            raise ValueError("策略文件路径指向数据目录外。")
        temporary.write_text(content, encoding="utf-8")
        try:
            validation = self.policy_validate(temporary_name, merged=False)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        validation["name"] = name
        return validation

    def policy_check(self, content: str) -> dict:
        if not isinstance(content, str) or len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("策略必须为不超过 1 MiB 的 YAML 文本。")
        directory = self.data / "config" / "auto_battle"
        name = "autozzz-check-" + uuid.uuid4().hex
        temporary = directory / (name + ".yml")
        temporary.write_text(content, encoding="utf-8")
        try:
            result = self.policy_validate(name, merged=False)
            result["name"] = "草稿"
            return result
        finally:
            temporary.unlink(missing_ok=True)

    def close(self) -> None:
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
           "policy_save", "policy_check", "set_battle_options"}
