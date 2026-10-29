"""实际登录节点和运行槽回放；凭据、截图、键鼠和剪贴板均为替身。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve();marker=data/'.autozzz-login-test'
    if data.exists() and any(data.iterdir()) and not marker.is_file():raise SystemExit('请使用空目录或本工具拥有的目录。')
    data.mkdir(parents=True,exist_ok=True);marker.write_text('isolated login fixture',encoding='utf-8')
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);RuntimePaths(defaults.source,defaults.python,data).prepare()
    platform.uname();sys.path.insert(0,str(defaults.source/'src'))
    import numpy as np
    from pynput.keyboard import Key
    from pynput.mouse import Button
    from autozzz.clipboard_input import ClipboardPaste
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_host import ControlledDevice
    from autozzz.onedragon_control import ObservationExpired,ExecutionStopped,WindowState
    from autozzz.onedragon_execution import OneDragonExecution
    from zzz_od.operation.enter_game.enter_game import EnterGame
    from test_clipboard_fixture import Clipboard
    from autozzz.task_lease import JoinedRunLease
    run_lease=JoinedRunLease.from_environment()
    service=OneDragonService(defaults.source,data,run_lease=run_lease);manager=None;checks=[]
    def forbidden(*_,**__):raise AssertionError('登录回放禁止真实输入、剪贴板或外部操作')
    with ExitStack() as blocks:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo','winreg.SetValueEx','winreg.DeleteValue','autozzz.windows.find_game','autozzz.windows.user32.PostMessageW','pynput.keyboard.Controller.press','pynput.mouse.Controller.press','win32clipboard.OpenClipboard'):
            blocks.enter_context(patch(name,side_effect=forbidden))
        blocks.enter_context(patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0))
        try:
            ctx=service.ctx;ctx.screen_loader.reload()
            manager=OneDragonExecution(ctx,run_lease=run_lease);service._execution=manager;control=manager.control;login=manager.login_control
            clip=Clipboard('original clipboard fixture');login.clipboard=ClipboardPaste(clip)
            fixture=SimpleNamespace(focused=True,world=False,current='',fields={},selected=False,modifiers=set(),clicks=[],submits=0,
                                    interruption=None,keyboard_hook=None,word=None,manual_b=False,menu=False,region='America',target='Asia',dragged=0)
            class Keyboard:
                def press(self,key):
                    if key==Key.ctrl:fixture.modifiers.add(key)
                    elif Key.ctrl in fixture.modifiers:
                        if key=='a':fixture.selected=True
                        elif key=='v':self.type(clip.value or '')
                    if fixture.keyboard_hook is not None and fixture.keyboard_hook(key):fixture.keyboard_hook=None
                def release(self,key):fixture.modifiers.discard(key)
                def type(self,text):
                    value='' if fixture.selected else fixture.fields.get(fixture.current,'')
                    fixture.fields[fixture.current]=value+text;fixture.selected=False
                    if fixture.interruption:
                        interrupt,fixture.interruption=fixture.interruption,None;interrupt('type')
            class Mouse:
                def press(self,key):
                    if fixture.interruption:
                        interrupt,fixture.interruption=fixture.interruption,None;interrupt('click')
                def release(self,key):pass
            raw_keyboard,raw_mouse=Keyboard(),Mouse()
            class Keys:
                def __init__(self):self.keyboard=ControlledDevice(raw_keyboard,control);self.mouse=ControlledDevice(raw_mouse,control)
                def press(self,key):self.keyboard.press(Key.ctrl if key=='ctrl' else key)
                def release(self,key):self.keyboard.release(Key.ctrl if key=='ctrl' else key)
                def tap(self,key):
                    self.press(key)
                    try:control.checkpoint(require_frame=True)
                    finally:self.release(key)
                def reset(self):self.keyboard.reset();self.mouse.reset()
            keys=Keys()
            class Controller:
                keyboard_controller=keys;screenshot_method='auto';is_game_window_ready=True
                screenshot_controller=SimpleNamespace(init_screenshot=lambda *_:None)
                game_win=SimpleNamespace(bound=None)
                def probe(self):return WindowState(True,fixture.focused,(0,0,1920,1080))
                def bind(self,path):pass
                def set_window_title(self,title):pass
                def sync_game_config(self,config):pass
                def init_before_context_run(self):return True
                def screenshot(self):return control.observe(lambda:(time.time(),np.zeros((1080,1920,3),dtype=np.uint8)))
                def drag_to(self,**params):fixture.dragged+=1;control.checkpoint(require_frame=True)
                def click(self,point,**params):
                    control.checkpoint(require_frame=True);keys.mouse.press(Button.left)
                    try:fixture.clicks.append('fixture popup');control.checkpoint(require_frame=True)
                    finally:keys.mouse.release(Button.left)
                    return True
            ctx.controller=Controller();config=ctx.game_account_config
            dummy=data/'client/ZenlessZoneZero.exe';dummy.parent.mkdir();dummy.write_bytes(b'fixture only')
            config.data.update(game_path=str(dummy),game_region='cn',game_language='cn',account='fixture-account-private',password='fixture-password-private')
            config.save();ctx.ready_for_application=True
            manager.connection=(ctx.current_instance_idx,config.game_path,config.game_region,config.game_language)
            def reset(region='cn',clipboard=False):
                keys.reset();fixture.focused=True;fixture.world=False;fixture.current='';fixture.fields={};fixture.selected=False
                fixture.clicks=[];fixture.submits=0;fixture.word=None;fixture.manual_b=False;fixture.interruption=None
                fixture.keyboard_hook=None
                fixture.menu=False;fixture.region='America';fixture.target='Asia';fixture.dragged=0
                config.data['game_region']=region;control.reset();control.poll();control.observe(lambda:None);login.reset()
                operation=EnterGame(ctx);operation.handle_init();operation.use_clipboard=clipboard
                return attach(operation)
            def attach(operation):
                operation.last_screenshot=np.zeros((1080,1920,3),np.uint8)
                def click(screen_name,area_name,**kwargs):
                    fixture.clicks.append(area_name)
                    if '账号输入区域' in area_name:fixture.current='account';fixture.selected=False
                    elif '密码输入区域' in area_name:fixture.current='password';fixture.selected=False
                    keys.mouse.press(Button.left)
                    try:
                        if '账号密码进入游戏' in area_name:fixture.submits+=1
                        if '换服-' in area_name:fixture.region=fixture.target;fixture.menu=False
                        control.checkpoint(require_frame=True)
                    finally:keys.mouse.release(Button.left)
                    return operation.round_success(area_name)
                operation.round_by_click_area=click
                operation.round_by_find_and_click_area=lambda screen,screen_name,area_name,**kwargs:click(screen_name,area_name,**kwargs)
                operation.round_by_find_area=lambda screen,screen_name,area_name,**kwargs:operation.round_success(area_name) if fixture.manual_b and area_name=='B服新-手机号登录' else operation.round_retry('fixture absent')
                operation.is_in_big_world=lambda screen:operation.round_success('大世界') if fixture.world else None
                operation.check_enter_click_status_text=lambda screen:(fixture.word,[])
                operation.check_screen_to_interact=lambda screen:None
                operation.round_by_ocr_and_click_by_priority=lambda *_,**__:open_menu(operation)
                operation.round_by_ocr=lambda screen,text,**kwargs:operation.round_success() if not fixture.menu and fixture.region==text else operation.round_retry('fixture mismatch')
                return operation
            def open_menu(operation):fixture.menu=True;return operation.round_success()
            def tick(method):
                control.poll();control.observe(lambda:None)
                with control.scope():return method()
            def fill(operation,variant):
                method=getattr(operation,{'cn':'input_account_password','cn_new':'input_account_password_new','global':'input_account_password_intl'}[variant])
                for _ in range(12):
                    result=tick(method)
                    if result.is_success:return result
                    assert not result.is_fail,result.status
                raise AssertionError('fixture form did not submit')
            for variant in ('cn','cn_new','global'):
                operation=reset('asia' if variant=='global' else 'cn',clipboard=variant=='cn_new')
                fill(operation,variant)
                assert fixture.fields==dict(account=config.account,password=config.password) and fixture.submits==1
                assert operation.already_login and login.status()['submitted'] and not login.status()['world_verified']
                assert clip.value=='original clipboard fixture' and not fixture.modifiers
                method=getattr(operation,{'cn':'input_account_password','cn_new':'input_account_password_new','global':'input_account_password_intl'}[variant])
                tick(method);assert fixture.submits==1
            checks.append('actual_three_form_nodes_replace_fields_use_controlled_keyboard_or_clipboard_and_never_repeat_observed_submit')

            operation=reset();tick(operation.input_account_password);tick(operation.input_account_password)
            tick(operation.input_account_password)
            def lose_focus(kind):fixture.focused=False;control.poll()
            fixture.interruption=lose_focus
            try:tick(operation.input_account_password);raise AssertionError('partial password input was not interrupted')
            except ObservationExpired:pass
            fixture.focused=True;control.poll();fill(operation,'cn')
            assert fixture.fields==dict(account=config.account,password=config.password) and fixture.submits==1
            checks.append('focus_loss_during_password_input_replaces_both_fields_from_fresh_observation_instead_of_appending_partial_text')

            operation=reset()
            for _ in range(4):tick(operation.input_account_password)
            fixture.interruption=lose_focus
            try:tick(operation.input_account_password);raise AssertionError('agreement click did not lose focus')
            except ObservationExpired:pass
            fixture.focused=True;control.poll();fill(operation,'cn')
            assert fixture.clicks.count('国服-同意按钮')==1 and fixture.submits==1
            operation=reset()
            for _ in range(5):tick(operation.input_account_password)
            fixture.interruption=lose_focus
            try:tick(operation.input_account_password);raise AssertionError('submit click did not lose focus')
            except ObservationExpired:pass
            assert login.status()['submitted'] and fixture.submits==1
            fixture.focused=True;control.poll();fill(operation,'cn');assert fixture.submits==1
            checks.append('physical_press_witness_survives_focus_loss_without_toggling_agreement_or_resending_uncertain_login_submit')

            operation=reset(clipboard=True);tick(operation.input_account_password)
            def pause_before_paste(key):
                if key==Key.ctrl and clip.value==config.account:
                    fixture.focused=False;control.poll();return True
                return False
            fixture.keyboard_hook=pause_before_paste
            try:tick(operation.input_account_password);raise AssertionError('clipboard paste did not stop at focus loss')
            except ObservationExpired:pass
            assert not fixture.fields.get('account') and clip.value=='original clipboard fixture' and not fixture.modifiers
            fixture.focused=True;control.poll();fill(operation,'cn')
            assert fixture.fields==dict(account=config.account,password=config.password) and fixture.submits==1
            checks.append('focus_loss_between_clipboard_copy_and_paste_restores_original_text_and_never_pastes_into_inactive_window')

            operation=reset();fill(operation,'cn');fixture.focused=False;control.poll();control.set_manual_pause(True)
            fixture.focused=True;control.poll();assert control.manual_paused
            try:tick(operation.input_account_password);raise AssertionError('manual pause was cancelled by focus return')
            except ObservationExpired:pass
            control.set_manual_pause(False);tick(operation.input_account_password);assert fixture.submits==1
            control.stop()
            try:tick(operation.input_account_password);raise AssertionError('stopped login sent input')
            except ExecutionStopped:pass
            assert fixture.submits==1
            checks.append('submitted_login_survives_focus_and_manual_pause_without_resubmission_and_stop_blocks_future_input')

            operation=reset('asia');fill(operation,'global')
            for _ in range(5):
                result=tick(operation.check_server)
                if result.is_success:break
            assert result.is_success and fixture.region=='Asia' and login.status()['server_verified']
            fixture.world=True;assert tick(operation.check_screen).is_success and login.status()['world_verified']
            operation=reset('asia');fill(operation,'global');fixture.world=True
            assert tick(operation.check_screen).is_fail and not login.status()['world_verified']
            checks.append('actual_international_server_node_requires_new_frame_confirmation_and_refuses_world_before_region_verified')

            operation=reset('cn_b');fixture.manual_b=True
            result=tick(operation.check_screen);assert not result.is_fail and login.status()['manual_wait']
            assert not fixture.clicks
            fixture.manual_b=False;fixture.world=True
            assert tick(operation.check_screen).is_success
            checks.append('bilibili_phone_or_captcha_waits_without_input_then_accepts_actual_world_detection')

            operation=reset();fill(operation,'cn')
            operation.check_screen_to_interact=forbidden
            result=tick(operation.check_screen);assert not result.is_fail and login.status()['manual_wait'] and fixture.submits==1
            assert 'fixture-' not in json.dumps(login.status())
            checks.append('post_submit_wait_and_report_do_not_echo_credentials_or_fabricate_world_success')

            operation=reset()
            from one_dragon.base.matcher.match_result import MatchResult,MatchResultList
            matches=MatchResultList();matches.append(MatchResult(1,900,500,20,20))
            def absent(screen,screen_name,area_name,**kwargs):return operation.round_retry('fixture absent')
            operation.round_by_find_and_click_area=absent
            def pause_before_popup(seconds):fixture.focused=False;control.poll()
            fake_ocr=SimpleNamespace(run_ocr=lambda screen:{'领取':matches})
            with patch.object(ctx,'ocr',fake_ocr),patch('zzz_od.operation.enter_game.enter_game.time',SimpleNamespace(sleep=pause_before_popup)):
                try:tick(lambda:EnterGame.check_screen_to_interact(operation,operation.last_screenshot));raise AssertionError('popup click did not pause')
                except ObservationExpired:pass
            assert not operation.interact_ignore_word_list and 'fixture popup' not in fixture.clicks
            fixture.focused=True;control.poll()
            with patch.object(ctx,'ocr',fake_ocr),patch('zzz_od.operation.enter_game.enter_game.time',SimpleNamespace(sleep=lambda _:None)):
                tick(lambda:EnterGame.check_screen_to_interact(operation,operation.last_screenshot))
            assert '领取' in operation.interact_ignore_word_list and fixture.clicks.count('fixture popup')==1
            checks.append('post_login_reward_ignore_marker_is_restored_when_focus_loss_prevents_actual_popup_click')

            operation=reset();login.reset('forced_enter');fixture.world=True
            result=tick(operation.wait_loading)
            assert result.is_fail and '换号登录' in result.status and not login.status()['world_verified']
            operation=reset('asia');login.reset('forced_enter');fill(operation,'global');fixture.world=True
            result=tick(operation.wait_loading)
            assert result.is_fail and '服务器' in result.status and not login.status()['world_verified']
            checks.append('loading_node_also_rejects_unwitnessed_forced_login_and_unverified_international_server')

            # 真实 RunSlot 的 Operation 分支；只替换节点的画面和设备。
            operation=reset();manager.login_control.reset('forced_enter')
            original_enter=EnterGame.__init__
            def constructed(op,context,switch=False):
                original_enter(op,context,switch)
                attach(op)
                op.check_login_related=lambda screen:op.round_success('国服-账号密码')
                original_click=op.round_by_find_and_click_area
                def completed(screen,screen_name,area_name,**kwargs):
                    result=original_click(screen,screen_name,area_name,**kwargs)
                    if '账号密码进入游戏' in area_name:fixture.world=True
                    return result
                op.round_by_find_and_click_area=completed
            manager.login_control.reset();config.data['game_region']='cn';config.save()
            manager.connection=(ctx.current_instance_idx,config.game_path,config.game_region,config.game_language)
            prepared=service.prepare_login('forced_enter');manager.worker.join(5)
            state=service.execution_status();assert not state['busy'] and state['state']=='prepared',state
            with patch.object(EnterGame,'__init__',constructed):
                started=service.start_login('forced_enter',timeout_seconds=30);manager.worker.join(15)
            state=service.execution_status();assert state['job_id']==started['job_id'] and not state['busy'] and state['state']=='success',state
            assert state['login']['world_verified'] and fixture.submits==1 and not state['uses_charge']
            try:service.start_login('forced_enter');raise AssertionError('old login preparation reused')
            except ValueError as exc:assert '准备' in str(exc)
            checks.append('actual_login_prepare_and_run_slot_operation_complete_once_without_battle_budget_and_require_new_preparation_for_retry')

            prepared=service.prepare_login('enter');manager.worker.join(5)
            assert service.execution_status()['state']=='prepared'
            with patch.object(manager,'_run_bound_app',side_effect=lambda *_,**__:setattr(manager,'state','success')):
                service.start_login('enter',timeout_seconds=30);manager.worker.join(5)
            state=service.execution_status();assert state['state']=='failed' and '未核实' in state['error'] and not state['login']['world_verified'],state
            checks.append('nominal_operation_success_without_world_evidence_is_reported_failed')

            prepared=service.prepare_login('forced_enter');manager.worker.join(5)
            config.password='changed-fixture-password';config.save()
            with patch.object(manager,'_run_bound_app',side_effect=forbidden):
                service.start_login('forced_enter',timeout_seconds=30);manager.worker.join(5)
            state=service.execution_status();assert state['state']=='failed' and '配置已变化' in state['error'],state
            checks.append('changed_login_configuration_after_preparation_is_rejected_before_window_binding_or_input')

            service.prepare_login('switch');manager.worker.join(5);seen=[]
            def inspect_switch(app_id,factory,**kwargs):
                from zzz_od.operation.enter_game.switch_account import SwitchAccount
                operation=factory(ctx);assert isinstance(operation,SwitchAccount)
                seen.append(type(operation).__name__);manager.state='failed';manager.error='fixture dispatch only'
            with patch.object(manager,'_run_bound_app',side_effect=inspect_switch):
                service.start_login('switch',timeout_seconds=30);manager.worker.join(5)
            assert seen==['SwitchAccount']
            checks.append('switch_mode_constructs_actual_switch_account_operation_without_executing_logout_in_fixture')

            from zzz_od.operation.enter_game.switch_account import SwitchAccount
            from zzz_od.operation.goto.goto_menu import GotoMenu
            from one_dragon.base.operation.operation_base import OperationResult
            reset();switch=SwitchAccount(ctx);switch.handle_init();attach(switch)
            fixture.interruption=lose_focus
            try:tick(switch.more_logout_confirm);raise AssertionError('logout confirmation did not pause')
            except ObservationExpired:pass
            fixture.focused=True;control.poll();tick(switch.more_logout_confirm)
            assert fixture.clicks.count('更多登出确认')==1
            checks.append('logout_confirmation_is_not_repeated_after_observed_press_is_interrupted_by_focus_loss')

            reset();config.save()
            original_switch=SwitchAccount.__init__
            def constructed_switch(operation,context):
                original_switch(operation,context);attach(operation)
                operation.round_by_ocr_and_click=lambda screen,text,**kwargs:operation.round_by_click_area('菜单',text)
                operation.round_by_find_area=lambda screen,screen_name,area_name,**kwargs:operation.round_success(area_name)
                original_click=operation.round_by_find_and_click_area
                def logout(screen,screen_name,area_name,**kwargs):
                    result=original_click(screen,screen_name,area_name,**kwargs)
                    if area_name=='更多登出确认':fixture.world=False
                    return result
                operation.round_by_find_and_click_area=logout
            service.prepare_login('switch');manager.worker.join(5)
            with patch.object(SwitchAccount,'__init__',constructed_switch),patch.object(EnterGame,'__init__',constructed),patch.object(GotoMenu,'execute',return_value=OperationResult(True)):
                service.start_login('switch',timeout_seconds=30);manager.worker.join(15)
            state=service.execution_status()
            assert not state['busy'] and state['state']=='success' and state['login']['world_verified'],state
            assert fixture.submits==1 and fixture.clicks.count('更多登出确认')==1
            checks.append('actual_switch_account_graph_runs_logout_nodes_then_actual_enter_game_graph_in_one_run_slot')

            from autozzz.onedragon_login_account import LoginAccount
            fixture.login_screen=True;fixture.monthly=False;fixture.route=False
            def account_enter(op,context,switch=False):
                constructed(op,context,switch)
                op.round_by_find_area=lambda screen,screen_name,area_name,**kwargs:op.round_success(area_name) if fixture.login_screen and not fixture.world and area_name=='点击进入游戏' else op.round_retry('fixture absent')
                op.check_screen_with_can_go=lambda *_,**__:('快捷手册',True) if fixture.route else (None,False)
                op.round_by_ocr=lambda screen,text,**kwargs:op.round_success(text) if fixture.monthly and text in ('今日到账','月卡剩余') else op.round_retry('fixture absent')
                op.round_by_ocr_and_click_by_priority=lambda words,**kwargs:op.round_by_click_area('打开游戏',words[0])
            for in_world in (False,True):
                reset();fixture.world=in_world;config.save()
                service.prepare_login('account');manager.worker.join(5)
                with patch.object(EnterGame,'__init__',account_enter),patch.object(SwitchAccount,'__init__',constructed_switch),patch.object(GotoMenu,'execute',return_value=OperationResult(True)):
                    service.start_login('account',timeout_seconds=30);manager.worker.join(15)
                state=service.execution_status()
                assert not state['busy'] and state['state']=='success' and state['login']['world_verified'] and fixture.submits==1,state
                assert fixture.clicks.count('更多登出确认')==int(in_world)
            checks.append('account_mode_actual_operation_graph_chooses_forced_title_login_or_world_logout_and_requires_new_submission')
            reset();fixture.login_screen=False
            with patch.object(EnterGame,'__init__',account_enter):
                op=LoginAccount(ctx);op.last_screenshot=np.zeros((1080,1920,3),np.uint8)
                result=tick(op.recognize);assert not result.is_success and op.child is None and not fixture.clicks
                fixture.monthly=True;result=tick(op.recognize)
                assert not result.is_success and op.child is None and fixture.clicks==['今日到账']
                fixture.monthly=False;fixture.route=True;tick(op.recognize)
                assert isinstance(op.child,SwitchAccount)
            fixture.login_screen=True;fixture.route=False
            checks.append('account_origin_waits_without_unknown_clicks_and_only_claims_monthly_popup_with_both_required_markers')
            checks.append('recognized_ingame_screen_with_actual_menu_route_dispatches_switch_account_instead_of_treating_it_as_login')

            # 启动等待、绑定和真实登录图交接；启动器与进程清单仍为替身。
            from autozzz.onedragon_startup import GameStartup
            operation=reset();config.data['game_region']='cn';config.save()
            manager.connection=(ctx.current_instance_idx,config.game_path,config.game_region,config.game_language)
            launches=[];bound=threading.Event();allow_bind=threading.Event()
            original_bind=ctx.controller.bind
            def startup_factory(*args,**kwargs):
                return GameStartup(*args,**kwargs,inventory=lambda:[dict(pid=51,path=str(dummy),created=1.)] if launches else [],
                                   launch=lambda path:launches.append(path),find_window=lambda _:SimpleNamespace(hwnd=999))
            def delayed_bind(path):
                bound.set()
                if not allow_bind.wait(4):raise AssertionError('fixture bind was not released')
                ctx.controller.game_win.bound=SimpleNamespace(hwnd=999);ctx.controller.game_win.process_id=51
            service.prepare_login('forced_enter');manager.worker.join(5)
            with patch('autozzz.onedragon_startup.GameStartup',side_effect=startup_factory),patch.object(ctx.controller,'bind',delayed_bind),patch.object(EnterGame,'__init__',constructed),patch('autozzz.windows.user32.GetAsyncKeyState',side_effect=lambda key:0x8000 if key==0x78 else 0):
                service.start_login('forced_enter',timeout_seconds=30,auto_launch=True)
                deadline=time.monotonic()+3
                while not manager.manual_pause.is_set() and time.monotonic()<deadline:time.sleep(.01)
                assert manager.manual_pause.is_set() and not launches
                manager.pause(False)  # F9 仍按下；交接时不可再次切换暂停。
                assert bound.wait(4)
                manager.pause(True);allow_bind.set()
                deadline=time.monotonic()+3
                while not manager._input_ready and time.monotonic()<deadline:time.sleep(.01)
                assert manager._input_ready and manager.control.manual_paused and not fixture.clicks
                assert manager.status()['control']['manual_paused']
                manager.pause(False);manager.worker.join(15)
            state=service.execution_status();assert not state['busy'] and state['state']=='success' and state['login']['world_verified'],state
            assert len(launches)==1 and fixture.submits==1
            checks.append('actual_startup_and_login_graph_share_one_job_and_launch_once_preserving_manual_pause_and_held_f9_across_binding')

            reset();service.prepare_login('enter');manager.worker.join(5)
            def replaced_bind(path):
                ctx.controller.game_win.bound=SimpleNamespace(hwnd=998);ctx.controller.game_win.process_id=51
            with patch('autozzz.onedragon_startup.GameStartup',side_effect=startup_factory),patch.object(ctx.controller,'bind',replaced_bind),patch.object(EnterGame,'__init__',side_effect=forbidden):
                service.start_login('enter',timeout_seconds=30,auto_launch=True);manager.worker.join(5)
            state=service.execution_status();assert not state['busy'] and state['state']=='failed' and '窗口身份已变化' in state['error'],state
            assert not fixture.clicks
            checks.append('window_identity_change_between_startup_and_binding_refuses_login_before_any_input')

            reset();service.prepare_login('enter');manager.worker.join(5);launched=threading.Event()
            def waiting_startup(*args,**kwargs):
                def missing(_):
                    from autozzz.models import AutomationError
                    raise AutomationError('fixture waiting')
                return GameStartup(*args,**kwargs,inventory=lambda:[],launch=lambda _:launched.set(),find_window=missing)
            with patch('autozzz.onedragon_startup.GameStartup',side_effect=waiting_startup),patch.object(EnterGame,'__init__',side_effect=forbidden):
                service.start_login('enter',timeout_seconds=30,auto_launch=True)
                assert launched.wait(4);manager.stop();manager.worker.join(5)
            state=service.execution_status();assert not state['busy'] and state['state']=='stopped' and not fixture.clicks,state
            checks.append('stop_during_startup_wait_keeps_original_job_and_never_constructs_login_or_application')
            ctx.controller.game_win.bound=None;ctx.controller.bind=original_bind

            # 退出只作用于预先核实的 HWND/PID，关闭窗口后仍等待原进程退出证据。
            reset();config.save();exit_wait=threading.Event();allow_exit=threading.Event();posts=[];closed=[]
            game_window=SimpleNamespace(hwnd=555)
            def request_close(checkpoint,submit):
                checkpoint();submit(lambda:posts.append('wm_close') or True)
                def exited():exit_wait.set();return allow_exit.is_set()
                return SimpleNamespace(exited=exited,close=lambda:closed.append(True))
            game_window.request_close=request_close
            def bind_exit(path):ctx.controller.game_win.bound=game_window;ctx.controller.game_win.process_id=51
            original_probe=ctx.controller.probe
            target=dict(window_handle=555,process_id=51)
            service.prepare_client_close(target);manager.worker.join(5)
            assert service.execution_status()['state']=='prepared'
            with patch.object(ctx.controller,'bind',bind_exit),patch.object(ctx.controller,'probe',side_effect=lambda:WindowState(False,False) if posts else original_probe()),patch.object(ctx.controller.game_win,'clear_binding',create=True,side_effect=lambda:setattr(ctx.controller.game_win,'bound',None)):
                started=service.start_client_close(timeout_seconds=5)
                assert exit_wait.wait(3)
                state=service.execution_status();assert state['job_id']==started['job_id'] and state['busy'] and posts==['wm_close'] and not state['client_exit']['process_exited'],state
                allow_exit.set();manager.worker.join(5)
            state=service.execution_status();assert state['state']=='success' and state['client_exit']['process_exited'] and len(closed)==1,state
            assert ctx.controller.game_win.bound is None
            try:service.start_client_close();raise AssertionError('old close preparation reused')
            except ValueError:pass
            checks.append('client_close_uses_one_verified_window_request_and_waits_original_process_exit_in_same_job_without_duplicate_submission')

            service.prepare_client_close(target);manager.worker.join(5);posts.clear()
            with patch.object(ctx.controller,'bind',replaced_bind):
                service.start_client_close(timeout_seconds=5);manager.worker.join(5)
            state=service.execution_status();assert state['state']=='failed' and '窗口身份已变化' in state['error'] and not posts,state
            checks.append('client_close_refuses_rebound_window_identity_before_sending_close_request')

            service.prepare_client_close(target);manager.worker.join(5);posts.clear();closed.clear();allow_exit.clear();exit_wait.clear()
            with patch.object(ctx.controller,'bind',bind_exit):
                service.start_client_close(timeout_seconds=5);assert exit_wait.wait(3)
                manager.stop();manager.worker.join(5)
            state=service.execution_status();assert state['state']=='stopped' and state['client_exit']['requested'] and not state['client_exit']['process_exited'],state
            assert posts==['wm_close'] and closed==[True]
            checks.append('stop_after_close_request_keeps_unverified_exit_in_report_and_never_retries_or_force_terminates')
            ctx.controller.game_win.bound=None

            current=ctx.game_account_config;current.password='';current.save();clicks=len(fixture.clicks)
            service.prepare_login('switch');manager.worker.join(5)
            state=service.execution_status();assert state['state']=='failed' and '完整登录资料' in state['error'],state
            assert len(fixture.clicks)==clicks
            checks.append('missing_login_credentials_are_rejected_during_preparation_before_any_logout_or_input')
        finally:
            if manager is not None:manager.close();service._execution=None
            if service._ctx is not None:service._ctx.controller=None
            service.close()
            if run_lease is not None:run_lease.close()
    result=dict(checks=checks,count=len(checks),game_accessed=False,real_clipboard_accessed=False,network_blocked=True,leased_worker=run_lease is not None)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
