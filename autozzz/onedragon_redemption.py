"""兑换码输入与已尝试记录；没有到账证据时不报告兑换成功。"""
from __future__ import annotations

import copy
from functools import wraps,update_wrapper
from typing import Any


class RedemptionControl:
    def __init__(self,control:Any):
        self.control=control
        self.attempts:list[dict]=[]
        self.originals:list[tuple[Any,str,Any]]=[]

    def reset(self) -> None:self.attempts=[]

    def patch(self,cls:Any,name:str,replacement:Any) -> None:
        original=getattr(cls,name)
        update_wrapper(replacement,original)
        if hasattr(original,"operation_node_annotation"):
            node=copy.copy(original.operation_node_annotation)
            node.op_method=replacement
            replacement.operation_node_annotation=node
        self.originals.append((cls,name,original))
        setattr(cls,name,replacement)

    def install(self) -> None:
        from zzz_od.application.redemption_code.redemption_code_app import RedemptionCodeApp
        owner=self
        initialize=RedemptionCodeApp.handle_init
        def init(operation):
            initialize(operation)
            operation._autozzz_redemption={}
            operation.code_idx=0
        self.patch(RedemptionCodeApp,"handle_init",init)
        original=RedemptionCodeApp.input_code
        @wraps(original)
        def input_code(operation):
            if operation.code_idx>=len(operation.unused_code_list):return operation.round_success("全部兑换完毕")
            state=getattr(operation,"_autozzz_redemption",{})
            if state.get("index")!=operation.code_idx:
                state=operation._autozzz_redemption=dict(index=operation.code_idx,epoch=owner.control.epoch,phase="focus")
            if state["phase"]=="submitted":return operation.round_success("兑换码兑换")
            if state["epoch"]!=owner.control.epoch:
                state.update(epoch=owner.control.epoch,phase="focus")
            visible=operation.round_by_find_area(operation.last_screenshot,"菜单","兑换码兑换")
            if not visible.is_success:return operation.round_retry("等待兑换码输入框",wait=.5)
            if state["phase"]=="focus":
                result=operation.round_by_click_area("菜单","兑换码输入框")
                if result.is_success:state["phase"]="type"
                return operation.round_wait("等待输入框",wait=1)
            if state["phase"]=="type":
                keyboard=operation.ctx.controller.keyboard_controller
                keyboard.press("ctrl")
                try:keyboard.tap("a")
                finally:keyboard.release("ctrl")
                keyboard.keyboard.type(operation.unused_code_list[operation.code_idx])
                owner.control.checkpoint(require_frame=True)
                state["phase"]="submit"
                return operation.round_wait("等待兑换间隔",wait=6 if operation.code_idx else 1)
            result=operation.round_by_find_and_click_area(operation.last_screenshot,"菜单","兑换码兑换",success_wait=1,retry_wait=1)
            if result.is_success:
                state["phase"]="submitted"
                owner.attempts.append(dict(code=operation.unused_code_list[operation.code_idx],status="submitted_unverified",reward_verified=False))
            return result
        self.patch(RedemptionCodeApp,"input_code",input_code)

    def close(self) -> None:
        for cls,name,original in reversed(self.originals):setattr(cls,name,original)
        self.originals.clear()
