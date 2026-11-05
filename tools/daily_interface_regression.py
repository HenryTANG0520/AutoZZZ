"""固定 SDK 的无游戏回归：真实节点注解、失败收口、部分领奖、拖动事件。"""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'vendor/onedragon/src')]

from autozzz.onedragon_daily import DailyInterfaceControl
from autozzz.onedragon_charge import ChargeBudgetControl
from autozzz.onedragon_host import ManagedController
from autozzz.onedragon_group import GroupRunControl
from one_dragon.base.operation.operation_base import OperationResult
from one_dragon.base.operation.operation_round_result import OperationRoundResult,OperationRoundResultEnum as Kind
from zzz_od.application.charge_plan.charge_plan_app import ChargePlanApp
from zzz_od.application.engagement_reward.engagement_reward_app import EngagementRewardApp
from zzz_od.application.coffee.coffee_app import CoffeeApp
from zzz_od.application.coffee.coffee_config import CoffeeTransportPoint
from zzz_od.application.daily_signin.daily_signin_app import DailySignInApp
from zzz_od.operation.compendium.combat_simulation import CombatSimulation
from zzz_od.operation.choose_predefined_team import ChoosePredefinedTeam
from one_dragon.base.operation.application.group_application import GroupApplication
from one_dragon.base.operation.application_base import Application
from one_dragon.base.geometry.point import Point
from one_dragon.base.geometry.rectangle import Rect

def success(status=None,data=None,**_):return OperationRoundResult(Kind.SUCCESS,status,data)
def fail(status=None,data=None,**_):return OperationRoundResult(Kind.FAIL,status,data)
def by_result(value,status=None,**_):return success(status or value.status) if value.success else fail(status or value.status)
ctx=NS()
def op(**kw):return NS(ctx=ctx,round_success=success,round_fail=fail,round_by_op_result=by_result,**kw)
def forbidden(*_,**__):raise AssertionError('离线回归不得操作游戏、网络、物理输入')

