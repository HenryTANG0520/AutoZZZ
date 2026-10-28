"""将上游配置转换为有类型、有选项的原生表单，保留专用保存方法。"""
from __future__ import annotations

import ast
from datetime import datetime
from enum import Enum
import inspect
import math
from pathlib import Path
import textwrap
from typing import Any

from .config_transaction import atomic_apply, config_revision
from .run_lock import profile_lock


LABELS = {
    "loop": "循环执行", "daily_reset_plan_times": "每日清零已运行次数", "double_reward": "优先双倍活动",
    "combat_simulation_double_reward_config": "双倍活动计划", "restore_charge": "补充电量方式",
    "transport_point": "传送地点", "run_charge_plan_afterwards": "饮品后运行体力计划",
    "agent_name_1": "宣传员首选", "agent_name_2": "宣传员备选", "selected_sign": "签到地点",
    "dismantle_level": "拆解品质上限", "dismantle_abandon": "包含已弃置驱动盘",
    "pause_in_background": "失焦暂停", "dialog_click_interval": "对话间隔（秒）", "story_mode": "剧情处理",
    "dialog_option": "对话选项", "dodge_config": "闪避策略", "dodge_switch": "闪避助手快捷键",
    "auto_battle": "战斗策略", "auto_battle_switch": "战斗助手快捷键", "sleep_after_empty_screen": "空画面等待（秒）",
    "operation_template": "操作模板", "repeat_enabled": "循环调试", "frequency_second": "截图间隔（秒）",
    "length_second": "截图保留时间（秒）", "key_save": "截图保存快捷键", "dodge_detect": "闪避检测",
    "screenshot_before_key": "保留按键前截图", "mini_map_angle_detect": "小地图角度检测",
    "daily_plan_times": "每日次数", "weekly_plan_times": "每周次数", "extra_task": "额外目标",
    "extra_exit": "额外目标退出方式", "mission_name": "目标任务", "challenge_config": "挑战方案",
    "predefined_team_idx": "游戏内预编队伍", "auto_battle_config": "战斗策略", "exp_grind_mode": "经验刷取模式",
    "weekly_challenge_start_weekday": "每周挑战起始日", "critical_max_node_idx": "剧变节点上限",
    "route_list": "巡逻路线组", "ui_disappear_action": "界面消失处理", "ui_disappear_seconds": "界面消失等待（秒）",
    "route_retry_times": "路线重试次数", "route_retry_action": "再次卡住时", "daily_loop_count": "每日巡逻轮数",
    "loop_interval_seconds": "巡逻轮次间隔（秒）", "dodge_assistant_config": "闪避策略",
    "screenshot_interval": "识别间隔（秒）", "background_mode": "后台操作", "use_merged_file": "使用合并策略",
    "auto_ultimate_enabled": "自动终结技", "plan_list": "刷本计划", "team_list": "预编队伍", "codes": "自定义兑换码",
    "app_list": "执行顺序", "choose_team_by_priority": "按优先级选队", "manually_choose_agent": "手动选择角色",
    "team_info": "角色顺序", "artifact_priority_new": "使用新版鸣徽优先级", "artifact_priority": "鸣徽优先级",
    "artifact_priority_in_battle": "战斗中鸣徽优先级", "artifact_priority_2": "次级鸣徽优先级",
    "region_type_priority": "区域优先级", "period_buff_no": "周期增益", "buy_only_priority_2": "商店刷新次数",
    "store_gold": "保留硬币", "store_blood": "商店用血量交换", "store_blood_min": "保留血量下限",
    "investigation_strategy": "调查策略", "chase_new_mode": "优先新鸣徽",
    "resonium_priority": "鸣徽优先级", "event_priority": "事件优先级", "path_finding": "寻路方式",
    "target_agents": "目标角色", "go_in_1_step": "一步可达时优先", "waypoint": "必经节点", "avoid": "避开节点",
    "buy_only_priority": "仅购买优先鸣徽",
    "auto_manage_enabled":"自动托管", "good_goods_purchase_enabled":"好物铺采购", "craft_drag_times":"制造下拉次数上限",
    "yum_cha_sin":"每日饮茶", "yum_cha_sin_period_refresh":"刷新定期采办", "adventure_duration":"派遣时长",
    "adventure_mission_1":"任务 1", "adventure_mission_2":"任务 2", "adventure_mission_3":"任务 3", "adventure_mission_4":"任务 4",
    "boo_box_purchase_enabled":"购买邦布", "boo_box_adventure_price":"游历最低价格", "boo_box_craft_price":"制造最低价格", "boo_box_sell_price":"售卖最低价格",
    "pawnshop_omnicoin_enabled":"使用百宝通", "pawnshop_omnicoin_priority":"百宝通物品顺序",
    "pawnshop_crest_enabled":"使用云纹徽", "pawnshop_crest_priority":"云纹徽物品顺序", "pawnshop_crest_unlimited_denny_enabled":"兑换不限购丁尼",
}
READ_ONLY = {"last_daily_reset_dt", "skip_plan"}
FORM_GROUPS = {
    "charge_plan": (("执行",("loop","daily_reset_plan_times","double_reward")),("电量补充",("restore_charge",))),
    "battle_assistant": (("战斗",("auto_battle_config","use_merged_file","auto_ultimate_enabled")),
                         ("识别与输入",("screenshot_interval","background_mode","dodge_assistant_config"))),
    "commission_assistant": (("剧情",("story_mode","dialog_option","dialog_click_interval","sleep_after_empty_screen")),
                             ("战斗与闪避",("auto_battle","auto_battle_switch","dodge_config","dodge_switch","pause_in_background"))),
    "suibian_temple": (("经营",("auto_manage_enabled","good_goods_purchase_enabled","craft_drag_times")),
                       ("饮茶仙",("yum_cha_sin","yum_cha_sin_period_refresh")),
                       ("游历",("adventure_duration","adventure_mission_1","adventure_mission_2","adventure_mission_3","adventure_mission_4")),
                       ("邦巢",("boo_box_purchase_enabled","boo_box_adventure_price","boo_box_craft_price","boo_box_sell_price")),
                       ("德丰大押",("pawnshop_omnicoin_enabled","pawnshop_omnicoin_priority","pawnshop_crest_enabled","pawnshop_crest_priority","pawnshop_crest_unlimited_denny_enabled"))),
}


