"""使用真实应用与配队算法回放初始化、策略替换和路线加载，不执行游戏操作。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data",type=Path,required=True)
    args=parser.parse_args()
    if not (args.data/".autozzz-exploration-test").is_file():raise SystemExit("请先用探索准备检查创建独立测试目录。")
    root=Path(__file__).resolve().parents[1]
    from autozzz.onedragon_service import OneDragonService
    from autozzz.config import GAME_PATHS
    service=OneDragonService(root/"vendor/onedragon",args.data)
    def wait(state):
        job=state["job_id"]
        while True:
            state=service.execution_status()
            assert state["job_id"]==job
            if not state["busy"]:return state
            time.sleep(.05)
    checks=[]
    try:
        assert wait(service.initialize_execution("cn",GAME_PATHS["cn"]))["state"]=="ready"
        ctx=service.ctx
        assert len(ctx.run_context.default_group_apps)==len(set(ctx.run_context.default_group_apps))==19
        ctx.register_application_factory()
        assert len(ctx.run_context.default_group_apps)==len(set(ctx.run_context.default_group_apps))==19
        assert len(ctx.run_context._application_factory_map)==30
        checks.append("repeated_factory_discovery_has_unique_group_entries")
        with patch.object(ctx.controller,"bind",side_effect=AssertionError("不得绑定游戏")),patch.object(ctx.controller,"screenshot",side_effect=AssertionError("不得截取游戏")):
            from zzz_od.application.shiyu_defense.shiyu_defense_team_utils import DefensePhaseTeamInfo
            from zzz_od.game_data.agent import AgentEnum,DmgTypeEnum
            from zzz_od.operation.choose_predefined_team import ChoosePredefinedTeam
            apps={key:ctx.run_context._application_factory_map[key].create_application(ctx.current_instance_idx,"one_dragon")
                  for key in ("intel_board","lost_void","withered_domain","shiyu_defense","world_patrol")}
            assert len({type(app) for app in apps.values()})==5
            checks.append("five_real_application_factories")
            assert wait(service.prepare_application("intel_board"))["state"]=="prepared"
            assert apps["intel_board"].init_for_intel_board().is_success
            checks.append("intel_initialization_uses_real_target_model")
            assert wait(service.prepare_application("lost_void"))["state"]=="prepared"
            assert apps["lost_void"].init_for_lost_void().is_success
            assert ctx.lost_void.all_artifact_list and ctx.lost_void.investigation_strategy_list
            agents=[item.value for item in AgentEnum][:6]
            ctx.team_config.update_team_by_idx(0,"首发测试队",agents[:3])
            ctx.team_config.update_team_by_idx(1,"候补测试队",agents[3:])
            apps["lost_void"].priority_agent_list=[agents[4]]
            assert apps["lost_void"].get_target_team_idx_by_priority()==1
            checks.append("lost_void_real_priority_agent_selection")
            assert wait(service.prepare_application("withered_domain"))["state"]=="prepared"
            apps["withered_domain"].handle_init()
            assert ctx.withered_domain.challenge_config.auto_battle
            assert ctx.withered_domain.data_service.name_2_entry
            checks.append("withered_real_challenge_and_event_data")
            assert wait(service.prepare_application("shiyu_defense"))["state"]=="prepared"
            weakness=[item for item in DmgTypeEnum if item!=DmgTypeEnum.UNKNOWN][:2]
            targets=[DefensePhaseTeamInfo(weakness,[]) for _ in range(2)]
            choose=ChoosePredefinedTeam(ctx,shiyu_target_list=targets)
            choose.scanned_team_idx_list=[0,1]
            assert choose._select_shiyu_teams()
            assert set(choose.target_team_idx_list)=={0,1}
            prepared={item["name"] for item in service.execution_status()["prepared_requirements"]["policies"]}
            assert all(ctx.team_config.get_team_by_idx(index).auto_battle in prepared for index in choose.target_team_idx_list)
            checks.append("defense_real_team_scoring_avoids_duplicate_agents")
            assert wait(service.prepare_application("world_patrol"))["state"]=="prepared"
            assert apps["world_patrol"].init_world_patrol().is_success
            assert [route.full_id for route in apps["world_patrol"].route_list]==service.execution_status()["prepared_requirements"]["routes"]
            checks.append("world_patrol_real_route_order_matches_preparation")
            before=ctx.auto_battle_context.auto_op
            assert wait(service.prepare_application("world_patrol"))["state"]=="prepared"
            assert ctx.auto_battle_context.auto_op is not before
            assert not before.is_running
            checks.append("reprepare_replaces_actual_conditional_operator")
            # 非法配置只影响此次独立测试目录，不能静默使用默认方案或全部路线。
            path=args.data/"config/lost_void_challenge/默认-成就模式.sample.yml"
            alternative=path.with_name("默认-成就模式.yml")
            assert path.exists() or alternative.exists()
            hidden=[]
            for candidate in (path,alternative):
                if candidate.exists():
                    moved=candidate.with_suffix(candidate.suffix+".test-hidden")
                    candidate.rename(moved)
                    hidden.append((moved,candidate))
            try:
                state=wait(service.prepare_application("lost_void"))
                assert state["state"]=="failed" and "挑战方案不存在" in state["error"],state
                assert not state["prepared_app"] and not state["prepared_requirements"]
            finally:
                for moved,candidate in hidden:moved.rename(candidate)
            checks.append("missing_challenge_clears_prepared_app_without_fallback")
            # 模型或策略加载期间被外部修改，不能给旧操作树签上新配置摘要。
            policy=args.data/"config/auto_battle/全配队通用.merged.yml"
            original_content=policy.read_bytes()
            original_init=ctx.auto_battle_context.init_auto_op
            def change_during_prepare(*params,**kwargs):
                original_init(*params,**kwargs)
                policy.write_bytes(original_content+b"\n# preparation-conflict-fixture\n")
            try:
                with patch.object(ctx.auto_battle_context,"init_auto_op",side_effect=change_during_prepare):
                    state=wait(service.prepare_application("world_patrol"))
                    assert state["state"]=="failed" and "准备期间配置发生变化" in state["error"],state
                    assert not state["prepared_app"] and not service._execution.prepared_hash
            finally:policy.write_bytes(original_content)
            checks.append("configuration_changed_during_model_loading_rejected")
            # 无模型应用也存在“加载结束、发布就绪”之间的停止窗口。
            from autozzz.onedragon_requirements import ApplicationRequirements
            completed=threading.Event()
            proceed=threading.Event()
            original_load=ApplicationRequirements.load
            def pause_before_publish(requirements,*params):
                original_load(requirements,*params)
                completed.set()
                assert proceed.wait(5)
            try:
                with patch.object(ApplicationRequirements,"load",new=pause_before_publish):
                    task=service.prepare_application("email")
                    assert completed.wait(3)
                    service.stop_execution()
                    proceed.set()
                    state=wait(task)
                    assert state["state"]=="stopped" and not state["prepared_app"],state
                    service.stop_execution()
                    assert service.execution_status()["state"]=="stopped"
            finally:proceed.set()
            checks.append("cancelled_prepare_never_publishes_ready_or_sticks_stopping")
            assert not ctx.controller.game_win.bound
        print(json.dumps(dict(checks=checks,game_started=False,game_input_sent=False),ensure_ascii=False))
    finally:service.close()


if __name__=="__main__":main()
