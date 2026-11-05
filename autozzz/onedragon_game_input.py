"""账号级键鼠、Xbox、DS4 与镜头配置，写回实际上游 GameConfig。"""
from __future__ import annotations

import math

from .config_transaction import atomic_apply,config_revision
from .run_lock import profile_lock


class GameInputStore:
    def __init__(self,service):self.service,self.ctx,self.root=service,service.ctx,service.data

    def config(self):
        from one_dragon.base.config.yaml_operator import cached_yaml_data
        from zzz_od.config.game_config import GameConfig
        cached_yaml_data.clear()
        return GameConfig(self.ctx.current_instance_idx)

    def _fields(self,config):
        from .onedragon_config import _options
        from .onedragon_environment import field_value
        from one_dragon.base.config.basic_game_config import TypeInputWay,ScreenSizeEnum,FullScreenEnum,MonitorEnum
        from zzz_od.config.game_config import GameKeyAction,GamepadActionEnum,GamepadTypeEnum
        from one_dragon.base.controller.pc_button.xbox_button_controller import XboxButtonEnum
        from one_dragon.base.controller.pc_button.ds4_button_controller import Ds4ButtonEnum
        from pynput.keyboard import Key
        from pynput.mouse import Button
        fields=[]
        def add(name,label,group,kind='str',options=None,**metadata):
            from .onedragon_background import background_mouse_policy
            value=(background_mouse_policy(config) if name=='autozzz_background_mouse' else
                   config.get(name,False)if name=='autozzz_temporary_hdr'else getattr(config,name))
            fields.append(field_value(name,label,value,kind,options,group=group,**metadata))
        add('launch_argument','使用启动参数','游戏启动',kind='bool')
        add('screen_size','窗口尺寸','游戏启动',kind='enum',options=_options(ScreenSizeEnum))
        add('full_screen','全屏','游戏启动',kind='enum',options=_options(FullScreenEnum))
        add('popup_window','无边框窗口','游戏启动',kind='bool')
        add('monitor','显示器序号','游戏启动',kind='enum',options=_options(MonitorEnum))
        add('launch_argument_advance','高级启动参数','游戏启动',hint='Windows 参数格式；有空格的值请用双引号包围。仅对新启动生效。')
        add('autozzz_temporary_hdr','启动时临时禁用 Auto HDR','游戏启动',kind='bool',hint='仅对新启动的客户端生效；窗口就绪或启动中止后恢复该游戏原有 GPU 偏好。')
        add('background_mode','应用流程使用后台','输入',kind='bool',hint='独立战斗、闪避助手使用“战斗设置”的后台开关。')
        add('autozzz_background_mouse','后台鼠标与文本','输入',kind='enum',options=[dict(label='等待游戏回到前台',value='wait'),dict(label='短暂切到游戏后恢复',value='flash')],hint='短暂切换会改变前台窗口和鼠标位置。')
        add('type_input_way','文本输入方式','输入',kind='enum',options=_options(TypeInputWay))
        add('background_gamepad_type','后台手柄类型','输入',kind='enum',options=_options(GamepadTypeEnum))
        for name,label,lo,hi in (('xbox_key_press_time','Xbox 点按时长（秒）',.001,1),('ds4_key_press_time','DS4 点按时长（秒）',.001,1),
                                 ('turn_dx','每度转向像素',-1000,1000),('gamepad_turn_speed','摇杆镜头速度（像素/秒）',0,100000),('mouse_flash_duration','临时切换等待（秒）',.01,1)):
            add(name,label,'时长与镜头',kind='float',minimum=lo,maximum=hi)
        keyboard_options=[dict(label=value,value=value) for value in 'abcdefghijklmnopqrstuvwxyz0123456789']
        keyboard_options += [dict(label=name,value=name) for name in Key.__members__]
        keyboard_options += [dict(label='mouse_'+name,value='mouse_'+name) for name in Button.__members__]
        for prefix,title,choices in (('key','键鼠',keyboard_options),('xbox_key','Xbox',_options(XboxButtonEnum)),('ds4_key','DS4',_options(Ds4ButtonEnum))):
            for action in GameKeyAction:
                add(prefix+'_'+action.value.value,action.value.label,title,kind='suggestion' if prefix=='key' else 'enum',options=choices)
        for prefix,title,choices in (('xbox_action','Xbox 菜单组合键',_options(XboxButtonEnum)),('ds4_action','DS4 菜单组合键',_options(Ds4ButtonEnum))):
            for action in GamepadActionEnum:add(prefix+'_'+action.value.value,action.value.label,title,kind='priority',options=choices,order_hint='')
        return fields

    def _describe(self,config):return dict(app_id='_game_input',revision=config_revision(config),fields=self._fields(config))

    def describe(self):
        self.service._assert_idle()
        with profile_lock(self.root):return self._describe(self.config())

    def save(self,values,revision):
        self.service._assert_idle()
        with profile_lock(self.root):
            config=self.config();fields={field['name']:field for field in self._fields(config)}
            if not isinstance(values,dict) or set(values)-set(fields):raise ValueError('输入配置包含未知字段。')
            from pynput.keyboard import Key
            from pynput.mouse import Button
            for name,value in values.items():
                field=fields[name];kind=field['kind'];options=[option['value'] for option in field['options']]
                if kind=='bool' and type(value)is not bool:raise ValueError(field['label']+'须为开关值。')
                if name=='launch_argument_advance':
                    from .game_launch import windows_arguments
                    windows_arguments(value)
                if kind=='enum' and (type(value)is not str or value not in options):raise ValueError(field['label']+'：请选择有效按键或类型。')
                if kind=='float' and (type(value)not in (int,float) or not math.isfinite(value) or not field['minimum']<=value<=field['maximum']):
                    raise ValueError(field['label']+'：数值超出范围。')
                if kind=='priority' and (not isinstance(value,list) or any(type(key)is not str or key not in options for key in value) or len(set(value))!=len(value)):
                    raise ValueError(field['label']+'：组合键包含无效或重复按键。')
                if kind=='suggestion':
                    valid=type(value)is str and (len(value)==1 and value.isprintable() or value in Key.__members__ or
                          value.startswith('mouse_') and value[6:] in Button.__members__ or len(value)==8 and value.startswith('numpad_') and value[-1] in '0123456789')
                    if not valid:raise ValueError(field['label']+'：请输入单键、特殊键名或 mouse_ 按钮名。')
            def apply():
                for key,value in values.items():
                    if key in ('autozzz_background_mouse','autozzz_temporary_hdr'):config.update(key,value)
                    else:setattr(config,key,value)
            atomic_apply(config,revision,apply,self.root)
            from one_dragon.base.config.yaml_operator import invalidate_cache
            invalidate_cache(str(config.file_path));self.ctx.__dict__['game_config']=config
            return self._describe(config)

    def launch_options(self):
        from .game_launch import LaunchOptions
        self.service._assert_idle()
        with profile_lock(self.root):return LaunchOptions.from_config(self.config()).to_dict()

    def _hdr(self):
        from .game_launch import HdrStore
        return HdrStore(self.ctx.game_account_config.game_path,guard=self.service.runtime_guard.check_current)

    def hdr_status(self):
        self.service._assert_idle()
        with profile_lock(self.root):return self._hdr().status()

    def _hdr_action(self,action):
        from .run_lock import RunLock
        self.service._assert_idle()
        lease=getattr(self.service,'_run_lease',None)
        with (RunLock()if lease is None else lease.scope()),profile_lock(self.root):
            self.service._assert_idle();self.service.runtime_guard.check_current()
            return action(self._hdr())

    def hdr_set(self,enabled,revision,confirmed=False):
        return self._hdr_action(lambda store:store.set(enabled,revision,confirmed))

    def hdr_recover(self,journal_digest,confirmed=False):
        return self._hdr_action(lambda store:store.recover(journal_digest,confirmed))
