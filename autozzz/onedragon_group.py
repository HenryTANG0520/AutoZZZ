"""保留上游应用组编排，记录子应用结果并核实组终态。"""
from __future__ import annotations

import copy
from functools import wraps
import threading

from .onedragon_control import ExecutionStopped,ObservationExpired


class GroupRunControl:
    def __init__(self,ctx,control,application_ids):
        self.ctx,self.control=ctx,control
        self.lock=threading.Lock()
        self.items={key:dict(id=key,name=ctx.run_context.get_application_name(key),state="pending",detail="") for key in application_ids}
        self.originals=[]

    def update(self,app_id,state,detail=""):
        if app_id in self.items:
            with self.lock:self.items[app_id].update(state=state,detail=str(detail or ""))

    def _patch_node(self,cls,name,handler):
        original=getattr(cls,name)
        @wraps(original)
        def replacement(operation,*args,**kwargs):return handler(operation,original,*args,**kwargs)
        annotation=copy.copy(original.operation_node_annotation)
        annotation.op_method=replacement
        replacement.operation_node_annotation=annotation
        self.originals.append((cls,name,original))
        setattr(cls,name,replacement)

    def install(self):
        from one_dragon.base.operation.application.group_application import GroupApplication
        from one_dragon.base.operation.one_dragon_app import OneDragonApp
        from one_dragon.base.operation.application_base import Application

        def run_app(group,original):
            idx=group._current_app_idx
            if idx<0 or idx>=len(group._group_config.app_list):return original(group)
            item=group._group_config.app_list[idx]
            if not item.enabled:
                # 禁用应用不构建，避免它的构造过程加载未使用模型或配置。
                group._current_app_idx+=1
                return group.round_success("应用未启用 "+item.app_name)
            self.update(item.app_id,"running")
            context=self.ctx.run_context
            previous=context.current_group_id,context.current_app_id
            try:
                result=original(group)
            except (ExecutionStopped,ObservationExpired):
                self.update(item.app_id,"interrupted","操作中断，结果未核实")
                raise
            except Exception as exc:
                self.update(item.app_id,"failed",str(exc))
                if idx not in group._fail_app_idx_list:group._fail_app_idx_list.append(idx)
                # 原组流程会继续下一应用；异常也不重试已产生未知操作的应用。
                group._current_app_idx=max(group._current_app_idx,idx+1)
                return group.round_success(GroupApplication.STATUS_NEXT)
            finally:context.current_group_id,context.current_app_id=previous
            skipped=isinstance(result.status,str) and result.status.startswith("应用已完成")
            state="skipped" if skipped else "failed" if not result.is_success or idx in group._fail_app_idx_list else "success"
            with self.lock:detail=self.items.get(item.app_id,{}).get("detail")
            if skipped or not detail:detail=result.status
            self.update(item.app_id,state,detail)
            return result

        def after_all(group,original):
            with self.lock:unfinished=[item["name"] for item in self.items.values() if item["state"] not in ("success","skipped")]
            if unfinished:return group.round_fail("应用未完成："+"、".join(unfinished))
            return original(group)

        original_init=OneDragonApp.handle_init
        @wraps(original_init)
        def current_instance_only(app):
            # 当前入口明确运行当前配置；不静默继承上游“全部实例”的默认值。
            config=app.ctx.one_dragon_config
            config.set_temp_instance_indices([app.ctx.current_instance_idx])
            try:return original_init(app)
            finally:config.clear_temp_instance_indices()
        self.originals.append((OneDragonApp,"handle_init",original_init))
        OneDragonApp.handle_init=current_instance_only
        original_after=Application.after_operation_done
        @wraps(original_after)
        def record_result(app,result):
            try:return original_after(app,result)
            finally:self.update(app.app_id,"success" if result.success else "failed",result.status)
        self.originals.append((Application,"after_operation_done",original_after))
        Application.after_operation_done=record_result
        self._patch_node(GroupApplication,"run_app",run_app)
        self._patch_node(GroupApplication,"after_all_done",after_all)

    def status(self):
        with self.lock:return [dict(item) for item in self.items.values()]

    def close(self):
        for cls,name,method in reversed(self.originals):setattr(cls,name,method)
        self.originals.clear()