daily=DailyInterfaceControl(ctx)
charge=ChargeBudgetControl(ctx,NS(),NS(pending=[],data={'id':'fixture'},reserved=0,remaining=300,rounds_left=3),100)
original=CombatSimulation.choose_card_num
try:
    with ExitStack() as scope:
        for target in ('autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow',
                       'autozzz.windows.find_game','pynput.mouse.Controller.press','pynput.keyboard.Controller.press',
                       'requests.sessions.Session.request','subprocess.Popen'):
            scope.enter_context(patch(target,side_effect=forbidden))
        daily.install();charge.install()
        assert CombatSimulation.choose_card_num.operation_node_annotation.op_method is CombatSimulation.choose_card_num
        assert ChoosePredefinedTeam.choose_team.operation_node_annotation.op_method is ChoosePredefinedTeam.choose_team

        # 上游会跳过失败计划，但最终应用不得报告成功或领取成功记录。
        failed=op(previous_node=NS(is_success=False,status='画面208/20，计划100'),
            current_plan=NS(uid='five-card',skipped=False),temp_plan=None)
        assert ChargePlanApp.challenge_complete(failed).is_success
        assert failed.current_plan.skipped
        with patch('zzz_od.application.charge_plan.charge_plan_app.BackToNormalWorld',
                   return_value=NS(execute=lambda:OperationResult(True,'大世界-普通'))):
            ended=op(battery_charge=208)
            outcome=ChargePlanApp.back_to_world(ended)
            assert outcome.is_fail and '画面208/20，计划100' in outcome.status
            assert charge.status()['remaining']==300 and len(charge.failures)==1

        # 部分领取的确认与每日已满不是同一件事，返回原因必须保留。
        reward=op(last_screenshot=object(),
            round_by_find_and_click_area=lambda *_a,**_kw:success('确认'))
        EngagementRewardApp.check_reward(reward)
        assert reward._autozzz_reward_claimed
        area=NS(is_template_area=True,rect=Rect(1532,250,1602,350),template_sub_dir='compendium',
                template_id='completed',template_match_threshold=.7)
        ctx.screen_loader=NS(get_area=lambda *_:area)
        matched={1532}
        ctx.tm=NS(crop_and_match_template=lambda _image,rect,*_a,**_kw:
            NS(max=object() if rect.x1 in matched else None))
        with patch('zzz_od.application.engagement_reward.engagement_reward_app.BackToNormalWorld',
                   return_value=NS(execute=lambda:OperationResult(True))):
            reward.previous_node=EngagementRewardApp.check_engagement(reward)
            partial=EngagementRewardApp.back_afterwards(reward)
            assert partial.is_fail and '已确认奖励领取弹窗' in partial.status and '未核实四档' in partial.status
            assert daily.status()['engagement']==dict(activity_full=True,claimed_current_rewards=True,
                daily_complete=False,verified_reward_tiers=[4])
            matched.update((710,984,1258))
            reward.previous_node=EngagementRewardApp.check_engagement(reward)
            assert EngagementRewardApp.back_afterwards(reward).is_success
            assert daily.status()['engagement']['verified_reward_tiers']==[1,2,3,4]

        # 地图不可用时切换本次路线，不持久改写账号的地点配置。
        original_point='布亚斯特城区 - 片刻闲'
        config=NS(transport_point=original_point)
        calls=[]
        def coffee_transport(app):
            calls.append(app.config.transport_point)
            return fail('未找到目标地图区域') if app.config.transport_point==original_point else success('兼容路线')
        # 单独验证安装包装器时传入的真实 transport 节点。
        daily.close()
        with patch.object(CoffeeApp,'transport',coffee_transport):
            coffee_transport.operation_node_annotation=original.operation_node_annotation
            daily.install()
            coffee=op(app_id='coffee',config=config)
            assert CoffeeApp.transport(coffee).is_success
            assert calls==[original_point,CoffeeTransportPoint.POINT_1.value.value]
            assert config.transport_point==original_point
            assert daily.status()['transport_fallbacks'][0]['reason']=='未找到目标地图区域'
            daily.close()

        # 真实受控拖动方法：每段位置更新后均补 MOVE，结束松键；无 Windows 输入。
        events=[]
        drag=NS(using_background=False,
            _position=lambda point:events.append(('position',point.x,point.y)),
            keyboard_controller=NS(press=lambda key:events.append(('down',key)),
                release=lambda key:events.append(('up',key)),
                mouse=NS(move=lambda x,y:events.append(('move',x,y)))),
            control=NS(hold=lambda _:True,checkpoint=lambda **_:None))
        ManagedController._foreground_drag.__wrapped__(drag,Point(0,0),Point(100,0))
        assert events[-1]==('up','mouse_left')
        assert sum(event[0]=='move' for event in events)==sum(event[0]=='position' for event in events)-1

        # 真实应用组节点返回“下一个”；Application 无 detail 时也不拿它当原因。
        group_ctx=NS(run_context=NS(get_application_name=lambda key:key,current_group_id='one_dragon',current_app_id=''))
        recorder=GroupRunControl(group_ctx,NS(),['partial','later'])
        class ContextConfig:
            def set_temp_instance_indices(self,_):pass
            def clear_temp_instance_indices(self):pass
        def group_run(group):
            index=group._current_app_idx
            app_id=group._group_config.app_list[index].app_id
            recorder.update(app_id,'failed' if index==0 else 'success',None if index==0 else 'later complete')
            if index==0:group._fail_app_idx_list.append(index)
            group._current_app_idx+=1
            return success('下一个')
        with patch.object(GroupApplication,'run_app',group_run):
            group_run.operation_node_annotation=original.operation_node_annotation
            recorder.install()
            group=NS(_current_app_idx=0,_group_config=NS(app_list=[NS(app_id=key,enabled=True) for key in ('partial','later')]),_fail_app_idx_list=[])
            GroupApplication.run_app(group);GroupApplication.run_app(group)
            rows=recorder.status()
            assert [row['state'] for row in rows]==['failed','success'] and rows[0]['detail']!='下一个'
            assert rows[1]['detail']=='later complete'
            recorder.close()
    print(json.dumps(dict(real_sdk_nodes=True,failed_charge_not_success=True,partial_rewards_distinguished=True,
        map_fallback_no_config_write=True,foreground_drag_move_events=True,group_details_preserved=True,
        game_input=False,budget_modified=False)))
finally:
    charge.close();daily.close()
assert CombatSimulation.choose_card_num is original
