"""核对固定上游原子动作及全部自带策略构建，不执行原子输入。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    RuntimePaths(defaults.source,defaults.python,data).prepare();sys.path.insert(0,str(defaults.source/'src'));platform.uname()
    from autozzz.onedragon_service import OneDragonService
    from one_dragon.base.conditional_operation.operation_def import OperationDef
    from one_dragon.base.conditional_operation.atomic_op import AtomicOp
    from zzz_od.auto_battle.auto_battle_state import BattleStateEnum
    from zzz_od.auto_battle.atomic_op.turn import AtomicTurn
    from zzz_od.auto_battle.auto_battle_operator import AutoBattleOperator
    from one_dragon.base.conditional_operation.operator import ConditionalOperator
    from zzz_od.application.battle_assistant.auto_battle_config import get_auto_battle_op_config_list
    def forbidden(*_,**__):raise AssertionError('能力核对不得访问游戏、网络或设备')
    result=dict(actions=[],policies=[],game_accessed=False,game_input_sent=False,models_loaded=False)
    with ExitStack() as scope:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                     'pynput.keyboard.Controller.press','pynput.mouse.Controller.press','autozzz.windows.find_game','autozzz.windows.user32.SendInput'):
            scope.enter_context(patch(name,side_effect=forbidden))
        service=OneDragonService(defaults.source,data)
        try:
            context=service.ctx.auto_battle_context;factory=context.atomic_op_factory
            for state in BattleStateEnum:
                if not state.value.startswith('按键-'):continue
                definitions=[dict(op_name=state.value,way='点按',repeat=2,pre_delay=.01,post_delay=.01),
                             dict(op_name=state.value,way='按下',press=.2),dict(op_name=state.value,way='松开'),
                             dict(op_name=state.value+'-按下',press=.2),dict(op_name=state.value+'-松开')]
                for definition in definitions:
                    action=factory.get_atomic_op(OperationDef(definition));assert isinstance(action,AtomicOp)
                    assert type(action).__module__.startswith('zzz_od.auto_battle.atomic_op.')
                    result['actions'].append(dict(definition=definition,implementation=type(action).__module__+'.'+type(action).__name__))
            for definition in (dict(op_name='按键-切换角色',agent_name='安比'),dict(op_name='切换角色',agent_name='安比'),
                               dict(op_name='按键-快速支援'),dict(op_name='等待秒数',seconds=.01),
                               dict(op_name='设置状态',state='自定义-验证',seconds=1,value=2),dict(op_name='清除状态',state='自定义-验证')):
                action=factory.get_atomic_op(OperationDef(definition));assert isinstance(action,AtomicOp)
                result['actions'].append(dict(definition=definition,implementation=type(action).__module__+'.'+type(action).__name__))
            turn=AtomicTurn(context,100);assert isinstance(turn,AtomicOp)
            result['actions'].append(dict(definition=dict(periodic='自动转向'),implementation=type(turn).__module__+'.'+type(turn).__name__))
            for policy in service.policies():
                for merged in (False,True):
                    description=service.policy_validate(policy['name'],merged)
                    result['policies'].append(dict(directory='auto_battle',name=policy['name'],merged=merged,scenes=len(description['scenes'])))
            for policy in get_auto_battle_op_config_list('dodge'):
                for merged in (False,True):
                    operator=AutoBattleOperator(context,'dodge',policy.value,merged)
                    try:
                        ConditionalOperator.init(operator);assert operator.scenes
                        result['policies'].append(dict(directory='dodge',name=policy.value,merged=merged,scenes=len(operator.scenes)))
                    finally:operator.dispose()
            assert service.ctx.controller is None and not service.ctx._btn_listener_started
        finally:service.close()
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(actions=len(result['actions']),policy_builds=len(result['policies']),game_input_sent=False),ensure_ascii=False))


if __name__=='__main__':main()
