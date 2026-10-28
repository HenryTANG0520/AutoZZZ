"""真实一条龙应用组回放；子应用与设备替换为可记录的无输入任务。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data",type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/"vendor/onedragon/src"))
    import numpy as np
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_execution import OneDragonExecution
    from autozzz.onedragon_control import WindowState
    from autozzz.run_lock import RunLock
    from one_dragon.base.operation.application.application_factory import ApplicationFactory
    from one_dragon.base.operation.application_base import Application
    from one_dragon.base.operation.application_run_record import AppRunRecord
    from one_dragon.base.operation.operation_node import operation_node
    from one_dragon.base.operation.application.group_application import GroupApplication
    service=OneDragonService(root/"vendor/onedragon",args.data)
    ctx=service.ctx
    manager=OneDragonExecution(ctx)
    service._execution=manager
    control=manager.control
    events=[]
    constructed=[]
    failing=[True]
    class Controller:
        is_game_window_ready=True
        screenshot_method=ctx.env_config.screenshot_method
        keyboard_controller=SimpleNamespace(reset=lambda:None)
        def probe(self):return WindowState(True,True,(0,0,1920,1080))
        def bind(self,_path):pass
        def init_before_context_run(self):return True
        def screenshot(self):return control.observe(lambda:(time.time(),np.zeros((1080,1920,3),dtype=np.uint8)))
    class RecordedApp(Application):
        def __init__(self,key):super().__init__(ctx,key,op_name=key)
        @operation_node(name="无输入回放",is_start_node=True)
        def step(self):
            events.append(self.app_id)
            return self.round_fail("回放失败") if self.app_id=="scratch_card" and failing[0] else self.round_success("回放完成")
    class Factory(ApplicationFactory):
        def __init__(self,key):
            self.key=key
            super().__init__(SimpleNamespace(APP_ID=key,APP_NAME="回放 "+key,DEFAULT_GROUP=True,NEED_NOTIFY=False))
        def create_application(self,instance_idx,group_id):
            constructed.append(self.key)
            if self.key=="charge_plan":raise AssertionError("禁用应用不应构建")
            return RecordedApp(self.key)
        def create_run_record(self,instance_idx):return AppRunRecord(self.key,instance_idx)
    original_group=GroupApplication.run_app
    try:
        ctx.controller=Controller()
        ctx.ready_for_application=True
        ctx.run_context.registry_application([Factory(key) for key in ("daily_signin","scratch_card","email","charge_plan")])
        ctx.run_context.default_group_apps[:]=list(dict.fromkeys(ctx.run_context.default_group_apps))
        ctx.app_group_manager.set_default_apps(ctx.run_context.default_group_apps)
        config=ctx.app_group_manager.get_one_dragon_group_config(ctx.current_instance_idx)
        order=["daily_signin","scratch_card","email"]
        config.set_app_order(order)
        for item in config.app_list:config.set_app_enable(item.app_id,item.app_id in order)
        prepared=manager.prepare_application("one_dragon")
        manager.worker.join(5)
        assert manager.state=="prepared" and manager.prepared_applications==order,manager.status()
        mutex_name="Local\\AutoZZZ.GroupSmoke."+uuid.uuid4().hex
        original_mutex=RunLock
        manager._watch=lambda stop:stop.wait()
        with patch("autozzz.onedragon_execution.RunLock",side_effect=lambda:original_mutex(mutex_name)):
            first=manager.start_application("one_dragon",fixed_entry=True)
            manager.worker.join(8)
            assert not manager.busy,manager.status()
            state=manager.status()
            assert state["state"]=="failed" and "scratch_card" in state["error"],state
            assert events==order,events
            assert [item["state"] for item in state["applications"]]==["success","failed","success"],state
            assert constructed==order,constructed
            assert state['applications'][1]['detail']=='回放失败'
            assert GroupApplication.run_app is original_group
            assert ctx.one_dragon_config._temp_instance_indices is None
            failing[0]=False
            manager.prepare_application("one_dragon")
            manager.worker.join(5)
            assert manager.state=="prepared",manager.status()
            manager.start_application("one_dragon",fixed_entry=True)
            manager.worker.join(8)
            state=manager.status()
            assert state["state"]=="success",state
            assert events==order+["scratch_card"],events
            assert [item["state"] for item in state["applications"]]==["skipped","success","skipped"],state
            assert constructed==order+order,constructed
            assert GroupApplication.run_app is original_group
            # 预算初始化失败也必须还原已安装的组适配器。
            from autozzz.battle_budget import BattleBudget
            budgets=args.data/'fixture-budgets'
            old=BattleBudget(budgets,'old-pending','cn',20,1)
            old.reserve(20,'fixture')
            manager.prepare_application('one_dragon')
            manager.worker.join(5)
            manager.uses_charge=True
            with patch('autozzz.battle_budget.budget_directory',return_value=budgets):
                manager.start_application('one_dragon',fixed_entry=True,budget=dict(id='blocked',allowance=20,battle_limit=1,max_per_battle=20))
                manager.worker.join(5)
            assert manager.state=='failed' and GroupApplication.run_app is original_group,manager.status()
        print(json.dumps(dict(real_one_dragon_app=True,failed_app_does_not_skip_later_apps=True,
                              partial_failure_not_success=True,completed_record_skipped=True,
                              disabled_app_not_constructed=True,original_nodes_restored=True,game_input_sent=False)))
    finally:
        manager.close()
        ctx.controller=None
        service.close()


if __name__=="__main__":main()
