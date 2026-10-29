"""保留上游登录和加载识别，约束凭据输入、提交和服务器选择。"""
from __future__ import annotations

import copy
from contextvars import ContextVar
from functools import update_wrapper
import hashlib
import json

from .clipboard_input import ClipboardPaste,ClipboardFailure
from .onedragon_control import ObservationExpired,ExecutionStopped

_submission = ContextVar('autozzz_login_submission',default=None)


class LoginControl:
    FORMS = {
        'cn':('国服-账号输入区域','国服-密码输入区域','国服-同意按钮','国服-账号密码进入游戏'),
        'cn_new':('国服-账号输入区域-新','国服-密码输入区域-新','国服-同意按钮-新','国服-账号密码进入游戏-新'),
        'global':('国际服-账号输入区域','国际服-密码输入区域',None,'国际服-账号密码进入游戏'),
    }
    REGIONS={'asia':('Asia','国际服-换服-亚洲'),'us':('America','国际服-换服-美国'),
             'eu':('Europe','国际服-换服-欧洲'),'twhkmo':('TW,HK,MO','国际服-换服-港澳台')}

    def __init__(self,ctx,control,clipboard=None):
        self.ctx,self.control,self.clipboard=ctx,control,clipboard
        self.originals=[];self.enforce_region=False;self.reset()

    def reset(self,mode=''):
        self.mode=mode;self.enforce_region=bool(mode in ('switch','forced_enter','account') and self.ctx.game_account_config.game_region in self.REGIONS)
        self.enforce_submission=mode in ('switch','forced_enter','account')
        self.loading_seen=False
        self.progress=dict(phase='等待登录',submitted=False,world_verified=False,server_verified=False,manual_wait=False)

    def _patch(self,cls,name,replacement):
        original=getattr(cls,name);update_wrapper(replacement,original)
        if hasattr(original,'operation_node_annotation'):
            node=copy.copy(original.operation_node_annotation);node.op_method=replacement;replacement.operation_node_annotation=node
        self.originals.append((cls,name,original));setattr(cls,name,replacement)

    def _state(self,operation,variant):
        config=self.ctx.game_account_config
        identity=hashlib.sha256(json.dumps([self.ctx.current_instance_idx,config.game_region,config.account,config.password],ensure_ascii=False).encode()).hexdigest()
        state=getattr(operation,'_autozzz_login',None)
        if state is None:
            state=operation._autozzz_login=dict(variant=variant,identity=identity,epoch=self.control.epoch,phase='account_focus',submitted=False,agreement=False)
        if state['identity']!=identity or state['variant']!=variant:raise ValueError('登录配置已变化，请重新准备。')
        if state['epoch']!=self.control.epoch and not state['submitted']:
            state.update(epoch=self.control.epoch,phase='account_focus')
        return state

    def _witness(self,operation,state,kind,action):
        from pynput.mouse import Button
        device=self.ctx.controller.keyboard_controller.mouse
        previous=device.before_press
        def before(key):
            if previous is not None:previous(key)
            if _submission.get() is state and key==Button.left:
                if kind=='submit':
                    state.update(submitted=True,phase='submitted');operation.already_login=True
                    self.progress.update(submitted=True,phase='等待登录结果',server_verified=False)
                    self.enforce_region=self.ctx.game_account_config.game_region in self.REGIONS
                elif kind=='agreement':state['agreement']=True
                else:state['clicked']=True
        token=_submission.set(state);device.before_press=before
        try:return action()
        finally:device.before_press=previous;_submission.reset(token)

    def _replace(self,operation,text):
        keyboard=self.ctx.controller.keyboard_controller
        try:
            keyboard.press('ctrl')
            keyboard.tap('a')
        finally:keyboard.release('ctrl')
        self.control.checkpoint(require_frame=True)
        if operation.use_clipboard:
            if self.clipboard is None:self.clipboard=ClipboardPaste()
            self.clipboard.paste(self.control,keyboard,text)
        else:keyboard.keyboard.type(text)
        self.control.checkpoint(require_frame=True)

    def _password(self,operation,variant):
        self.control.checkpoint(require_frame=True)
        config=self.ctx.game_account_config
        if not isinstance(config.account,str) or not isinstance(config.password,str) or min(len(config.account.strip()),len(config.password.strip()))<=3:
            return operation.round_fail('登录账号与密码均须超过 3 个字符。')
        self.ctx.push_service.protect_transient(config.account,config.password)
        state=self._state(operation,variant);account,password,agreement,submit=self.FORMS[variant]
        phase=state['phase'];self.progress.update(phase='填写登录资料',manual_wait=False)
        try:
            if phase=='submitted':
                self.progress.update(phase='等待登录结果',submitted=True)
                return operation.round_success(submit)
            if phase in ('account_focus','password_focus'):
                result=operation.round_by_click_area('打开游戏',account if phase=='account_focus' else password)
                if result.is_success:state['phase']='account_type' if phase=='account_focus' else 'password_type'
                return operation.round_wait('等待登录输入框',wait=.3)
            if phase in ('account_type','password_type'):
                self._replace(operation,config.account if phase=='account_type' else config.password)
                state['phase']='password_focus' if phase=='account_type' else 'agreement' if agreement else 'submit'
                return operation.round_wait('等待登录输入',wait=.2)
            if phase=='agreement':
                if not state['agreement']:
                    result=self._witness(operation,state,'agreement',lambda:operation.round_by_click_area('打开游戏',agreement))
                    if not result.is_success:return result
                state['phase']='submit';return operation.round_wait('等待登录提交',wait=.2)
            result=self._witness(operation,state,'submit',lambda:operation.round_by_find_and_click_area(operation.last_screenshot,'打开游戏',submit,success_wait=.3,retry_wait=.5))
            if state['submitted']:return operation.round_success(submit)
            return result
        except (ObservationExpired,ExecutionStopped):raise
        except ClipboardFailure as exc:return operation.round_fail(str(exc))
        except Exception:return operation.round_fail('登录输入未完成，请检查输入方式或助手权限。')

    def _world_validation(self,operation):
        manual_bilibili=self.ctx.game_account_config.game_region=='cn_b' and (operation.already_login or self.progress['manual_wait'])
        if self.enforce_submission and not self.progress['submitted'] and not manual_bilibili:
            return operation.round_fail('尚未核实本次换号登录，不能继续运行该账号。')
        if self.enforce_region and not self.progress['server_verified']:
            return operation.round_fail('已进入游戏，但尚未核实国际服服务器；请返回登录页后重试。')
        return None

    def _world(self,operation):
        if operation.last_screenshot is None:return None
        result=operation.is_in_big_world(operation.last_screenshot)
        if result is None or not result.is_success:return None
        invalid=self._world_validation(operation)
        if invalid is not None:return invalid
        interact=operation.check_screen_to_interact(operation.last_screenshot)
        if interact is not None:
            self.loading_seen=True;self.progress.update(phase='处理进入游戏弹窗',manual_wait=False)
            return interact
        self.progress.update(phase='已进入大世界',world_verified=True,manual_wait=False)
        return result

    def _server(self,operation):
        region=self.REGIONS.get(self.ctx.game_account_config.game_region)
        if region is None:return operation.round_fail('国际服服务器配置无效。')
        if operation.last_screenshot is None:return operation.round_wait('等待当前游戏截图',wait=.5)
        self.enforce_region=True
        state=getattr(operation,'_autozzz_server',None)
        if state is None or state['epoch']!=self.control.epoch:
            state=operation._autozzz_server=dict(epoch=self.control.epoch,phase='open',dragged=False)
        self.control.checkpoint(require_frame=True)
        selector=self.ctx.screen_loader.get_area('打开游戏','国际服-换服')
        self.progress.update(phase='核实国际服服务器',manual_wait=False)
        if state['phase']=='verify':
            result=operation.round_by_ocr(operation.last_screenshot,region[0],area=selector,lcs_percent=.9)
            if result.is_success:
                self.progress['server_verified']=True
                return operation.round_success('国际服服务器已核实')
            state.update(phase='open',dragged=False)
            return operation.round_wait('等待服务器选择结果',wait=.5)
        if state['phase']=='open':
            result=operation.round_by_ocr_and_click_by_priority([value[0] for value in self.REGIONS.values()],screen=operation.last_screenshot,area=selector,pre_delay=0,success_wait=.3,retry_wait=.5)
            if result.is_success:state['phase']='select'
            else:self.progress.update(phase='等待登录结果或人工验证',manual_wait=True)
            return operation.round_wait('等待国际服服务器列表',wait=.5)
        result=operation.round_by_find_and_click_area(operation.last_screenshot,'打开游戏',region[1],success_wait=.3,retry_wait=.5)
        if result.is_success:
            state['phase']='verify';return operation.round_wait('核实服务器选择',wait=.5)
        if not state['dragged']:
            state['dragged']=True
            from one_dragon.base.geometry.point import Point
            area=self.ctx.screen_loader.get_area('打开游戏','国际服-换服-美国')
            self.ctx.controller.drag_to(start=area.center,end=area.center+Point(0,200))
        return operation.round_wait('等待目标服务器条目',wait=.5)

    def install(self):
        from zzz_od.operation.enter_game.enter_game import EnterGame
        from zzz_od.operation.enter_game.switch_account import SwitchAccount
        owner=self;initialize=EnterGame.handle_init;check=EnterGame.check_screen;wait=EnterGame.wait_loading;interact=EnterGame.check_screen_to_interact
        def init(operation):
            initialize(operation);operation._autozzz_login=None;operation._autozzz_server=None
            owner.loading_seen=False
            owner.progress=dict(phase='识别登录页面',submitted=False,world_verified=False,server_verified=False,manual_wait=False)
        self._patch(EnterGame,'handle_init',init)
        for name,variant in (('input_account_password','cn'),('input_account_password_new','cn_new'),('input_account_password_intl','global')):
            def password(operation,variant=variant):return owner._password(operation,variant)
            self._patch(EnterGame,name,password)
        def screen(operation):
            if operation.last_screenshot is None:return check(operation)
            result=owner._world(operation)
            if result is not None:return result
            screen=operation.last_screenshot
            if owner.ctx.game_account_config.game_region=='cn_b' and operation.round_by_find_area(screen,'打开游戏','B服新-手机号登录').is_success:
                owner.progress.update(phase='请在游戏中完成B服登录或验证码',manual_wait=True)
                return operation.round_wait(owner.progress['phase'],wait=1)
            word,_=operation.check_enter_click_status_text(screen)
            if word is not None:owner.loading_seen=True
            if owner.ctx.game_account_config.game_region in owner.REGIONS and not owner.progress['server_verified'] and (operation.already_login or word=='点击进入游戏'):
                result=owner._server(operation)
                return operation.round_wait('服务器已核实',wait=.2) if result.is_success else result
            if owner.progress['submitted'] and word is None:
                result=operation.check_screen_to_interact(screen) if owner.loading_seen else None
                if result is not None:return result
                owner.progress.update(phase='等待登录结果或人工验证',manual_wait=True)
                return operation.round_wait(owner.progress['phase'],wait=.5)
            return check(operation)
        self._patch(EnterGame,'check_screen',screen)
        def server(operation):
            result=owner._world(operation)
            return result if result is not None else owner._server(operation)
        self._patch(EnterGame,'check_server',server)
        def popup(operation,screen):
            previous=list(operation.interact_ignore_word_list);observed=dict(clicked=False)
            try:return owner._witness(operation,observed,'popup',lambda:interact(operation,screen))
            except BaseException:
                if not observed['clicked']:operation.interact_ignore_word_list[:]=previous
                raise
        self._patch(EnterGame,'check_screen_to_interact',popup)
        def loading(operation):
            owner.loading_seen=True
            result=wait(operation)
            if result.is_success and result.status=='大世界':
                invalid=owner._world_validation(operation)
                if invalid is not None:return invalid
                owner.progress.update(phase='已进入大世界',world_verified=True,manual_wait=False)
            return result
        self._patch(EnterGame,'wait_loading',loading)
        logout_init=SwitchAccount.handle_init;logout_confirm=SwitchAccount.more_logout_confirm
        def switch_init(operation):
            logout_init(operation);operation._autozzz_logout=dict(clicked=False)
        def confirm_logout(operation):
            state=operation._autozzz_logout
            owner.progress.update(phase='正在退出当前游戏账号',manual_wait=False)
            if state['clicked']:return operation.round_success('登出确认已尝试')
            return owner._witness(operation,state,'logout',lambda:logout_confirm(operation))
        self._patch(SwitchAccount,'handle_init',switch_init)
        self._patch(SwitchAccount,'more_logout_confirm',confirm_logout)

    def status(self):return dict(self.progress)

    def close(self):
        for cls,name,original in reversed(self.originals):setattr(cls,name,original)
        self.originals.clear()
