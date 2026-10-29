"""经营消费清单与拆解控制；游戏流程继续使用固定上游节点。"""
from __future__ import annotations

import copy
from contextvars import ContextVar
from enum import Enum
from functools import wraps
import hashlib
import json
from pathlib import Path
from typing import Any
import uuid

_resource_app: ContextVar[str | None] = ContextVar("autozzz_resource_app", default=None)


def resource_confirmation_label(actions: list[dict]) -> str:
    sensitive = [item for item in actions if item["requires_confirmation"]]
    plugins=any(item.get('kind')=='plugin' for item in sensitive)
    disc = any(item.get("app_id") == "drive_disc_dismantle" for item in sensitive)
    if sensitive and all(item.get('kind')=='restore_charge' for item in sensitive):return '允许本轮按以上配置补充电量'
    if sensitive and all(item.get('kind')=='synthesize_battery' for item in sensitive):return '允许本轮合成以太电池（消耗电量、储值电卡和丁尼）'
    if plugins:
        if disc:return '允许本轮执行以上插件和资源操作（拆解无法恢复）'
        return '允许本轮执行以上操作'
    consumption = any(item.get("app_id") != "drive_disc_dismantle" for item in sensitive)
    if disc and consumption:
        return "允许本轮消费并拆解驱动盘（拆解无法恢复）"
    if disc:
        return "允许本轮拆解以上驱动盘（无法恢复）"
    return "允许本轮按以上配置消费"


def resource_result_warning(results):
    names={'restore_charge':'电量恢复','synthesize_battery':'电池合成','dismantle':'拆解'}
    unverified=[item for item in results if not item.get('reward_verified')]
    if not unverified:return ''
    labels=list(dict.fromkeys(names.get(item.get('action'),'拆解' if item.get('app_id')=='drive_disc_dismantle' else '资源操作') for item in unverified))
    return '、'.join(labels)+'结果未核实'


class ResourceConfirmation:
    def __init__(self) -> None:
        self.prepare([], "")

    def prepare(self, actions: list[dict], digest: str) -> None:
        self.actions = copy.deepcopy(actions)
        self.digest = digest
        self.required = any(item["requires_confirmation"] for item in actions)
        self.token = uuid.uuid4().hex if self.required else ""
        self.accepted: list[str] = []

    def verify(self, token: str | None, digest: str) -> None:
        if token is not None and not isinstance(token, str):
            raise ValueError("资源操作确认编号须为文本或空值。")
        if digest != self.digest:
            raise ValueError("资源操作配置已变化，请重新准备并确认。")
        if self.required and (not self.token or token != self.token):
            raise ValueError("请确认本轮消费或拆解清单后再开始；旧确认不能复用。")
        if not self.required and token:
            raise ValueError("本轮无需资源操作确认，请重新准备。")

    def consume(self, token: str | None, digest: str) -> None:
        self.verify(token, digest)
        self.accepted = [item["id"] for item in self.actions if item["requires_confirmation"]]
        self.token = ""

    def status(self) -> dict:
        return dict(actions=copy.deepcopy(self.actions), confirmation_id=self.token,
                    confirmation_required=self.required, confirmed=list(self.accepted))