def _plain(value: Any) -> Any:
    if value is None or type(value) in (str, int, float, bool): return value
    if isinstance(value, Enum): return value.name
    if isinstance(value, (list, tuple)): return [_plain(item) for item in value]
    if isinstance(value, dict): return {str(key): _plain(item) for key, item in value.items()}
    if hasattr(value, "to_dict"): return value.to_dict()
    return {key: _plain(item) for key, item in vars(value).items() if not key.startswith("_")}


def _options(enum: type[Enum], use_name: bool = False) -> list[dict]:
    result = []
    for item in enum:
        value = item.value
        label = getattr(value, "label", str(value))
        stored = item.name if use_name else getattr(value, "value", value)
        if type(stored) in (str, int, float, bool):
            result.append(dict(label=label, value=stored))
    return result


def _getter_metadata(prop: property) -> tuple[str | None, list[dict]]:
    source = ast.parse(textwrap.dedent(inspect.getsource(prop.fget)))
    function = source.body[0]
    doc = ast.get_docstring(function) or ""
    label = doc.splitlines()[0].replace("-开关", "").replace("-", " · ") if doc else None
    for node in ast.walk(function):
        if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute) or node.func.attr!="get" or len(node.args)<2:
            continue
        # 只读取字段默认值的枚举，不把旧配置迁移表达式里的枚举当作当前字段类型。
        chain, base = [], node.args[1]
        while isinstance(base, ast.Attribute):
            chain.insert(0, base.attr)
            base = base.value
        if not isinstance(base, ast.Name): continue
        enum = prop.fget.__globals__.get(base.id)
        if isinstance(enum, type) and issubclass(enum, Enum) and chain and chain[0] in enum.__members__:
            return label, _options(enum, use_name=chain[-1] == "name")
    return label, []


