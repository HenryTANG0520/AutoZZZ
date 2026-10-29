"""实际 SDK 合成操作图、计划计数与运行槽；设备、识别及游戏均为隔离夹具。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import shutil
import sys
import threading
import time
from unittest.mock import PropertyMock,patch


def wait(predicate, timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('合成回放等待超时')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--models',type=Path,required=True);parser.add_argument('--package',type=Path)
    args=parser.parse_args();base=args.data.resolve()
    if base.exists():raise ValueError('请使用新的隔离验证目录。')
    project=Path(__file__).resolve().parents[1];bridge=args.package.resolve()/'onedragon/bridge' if args.package else project;sys.path.insert(0,str(bridge))
    import autozzz
    assert Path(autozzz.__file__).resolve().is_relative_to(bridge)
    if args.package:assert Path(sys.executable).resolve()==args.package.resolve()/'onedragon/python/python.exe'
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(args.package or project);data=base/'profile';RuntimePaths(defaults.source,defaults.python,data,defaults.bundle).prepare()
    shutil.copytree(args.models/'onnx_ocr',data/'assets/models/onnx_ocr',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    game=base/'game/ZenlessZoneZero.exe';game.parent.mkdir();game.write_bytes(b'fixture only');sys.path.insert(0,str(defaults.source/'src'));platform.uname()
    from one_dragon.utils import os_utils
    os_utils.set_work_dir(data)
    import numpy as np
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_control import WindowState
    from autozzz.battle_budget import BattleBudget
    from one_dragon.base.operation.application_base import Application
    from one_dragon.base.operation.operation_node import operation_node
    from zzz_od.operation.zzz_operation import ZOperation
    from zzz_od.operation.exchange_ether_battery import ExchangeEtherBattery
    from zzz_od.application.charge_plan.charge_plan_config import ChargePlanItem
    scenario={};checks=[];events=[];service=None;ready=threading.Event();window=[WindowState(True,True,(0,0,1920,1080))]
    def forbidden(*_,**__):raise AssertionError('合成回放不能访问游戏、网络或物理输入。')
    with ExitStack() as scope:
        for target in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                       'autozzz.windows.find_game','autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow','autozzz.windows.user32.ShowWindow',
                       'pynput.keyboard.Controller.press','pynput.mouse.Controller.press','pynput.keyboard.Controller.release','pynput.mouse.Controller.release'):
            scope.enter_context(patch(target,side_effect=forbidden))
        scope.enter_context(patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0))
        scope.enter_context(patch('autozzz.battle_budget.budget_directory',side_effect=lambda:base/'budgets'/str(scenario['case'])))
        try:
            service=OneDragonService(defaults.source,data);service.initialize_execution('cn',str(game));manager=service._execution
            wait(lambda:not manager.busy);assert manager.state=='ready',manager.status();ctx=service.ctx;control=manager.control;controller=ctx.controller
            class Raw:
                position=(0,0)
                def press(self,key):events.append(['down',str(key)])
                def release(self,key):events.append(['up',str(key)])
                def type(self,text):events.append(['type',text])
            controller.keyboard_controller.keyboard.raw=Raw();controller.keyboard_controller.mouse.raw=Raw();controller.bind=lambda _:None
            controller.probe=lambda:window[0];controller.init_before_context_run=lambda:True
            scope.enter_context(patch.object(type(controller),'is_game_window_ready',new_callable=PropertyMock,return_value=True))
            image=np.zeros((1080,1920,3),np.uint8);controller.screenshot=lambda independent=False:control.observe(lambda:(time.time(),image))
            controller.mouse_move=lambda *_:control.checkpoint(require_frame=True)
            controller.click=lambda *_,**__:control.input(lambda:events.append(['click',scenario['phase']])) or True

            def present(screen,name):
                phase=scenario['phase']
                return ((screen=='快捷手册' and name=='以太电池' and phase=='entry')
                    or (screen=='道具处理' and ((name in ('标题-道具处理','按钮-合成') and phase=='material') or name=='合成素材不足' and scenario['insufficient']))
                    or (screen=='道具处理-合成确认' and name in ('标题-合成确认','按钮-确认') and phase=='confirm')
                    or (screen=='道具处理-获得' and name in ('标题-获得','按钮-确认') and phase=='obtained'))
            def find(op,screen,screen_name,area_name,**_):
                return op.round_success(area_name) if present(screen_name,area_name) else op.round_retry('fixture missing')
            def click(op,screen,screen_name,area_name,**_):
                if not present(screen_name,area_name):return op.round_retry('fixture missing',wait=.01)
                controller.click(None)
                if screen_name=='快捷手册':scenario['phase']='details'
                elif screen_name=='道具处理':scenario['phase']='confirm'
                elif screen_name=='道具处理-合成确认':
                    scenario['consume'].append(60);scenario['phase']='obtained' if scenario['obtained'] else 'confirm'
                elif screen_name=='道具处理-获得':scenario['phase']='material'
                return op.round_success(area_name)
            def ocr_click(op,screen,target,*_,**__):
                if target!='[获取]合成' or scenario['phase']!='details':return op.round_retry('fixture missing')
                controller.click(None);scenario['phase']='material';return op.round_success()
            def ocr(op,screen,target,*_,**__):
                return op.round_success(target) if target=='以太电池' and scenario['item'] else op.round_fail('wrong item')
            def header(*_,**__):
                scenario['reads']+=1
                if scenario['paused'] and scenario['reads']==1:
                    before=control.epoch;ready.set();wait(lambda:control.epoch>before)
                return '确认使用下列素材合成'+str(scenario['quantity'])+'个[以太电池]吗？'
            scope.enter_context(patch.object(ZOperation,'round_by_find_area',find));scope.enter_context(patch.object(ZOperation,'round_by_find_and_click_area',click))
            scope.enter_context(patch.object(ZOperation,'round_by_ocr_and_click',ocr_click));scope.enter_context(patch.object(ZOperation,'round_by_ocr',ocr))
            scope.enter_context(patch.object(ZOperation,'check_and_update_current_screen',lambda *_:'道具处理' if scenario['phase']=='material' else None))
            scope.enter_context(patch.object(ctx.ocr,'run_ocr_single_line',side_effect=header))
            class Root(Application):
                def __init__(self,ctx):super().__init__(ctx,'charge_plan',op_name='原生合成回放')
                @operation_node(name='执行实际合成',is_start_node=True)
                def step(self):
                    config=ctx.run_context.get_config('charge_plan',ctx.current_instance_idx,'one_dragon')
                    if scenario['change']:
                        from one_dragon.utils.yaml_utils import safe_load
                        import yaml
                        path=Path(config.file_path);value=safe_load(path.read_bytes());value['plan_list'][0]['plan_times']=100
                        path.write_text(yaml.safe_dump(value,allow_unicode=True),encoding='utf-8')
                    return self.round_by_op_result(ExchangeEtherBattery(ctx,config.plan_list[0]).execute())
            ctx.run_context._application_factory_map['charge_plan'].create_application=lambda instance_idx,group_id:Root(ctx)

            def run(times=1,allowance=60,limit=1,**changes):
                scenario.update(case=len(checks),phase='entry',item=True,insufficient=False,obtained=True,quantity=1,change=False,paused=False,reads=0,consume=[]);scenario.update(changes);ready.clear()
                description=service.describe_configuration('charge_plan');plan=ChargePlanItem(category_name='合成电池',mission_type_name='',mission_name=None,auto_battle_config='不存在的无关策略',plan_times=times).to_dict();plan.pop('plan_id')
                service.save_configuration('charge_plan',dict(restore_charge='不使用',double_reward=False,loop=False,plan_list=[plan]),description['revision'])
                manager.prepare_application('charge_plan');wait(lambda:not manager.busy);assert manager.state=='prepared',manager.status()
                state=manager.status();assert state['resources']['confirmation_required'] and not manager.prepared_requirements['policies']
                try:manager.start_application('charge_plan',fixed_entry=True,budget=dict(id='no-token',allowance=60,battle_limit=1,max_per_battle=60));raise AssertionError('unconfirmed synthesis started')
                except ValueError:pass
                errors=[];pauser=None
                if scenario['paused']:
                    def pause():
                        try:
                            assert ready.wait(10);manager.manual_pause.set();control.set_manual_pause(True)
                            window[0]=WindowState(True,False,window[0].geometry);control.poll();time.sleep(.1)
                            manager.manual_pause.clear();control.set_manual_pause(False);assert control.status()['focus_waiting']
                            window[0]=WindowState(True,True,window[0].geometry);control.poll()
                        except Exception as exc:errors.append(str(exc))
                    pauser=threading.Thread(target=pause);pauser.start()
                manager.start_application('charge_plan',fixed_entry=True,budget=dict(id='run',allowance=allowance,battle_limit=limit,max_per_battle=60),timeout_seconds=30,resource_confirmation=state['resources']['confirmation_id'])
                wait(lambda:not manager.busy)
                if pauser is not None:pauser.join(2);assert not pauser.is_alive() and not errors,errors
                return manager.status(),service.describe_configuration('charge_plan')['fields']

            state,_=run(times=2,allowance=120,limit=2);assert state['state']=='success' and scenario['consume']==[60,60],state
            assert state['budget']['reserved']==120 and state['budget']['pending']==0 and all(r['flow_completed'] for r in state['resource_results'])
            config=ctx.run_context.get_config('charge_plan',ctx.current_instance_idx,'one_dragon');assert config.plan_list[0].run_times==2
            checks.append('actual_source_synthesis_graph_loops_and_saves_plan_progress')
            state,_=run(times=2);assert state['budget']['limited'] and scenario['consume']==[60] and state['budget']['pending']==0,state
            checks.append('shared_energy_and_action_budget_stops_second_synthesis')
            state,_=run(quantity=2);assert state['state']=='failed' and not scenario['consume'] and state['budget']['reserved']==0,state
            checks.append('changed_game_quantity_is_rejected_before_resource_reservation')
            state,_=run(insufficient=True);assert state['state']=='failed' and not scenario['consume'] and not state['budget']['reserved'],state
            checks.append('native_material_shortage_does_not_consume_budget')
            state,_=run(item=False);assert state['state']=='failed' and not scenario['consume'],state
            checks.append('native_wrong_product_is_rejected')
            state,_=run(paused=True);assert state['state']=='success' and scenario['consume']==[60] and scenario['reads']>=2,state
            checks.append('pause_rechecks_confirmation_quantity_before_spending')
            state,_=run(change=True);assert state['state']=='failed' and not scenario['consume'],state
            checks.append('external_plan_changes_revoke_run_confirmation')
            state,_=run(obtained=False);assert state['state'] in ('failed','stopped') and scenario['consume']==[60] and state['budget']['pending']==1,state
            assert not state['resource_results'][0]['reward_verified']
            try:BattleBudget(base/'budgets'/str(scenario['case']),'new','cn',60,1);raise AssertionError('pending synthesis was bypassed')
            except Exception as exc:assert '未结算' in str(exc),str(exc)
            checks.append('missing_obtained_page_keeps_pending_ledger_and_never_repeats_submission')
        finally:
            if service is not None:service.close()
    result=dict(checks=checks,actual_source_synthesis_graph_and_run_slot=True,recognition_and_device_fixtures=True,package=str(args.package.resolve()) if args.package else None,bridge=str(bridge),python=sys.executable,game_accessed=False,game_input_sent=False)
    (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
