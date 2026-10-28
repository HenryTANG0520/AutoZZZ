"""用实际节点和 OCR 回放预算；所有设备输入替换为记录器。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data",type=Path,required=True)
    parser.add_argument("--stage",type=Path,required=True)
    parser.add_argument("--result",type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    import numpy as np
    from PIL import Image
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_execution import OneDragonExecution
    from autozzz.onedragon_charge import ChargeBudgetControl
    from autozzz.onedragon_control import WindowState
    from autozzz.config import GAME_PATHS
    from autozzz.battle_budget import BattleBudget
    service=OneDragonService(root/"vendor"/"onedragon",args.data)
    ctx=service.ctx
    manager=OneDragonExecution(ctx)
    service._execution=manager
    manager.initialize("cn",GAME_PATHS["cn"])
    manager.worker.join()
    assert manager.state=="ready",manager.status()
    from zzz_od.application.charge_plan.charge_plan_config import ChargePlanItem
    from zzz_od.operation.compendium.combat_simulation import CombatSimulation
    from zzz_od.operation.challenge_mission.check_next_after_battle import ChooseNextOrFinishAfterBattle
    from one_dragon.base.operation.operation_round_result import OperationRoundResultEnum
    from autozzz.onedragon_control import ObservationExpired
    control=manager.control
    control.probe=lambda:WindowState(True,True,(0,0,1920,1080))
    control.reset()
    control.poll()
    stage=np.array(Image.open(args.stage).convert("RGB").resize((1920,1080)))
    result=np.array(Image.open(args.result).convert("RGB").resize((1920,1080)))
    images=[stage]
    clicks=[]
    controller=ctx.controller
    controller.init_before_context_run=lambda:True
    controller.screenshot=lambda independent=False:control.observe(lambda:(time.time(),images[0]))
    controller.click=lambda point=None,**kw:control.input(lambda:clicks.append((point.x,point.y))) or True
    controller.mouse_move=lambda point:control.checkpoint(require_frame=True)
    def node(operation,name):
        operation._current_node=operation._node_map[name]
        operation.round_start_time=time.time()
        return operation._execute_one_round()
    def mission():
        plan=ChargePlanItem(category_name="实战模拟室",mission_type_name="代理人晋升",mission_name="异常演练",card_num="1",plan_times=2)
        op=CombatSimulation(ctx,plan)
        op.config.plan_list=[plan]
        op._init_before_execute()
        return op
    adapter=None
    try:
        assert ctx.run_context.start_running()
        with tempfile.TemporaryDirectory(prefix="autozzz-charge-ledger-") as folder:
            ledger=BattleBudget(folder,"replay","cn",40,2)
            adapter=ChargeBudgetControl(ctx,control,ledger,20)
            adapter.install()
            op=mission()
            op.last_screenshot=stage
            available,cost=adapter._read_cost(op)
            assert cost==20 and available>=20,(available,cost)
            first=node(op,"下一步")
            assert ledger.reserved==20 and len(ledger.pending)==1 and clicks,(first,ledger.data,clicks)
            node(op,"下一步")
            assert len(ledger.data["entries"])==1,"same node retry must not reserve twice"
            images[0]=result
            controller.screenshot()
            ctx.auto_battle_context._last_check_end_time=0
            ctx.auto_battle_context.check_battle_state(result,time.time(),check_battle_end_normal_result=True,sync=True)
            assert ctx.auto_battle_context.last_check_end_result=="普通战斗-完成"
            node(op,"战斗结束")
            assert not ledger.pending
            again=ChooseNextOrFinishAfterBattle(ctx,True)
            again._init_before_execute()
            node(again,"判断再来一次")
            assert ledger.reserved==40 and len(ledger.pending)==1
            node(again,"判断再来一次")
            assert len(ledger.data["entries"])==2
            node(op,"战斗结束")
            assert not ledger.pending
            finish=ChooseNextOrFinishAfterBattle(ctx,True)
            finish._init_before_execute()
            node(finish,"判断再来一次")
            assert not finish.try_next and adapter.limited and len(ledger.data["entries"])==2
            adapter.close()
            adapter=None
            bad_ledger=BattleBudget(folder,"unreadable","cn",20,1)
            adapter=ChargeBudgetControl(ctx,control,bad_ledger,20)
            adapter.install()
            images[0]=stage
            bad=mission()
            before=len(clicks)
            with patch.object(ctx.ocr,"run_ocr",return_value={}):
                failure=node(bad,"下一步")
            assert failure.result==OperationRoundResultEnum.RETRY and not bad_ledger.data["entries"] and len(clicks)==before
            assert not manager.battle_control.allowed,"result frame must disable condition battle input"
            print(json.dumps(dict(actual_node_rounds=8,ocr_cost=cost,ocr_available=available,
                                  reserved=ledger.reserved,settled_entries=2,retry_does_not_reserve_twice=True,
                                  unreadable_cost_does_not_click=True,game_input_sent=False),ensure_ascii=False))
    finally:
        if adapter is not None:adapter.close()
        service.close()


if __name__=="__main__":main()
