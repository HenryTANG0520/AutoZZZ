"""从实际上游配置收集应用依赖，在绑定游戏前验证并加载。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


class PreparationCancelled(RuntimeError):
    pass


class ApplicationRequirements:
    def __init__(self, ctx: Any, data: Path, store: Any):
        self.ctx, self.data, self.store = ctx, Path(data), store
        self.policies: set[tuple[str, str]] = set()
        self.models: set[str] = set()
        self.contexts: set[str] = set()
        self.challenges: dict[str, str] = {}
        self.routes: list[str] = []
        self.scripts: list[dict] = []
        self.shortcuts: dict[str, str] = {}
        self.resource_actions: list[dict] = []
        self.resource_settings: dict[str, str] = {}
        self.uses_charge = False
        self.uses_hunt = False

    def policy(self, name: str, directory: str = "auto_battle") -> None:
        from .onedragon_service import OneDragonService
        self.policies.add((directory, OneDragonService._name(name)))

    def team_policy(self, index: int, fallback: str | None = None) -> None:
        if index == -1 and fallback is not None:
            self.policy(fallback)
            return
        teams = self.ctx.team_config.team_list
        if type(index) is not int or not 0 <= index < len(teams):
            raise ValueError("预编队伍位置无效。")
        self.policy(teams[index].auto_battle)

    def all_team_policies(self) -> None:
        for index in range(len(self.ctx.team_config.team_list)):
            self.team_policy(index)

    def challenge(self, directory: str, name: str) -> Any:
        from .onedragon_service import OneDragonService
        OneDragonService._name(name)
        folder = self.data / "config" / directory
        if not any((folder / (name + suffix)).is_file() for suffix in (".yml", ".sample.yml")):
            raise ValueError("挑战方案不存在：" + name)
        self.challenges[directory] = name
        return self.store.get(directory + ":" + name)

    def collect(self, applications: list[str]) -> None:
        self.ctx.push_service.reload_config()
        notify = self.ctx.notify_config
        if type(notify.enable_notify) is not bool: raise ValueError("通知开关类型无效。")
        if notify.enable_notify:
            if type(self.ctx.push_service.push_config.send_image) is not bool: raise ValueError("通知截图开关类型无效。")
            from .onedragon_notify import validate_modes
            validate_modes([dict(app_id=key, lifecycle=notify.get_app_lifecycle_mode(key), detail=notify.get_app_detail_mode(key)) for key in notify.app_map], notify.app_map)
            if not self.ctx.push_service.available_channels():
                raise ValueError("通知已启用，但没有完整且已启用的发送渠道。")
        self.uses_charge = "charge_plan" in applications or (
            "coffee" in applications and self.store.get("coffee").run_charge_plan_afterwards)
        if self.uses_charge:
            self._charge()
        for app_id in applications:
            if app_id == "auto_battle":
                self.policy(self.ctx.battle_assistant_config.auto_battle_config)
            elif app_id == "dodge_assistant":
                self.policy(self.ctx.battle_assistant_config.dodge_assistant_config, "dodge")
            elif app_id == "intel_board":
                config = self.store.get(app_id)
                self.team_policy(config.predefined_team_idx, config.auto_battle_config)
                self.models.add("lost_void_det")
            elif app_id == "lost_void":
                config = self.store.get(app_id)
                challenge = self.challenge("lost_void_challenge", config.challenge_config)
                if config.mission_name == "矩阵行动":
                    self.team_policy(0 if challenge.predefined_team_idx == -1 else challenge.predefined_team_idx)
                else:
                    self.team_policy(challenge.predefined_team_idx, challenge.auto_battle)
                    if config.mission_name == "特遣调查" and challenge.choose_team_by_priority:
                        self.all_team_policies()
                self.contexts.add(app_id)
                self.models.add("lost_void_det")
            elif app_id == "withered_domain":
                config = self.store.get(app_id)
                challenge = self.challenge("hollow_zero_challenge", config.challenge_config)
                self.policy(challenge.auto_battle)
                self.contexts.add(app_id)
                self.models.add("hollow_zero_event")
            elif app_id == "shiyu_defense":
                # 固定版本会扫描全编队并按弱点选队，不读取旧版 team_list 筛选。
                self.all_team_policies()
            elif app_id == "world_patrol":
                config = self.store.get(app_id)
                self.policy(config.auto_battle)
                self._world_patrol(config)
            elif app_id == "commission_assistant":
                from .onedragon_assistant import validate_shortcuts
                config=self.store.get(app_id)
                validate_shortcuts(config)
                self.shortcuts=dict(dodge=config.dodge_switch,battle=config.auto_battle_switch)
                self.policy(config.auto_battle)
                self.policy(config.dodge_config,"dodge")
                hollow=self.store.get("withered_domain")
                challenge=self.challenge("hollow_zero_challenge",hollow.challenge_config)
                self.policy(challenge.auto_battle)
                self.contexts.add("withered_domain")
                self.models.add("hollow_zero_event")
            elif app_id == "life_on_line":
                config=self.store.get(app_id)
                if config.predefined_team_idx!=-1:
                    teams=self.ctx.team_config.team_list
                    if type(config.predefined_team_idx)is not int or not 0<=config.predefined_team_idx<len(teams):raise ValueError("预编队伍位置无效。")
                self.scripts.append(dict(kind="key_sim",name="真拿命验收",app_id=app_id))
            elif app_id == "operation_debug":
                self.scripts.append(dict(kind="operation_template",name=self.store.get(app_id).operation_template))
            elif app_id == "notorious_hunt":
                from zzz_od.application.charge_plan.charge_plan_config import ChargePlanConfig
                config = self.store.get(app_id)
                if not config.plan_list or not any(plan.plan_times > 0 for plan in config.plan_list):
                    raise ValueError("请先添加恶名狩猎计划并设置挑战次数。")
                for field in self.store._describe(app_id, config)["fields"]:
                    if field["kind"] != "plans":self.store._validate_scalar(field, field["value"])
                for plan in config.plan_list:
                    error = ChargePlanConfig.validate_item(self.ctx, plan)
                    if error:raise ValueError(error)
                    if plan.category_name != "恶名狩猎":raise ValueError("每周计划须选择恶名狩猎副本。")
                    self.team_policy(plan.predefined_team_idx, plan.auto_battle_config)
                self.uses_hunt = True
                self.models.add("lost_void_det")
            elif app_id in ("suibian_temple", "drive_disc_dismantle"):
                from .onedragon_resources import resource_actions, resource_fingerprint
                config = self.store.get(app_id)
                for field in self.store._describe(app_id, config)["fields"]:
                    if field.get("supported_in_runtime", True):
                        self.store._validate_scalar(field, field["value"])
                self.resource_actions.extend(resource_actions(app_id, config))
                self.resource_settings[app_id] = resource_fingerprint(config)

    def _charge(self) -> None:
        from .onedragon_charge import ChargeBudgetControl
        from zzz_od.application.charge_plan.charge_plan_config import ChargePlanConfig
        config = self.store.get("charge_plan")
        if config.restore_charge != "不使用":
            raise ValueError("本轮仅使用现有电量，请将补充电量方式设为“不使用”。")
        plans = config.plan_list + ([config.combat_simulation_double_reward_config] if config.double_reward else [])
        if not plans:
            raise ValueError("请先添加刷本计划。")
        self.ctx.compendium_service.reload()
        for plan in plans:
            if plan.category_name not in ChargeBudgetControl.CATEGORIES:
                raise ValueError("该训练模式的预算适配尚未完成。")
            error = ChargePlanConfig.validate_item(self.ctx, plan)
            if error:
                raise ValueError(error)
            self.team_policy(plan.predefined_team_idx, plan.auto_battle_config)
            if plan.category_name == "恶名狩猎":
                self.uses_hunt = True
                self.models.add("lost_void_det")

    def _world_patrol(self, config: Any) -> None:
        from .onedragon_library_rules import coordinate, route_selection
        service = self.ctx.world_patrol_service
        service.load_data()
        routes = [route for area in service.area_list for route in service.get_world_patrol_routes_by_area(area)]
        routes = route_selection(routes)
        if config.route_list:
            selected = next((item for item in service.get_world_patrol_route_lists() if item.name == config.route_list), None)
            if selected is None:
                raise ValueError("路线列表不存在：" + config.route_list)
            routes = route_selection(routes, selected)
        if not routes:
            raise ValueError("所选路线列表没有可运行的路线。")
        for route in routes:
            large_map = service.get_route_large_map(route)
            if large_map is None or service.get_route_tp_icon(route) is None: raise ValueError("路线地图或传送点不完整：" + route.full_id)
            height, width = large_map.road_mask.shape[:2]
            for operation in route.op_list:
                if operation.op_type != "move" or not isinstance(operation.data, list) or len(operation.data) != 2: raise ValueError("路线操作无效：" + route.full_id)
                coordinate(operation.data[0], width); coordinate(operation.data[1], height)
        self.routes = [route.full_id for route in routes]

    def load(self, cancelled: Callable[[], bool], progress: Callable[[str], None]) -> None:
        from one_dragon.base.conditional_operation.loader import ConditionalOperatorLoader
        if cancelled():raise PreparationCancelled("应用准备已取消。")
        def step(label: str, action: Callable[[], None]) -> None:
            if cancelled():raise PreparationCancelled("应用准备已取消。")
            progress(label)
            action()
            if cancelled():raise PreparationCancelled("应用准备已取消。")

        # 先核实所有策略文件，避免下载完模型才发现队伍指向无效策略。
        for directory, name in sorted(self.policies):
            selected = ConditionalOperatorLoader.get_yaml_file_path([directory], name, self.ctx.battle_assistant_config.use_merged_file)
            if not selected:raise ValueError("战斗策略不存在：" + name)
            path = Path(selected).resolve()
            if path.parent != (self.data / "config" / directory).resolve() or not path.is_file():
                raise ValueError("战斗策略不存在：" + name)
        from .onedragon_scripts import compile_key_sim,compile_operation_template
        for script in self.scripts:
            def compile(script=script):
                operations=compile_key_sim(self.ctx,script["name"],script["app_id"]) if script["kind"]=="key_sim" else compile_operation_template(self.ctx,script["name"])
                try:script["operations"]=len(operations)
                finally:
                    for operation in operations:operation.dispose()
            step("键鼠指令 · "+script["name"],compile)
        if "lost_void" in self.contexts:
            step("迷失之地 · 挑战方案与鸣徽", self.ctx.lost_void.init_before_run)
        elif "lost_void_det" in self.models:
            step("目标识别模型", self.ctx.lost_void.init_lost_void_det_model)
        if "withered_domain" in self.contexts:
            step("枯萎之都 · 挑战方案与事件", self.ctx.withered_domain.init_before_run)
            step("空洞事件模型", self.ctx.withered_domain.map_service.init_event_yolo)
        if self.policies:
            battle=self.ctx.auto_battle_context
            old={id(operator):operator for operator in battle._op_cache.values()}
            if battle.auto_op is not None:old[id(battle.auto_op)]=battle.auto_op
            for operator in old.values():operator.dispose()
            battle.auto_op=None
            battle._op_cache.clear()
        for directory, name in sorted(self.policies):
            step("战斗策略 · " + name, lambda directory=directory, name=name: self.ctx.auto_battle_context.init_auto_op(name, directory))
        if cancelled():raise PreparationCancelled("应用准备已取消。")
        progress("")

    def summary(self) -> dict:
        return dict(policies=[dict(directory=directory, name=name) for directory, name in sorted(self.policies)],
                    models=sorted(self.models | ({"flash_classifier"} if self.policies else set())),
                    challenges=dict(self.challenges), routes=list(self.routes),scripts=list(self.scripts),shortcuts=dict(self.shortcuts),
                    resource_actions=list(self.resource_actions), resource_settings=dict(self.resource_settings), uses_hunt=self.uses_hunt)
