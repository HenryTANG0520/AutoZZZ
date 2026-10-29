"""实际地图领域类、拼接与整组路线迁移回放，不连接游戏。"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import platform
import sys
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',required=True,type=Path);args=parser.parse_args()
    data=args.data.resolve();marker=data/'.autozzz-maps-test'
    if data.exists() and any(data.iterdir()) and not marker.is_file():raise SystemExit('请使用空目录或本工具拥有的目录。')
    data.mkdir(parents=True,exist_ok=True);marker.write_text('isolated actual map test',encoding='utf-8')
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);RuntimePaths(defaults.source,defaults.python,data).prepare()
    platform.uname();sys.path.insert(0,str(defaults.source/'src'))
    import cv2,numpy as np
    from PIL import Image
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_maps import MAP_KIND,snapshot_payload
    from zzz_od.application.devtools.large_map_recorder.large_map_recorder_wrapper import MiniMapSnapshot
    from one_dragon.base.geometry.point import Point
    from one_dragon.utils import yaml_utils
    checks=[]
    def forbidden(*_,**__):raise AssertionError('地图回放禁止真实外部操作')
    with ExitStack() as blocks:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo','winreg.SetValueEx','winreg.DeleteValue','autozzz.windows.find_game','pynput.keyboard.Controller.press','pynput.mouse.Controller.press'):
            blocks.enter_context(patch(name,side_effect=forbidden))
        service=OneDragonService(defaults.source,data)
        try:
            catalog=service.library_catalog();route=next(row for row in catalog['items'] if row['kind']=='world_patrol_route')
            key=route['area_id'];document=service.library_read(MAP_KIND,key);store=service._maps
            initial=store._scope(key);old_preview=copy.deepcopy(document['preview']);old_rows=store._routes(key)
            assert old_rows and document['preview']['width']>0 and any(row['kind']==MAP_KIND for row in catalog['items'])
            checks.append('actual_map_catalog_and_lazy_domain_load_without_models_or_controller')
            document=service.map_edit(document['data'],'scale',150)
            rows=copy.deepcopy(document['preview']['icons']);name=old_rows[0][1]['tp_name']
            selected=next(row for row in rows if row['template_id']=='map_icon_01' and row['icon_name']==name);selected['icon_name']=name+'-迁移验证'
            document=service.map_edit(document['data'],'icons',rows)
            saved=service.library_save(MAP_KIND,key,document['data'],document['revision'])
            assert saved['preview']['width']==round(old_preview['width']*1.5)
            sx=saved['preview']['width']/old_preview['width'];sy=saved['preview']['height']/old_preview['height']
            for path,previous,_ in old_rows:
                current=yaml_utils.safe_load(path.read_bytes())
                if previous['tp_name']==name:assert current['tp_name']==name+'-迁移验证'
                for old,new in zip(previous['op_list'],current['op_list']):assert list(map(int,new['data']))==[round(int(old['data'][0])*sx),round(int(old['data'][1])*sy)]
            world=service.ctx.world_patrol_service;world.load_data()
            for actual in world.get_world_patrol_routes_by_area(store._area(key)):assert world.get_route_tp_icon(actual) is not None
            archive=saved['archive_id'];document=service.map_restore(key,archive,saved['revision'])
            assert store._scope(key)==initial
            checks.append('actual_resize_and_teleport_rename_migrate_all_referencing_routes_and_archive_restores_exact_group')
            document=service.map_edit(document['data'],'scale',100)
            saved=service.library_save(MAP_KIND,key,document['data'],document['revision'])
            extra=old_rows[0][0].with_name('99.yml');assert not extra.exists()
            extra_data=copy.deepcopy(old_rows[0][1]);extra_data['idx']=99
            import yaml
            extra.write_text(yaml.safe_dump(extra_data,allow_unicode=True,sort_keys=False),encoding='utf-8')
            try:
                current=service.library_read(MAP_KIND,key);before_restore=store._scope(key)
                try:service.map_restore(key,saved['archive_id'],current['revision']);raise AssertionError('新增路线后仍覆盖旧坐标帧')
                except ValueError as exc:assert '修改' in str(exc)
                assert store._scope(key)==before_restore
            finally:extra.unlink()
            document=service.map_restore(key,saved['archive_id'],service.library_read(MAP_KIND,key)['revision'])
            assert store._scope(key)==initial
            checks.append('archive_restore_rejects_new_routes_added_after_snapshot_without_overwriting_any_file')

            rows=copy.deepcopy(document['preview']['icons']);rows=[row for row in rows if row['icon_name']!=name]
            document=service.map_edit(document['data'],'icons',rows)
            try:service.library_save(MAP_KIND,key,document['data'],document['revision']);raise AssertionError('引用的传送点被删除')
            except ValueError as exc:assert '引用' in str(exc)
            assert store._scope(key)==initial
            checks.append('referenced_teleport_removal_rejected_without_any_map_or_route_write')

            document=service.library_read(MAP_KIND,key);base_image=store.state['large'].road_mask.copy()
            mask=np.zeros((100,100),np.uint8);cv2.line(mask,(10,50),(90,50),255,13);cv2.line(mask,(50,10),(50,90),255,13)
            payload=snapshot_payload(MiniMapSnapshot(mask,[]))
            document=service.map_edit(document['data'],'captured',payload)
            document=service.map_edit(document['data'],'anchor',[0,0])
            document=service.map_edit(document['data'],'merge')
            sx,sy,dx,dy=store.state['transform'];assert sx==sy==1 and dx>0 and dy>0
            image=store.state['large'].road_mask;padded=np.zeros_like(image)
            padded[round(dy):round(dy)+base_image.shape[0],round(dx):round(dx)+base_image.shape[1]]=base_image
            assert np.any(cv2.bitwise_and(image,cv2.bitwise_not(padded))>0),'新增道路没有进入掩码'
            document=service.library_save(MAP_KIND,key,document['data'],document['revision'])
            for path,previous,_ in old_rows:
                current=yaml_utils.safe_load(path.read_bytes())
                for old,new in zip(previous['op_list'],current['op_list']):assert list(map(int,new['data']))==[int(old['data'][0])+round(dx),int(old['data'][1])+round(dy)]
            document=service.map_restore(key,document['archive_id'],document['revision'])
            assert store._scope(key)==initial
            checks.append('actual_road_copy_edge_extension_moves_icons_and_routes_in_same_coordinate_frame')

            document=service.map_edit(document['data'],'scale',125)
            document=service.map_edit(document['data'],'undo')
            assert document['preview']['width']==old_preview['width'] and not document['draft']
            document=service.map_edit(document['data'],'scale',100)
            previous=old_rows[0][0].read_bytes();old_rows[0][0].write_bytes(previous+b'\n# external\n')
            try:
                try:service.library_save(MAP_KIND,key,document['data'],document['revision']);raise AssertionError('外部修改未拒绝')
                except ValueError as exc:assert '变化' in str(exc)
            finally:old_rows[0][0].write_bytes(previous)
            assert store._scope(key)==initial
            checks.append('undo_restores_draft_frame_and_external_route_edit_blocks_stale_map_publish')

            # 缩小的边缘取整必须在后续放大时，对图标和路线采用完全相同的步骤。
            edge_path,edge_original,edge_raw=old_rows[0];edge_data=copy.deepcopy(edge_original)
            edge=[old_preview['width']-1,old_preview['height']-1];edge_data['op_list'][0]['data']=list(map(str,edge))
            import yaml
            edge_path.write_text(yaml.safe_dump(edge_data,allow_unicode=True,sort_keys=False),encoding='utf-8')
            try:
                document=service.library_read(MAP_KIND,key);rows=copy.deepcopy(document['preview']['icons'])
                tp=next(row for row in rows if row['template_id']=='map_icon_01' and row['icon_name']==edge_data['tp_name']);tp['lm_pos']=edge;tp['tp_pos']=edge
                document=service.map_edit(document['data'],'icons',rows)
                document=service.map_edit(document['data'],'scale',25);document=service.map_edit(document['data'],'scale',400)
                saved=service.library_save(MAP_KIND,key,document['data'],document['revision'])
                tp=next(row for row in saved['preview']['icons'] if row['icon_name']==edge_data['tp_name'])
                assert list(map(int,yaml_utils.safe_load(edge_path.read_bytes())['op_list'][0]['data']))==tp['tp_pos']
                service.map_restore(key,saved['archive_id'],saved['revision'])
            finally:edge_path.write_bytes(edge_raw)
            assert store._scope(key)==initial
            checks.append('multiple_resize_edge_rounding_uses_same_steps_for_icons_and_route_points')

            document=service.library_read(MAP_KIND,key)
            document=service.map_edit(document['data'],'scale',110)
            original_put=store.tx._put
            def interrupted(path,raw):
                if path.name=='icon.yml':raise SystemExit('fixture process exit')
                original_put(path,raw)
            with patch.object(store.tx,'_put',side_effect=interrupted):
                try:service.library_save(MAP_KIND,key,document['data'],document['revision']);raise AssertionError('中断未发生')
                except SystemExit:pass
            assert store.tx.pending.exists()
            try:service.initialize_execution('cn','no-game.exe');raise AssertionError('混合地图版本仍初始化执行')
            except ValueError as exc:assert '未完成' in str(exc)
            result=service.map_recover();assert result['recovered'] and store._scope(key)==initial
            checks.append('interrupted_real_map_group_blocks_execution_until_original_files_recovered')

            empty=next(row for row in catalog['items'] if row['kind']==MAP_KIND and not store._paths(row['key'])[0].exists() and not store._paths(row['key'])[2])
            document=service.library_read(MAP_KIND,empty['key']);assert document['preview']['width']==0
            new_payload=snapshot_payload(MiniMapSnapshot(mask,[('map_icon_01',Point(50,50))]))
            document=service.map_edit(document['data'],'captured',new_payload);document=service.map_edit(document['data'],'merge')
            rows=copy.deepcopy(document['preview']['icons']);rows[0]['icon_name']='录制传送点'
            document=service.map_edit(document['data'],'icons',rows)
            saved=service.library_save(MAP_KIND,empty['key'],document['data'],document['revision']);created=saved['archive_id']
            assert saved['preview']['width']==300 and store._paths(empty['key'])[0].exists()
            document=service.map_restore(empty['key'],created,saved['revision'])
            assert document['preview']['width']==0 and not store._paths(empty['key'])[0].exists()
            checks.append('new_map_uses_actual_initial_snapshot_named_teleport_save_and_absent_file_archive_restore')
            # 真实取景、双帧合并与观察中断，只替换窗口和输入设备。
            document=service.library_read(MAP_KIND,key)
            service.ctx.screen_loader.reload();rect=service.ctx.screen_loader.get_area('大世界','小地图').rect
            frame=np.full((1080,1920,3),180,np.uint8);mini=frame[rect.y1:rect.y2,rect.x1:rect.x2];mini[:]=100
            center=mini.shape[0]//2
            cv2.line(mini,(20,center),(mini.shape[1]-20,center),(15,15,15),21)
            cv2.rectangle(mini,(center-8,center-8),(center+8,center+8),(255,200,70),-1)
            second=frame.copy();cv2.line(second[rect.y1:rect.y2,rect.x1:rect.x2],(30,20),(30,mini.shape[0]-20),(15,15,15),17)
            game=data/'fixture-game/ZenlessZoneZero.exe';game.parent.mkdir(exist_ok=True);game.write_bytes(b'non-executable fixture')
            from autozzz.onedragon_control import WindowState
            focused=[True];mode=['normal'];turns=[];captures=[]
            class Device:
                def reset(self):pass
            class Controller:
                def __init__(self,ctx,control):self.control=control;self.keyboard_controller=Device();self.count=0
                def bind(self,path):assert path==str(game.resolve())
                def probe(self):return WindowState(True,focused[0],(0,0,1920,1080))
                def screenshot(self):
                    def take():
                        value=frame if self.count==0 else second;self.count+=1;captures.append(1);return time.time(),value.copy()
                    return self.control.observe(take)
                def turn_by_angle_diff(self,value):
                    turns.append(value)
                    if mode[0]=='interrupt':focused[0]=False;self.control.poll();self.control.checkpoint(require_frame=True)
                    if mode[0]=='pause':self.control.set_manual_pause(True)
                def cleanup_after_app_shutdown(self):pass
            def wait(predicate):
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    if predicate():return
                    time.sleep(.01)
                raise AssertionError('原取景任务未完成；不能自动重新启动')
            with patch('autozzz.onedragon_host.ManagedController',Controller),patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0):
                job=service.map_capture_start(document['data'],str(game),True);identifier=job['job_id']
                wait(lambda:not service.map_capture_status(identifier)['busy'])
                result=service.map_capture_status(identifier);assert result['state']=='success' and not result['result']['warning'],result
                assert len(result['result']['views'])==2
                import base64,io
                with Image.open(io.BytesIO(base64.b64decode(result['result']['views'][0]))) as preview:
                    assert preview.getpixel((preview.width//2,preview.height//2))==(255,200,70)
                json.dumps(result)
                assert turns==[180] and len(captures)==2 and store.state['mini'] is None and not store.state['dirty']
                document=service.map_edit(document['data'],'captured',result['result'])
                from autozzz.onedragon_maps import mini_snapshot
                first_mask=mini_snapshot(service.ctx,frame,.7).road_mask
                assert np.any(cv2.bitwise_and(store.state['mini'].road_mask,cv2.bitwise_not(first_mask))>0)
                assert store._scope(key)==initial
                checks.append('actual_double_view_snapshot_merges_both_road_masks_once_without_saving_map')
                for operation in ('interrupt','pause'):
                    turns.clear();captures.clear();mode[0]=operation
                    identifier=service.map_capture_start(document['data'],str(game),True)['job_id']
                    if operation=='interrupt':
                        wait(lambda:service.map_capture_status(identifier)['control']['focus_waiting']);focused[0]=True
                    else:
                        wait(lambda:service.map_capture_status(identifier)['control']['manual_paused']);service.map_capture_pause(identifier,False)
                    wait(lambda:not service.map_capture_status(identifier)['busy'])
                    result=service.map_capture_status(identifier)
                    assert result['state']=='success' and result['result']['warning'] and turns==[180] and len(captures)==2,result
                    assert len(result['result']['views'])==1
                checks.append('focus_or_manual_pause_during_rotation_keeps_original_job_uses_fresh_frame_and_never_repeats_turn')
            image_path=data/'synthetic-client.png'
            noisy=np.random.default_rng(7).integers(150,185,(1080,1920),dtype=np.uint8);noisy=np.stack([noisy]*3,axis=2)
            noisy[rect.y1:rect.y2,rect.x1:rect.x2]=mini;Image.fromarray(noisy).save(image_path)
            from autozzz.onedragon_route_recorder import encode_client_image
            encoded=encode_client_image(image_path);assert len(encoded)>65536
            document=service.map_edit(document['data'],'import',[encoded]);document=service.map_edit(document['data'],'append_frame',encoded)
            assert store.state['mini'] is not None and store._scope(key)==initial
            checks.append('large_client_frames_are_accepted_and_merged_sequentially_without_generic_text_limit')
            assert service.ctx.controller is None and service.ctx.ocr._model is None and not service.ctx._btn_listener_started
            (data/'result.json').write_text(json.dumps(dict(checks=checks,count=len(checks),game_accessed=False,external_operations_blocked=True),ensure_ascii=False,indent=2),encoding='utf-8')
            (data/'ui-descriptions.json').write_text(json.dumps(dict(catalog=service.library_catalog(),map=service.library_read(MAP_KIND,key)),ensure_ascii=False),encoding='utf-8')
            print(json.dumps(dict(checks=checks,count=len(checks),game_accessed=False,external_operations_blocked=True),ensure_ascii=False),flush=True)
        finally:service.close()


if __name__=='__main__':main()
