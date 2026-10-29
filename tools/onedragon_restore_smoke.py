"""实际恢复电量操作图和 RunSlot；识别、窗口和输入均为隔离夹具。"""
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
from types import SimpleNamespace as NS
from unittest.mock import PropertyMock,patch

def wait(predicate,timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=predicate()
        if value:return value
        time.sleep(.01)
    raise AssertionError('恢复电量回放超时')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--models',type=Path,required=True);parser.add_argument('--package',type=Path);args=parser.parse_args();base=args.data.resolve()
    if base.exists():raise ValueError('请使用新的隔离验证目录。')
    project=Path(__file__).resolve().parents[1];bridge=args.package.resolve()/'onedragon/bridge' if args.package else project;sys.path.insert(0,str(bridge))
    import autozzz
    assert Path(autozzz.__file__).resolve().is_relative_to(bridge)
    if args.package:assert Path(sys.executable).resolve()==args.package.resolve()/'onedragon/python/python.exe'
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(args.package or project);data=base/'profile';RuntimePaths(defaults.source,defaults.python,data,defaults.bundle).prepare()
    for name in ('onnx_ocr','flash_classifier'):shutil.copytree(args.models/name,data/'assets/models'/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    game=base/'game/ZenlessZoneZero.exe';game.parent.mkdir();game.write_bytes(b'fixture only');sys.path.insert(0,str(defaults.source/'src'));platform.uname()
    from one_dragon.utils import os_utils
    os_utils.set_work_dir(data)
    import numpy as np
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_control import WindowState
    from autozzz.onedragon_restore import NONE,BACKUP,ETHER,BOTH
    from one_dragon.base.operation.application_base import Application
    from one_dragon.base.operation.operation_node import operation_node
    from zzz_od.operation.zzz_operation import ZOperation
    from zzz_od.operation.restore_charge import RestoreCharge
    from zzz_od.operation.compendium.combat_simulation import CombatSimulation
    from zzz_od.application.charge_plan.charge_plan_config import ChargePlanItem
    scenario=dict(mode=BACKUP,phase='selection',chosen=None,backup=100,ether=4,available=0,cost=20,source_lists=[],consume=[],obtained=True,change=False,paused=False,stuck=False,reads=0)
    ready=threading.Event();window=[WindowState(True,True,(0,0,1920,1080))]
    events=[];checks=[];service=None
    def forbidden(*_,**__):raise AssertionError('恢复电量回放不得访问游戏、网络或物理输入。')
    with ExitStack() as scope:
        for target in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                       'autozzz.windows.find_game','autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow','autozzz.windows.user32.ShowWindow',
                       'pynput.keyboard.Controller.press','pynput.mouse.Controller.press','pynput.keyboard.Controller.release','pynput.mouse.Controller.release'):
            scope.enter_context(patch(target,side_effect=forbidden))
        scope.enter_context(patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0));scope.enter_context(patch('autozzz.battle_budget.budget_directory',return_value=base/'budgets'))
        try:
            service=OneDragonService(defaults.source,data);start=service.initialize_execution('cn',str(game));manager=service._execution
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
            scope.enter_context(patch.object(ctx.ocr,'run_ocr',side_effect=lambda *_args,**_kwargs:{f"{scenario['available']}/{scenario['cost']}":NS(max=NS(confidence=1.))}))

            def present(screen,name):
                phase=scenario['phase']
                if screen=='实战模拟室':return name=='下一步' and phase=='selection'
                if screen=='恢复电量':
                    return name=='标题-恢复电量' and phase=='source' or name=='标题-快捷使用' and phase=='quick' or name=='标题-获得' and phase=='obtained' or name=='确认' and phase in ('source','quick','obtained')
                return screen=='菜单' and name=='关闭' and phase=='quick'
            def find(op,_image,screen,name,**_):return op.round_success(name) if present(screen,name) else op.round_retry('fixture absent')
            def click(op,_image,screen,name,**options):
                if not present(screen,name):return op.round_retry('fixture absent')
                controller.click()
                if screen=='实战模拟室':scenario['phase']='source'
                elif screen=='菜单':scenario['phase']='source'
                elif scenario['phase']=='source':scenario['phase']='quick'
                elif scenario['phase']=='quick':
                    source=scenario['chosen'];amount=20 if source==RestoreCharge.SOURCE_BACKUP_CHARGE else 1
                    scenario['consume'].append([source,amount]);scenario['available']+=amount if source==RestoreCharge.SOURCE_BACKUP_CHARGE else amount*60
                    scenario['phase']='quick' if scenario['stuck'] else 'obtained' if scenario['obtained'] else 'selection'
                else:scenario['phase']='selection'
                return op.round_success(name,wait=options.get('success_wait',0))
            def source(op,screen,target_cn_list,**_):
                scenario['source_lists'].append(list(target_cn_list));scenario['chosen']=target_cn_list[0]
                controller.click();return op.round_success(target_cn_list[0])
            def amount(op,area):
                source=scenario['chosen'];owned=scenario['backup'] if source==RestoreCharge.SOURCE_BACKUP_CHARGE else scenario['ether']
                if area=='当前数量':
                    scenario['reads']+=1
                    if scenario['reads']>=2:ready.set()
                return owned if area=='当前数量' else 20 if source==RestoreCharge.SOURCE_BACKUP_CHARGE else 1
            scope.enter_context(patch.object(ZOperation,'round_by_find_area',find));scope.enter_context(patch.object(ZOperation,'round_by_find_and_click_area',click));scope.enter_context(patch.object(ZOperation,'round_by_ocr_and_click_by_priority',source));scope.enter_context(patch.object(RestoreCharge,'_get_amount_by_area',amount))

            class Root(Application):
                def __init__(self,ctx):super().__init__(ctx,'charge_plan',op_name='补电实际操作图回放')
                @operation_node(name='调用原生补电',is_start_node=True)
                def step(self):
                    plan=ctx.run_context.get_config('charge_plan',ctx.current_instance_idx,'one_dragon').plan_list[0]
                    selected=CombatSimulation(ctx,plan);selected._init_before_execute();selected.last_screenshot=self.last_screenshot;selected.round_start_time=time.time()
                    selected.click_next()
                    op=RestoreCharge(ctx)
                    if scenario['change']:
                        from one_dragon.utils.yaml_utils import safe_load
                        path=Path(op.config.file_path);value=safe_load(path.read_bytes());value['restore_charge']=NONE
                        import yaml
                        path.write_text(yaml.safe_dump(value,allow_unicode=True),encoding='utf-8')
                    return self.round_by_op_result(op.execute())
            factory=ctx.run_context._application_factory_map['charge_plan'];factory.create_application=lambda instance_idx,group_id:Root(ctx)
            def save(mode):
                description=service.describe_configuration('charge_plan');plan=ChargePlanItem(card_num='1').to_dict();plan.pop('plan_id')
                service.save_configuration('charge_plan',dict(restore_charge=mode,double_reward=False,loop=False,plan_list=[plan]),description['revision'])
            def run(mode,**changes):
                scenario.update(mode=mode,phase='selection',chosen=None,backup=100,ether=4,available=0,cost=20,source_lists=[],consume=[],obtained=True,change=False,paused=False,stuck=False,reads=0);scenario.update(changes);events.clear();ready.clear();save(mode)
                manager.prepare_application('charge_plan');wait(lambda:not manager.busy);assert manager.state=='prepared',manager.status()
                state=manager.status();assert state['resources']['confirmation_required']==(mode!=NONE)
                if mode!=NONE:
                    try:manager.start_application('charge_plan',fixed_entry=True,budget=dict(id='no-token',allowance=20,battle_limit=1,max_per_battle=20));raise AssertionError('unconfirmed restore started')
                    except ValueError:pass
                budget=dict(id='case-'+str(len(checks)),allowance=20,battle_limit=1,max_per_battle=20)
                pauser=None;pause_errors=[]
                if scenario['paused']:
                    def pause_at_quantity():
                        try:
                            assert ready.wait(15)
                            manager.manual_pause.set();control.set_manual_pause(True)
                            window[0]=WindowState(True,False,window[0].geometry);control.poll();time.sleep(.1)
                            manager.manual_pause.clear();control.set_manual_pause(False);assert control.status()['focus_waiting']
                            window[0]=WindowState(True,True,window[0].geometry);control.poll()
                        except Exception as exc:pause_errors.append(str(exc))
                    pauser=threading.Thread(target=pause_at_quantity,daemon=False);pauser.start()
                manager.start_application('charge_plan',fixed_entry=True,budget=budget,timeout_seconds=30,resource_confirmation=state['resources']['confirmation_id'] or None)
                wait(lambda:not manager.busy)
                if pauser is not None:pauser.join(2);assert not pauser.is_alive() and not pause_errors,pause_errors
                return manager.status()
            for mode,expected in ((BACKUP,RestoreCharge.SOURCE_BACKUP_CHARGE),(ETHER,RestoreCharge.SOURCE_ETHER_BATTERY),(BOTH,RestoreCharge.SOURCE_BACKUP_CHARGE)):
                state=run(mode);assert state['state']=='success' and len(scenario['consume'])==1 and scenario['consume'][0][0]==expected,state
                assert state['resource_results'][0]['reward_verified'] and state['resource_results'][0]['flow_completed'];assert state['budget']['reserved']==0
                checks.append('actual_source_restore_'+mode)
            state=run(BOTH,backup=20);assert state['state']=='success' and scenario['consume']==[[RestoreCharge.SOURCE_ETHER_BATTERY,1]],state
            assert scenario['source_lists']==[[RestoreCharge.SOURCE_BACKUP_CHARGE,RestoreCharge.SOURCE_ETHER_BATTERY],[RestoreCharge.SOURCE_ETHER_BATTERY]];checks.append('source_backup_reselection_falls_back_to_ether')
            state=run(BACKUP,obtained=False);assert state['state']=='success' and state['resource_results'][0]['flow_completed'] and not state['resource_results'][0]['reward_verified'],state
            checks.append('source_flow_without_obtained_screen_reports_unverified_resource_result')
            state=run(BACKUP,paused=True);assert state['state']=='success' and len(scenario['consume'])==1 and len(scenario['source_lists'])==2,state
            checks.append('pause_epoch_reselects_source_and_quantity_before_single_confirmation')
            state=run(BACKUP,stuck=True);assert state['state']in ('failed','stopped') and len(scenario['consume'])==1 and not state['resource_results'][0]['reward_verified'],state
            checks.append('unchanged_quick_use_popup_does_not_repeat_resource_submission')
            state=run(BACKUP,change=True);assert state['state']=='failed' and not scenario['consume'],state
            checks.append('changed_restore_mode_is_rejected_before_source_operation')
            state=run(NONE);assert state['state']in ('failed','stopped') and not scenario['consume'],state;checks.append('default_no_restore_still_blocks_recovery')
        finally:
            if service is not None:service.close()
    result=dict(checks=checks,actual_source_restore_graph_and_run_slot=True,recognition_and_device_fixtures=True,package=str(args.package.resolve()) if args.package else None,bridge=str(bridge),python=sys.executable,game_accessed=False,game_input_sent=False)
    (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
