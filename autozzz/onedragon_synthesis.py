"""为原生以太电池合成连接资源确认、持久预算与结果核实。"""
from __future__ import annotations

import copy
from functools import wraps
from pathlib import Path
import re
import unicodedata

ACTION = 'charge_plan:synthesize'
CATEGORY = '合成电池'
COST = 60


def plan_spec(value):
    return {key: item for key, item in value.items() if key not in ('run_times', 'skipped')}


def synthesis_action(plans, loop):
    if not any(plan.category_name == CATEGORY and plan.plan_times > 0 for plan in plans):
        return None
    return dict(id=ACTION, app_id='charge_plan', kind='synthesize_battery', requires_confirmation=True,
                text='合成以太电池：每次 1 个，消耗 60 电量、100 储值电卡和丁尼；'
                     + ('循环计划，受本轮电量及次数预算限制。' if loop else '按计划次数执行，计入本轮电量及次数预算。'))


def confirmation_quantity(text):
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))
    values = re.findall(r'合成(\d+)个[\[「【]?以太电池', text)
    if len(values) != 1:
        raise ValueError('未能核实合成确认中的以太电池数量。')
    if values != ['1']:
        raise ValueError('原生合成计划每次仅合成 1 个以太电池。')
    return 1


class SynthesisControl:
    def __init__(self, ctx, control, budget_control):
        self.ctx, self.control, self.budget_control = ctx, control, budget_control
        self.config = ctx.run_context.get_config('charge_plan', ctx.current_instance_idx, 'one_dragon')
        self.path = Path(self.config.file_path).absolute()
        self.specs = [plan_spec(plan.to_dict()) for plan in self.config.plan_list]
        self.loop = self.config.loop
        self.originals, self.results = [], []
        self.active = None
        self.previous_validator = None

    def authorize(self, operation):
        from one_dragon.utils.yaml_utils import safe_load
        if self.path.resolve() != self.path or not self.path.is_file():
            raise ValueError('合成计划配置缺失或被重定向。')
        saved = safe_load(self.path.read_bytes())
        if (not isinstance(saved, dict) or saved.get('loop', True) != self.loop
                or [plan_spec(p) for p in saved.get('plan_list', [])] != self.specs
                or [plan_spec(p.to_dict()) for p in operation.config.plan_list] != self.specs
                or operation.config.loop != self.loop):
            raise ValueError('合成计划已变化，请重新准备并确认。')
        if operation.plan.category_name != CATEGORY or plan_spec(operation.plan.to_dict()) not in self.specs:
            raise ValueError('当前合成计划不在已确认清单中。')

    def patch(self, cls, name, handler):
        original = getattr(cls, name)
        @wraps(original)
        def replacement(operation, *args, **kwargs):
            return handler(operation, original, *args, **kwargs)
        node = copy.copy(original.operation_node_annotation)
        node.op_method = replacement
        replacement.operation_node_annotation = node
        self.originals.append((cls, name, original))
        setattr(cls, name, replacement)

    def install(self):
        from zzz_od.operation.exchange_ether_battery import ExchangeEtherBattery
        self.previous_validator = self.control.input_validator
        def validate():
            if self.previous_validator is not None:
                self.previous_validator()
            if self.active is not None:
                self.authorize(self.active)
        self.control.input_validator = validate
        original = ExchangeEtherBattery.execute
        @wraps(original)
        def execute(operation):
            self.authorize(operation)
            previous = self.active
            self.active = operation
            operation._autozzz_synthesis = None
            try:
                return original(operation)
            finally:
                self.active = previous
        self.originals.append((ExchangeEtherBattery, 'execute', original))
        ExchangeEtherBattery.execute = execute

        def material(operation, original):
            self.authorize(operation)
            record = operation._autozzz_synthesis
            if record is not None and record['submitted'] and not record['flow_completed']:
                self.budget_control._block('上一笔合成结果未知，不能重复合成。')
            operation._autozzz_synthesis = None
            return original(operation)

        def confirm(operation, original):
            self.authorize(operation)
            record = operation._autozzz_synthesis
            if record is not None and record['submitted']:
                return operation.round_success('已提交合成')
            if not operation.round_by_find_area(operation.last_screenshot, '道具处理-合成确认', '标题-合成确认').is_success:
                return operation.round_retry('等待合成确认', wait=.5)
            from one_dragon.utils import cv2_utils
            area = self.ctx.screen_loader.get_area('道具处理-合成确认', '标题-合成确认')
            text = self.ctx.ocr.run_ocr_single_line(cv2_utils.crop_image_only(operation.last_screenshot, area.rect))
            try:
                confirmation_quantity(text)
            except ValueError as exc:
                return operation.round_fail(str(exc))
            if not operation.round_by_find_area(operation.last_screenshot, '道具处理-合成确认', '按钮-确认').is_success:
                return operation.round_retry('未找到合成确认按钮', wait=.5)
            self.budget_control._reserve(operation, COST)
            record = dict(app_id='charge_plan', action='synthesize_battery', source=operation.plan.uid,
                          amount=1, cost=COST, state='submission_pending', submitted=True,
                          reward_verified=False, flow_completed=False, click_returned_success=False)
            operation._autozzz_synthesis = record
            self.results.append(record)
            result = original(operation)
            if result.is_success:
                record.update(state='submitted_unverified', click_returned_success=True)
            return result

        def obtained(operation, original):
            self.authorize(operation)
            record = operation._autozzz_synthesis
            if record is None or not record['submitted']:
                return operation.round_fail('缺少合成提交记录。')
            found = operation.round_by_find_area(operation.last_screenshot, '道具处理-获得', '标题-获得').is_success
            button = operation.round_by_find_area(operation.last_screenshot, '道具处理-获得', '按钮-确认').is_success
            if not found or not button:
                return operation.round_retry('等待核实合成获得页面', wait=.5)
            result = original(operation)
            if result.is_success:
                record.update(reward_verified=True, state='reward_verified')
            return result

        def finish(operation, original):
            self.authorize(operation)
            record = operation._autozzz_synthesis
            if record is None or not record['reward_verified']:
                self.budget_control._block('合成结果未核实，预留电量保持待核对。')
            reservation = self.budget_control.reservation
            if self.budget_control.owner is not operation or reservation is None:
                self.budget_control._block('缺少本次合成的电量预算记录。')
            self.budget_control.budget.settle(reservation, None)
            self.budget_control.reservation = None
            record.update(flow_completed=True, state='completed')
            return original(operation)

        self.patch(ExchangeEtherBattery, 'check_material', material)
        self.patch(ExchangeEtherBattery, 'confirm_synthesize', confirm)
        self.patch(ExchangeEtherBattery, 'confirm_obtained', obtained)
        self.patch(ExchangeEtherBattery, 'finish_exchange', finish)

    def close(self):
        self.control.input_validator = self.previous_validator
        for cls, name, original in reversed(self.originals):
            setattr(cls, name, original)
        self.originals.clear()
