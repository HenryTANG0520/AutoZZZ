"""实际小地图匹配、截图作业和 RunSlot 调试回放；设备与外部操作硬拦截。"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def wait(predicate, timeout=30):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.01)
    raise AssertionError("原作业未在检查时限内完成；不能自动重开")


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    args.data=args.data.resolve();marker=args.data/'.autozzz-route-recorder-test'
    if args.data.exists() and any(args.data.iterdir()) and not marker.is_file():raise SystemExit('请使用空目录或本工具拥有的目录。')
    args.data.mkdir(parents=True,exist_ok=True);marker.write_text('isolated route test',encoding='utf-8')
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(root);RuntimePaths(defaults.source,defaults.python,args.data).prepare()
    cached=root/'runs/onedragon-20261029/library-exploration-regression/assets/models'
    if not cached.is_dir():raise SystemExit('本检查需要已下载的模型缓存；不自动下载。')
    for name in ('onnx_ocr','flash_classifier'):shutil.copytree(cached/name,args.data/'assets/models'/name,dirs_exist_ok=True)
    platform.uname()
    sys.path.insert(0,str(defaults.source/'src'))
    import cv2
    import numpy as np
    from one_dragon.utils import cv2_utils
    from one_dragon.base.geometry.point import Point
    from one_dragon.base.operation.operation_base import OperationResult
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_library import LibraryStore
    from autozzz.onedragon_control import WindowState
    import autozzz.onedragon_route_recorder as recorder
    from zzz_od.application.world_patrol.world_patrol_area import road_mask_path,icon_yaml_path
    from zzz_od.application.world_patrol.operation.world_patrol_run_route import WorldPatrolRunRoute
    checks=[]
    def forbidden(*_,**__):raise AssertionError('路线回放禁止真实外部操作')
    with ExitStack() as blocks:
        for target in ('subprocess.Popen','winreg.SetValueEx','winreg.DeleteValue','requests.sessions.Session.request',
                       'socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo','autozzz.windows.find_game',
                       'pynput.keyboard.Controller.press','pynput.keyboard.Controller.release','pynput.mouse.Controller.press','pynput.mouse.Controller.release'):
            blocks.enter_context(patch(target,side_effect=forbidden))
        service=OneDragonService(defaults.source,args.data)
        gate=None;original_controller=None
        try:
            ctx=service.ctx
            catalog=service.library_catalog();item=next(row for row in catalog['items'] if row['kind']=='world_patrol_route')
            document=service.library_read(item['kind'],item['key'])
            store=LibraryStore(service);area=store.areas[item['area_id']]
            ctx.screen_loader.reload()
            rect=ctx.screen_loader.get_area('大世界','小地图').rect
            frame=np.full((1080,1920,3),180,np.uint8)
            mini=frame[rect.y1:rect.y2,rect.x1:rect.x2]
            mini[:]=[100,100,100]
            size=mini.shape[0];center=size//2
            cv2.line(mini,(30,center),(size-30,center),(15,15,15),27)
            cv2.line(mini,(center-30,20),(center-30,size-30),(15,15,15),21)
            cv2.rectangle(mini,(center-8,center-8),(center+8,center+8),(255,200,70),-1)
            wrapper=ctx.world_patrol_service.cut_mini_map(frame)
            assert wrapper.play_mask_found
            width,height=wrapper.road_mask.shape[1],wrapper.road_mask.shape[0]
            expected=[300+width//2,250+height//2]
            large=np.zeros((900,900),np.uint8);large[250:250+height,300:300+width]=wrapper.road_mask
            map_path=Path(road_mask_path(area));icons_path=Path(icon_yaml_path(area))
            originals={path:path.read_bytes() for path in (map_path,icons_path)}
            try:
                cv2_utils.save_image(large,str(map_path))
                import yaml
                icons_path.write_text(yaml.safe_dump([dict(icon_name='fixture',template_id='map_icon_01',lm_pos=expected,tp_pos=expected)]),encoding='utf-8')
                ok,png=cv2.imencode('.png',cv2.cvtColor(frame,cv2.COLOR_RGB2BGR));assert ok
                encoded=base64.b64encode(png).decode()
                result=service.route_locate(item['area_id'],expected,encoded)
                assert result['found'] and result['position']==expected,result
                checks.append('actual_dynamic_crop_player_mask_road_snapshot_and_upstream_match_returns_known_pixels')
                missing=frame.copy();missing[rect.y1:rect.y2,rect.x1:rect.x2]=100
                ok,png=cv2.imencode('.png',missing);assert ok
                try:service.route_locate(item['area_id'],expected,base64.b64encode(png).decode());raise AssertionError('没有玩家标记仍接受定位')
                except ValueError as exc:assert '玩家标记' in str(exc)
                checks.append('missing_world_player_marker_rejected_without_old_candidate')

                focused=[False];captures=[];cleanups=[];keys={}
                class Device:
                    def reset(self):pass
                class Controller:
                    def __init__(self,context,control):self.control=control;self.keyboard_controller=Device()
                    def bind(self,path):assert path==str(game.resolve())
                    def probe(self):return WindowState(True,focused[0],(0,0,1920,1080))
                    def screenshot(self):
                        def take():captures.append(1);return time.time(),frame.copy()
                        return self.control.observe(take)
                    def cleanup_after_app_shutdown(self):cleanups.append(1)
                game=args.data/'fixture-game/ZenlessZoneZero.exe';game.parent.mkdir(exist_ok=True);game.write_bytes(b'not executable fixture')
                with patch('autozzz.onedragon_host.ManagedController',Controller),patch('autozzz.windows.user32.GetAsyncKeyState',side_effect=lambda key:keys.get(key,0)):
                    job=service.route_capture_start(item['area_id'],expected,str(game));identifier=job['job_id']
                    wait(lambda:service.route_capture_status(identifier)['control']['focus_waiting'])
                    assert not captures
                    keys[0x78]=0x8000;wait(lambda:service.route_capture_status(identifier)['control']['manual_paused']);keys.clear()
                    focused[0]=True;wait(lambda:not service.route_capture_status(identifier)['control']['focus_waiting'])
                    assert service.route_capture_status(identifier)['control']['manual_paused'] and not captures
                    try:service.library_catalog();raise AssertionError('截图期间允许重载资料')
                    except RuntimeError:pass
                    service.route_capture_pause(identifier,False)
                    wait(lambda:not service.route_capture_status(identifier)['busy'])
                    final=service.route_capture_status(identifier)
                    assert final['state']=='success' and final['result']['position']==expected and len(captures)==1,final
                    assert len(cleanups)==1 and ctx.controller is None
                    checks.append('same_capture_job_waits_focus_manual_pause_survives_focus_return_and_closes_controller')

                    entered=threading.Event();gate=threading.Event();original_locate=recorder.locate_position
                    count=[0]
                    def delayed(*args):
                        count[0]+=1
                        value=original_locate(*args)
                        if count[0]==1:entered.set();gate.wait(10)
                        return value
                    captures.clear()
                    with patch.object(recorder,'locate_position',side_effect=delayed):
                        identifier=service.route_capture_start(item['area_id'],expected,str(game))['job_id']
                        assert entered.wait(5)
                        focused[0]=False;wait(lambda:service.route_capture_status(identifier)['control']['focus_waiting'])
                        gate.set();wait(lambda:count[0]==1 and service.route_capture_status(identifier)['busy'])
                        assert service.route_capture_status(identifier)['result'] is None
                        focused[0]=True;wait(lambda:not service.route_capture_status(identifier)['busy'])
                    assert count[0]==2 and len(captures)==2 and service.route_capture_status(identifier)['result']['position']==expected
                    checks.append('focus_change_during_matching_discards_stale_candidate_and_recaptures_in_original_job')

                    entered.clear();gate.clear()
                    def blocked(*args):entered.set();gate.wait(10);return original_locate(*args)
                    with patch.object(recorder,'locate_position',side_effect=blocked):
                        identifier=service.route_capture_start(item['area_id'],expected,str(game))['job_id']
                        assert entered.wait(5)
                        keys[0x79]=0x8000
                        wait(lambda:service.route_capture_status(identifier)['control']['state']=='stopped')
                        assert service.route_capture_status(identifier)['busy']
                        gate.set();wait(lambda:not service.route_capture_status(identifier)['busy'])
                        keys.clear()
                    final=service.route_capture_status(identifier)
                    assert final['state']=='cancelled' and final['result'] is None,final
                    checks.append('F10_during_matching_cancels_original_job_without_publishing_candidate')
                    try:service.route_capture_status('other-job');raise AssertionError('未知作业编号未拒绝')
                    except ValueError:pass
                    entered.clear();gate.clear()
                    with patch.object(recorder,'locate_position',side_effect=blocked):
                        identifier=service.route_capture_start(item['area_id'],expected,str(game))['job_id']
                        assert entered.wait(5)
                        closer=threading.Thread(target=service._route_capture.close,daemon=False);closer.start()
                        wait(lambda:service.route_capture_status(identifier)['control']['state']=='stopped')
                        assert closer.is_alive() and service.route_capture_status(identifier)['busy']
                        gate.set();closer.join(5);assert not closer.is_alive()
                        assert not service._route_capture.worker.is_alive() and service.route_capture_status(identifier)['result'] is None
                    checks.append('close_cancels_and_waits_original_computing_worker_before_disposing_context')
            finally:
                for path,raw in originals.items():path.write_bytes(raw)

            # 真实模型准备，禁止绑定游戏；Operation 路径仅替换最终 execute 为记录器。
            initial=service.initialize_execution('cn',str(game))
            wait(lambda:not service.execution_status()['busy'],timeout=60)
            assert service.execution_status()['state']=='ready',service.execution_status()
            original_controller=ctx.controller
            current=service.library_read(item['kind'],item['key'])
            request=dict(key=current['key'],revision=current['revision'],start_index=1 if len(current['data']['op_list'])>1 else 0)
            baseline={str(path.relative_to(args.data)):hashlib.sha256(path.read_bytes()).hexdigest() for path in (args.data/'config').rglob('*.yml')}
            initial=service.prepare_application('world_patrol',route_debug=request)
            wait(lambda:not service.execution_status()['busy'],timeout=60)
            prepared=service.execution_status()
            if prepared['state']!='prepared':
                changed={str(path.relative_to(args.data)):hashlib.sha256(path.read_bytes()).hexdigest() for path in (args.data/'config').rglob('*.yml')}
                print(json.dumps(dict(changed_during_prepare=[name for name in set(baseline)|set(changed) if baseline.get(name)!=changed.get(name)]),ensure_ascii=False))
            assert prepared['state']=='prepared' and prepared['prepared_requirements']['routes']==[current['key']],prepared
            assert prepared['prepared_requirements']['route_debug']['start_index']==request['start_index']
            assert prepared['prepared_requirements']['models']==['flash_classifier'] and not prepared['uses_charge'],prepared
            probe=service.runtime_probe()
            assert '400' in probe['ocr_text'] and probe['flash_model']['providers'],probe
            assert not probe['window_bound'] and not probe['upstream_hotkeys_started'],probe
            checks.append('actual_route_debug_preparation_loads_real_policy_and_flash_model_for_only_selected_route')
            before={str(path):path.read_bytes() for path in (args.data/'config').rglob('*world_patrol*.yml')}
            captured=[]
            class RuntimeController:
                keyboard_controller=SimpleNamespace(reset=lambda:None)
                game_win=SimpleNamespace(bound=None)
                is_game_window_ready=True
                def init_before_context_run(self):return True
                def bind(self,path):assert path==str(game.resolve())
                def probe(self):return WindowState(True,True,(0,0,1920,1080))
                def screenshot(self):return service._execution.control.observe(lambda:(time.time(),frame.copy()))
            ctx.controller=RuntimeController()
            def execute(operation):
                operation._init_network()
                captured.append(dict(key=operation.route.full_id,index=operation.current_idx,nodes=list(operation._node_map)))
                return OperationResult(True,'fixture route complete')
            with patch.object(WorldPatrolRunRoute,'execute',execute),patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0):
                service.start_application('world_patrol',timeout_seconds=30)
                wait(lambda:not service.execution_status()['busy'])
            result=service.execution_status()
            assert result['state']=='success' and len(captured)==1,(result,captured)
            assert captured[0]['key']==request['key'] and captured[0]['index']==request['start_index'] and '运行指令' in captured[0]['nodes'],captured
            assert service._execution.backend.run_slot.run_type.value=='operation'
            assert all(Path(path).read_bytes()==raw for path,raw in before.items())
            checks.append('actual_RunSlot_operation_branch_constructs_one_real_route_with_start_index_and_leaves_daily_records_unchanged')
            ctx.controller=original_controller
            bad=dict(request,revision='stale')
            try:service.prepare_application('world_patrol',route_debug=bad);raise AssertionError('旧路线版本仍可调试')
            except ValueError:pass
            for index in (-1,True,len(current['data']['op_list'])):
                try:service.prepare_application('world_patrol',route_debug=dict(request,start_index=index));raise AssertionError('无效开始下标被接受')
                except ValueError:pass
            checks.append('stale_route_version_and_invalid_start_indices_rejected_before_execution')
            # 图片虽不在 config 中，准备后的变更同样必须阻止启动。
            original=map_path.read_bytes();map_path.write_bytes(original+b'\n')
            try:
                service.start_application('world_patrol',timeout_seconds=30)
                wait(lambda:not service.execution_status()['busy'])
                assert service.execution_status()['state']=='failed' and '变化' in service.execution_status()['error']
                assert len(captured)==1
            finally:map_path.write_bytes(original)
            checks.append('map_asset_change_invalidates_prepared_route_before_binding_or_execution')
            service.prepare_application('world_patrol')
            wait(lambda:not service.execution_status()['busy'])
            state=service.execution_status()
            assert state['state']=='prepared' and service._execution.route_debug is None and len(state['prepared_requirements']['routes'])>1,state
            assert 'route_debug' not in state['prepared_requirements']
            checks.append('normal_world_patrol_preparation_clears_debug_mode_and_restores_saved_route_group')
            (args.data/'result.json').write_text(json.dumps(dict(checks=checks,count=len(checks),probe=probe,game_accessed=False,external_operations_blocked=True),ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(checks=checks,count=len(checks),game_accessed=False,external_operations_blocked=True),ensure_ascii=False),flush=True)
        finally:
            if gate is not None:gate.set()
            if original_controller is not None:service.ctx.controller=original_controller
            service.close()


if __name__=='__main__':main()