def _field(name: str, value: Any, prop: property | None = None) -> dict:
    doc, options = _getter_metadata(prop) if prop is not None else (None, [])
    label = LABELS.get(name) or doc
    if not label:
        raise ValueError(f"尚未定义配置字段的显示名称：{name}")
    annotation = str(prop.fget.__annotations__.get("return", "")) if prop else ""
    kind = "bool" if type(value) is bool else "float" if "float" in annotation or type(value) is float else "int" if type(value) is int else "lines" if isinstance(value, list) else "str"
    result = dict(name=name, label=label, kind=kind, value=_plain(value), options=options)
    if options: result["kind"] = "enum"
    if kind in ("float", "int"):
        result["minimum"] = .001 if name in ("screenshot_interval", "frequency_second") else -1 if name == "predefined_team_idx" else 0
    return result


class ConfigurationStore:
    def __init__(self, service: Any):
        self.service, self.ctx, self.root = service, service.ctx, service.data

    def get(self, app_id: str) -> Any:
        # 配置编辑不能依赖仅按 mtime 判断的上游进程内缓存。
        from one_dragon.base.config.yaml_operator import cached_yaml_data
        cached_yaml_data.clear()
        if app_id == "battle_assistant":
            from zzz_od.application.battle_assistant.battle_assistant_config import BattleAssistantConfig
            return BattleAssistantConfig(self.ctx.current_instance_idx)
        if app_id == "teams":
            from zzz_od.config.team_config import TeamConfig
            return TeamConfig(self.ctx.current_instance_idx)
        if app_id == "_group":
            self.ctx.app_group_manager.set_default_apps(self.ctx.run_context.default_group_apps)
            self.ctx.app_group_manager.clear_config_cache()
            return self.ctx.app_group_manager.get_one_dragon_group_config(self.ctx.current_instance_idx)
        if app_id.startswith("lost_void_challenge:"):
            from zzz_od.application.hollow_zero.lost_void.lost_void_challenge_config import LostVoidChallengeConfig
            return LostVoidChallengeConfig(self.service._name(app_id.split(":", 1)[1]))
        if app_id.startswith("hollow_zero_challenge:"):
            from zzz_od.hollow_zero.hollow_zero_challenge_config import HollowZeroChallengeConfig
            return HollowZeroChallengeConfig(self.service._name(app_id.split(":", 1)[1]))
        factory = self.ctx.run_context._application_factory_map.get(app_id)
        if factory is None: raise ValueError("应用未注册。")
        return factory.create_config(self.ctx.current_instance_idx, "one_dragon")

    def options(self) -> dict:
        from zzz_od.application.charge_plan.charge_plan_config import CardNumEnum
        from zzz_od.application.notorious_hunt.notorious_hunt_config import NotoriousHuntLevelEnum
        from zzz_od.game_data.agent import AgentEnum, DmgTypeEnum
        challenges = []
        for folder, label in (("lost_void_challenge", "迷失之地"), ("hollow_zero_challenge", "枯萎之都")):
            for name in sorted({p.name.removesuffix(".yml").removesuffix(".sample") for p in (self.root/"config"/folder).glob("*.yml")}):
                challenges.append(dict(id=folder+":"+name, name=label+" · "+name, configurable=True))
        return dict(training=self.compendium(), policies=[dict(label=p["name"], value=p["name"]) for p in self.service.policies()],
                    teams=[dict(label=f"{t.idx+1} · {t.name}", value=t.idx) for t in self.get("teams").team_list],
                    agents=[dict(label="未指定", value="unknown")]+[dict(label=a.value.agent_name, value=a.value.agent_id) for a in AgentEnum],
                    weaknesses=[dict(label=d.value, value=d.name) for d in DmgTypeEnum],
                    cards=_options(CardNumEnum), levels=_options(NotoriousHuntLevelEnum), challenges=challenges)

    def _choices(self, app_id: str, field: dict) -> None:
        name = field["name"]
        if name in ("auto_battle", "auto_battle_config"):
            field["options"] = [dict(label=p["name"], value=p["name"]) for p in self.service.policies()]
            field["kind"] = "enum"
        if name in ("dodge_config", "dodge_assistant_config", "operation_template", "route_list", "challenge_config"):
            folder = {"dodge_config": "dodge", "dodge_assistant_config": "dodge", "operation_template": "auto_battle_operation",
                      "route_list": "world_patrol_route_list", "challenge_config": "lost_void_challenge" if app_id == "lost_void" else "hollow_zero_challenge"}[name]
            names = sorted({p.name.removesuffix(".yml").removesuffix(".sample") for p in (self.root/"config"/folder).glob("*.yml")})
            field.update(kind="enum", options=[dict(label=n, value=n) for n in names])
        if name == "selected_sign":
            field.update(kind="enum", options=[dict(label=label, value=value) for label, value in
                         (("吼吼饼铺", "hou_hou_bakery"), ("卦象集录", "trigrams_collection"), ("刮刮卡", "scratch_card"))])
        if name in ("agent_name_1", "agent_name_2"):
            from zzz_od.game_data.agent import AgentEnum
            field.update(kind="agent", options=[dict(label=n, value=n) for n in ["随机"]+[a.value.agent_name for a in AgentEnum]])
        if name == "predefined_team_idx":
            field.update(kind="enum", options=[dict(label="当前游戏队伍", value=-1)] +
                         [dict(label=f"{team.idx+1} · {team.name}", value=team.idx) for team in self.ctx.team_config.team_list])
        if name == "ui_disappear_action":
            field.update(kind="enum", options=[dict(label=l, value=v) for l,v in (("结束并记录", "silent_fail"), ("重启并跳过", "restart_and_skip"), ("重启并重试", "restart_and_retry"))])
        if name == "route_retry_action":
            field.update(kind="enum", options=[dict(label="跳过路线", value="skip_on_stuck_again"), dict(label="再次重试", value="retry_on_stuck_again")])
        if name == "route_list":
            names = sorted({r.name for r in self.ctx.world_patrol_service.get_world_patrol_route_lists()})
            field.update(kind="enum", options=[dict(label="全部路线",value="")]+[dict(label=n,value=n) for n in names])
        if name in ("team_info", "target_agents"):
            from zzz_od.game_data.agent import AgentEnum, AgentTypeEnum
            field.update(kind="agents", options=[dict(label="未指定", value="unknown")]+[dict(label=a.value.agent_name, value=a.value.agent_id) for a in AgentEnum])
            if name=="target_agents":
                field["options"][0]["value"] = None
                field["options"] += [dict(label=a.value,value=a.value) for a in AgentTypeEnum if a!=AgentTypeEnum.UNKNOWN]
        if name in ("pawnshop_omnicoin_priority","pawnshop_crest_priority"):
            from zzz_od.application.suibian_temple.suibian_temple_config import PawnshopOmnicoinGoods, PawnshopCrestGoods
            field.update(kind="priority",options=_options(PawnshopOmnicoinGoods if "omnicoin" in name else PawnshopCrestGoods,use_name=True))

    def _describe(self, app_id: str, config: Any) -> dict:
        fields = []
        for name, prop in inspect.getmembers(type(config), lambda item: isinstance(item, property)):
            if name.startswith("_") or prop.fset is None or name in READ_ONLY or name.endswith("_str"): continue
            field = _field(name, getattr(config, name), prop)
            self._choices(app_id, field)
            if name == "combat_simulation_double_reward_config": field["kind"] = "plan"
            fields.append(field)
        ranges = {"weekly_challenge_start_weekday": (1,7), "critical_max_node_idx": (1,10),
                  "screenshot_interval": (.01,1), "store_blood_min": (0,100)}
        for field in fields:
            if field["name"] in ranges:
                field["minimum"], field["maximum"] = ranges[field["name"]]
        ranks = {}
        for group,names in FORM_GROUPS.get(app_id,()):
            for name in names:ranks[name]=(len(ranks),group)
        for field in fields:
            if field["name"] in ranks:
                field["group"] = ranks[field["name"]][1]
                prefix = field["group"]+" · "
                if field["label"].startswith(prefix):field["label"]=field["label"][len(prefix):]
        fields.sort(key=lambda field:ranks.get(field["name"],(len(ranks),""))[0])
        target = config.user_config if app_id == "redemption_code" else config
        if app_id in ("charge_plan", "notorious_hunt"):
            fields.append(dict(name="plan_list", label="刷本计划", kind="plans", value=[p.to_dict() for p in config.plan_list], options=[]))
        if app_id == "teams":
            fields.append(dict(name="team_list", label="游戏内预编队伍", kind="teams", value=_plain(config.team_list), options=[]))
        if app_id == "shiyu_defense":
            # 保留旧数据的读写兼容；当前上游按游戏扫描结果选队，不使用此列表。
            fields.append(dict(name="team_list", label="防卫战候选队伍", kind="defense_teams", value=_plain(config.team_list), options=[],supported_in_runtime=False))
        if app_id == "_group":
            fields.append(dict(name="app_list", label="执行顺序", kind="applications", options=[],
                               value=[dict(app_id=i.app_id, name=i.app_name, enabled=i.enabled) for i in config.app_list]))
        if app_id == "redemption_code":
            fields.append(dict(name="codes", label="自定义兑换码", kind="codes", options=[],
                               value=[dict(code=k, expires=v) for k,v in config.user_codes_dict.items()]))
        return dict(app_id=app_id, revision=config_revision(target), fields=fields)

    def describe(self, app_id: str) -> dict:
        self.service._assert_idle()
        with profile_lock(self.root):
            return self._describe(app_id, self.get(app_id))

    def compendium(self) -> dict:
        self.ctx.compendium_service.reload()
        result = {}
        for category in self.ctx.compendium_service.get_charge_plan_category_list():
            types = {}
            for mission_type in self.ctx.compendium_service.get_charge_plan_mission_type_list(category.value):
                missions = self.ctx.compendium_service.get_charge_plan_mission_list(category.value, mission_type.value)
                types[mission_type.value] = dict(label=mission_type.label,
                    missions=[dict(label=mission.label, value=mission.value) for mission in missions])
            result[category.value] = dict(label=category.label, types=types)
        return result

    def _validate_scalar(self, field: dict, value: Any) -> None:
        kind = field["kind"]
        if kind in ("enum", "agent"):
            if not any(type(value) is type(option["value"]) and value == option["value"] for option in field["options"]):
                raise ValueError(f"{field['label']}：请选择或输入有效值。")
        elif kind == "bool" and type(value) is not bool:
            raise ValueError(f"{field['label']}必须为开关值。")
        elif kind in ("int", "float"):
            if type(value) not in ((int,) if kind == "int" else (int,float)) or not math.isfinite(value) or value < field.get("minimum",0) or value > field.get("maximum",float("inf")):
                raise ValueError(f"{field['label']}超出允许范围。")
        elif kind == "agents":
            valid = {o["value"] for o in field["options"]}|{None}
            if not isinstance(value,list) or len(value)!=3 or any(a not in valid for a in value):
                raise ValueError("角色顺序需要三个有效角色或未指定位置。")
        elif kind in ("lines","priority"):
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"{field['label']}需要文本列表。")
            if kind=="priority" and (len(value)!=len(set(value)) or any(v not in {o['value'] for o in field['options']} for v in value)):
                raise ValueError(f"{field['label']}包含未知或重复物品。")
        elif kind == "str" and not isinstance(value, str):
            raise ValueError(f"{field['label']}需要文本。")

    def _plans(self, app_id: str, rows: list, existing: list) -> list:
        from zzz_od.application.charge_plan.charge_plan_config import ChargePlanItem, ChargePlanConfig, CardNumEnum
        if not isinstance(rows, list): raise ValueError("刷本计划需要列表。")
        self.ctx.compendium_service.reload()
        ids = {plan.plan_id for plan in existing}
        result, used = [], set()
        allowed = {"tab_name", "category_name", "mission_type_name", "mission_name", "level", "auto_battle_config", "run_times", "plan_times", "card_num", "predefined_team_idx", "notorious_hunt_buff_num", "plan_id"}
        policies = {p["name"] for p in self.service.policies()}
        for row in rows:
            if not isinstance(row, dict) or set(row)-allowed: raise ValueError("刷本计划包含未知字段。")
            row = row.copy()
            if row.get("plan_id") is not None and (type(row["plan_id"]) is not str or row["plan_id"] not in ids):
                raise ValueError("计划编号不能伪造，请新增计划。")
            plan = ChargePlanItem.from_dict(row)
            for name in ("tab_name", "category_name", "mission_type_name", "level", "auto_battle_config", "card_num"):
                if not isinstance(getattr(plan,name),str): raise ValueError(f"计划字段 {name} 需要文本。")
            if plan.mission_name is not None and not isinstance(plan.mission_name,str): raise ValueError("副本名称需要文本或空值。")
            if plan.plan_id in used: raise ValueError("计划编号重复。")
            used.add(plan.plan_id)
            for name in ("run_times", "plan_times"):
                if type(getattr(plan,name)) is not int or getattr(plan,name)<0: raise ValueError("计划次数必须为非负整数。")
            if type(plan.predefined_team_idx) is not int or not -1 <= plan.predefined_team_idx < 20: raise ValueError("预编队伍序号无效。")
            if plan.auto_battle_config not in policies: raise ValueError("战斗策略不存在。")
            if plan.card_num not in {item.value.value for item in CardNumEnum}: raise ValueError("怪物卡数量无效。")
            if plan.notorious_hunt_buff_num not in (1,2,3) or type(plan.notorious_hunt_buff_num) is not int: raise ValueError("恶名狩猎增益无效。")
            if plan.tab_name != "训练": raise ValueError("体力计划应位于训练页。")
            if app_id == "notorious_hunt" and plan.category_name != "恶名狩猎": raise ValueError("恶名狩猎计划不能选择其他分类。")
            error = ChargePlanConfig.validate_item(self.ctx, plan)
            if error: raise ValueError(error)
            result.append(plan)
        return result

    def save(self, app_id: str, values: dict, revision: str) -> dict:
        self.service._assert_idle()
        with profile_lock(self.root):
            config = self.get(app_id)
            description = self._describe(app_id, config)
            fields = {field["name"]: field for field in description["fields"]}
            if not isinstance(values,dict) or set(values)-set(fields): raise ValueError("配置包含未知或只读字段。")
            converted = {}
            for name, value in values.items():
                field = fields[name]
                if field["kind"] == "plans":
                    converted[name] = self._plans(app_id, value, config.plan_list)
                elif field["kind"] == "plan":
                    old = config.combat_simulation_double_reward_config
                    if not isinstance(value,dict): raise ValueError("双倍活动计划需要一条计划记录。")
                    value = dict(value, plan_id=old.plan_id)
                    converted[name] = self._plans("charge_plan", [value], [old])[0]
                elif field["kind"] in ("teams", "defense_teams", "applications", "codes"):
                    converted[name] = self._records(field["kind"], value, config)
                else:
                    self._validate_scalar(field, value)
                    converted[name] = value
            if app_id == "battle_assistant" and {"auto_battle_config", "use_merged_file"}&set(values):
                self.service._policy_validate(values.get("auto_battle_config",config.auto_battle_config),
                                              values.get("use_merged_file",config.use_merged_file))
            target = config.user_config if app_id == "redemption_code" else config
            def apply():
                for name, value in converted.items():
                    if name == "plan_list": config.plan_list = value
                    elif app_id == "teams" and name == "team_list":
                        for team in value: config.update_team(team)
                    elif app_id == "shiyu_defense" and name == "team_list":
                        config.team_list = value
                        config.save_team_list()
                    elif app_id == "_group" and name == "app_list":
                        config.set_app_order([row["app_id"] for row in value])
                        for row in value: config.set_app_enable(row["app_id"], row["enabled"])
                    elif app_id == "redemption_code" and name == "codes": target.update("codes", value)
                    else: setattr(config, name, value)
            atomic_apply(target, revision, apply, self.root)
            from one_dragon.base.config.yaml_operator import invalidate_cache
            invalidate_cache(str(target.file_path))
            self.ctx.run_context.clear_application_cache()
            for key in ("team_config", "battle_assistant_config"):
                self.ctx.__dict__.pop(key, None)
            return self._describe(app_id, self.get(app_id))

    def _records(self, kind: str, rows: Any, config: Any) -> Any:
        if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows): raise ValueError("配置需要记录列表。")
        if kind == "codes":
            result = {}
            for row in rows:
                if set(row)!={"code","expires"} or not isinstance(row["code"],str) or not row["code"].strip(): raise ValueError("兑换码不能为空。")
                if type(row["expires"]) is not int or len(str(row["expires"]))!=8:
                    raise ValueError("兑换码到期日需要 8 位 YYYYMMDD 数字。")
                try:datetime.strptime(str(row["expires"]), "%Y%m%d")
                except ValueError:raise ValueError("兑换码到期日不是有效日期。") from None
                code = row["code"].strip()
                if code in result: raise ValueError("兑换码重复。")
                result[code] = row["expires"]
            return result
        if kind == "applications":
            ids = [row.get("app_id") for row in rows]
            if len(ids)!=len(set(ids)) or set(ids)!={item.app_id for item in config.app_list}: raise ValueError("应用顺序必须包含每个应用且不能重复。")
            for row in rows:
                if set(row)-{"app_id","name","enabled"} or type(row.get("enabled")) is not bool: raise ValueError("应用启用状态无效。")
            return rows
        if kind == "teams":
            from zzz_od.config.team_config import PredefinedTeamInfo
            from zzz_od.game_data.agent import AgentEnum
            agents = {item.value.agent_id for item in AgentEnum}|{"unknown"}
            policies = {p["name"] for p in self.service.policies()}
            result, used = [], set()
            for row in rows:
                if set(row)!={"idx","name","auto_battle","agent_id_list"}: raise ValueError("队伍字段不完整。")
                idx = row["idx"]
                if type(idx) is not int or not 0<=idx<20 or idx in used: raise ValueError("预编队伍序号无效或重复。")
                used.add(idx)
                if not isinstance(row["name"],str) or not row["name"].strip(): raise ValueError("队伍名称不能为空。")
                if row["auto_battle"] not in policies: raise ValueError("队伍策略不存在。")
                if not isinstance(row["agent_id_list"],list) or len(row["agent_id_list"])>3 or any(a not in agents for a in row["agent_id_list"]): raise ValueError("队伍角色无效。")
                result.append(PredefinedTeamInfo(**row))
            return result
        from zzz_od.application.shiyu_defense.shiyu_defense_config import ShiyuDefenseTeamConfig
        from zzz_od.game_data.agent import DmgTypeEnum
        result, used = [], set()
        for row in rows:
            if set(row)!={"team_idx","weakness_list","for_critical"}: raise ValueError("防卫战队伍字段无效。")
            idx = row["team_idx"]
            if type(idx)is not int or not 0<=idx<20 or idx in used or type(row["for_critical"])is not bool: raise ValueError("防卫战队伍序号或状态无效。")
            used.add(idx)
            if not isinstance(row["weakness_list"],list) or any(name not in DmgTypeEnum.__members__ for name in row["weakness_list"]): raise ValueError("弱点属性无效。")
            result.append(ShiyuDefenseTeamConfig(idx,[DmgTypeEnum[name] for name in row["weakness_list"]],row["for_critical"]))
        return result