def resource_fingerprint(config: Any) -> str:
    def plain(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.name
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        return value
    settings = {name: plain(getattr(config, name)) for name, value in vars(type(config)).items()
                if isinstance(value, property) and not name.startswith("_")}
    return hashlib.sha256(json.dumps(settings, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def resource_actions(app_id: str, config: Any) -> list[dict]:
    actions: list[dict] = []
    def add(key: str, text: str, sensitive: bool = True) -> None:
        actions.append(dict(id=app_id + ":" + key, app_id=app_id, text=text, requires_confirmation=sensitive))
    if app_id == "drive_disc_dismantle":
        if config.dismantle_level not in ("B", "A及以下", "S及以下") or type(config.dismantle_abandon) is not bool:
            raise ValueError("驱动盘拆解条件无效。")
        extra = "；额外包含全部已弃置驱动盘" if config.dismantle_abandon else ""
        add("dismantle", "驱动盘：拆解" + config.dismantle_level + extra + "。拆解后无法恢复。")
    elif app_id == "suibian_temple":
        for key in ("auto_manage_enabled", "yum_cha_sin", "yum_cha_sin_period_refresh",
                    "good_goods_purchase_enabled", "boo_box_purchase_enabled"):
            if type(getattr(config, key)) is not bool:
                raise ValueError("随便观开关配置无效：" + key)
        if config.auto_manage_enabled:
            add("hosting", "随便观：领取托管收益并重新开启托管。", False)
        else:
            add("management", "随便观：派遣游历、制造和售卖，消耗经营材料及邦布续航。")
            if config.yum_cha_sin:
                refresh = "开启" if config.yum_cha_sin_period_refresh else "关闭"
                add("procurement", "饮茶仙：提交材料，缺料时尝试制造或派遣；定期采办刷新" + refresh + "。")
        if config.good_goods_purchase_enabled:
            add("goods", "好物铺：使用云纹徽批量购买邦布能源插件，可能用完对应资源。")
        if config.boo_box_purchase_enabled:
            from zzz_od.application.suibian_temple.suibian_temple_config import BangbooPrice
            thresholds = "／".join(label + BangbooPrice[getattr(config, key)].value for label, key in (
                ("游历", "boo_box_adventure_price"), ("制造", "boo_box_craft_price"), ("售卖", "boo_box_sell_price")))
            add("bangboo", "邦巢：刷新并使用云纹徽批量聘用 S 级邦布；最低价格" + thresholds + "，可能用完云纹徽。请先关闭游戏内“快速选择”。")
    return actions


class ResourceActionControl:
    DISC = "仓库-驱动仓库-驱动盘拆解"
    RESELECT = "重新选择筛选条件"

    def __init__(self, ctx: Any, control: Any, fingerprints: dict[str, str]):
        self.ctx, self.control, self.fingerprints = ctx, control, dict(fingerprints)
        self.originals: list[tuple[Any, str, Any]] = []
        self.results: list[dict] = []
        self.files: dict[str, tuple[Path, bytes | None]] = {}
        self.previous_input_validator: Any = control.input_validator

    def authorize_file(self, app_id: str) -> None:
        path, expected = self.files[app_id]
        actual = path.read_bytes() if path.is_file() else None
        if actual != expected:
            raise ValueError("资源操作配置文件已变化，请重新准备并确认：" + app_id)

    def authorize(self, app_id: str, config: Any) -> None:
        if self.fingerprints.get(app_id) != resource_fingerprint(config):
            raise ValueError("资源操作配置已变化，请重新准备并确认：" + app_id)
        self.authorize_file(app_id)

    def patch_node(self, cls: Any, name: str, handler: Any, *, remove_failed: bool = False) -> Any:
        original = getattr(cls, name)
        @wraps(original)
        def replacement(operation, *args, **kwargs):
            app_id = "suibian_temple" if cls.__name__ == "SuibianTempleApp" else "drive_disc_dismantle"
            token = _resource_app.set(app_id)
            try:
                return handler(operation, original, *args, **kwargs)
            finally:
                _resource_app.reset(token)
        node = copy.copy(original.operation_node_annotation)
        node.op_method = replacement
        replacement.operation_node_annotation = node
        if remove_failed:
            replacement.operation_edge_annotation = [copy.copy(edge) for edge in original.operation_edge_annotation if edge.success]
        self.originals.append((cls, name, original))
        setattr(cls, name, replacement)
        return replacement

    def selection(self, operation: Any) -> dict:
        state = getattr(operation, "_autozzz_selection", None)
        if state is None:
            state = operation._autozzz_selection = dict(epoch=self.control.epoch, phase="reset", level=False, abandon=False, applied=False)
        if state["epoch"] != self.control.epoch and not state["applied"]:
            state.update(epoch=self.control.epoch, phase="reset", level=False, abandon=False)
        return state

    def install(self) -> None:
        from one_dragon.base.operation.operation_edge import OperationEdgeDesc
        from one_dragon.base.screen.screen_area import ScreenArea
        from one_dragon.base.geometry.rectangle import Rect
        from zzz_od.application.drive_disc_dismantle.drive_disc_dismantle_app import DriveDiscDismantleApp
        from zzz_od.application.suibian_temple.suibian_temple_app import SuibianTempleApp
        owner = self
        for app_id in self.fingerprints:
            config = self.ctx.run_context.get_config(app_id, self.ctx.current_instance_idx, "one_dragon")
            path = Path(config.file_path)
            self.files[app_id] = path, path.read_bytes() if path.is_file() else None
            self.authorize(app_id, config)
        self.previous_input_validator = self.control.input_validator
        def validate_input():
            if self.previous_input_validator is not None:
                self.previous_input_validator()
            app_id = _resource_app.get()
            if app_id in self.fingerprints:
                self.authorize_file(app_id)
        self.control.input_validator = validate_input

        initialize = DriveDiscDismantleApp.handle_init
        @wraps(initialize)
        def initialize_disc(operation):
            initialize(operation)
            operation._autozzz_selection = None
            operation._autozzz_dismantle_submitted = False
            operation._autozzz_resource_result = None
            owner.authorize("drive_disc_dismantle", operation.config)
        self.originals.append((DriveDiscDismantleApp, "handle_init", initialize))
        DriveDiscDismantleApp.handle_init = initialize_disc
        finish = DriveDiscDismantleApp.after_operation_done
        @wraps(finish)
        def finish_disc(operation, result):
            if result.success and getattr(operation, "_autozzz_resource_result", None) is None:
                record = dict(app_id="drive_disc_dismantle", state="not_verified", reward_verified=False)
                operation._autozzz_resource_result = record
                owner.results.append(record)
            return finish(operation, result)
        self.originals.append((DriveDiscDismantleApp, "after_operation_done", finish))
        DriveDiscDismantleApp.after_operation_done = finish_disc

        def guard(operation, original):
            owner.authorize("suibian_temple", operation.config)
            return original(operation)
        for name, method in list(vars(SuibianTempleApp).items()):
            if hasattr(method, "operation_node_annotation"):
                self.patch_node(SuibianTempleApp, name, guard)

        def level(operation, original):
            owner.authorize("drive_disc_dismantle", operation.config)
            state = owner.selection(operation)
            if state["applied"]:
                return operation.round_success()
            if state["phase"] == "reset":
                found = operation.round_by_find_area(operation.last_screenshot, owner.DISC, "按钮-快速选择-确认")
                if not found.is_success:
                    return operation.round_retry("等待快速选择弹窗", wait=.5)
                # 弹窗范围由已建档的选择项和确认区域合并，重置仅在此区域识别。
                areas = [owner.ctx.screen_loader.get_area(owner.DISC, key).rect for key in
                         ("按钮-全选已弃置", "按钮-B", "按钮-A及以下", "按钮-S及以下", "按钮-快速选择-确认")]
                rect = Rect(min(area.x1 for area in areas), min(area.y1 for area in areas),
                            max(area.x2 for area in areas), max(area.y2 for area in areas))
                result = operation.round_by_ocr_and_click(operation.last_screenshot, "重置", area=ScreenArea(pc_rect=rect), lcs_percent=1)
                if not result.is_success:
                    return operation.round_retry("未识别筛选重置按钮", wait=.5)
                owner.control.checkpoint(require_frame=True)
                state["phase"] = "level"
                return operation.round_wait("已重置筛选", wait=.2)
            result = original(operation)
            owner.control.checkpoint(require_frame=True)
            if result.is_success:
                state["level"] = True
            return result

        def abandon(operation, original):
            owner.authorize("drive_disc_dismantle", operation.config)
            state = owner.selection(operation)
            if not state["level"]:
                return operation.round_success(owner.RESELECT)
            result = original(operation)
            owner.control.checkpoint(require_frame=True)
            if result.is_success:
                state["abandon"] = True
            return result

        def apply_filter(operation, original):
            owner.authorize("drive_disc_dismantle", operation.config)
            state = owner.selection(operation)
            if state["applied"]:
                return operation.round_success()
            if not state["level"] or not state["abandon"]:
                return operation.round_success(owner.RESELECT)
            result = operation.round_by_find_and_click_area(operation.last_screenshot, owner.DISC, "按钮-快速选择-确认", retry_wait=1)
            if result.is_success:
                state["applied"] = True
                return operation.round_success(result.status, wait=1)
            return result

        def salvage(operation, original):
            owner.authorize("drive_disc_dismantle", operation.config)
            if not owner.selection(operation)["applied"]:
                return operation.round_fail("筛选尚未核实，未执行拆解。")
            return original(operation)

        def confirm(operation, original):
            owner.authorize("drive_disc_dismantle", operation.config)
            if not owner.selection(operation)["applied"]:
                return operation.round_fail("筛选尚未核实，未执行拆解。")
            if getattr(operation, "_autozzz_dismantle_submitted", False):
                return operation.round_success("按钮-拆解-确认")
            result = operation.round_by_find_and_click_area(operation.last_screenshot, owner.DISC, "按钮-拆解-确认", retry_wait=1)
            if result.is_success:
                operation._autozzz_dismantle_submitted = True
                record = dict(app_id="drive_disc_dismantle", state="submitted_unverified", reward_verified=False)
                operation._autozzz_resource_result = record
                owner.results.append(record)
                return operation.round_success(result.status, wait=1)
            return result

        def obtained(operation, original):
            owner.authorize("drive_disc_dismantle", operation.config)
            record = getattr(operation, "_autozzz_resource_result", None)
            if record is not None and record["reward_verified"]:
                return operation.round_success("按钮-确认")
            result = operation.round_by_find_and_click_area(operation.last_screenshot, "道具处理-获得", "按钮-确认", retry_wait=1)
            if result.is_success:
                if record is not None:
                    record.update(state="reward_verified", reward_verified=True)
                return operation.round_success(result.status, wait=1)
            return result

        self.patch_node(DriveDiscDismantleApp, "choose_level", level)
        self.patch_node(DriveDiscDismantleApp, "choose_abandon", abandon)
        filtered = self.patch_node(DriveDiscDismantleApp, "click_filter_confirm", apply_filter, remove_failed=True)
        filtered.operation_edge_annotation = list(filtered.operation_edge_annotation)
        target = DriveDiscDismantleApp.choose_level
        target.operation_edge_annotation = list(target.operation_edge_annotation) + [
            OperationEdgeDesc("选择弃置", status=self.RESELECT),
            OperationEdgeDesc("快速选择确认", status=self.RESELECT),
        ]
        self.patch_node(DriveDiscDismantleApp, "click_salvage", salvage)
        self.patch_node(DriveDiscDismantleApp, "click_salvage_confirm", confirm)
        self.patch_node(DriveDiscDismantleApp, "confirm_obtained", obtained)

    def close(self) -> None:
        self.control.input_validator = self.previous_input_validator
        for cls, name, original in reversed(self.originals):
            setattr(cls, name, original)
        self.originals.clear()
