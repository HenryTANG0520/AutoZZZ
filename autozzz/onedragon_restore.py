"""原生电量恢复操作适配；保留来源选择、数量判断和获得层处理。"""
from __future__ import annotations

import copy
from functools import wraps
from pathlib import Path

NONE='不使用'
BACKUP='使用储蓄电量'
ETHER='使用以太电池'
BOTH='同时使用储蓄电量和以太电池'
MODES=(NONE,BACKUP,ETHER,BOTH)
ACTION='charge_plan:restore'


def restore_action(mode):
    if mode not in MODES:raise ValueError('补充电量方式无效。')
    if mode==NONE:return None
    return dict(id=ACTION,app_id='charge_plan',kind='restore_charge',requires_confirmation=True,
        text=f'电量恢复：{mode}。使用游戏建议数量，恢复量可能高于本轮刷本消耗；不使用菲林。')


class RestoreChargeControl:
    def __init__(self,ctx,control,mode,budget_control):
        if mode not in MODES or mode==NONE:raise ValueError('本轮没有启用电量恢复。')
        self.ctx,self.control,self.mode,self.budget_control=ctx,control,mode,budget_control
        self.originals=[];self.results=[];self.active=None;self.previous_validator=None
        self.config=ctx.run_context.get_config('charge_plan',ctx.current_instance_idx,'one_dragon')
        self.path=Path(self.config.file_path).absolute()

    def authorize(self,operation):
        from one_dragon.utils.yaml_utils import safe_load
        if operation.config.restore_charge!=self.mode:raise ValueError('电量恢复方式已变化，请重新准备并确认。')
        if self.path.resolve()!=self.path or not self.path.is_file():raise ValueError('电量恢复配置文件缺失或被重定向。')
        value=safe_load(self.path.read_bytes())
        if not isinstance(value,dict) or value.get('restore_charge',NONE)!=self.mode:raise ValueError('电量恢复方式已变化，请重新准备并确认。')
        self.budget_control.check_restore_budget()

    def patch(self,cls,name,handler):
        original=getattr(cls,name)
        @wraps(original)
        def replacement(operation,*args,**kwargs):return handler(operation,original,*args,**kwargs)
        node=copy.copy(original.operation_node_annotation);node.op_method=replacement;replacement.operation_node_annotation=node
        self.originals.append((cls,name,original));setattr(cls,name,replacement)

    def install(self):
        from zzz_od.operation.restore_charge import RestoreCharge
        self.previous_validator=self.control.input_validator
        def validate():
            if self.previous_validator is not None:self.previous_validator()
            if self.active is not None:self.authorize(self.active)
        self.control.input_validator=validate
        original=RestoreCharge.execute
        @wraps(original)
        def execute(operation):
            self.authorize(operation);previous=self.active;self.active=operation
            operation._autozzz_restore=None;operation._autozzz_source_epoch=None
            try:return original(operation)
            finally:self.active=previous
        self.originals.append((RestoreCharge,'execute',original));RestoreCharge.execute=execute

        def source(operation,original):
            self.authorize(operation);result=original(operation)
            if result.is_success:operation._autozzz_source_epoch=self.control.epoch
            return result

        def source_confirm(operation,original):
            self.authorize(operation)
            if (operation._autozzz_source_epoch!=self.control.epoch
                    or not operation.round_by_find_area(operation.last_screenshot,'恢复电量','标题-恢复电量').is_success):
                return operation.round_success('重新选择电量来源')
            return original(operation)

        def amount(operation,original):
            self.authorize(operation);result=original(operation)
            sources={RestoreCharge.SOURCE_BACKUP_CHARGE,RestoreCharge.SOURCE_ETHER_BATTERY}
            if result.is_success and result.status in sources:
                allowed={RestoreCharge.SOURCE_BACKUP_CHARGE} if self.mode==BACKUP else {RestoreCharge.SOURCE_ETHER_BATTERY} if self.mode==ETHER else sources
                current=operation._get_amount_by_area('当前数量');count=result.data
                if result.status not in allowed or type(current)is not int or type(count)is not int or not 0<count<current:
                    return operation.round_fail('未核实允许使用的电量来源或数量。')
                operation._autozzz_restore=dict(app_id='charge_plan',action='restore_charge',source=result.status,before=current,amount=count,
                    state='prepared',submitted=False,reward_verified=False,flow_completed=False,observation_epoch=operation._autozzz_source_epoch)
            return result

        def confirm(operation,original):
            self.authorize(operation);record=getattr(operation,'_autozzz_restore',None)
            if record is None:return operation.round_fail('缺少电量恢复数量核实。')
            if record['submitted']:return operation.round_success('已提交恢复电量')
            if (record['observation_epoch']!=self.control.epoch
                    or operation._get_amount_by_area('当前数量')!=record['before']
                    or operation._get_amount_by_area('兑换数量-数字输入框')!=record['amount']):
                return operation.round_success('重新选择电量来源')
            if not operation.round_by_find_area(operation.last_screenshot,'恢复电量','确认').is_success:
                return operation.round_retry('未找到电量恢复确认按钮',wait=.5)
            self.control.checkpoint(require_frame=True)
            record.update(submitted=True,state='submission_pending',click_returned_success=False);self.results.append(record)
            result=original(operation)
            if result.is_success:
                record.update(state='submitted_unverified',click_returned_success=True)
            return result

        def after(operation,original):
            self.authorize(operation);record=getattr(operation,'_autozzz_restore',None)
            if record is None or not record['submitted']:return operation.round_fail('未核实电量恢复提交。')
            obtained=operation.round_by_find_area(operation.last_screenshot,'恢复电量','标题-获得').is_success
            quick=operation.round_by_find_area(operation.last_screenshot,'恢复电量','标题-快捷使用').is_success
            if quick and not obtained:return operation.round_retry('等待电量恢复确认完成',wait=.5)
            result=original(operation)
            from one_dragon.base.operation.operation_round_result import OperationRoundResultEnum
            if obtained and result.result==OperationRoundResultEnum.WAIT:record.update(reward_verified=True,state='reward_verified')
            if result.is_success:
                record.update(flow_completed=True)
                if not record['reward_verified']:record['state']='flow_completed_unverified'
            return result
        self.patch(RestoreCharge,'select_charge_source',source)
        self.patch(RestoreCharge,'confirm_charge_source',source_confirm)
        self.patch(RestoreCharge,'set_charge_amount',amount)
        self.patch(RestoreCharge,'confirm_restore_charge',confirm)
        self.patch(RestoreCharge,'confirm_after_restore',after)
        # 焦点换代后先关闭旧快捷使用层，再沿源操作图重新选来源并核实数量。
        from one_dragon.base.operation.operation_edge import OperationEdgeDesc
        self.patch(RestoreCharge,'close_quick_use_popup',lambda operation,original:original(operation))
        RestoreCharge.close_quick_use_popup.operation_edge_annotation=list(RestoreCharge.close_quick_use_popup.operation_edge_annotation)+[
            OperationEdgeDesc('确认恢复电量',status='重新选择电量来源'),OperationEdgeDesc('确认电量来源',status='重新选择电量来源')]

    def close(self):
        self.control.input_validator=self.previous_validator
        for cls,name,original in reversed(self.originals):setattr(cls,name,original)
        self.originals.clear()
