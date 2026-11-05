"""Six source agent templates with isolated drafts and recoverable grouped publication."""
from __future__ import annotations
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import uuid
from unittest.mock import patch

from .map_transaction import MapTransaction, fingerprint
from .run_lock import RunLock, profile_lock

ID = re.compile(r'^[a-z][a-z0-9_]{0,79}$')
FILES = ('raw.png','mask.png','config.yml','features.xml')


class TemplateTransaction(MapTransaction):
    def __init__(self,root):
        self.root=Path(root).resolve();self.pending=self.inside(self.root/'config/autozzz-agent-templates-transaction.json')
    def inside(self,path):
        absolute=Path(path).absolute()
        if absolute!=absolute.resolve() or absolute.exists() and absolute.is_file() and absolute.stat().st_nlink!=1:
            raise ValueError('模板资料路径被重定向或共享。')
        return super().inside(absolute)
    @staticmethod
    def _allowed(relative):
        p=relative.parts
        return (len(p)==5 and p[:2]==('assets','template') and p[2]in ('battle','hollow','predefined_team')
                and re.fullmatch(r'avatar_(?:1_|2_|chain_|quick_)?[a-z][a-z0-9_]{0,79}',p[3]) is not None and p[4]in FILES)
    def archive_dir(self,identifier):
        if type(identifier)is not str or re.fullmatch('[0-9a-f]{32}',identifier)is None:raise ValueError('模板归档编号无效。')
        return self.inside(self.root/'archives/agent-templates'/identifier)
    def assert_clear(self):
        if self.pending.exists():raise ValueError('模板保存尚未恢复，请先恢复未完成保存。')


class AgentTemplates:
    def __init__(self,service):
        from zzz_od.gui.view.devtools.agent_template_generator_interface import TEMPLATE_STEPS
        self.service=service;self.root=service.data;self.steps=TEMPLATE_STEPS;self.tx=TemplateTransaction(self.root)
        self.draft=None

    def agent_id(self,value):
        if type(value)is not str or ID.fullmatch(value)is None:raise ValueError('模板名称须以小写英文字母开头，仅含小写字母、数字、下划线，最多80字。')
        return value

    def paths(self,agent):
        self.agent_id(agent)
        return [self.tx.inside(self.root/'assets/template'/step.sub_dir/step.template_id_of(agent)) for step in self.steps]

    def signature(self,agent):
        values={str(path/file):fingerprint(self.tx.read(path/file)) for path in self.paths(agent) for file in FILES}
        return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()

    def catalog(self):
        from .agent_template_registry import registrations,recognition
        registered=registrations()
        rows={}
        for step in self.steps:
            parent=self.tx.inside(self.root/'assets/template'/step.sub_dir)
            prefix=step.template_id_of('')
            if not parent.exists():continue
            for path in parent.iterdir():
                if not path.is_dir() or not path.name.startswith(prefix):continue
                agent=path.name[len(prefix):]
                if ID.fullmatch(agent):rows[agent]=True
        result=[]
        for agent in sorted(rows):
            paths=self.paths(agent);done=sum((path/'raw.png').is_file() for path in paths)
            if done:result.append(dict(id=agent,completed=done,total=len(self.steps),registration=recognition(agent)))
        archive_root=self.tx.inside(self.root/'archives/agent-templates');archives=[]
        if archive_root.exists():
            for path in sorted(archive_root.iterdir(),reverse=True):
                try:meta,_,_=self.tx.load(path.name)
                except (ValueError,OSError):continue
                archives.append(dict(id=path.name,metadata=meta['metadata']))
        return dict(agents=result,steps=[dict(key=s.key,name=s.name,hint=s.hint,side_selectable=s.side_selectable) for s in self.steps],
                    archives=archives,pending=self.tx.pending.exists(),registered_templates=list(registered.values()))

    def open(self,agent=None):
        if agent is not None:self.agent_id(agent)
        self.draft=dict(token=uuid.uuid4().hex,agent=agent,signature=None if agent is None else self.signature(agent),
                        screens=[None]*len(self.steps),sides=['left']*len(self.steps))
        return self.view()

    def current(self,token):
        if self.draft is None or self.draft['token']!=token:raise ValueError('模板草稿已变化，请重新打开。')
        return self.draft

    def reference(self,index,side):
        from one_dragon.base.screen.template_info import TemplateInfo
        from one_dragon.base.geometry.point import Point
        ref=TemplateInfo('template',self.steps[index].template_ref)
        if not ref.is_file_exists or ref.get_template_rect_by_point() is None:raise ValueError('缺少源参考模板：'+self.steps[index].template_ref)
        dx=0
        if self.steps[index].side_selectable and side=='right':
            from one_dragon.base.screen.screen_loader import ScreenContext
            screen=ScreenContext();screen.reload()
            left=screen.get_area('战斗画面','连携技-1');right=screen.get_area('战斗画面','连携技-2')
            if left is None or right is None:raise ValueError('缺少源连携区域。')
            dx=right.rect.x1-left.rect.x1
        ref.point_list=[Point(p.x+dx,p.y) for p in ref.point_list]
        return ref

    @staticmethod
    def png(image):
        import cv2
        if image is None:return None
        if image.ndim==3:image=cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
        ok,data=cv2.imencode('.png',image)
        if not ok:raise ValueError('无法编码模板。')
        return data.tobytes()

    def import_image(self,token,index,path,side='left'):
        import cv2
        import numpy as np
        from PIL import Image
        draft=self.current(token)
        if type(index)is not int or not 0<=index<len(self.steps) or side not in ('left','right'):raise ValueError('模板步骤或连携方向无效。')
        path=Path(path)
        if not path.is_file() or path.stat().st_size>80_000_000:raise ValueError('PNG截图不存在或超过80MB。')
        with Image.open(path) as header:
            if header.format!='PNG' or header.width*header.height>36_000_000 or header.width<320 or abs(header.width/header.height-16/9)>.02:
                raise ValueError('请选择完整的16:9游戏客户区PNG，最大3600万像素。')
            header.verify()
        raw=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),cv2.IMREAD_COLOR)
        if raw is None:raise ValueError('无法读取PNG。')
        screen=cv2.cvtColor(raw,cv2.COLOR_BGR2RGB)
        if screen.shape[:2]!=(1080,1920):screen=cv2.resize(screen,(1920,1080),interpolation=cv2.INTER_AREA)
        self.reference(index,side)
        draft['screens'][index]=screen;draft['sides'][index]=side
        return self.view(index)

    def change_side(self,token,side):
        draft=self.current(token)
        if side not in ('left','right'):raise ValueError('连携方向无效。')
        draft['sides'][2]=side;self.reference(2,side);return self.view(2)

    def view(self,selected=0):
        import cv2
        from one_dragon.utils import cv2_utils
        from one_dragon.base.screen.template_info import TemplateInfo
        draft=self.draft;images=[];masks=[];preview=None;rect=None
        for i,step in enumerate(self.steps):
            screen=draft['screens'][i]
            if screen is not None:
                ref=self.reference(i,draft['sides'][i]);ref.screen_image=screen;image=ref.get_template_raw_by_screen_point()
                mask=ref.get_template_mask_by_screen_point() if ref.auto_mask else None
                if i==selected:
                    r=ref.get_template_rect_by_point();rect=[r.x1,r.y1,r.x2,r.y2]
                    small=cv2.resize(screen,(960,540),interpolation=cv2.INTER_AREA)
                    cv2.rectangle(small,(int(r.x1/2),int(r.y1/2)),(int(r.x2/2),int(r.y2/2)),(164,205,0),2)
                    ok,encoded=cv2.imencode('.jpg',cv2.cvtColor(small,cv2.COLOR_RGB2BGR),[cv2.IMWRITE_JPEG_QUALITY,85])
                    preview=base64.b64encode(encoded).decode() if ok else None
            elif draft['agent']:
                template=TemplateInfo(step.sub_dir,step.template_id_of(draft['agent']));image=template.raw;mask=template.mask
            else:image=mask=None
            encoded=self.png(image)
            images.append(None if encoded is None else base64.b64encode(encoded).decode())
            encoded_mask=self.png(mask);masks.append(None if encoded_mask is None else base64.b64encode(encoded_mask).decode())
        from .agent_template_registry import recognition
        return dict(token=draft['token'],agent=draft['agent'],signature=draft['signature'],images=images,masks=masks,preview=preview,rect=rect,
                    registration=None if draft['agent'] is None else recognition(draft['agent']),
                    changed=[i for i,x in enumerate(draft['screens']) if x is not None],sides=draft['sides'],selected=selected)

    def save(self,token,agent):
        from one_dragon.base.screen.template_info import TemplateInfo
        draft=self.current(token);self.agent_id(agent)
        if draft['agent'] not in (None,agent):raise ValueError('已有模板名称不能改写。')
        expected=draft['signature']
        if expected is None:
            if any((path/'raw.png').exists() for path in self.paths(agent)):raise ValueError('模板已存在，请从列表打开编辑。')
            expected=self.signature(agent)
        current=self.view()
        if any(image is None for image in current['images']):raise ValueError('六类模板齐全后才能保存。')
        self.service._assert_idle()
        with RunLock(),profile_lock(self.root):
            self.tx.assert_clear()
            if self.signature(agent)!=expected:raise ValueError('模板已在其他窗口变化，请重新读取。')
            writes={}
            for i,screen in enumerate(draft['screens']):
                if screen is None:continue
                step=self.steps[i];ref=self.reference(i,draft['sides'][i]);template=TemplateInfo(step.sub_dir,step.template_id_of(agent))
                template.screen_image=screen;template.template_shape=ref.template_shape;template.point_list=copy.deepcopy(ref.point_list);template.auto_mask=ref.auto_mask
                # Execute the source save calculation, collecting its output before grouped publication.
                template.make_template_dir=lambda:None
                def collect(image,path):writes[Path(path)]=self.png(image)
                with patch('one_dragon.utils.cv2_utils.save_image',side_effect=collect):template.save_raw();template.save_mask()
                if not ref.auto_mask:writes[self.paths(agent)[i]/'mask.png']=None
            if not writes:raise ValueError('没有需要保存的新截图。')
            archive=self.tx.publish(writes,dict(agent=agent,action='save'),lambda:self.signature(agent)==expected)
            self.open(agent)
            return dict(archive=archive,catalog=self.catalog(),draft=self.view())

    def remove(self,agent,signature):
        self.service._assert_idle()
        with RunLock(),profile_lock(self.root):
            writes={path/file:None for path in self.paths(agent) for file in FILES if (path/file).exists()}
            archive=self.tx.publish(writes,dict(agent=agent,action='delete'),lambda:self.signature(agent)==signature)
            return dict(archive=archive,catalog=self.catalog())

    def restore(self,identifier):
        self.service._assert_idle()
        with RunLock(),profile_lock(self.root):
            self.tx.assert_clear();manifest,files,_=self.tx.load(identifier)
            guard=lambda:all(fingerprint(self.tx.read(path))==fingerprint(new) for path,old,new in files)
            archive=self.tx.publish({path:old for path,old,new in files},dict(action='restore',source=identifier,agent=manifest['metadata'].get('agent','')),guard)
            return dict(archive=archive,catalog=self.catalog())

    def recover(self):
        self.service._assert_idle()
        with RunLock(),profile_lock(self.root):self.tx.recover();return self.catalog()

    def preferences(self):
        from zzz_od.config.agent_template_generator_config import AgentTemplateGeneratorConfig
        from zzz_od.gui.view.devtools.agent_template_generator_interface import HOTKEY_ACTIONS
        from .config_transaction import config_revision
        from one_dragon.base.config.yaml_operator import cached_yaml_data
        cached_yaml_data.clear();config=AgentTemplateGeneratorConfig()
        values=dict(hotkeys={a.key:config.get_hotkey(a.key,a.default_value) for a in HOTKEY_ACTIONS},
            float_enabled=config.float_enabled,float_expanded=config.float_expanded,float_x=config.float_x,float_y=config.float_y)
        return dict(values=values,revision=config_revision(config))

    def preferences_save(self,values,revision):
        from zzz_od.config.agent_template_generator_config import AgentTemplateGeneratorConfig
        from .agent_template_hotkeys import validate_bindings
        from .config_transaction import atomic_apply
        if type(values)is not dict or set(values)!={'hotkeys','float_enabled','float_expanded','float_x','float_y'}:raise ValueError('模板偏好字段无效。')
        bindings=validate_bindings(values['hotkeys'])
        if any(type(values[k])is not bool for k in ('float_enabled','float_expanded')) or any(type(values[k])is not int or not -10000<=values[k]<=10000 for k in ('float_x','float_y')):
            raise ValueError('悬浮窗开关或位置无效。')
        with profile_lock(self.root/'.agent-template-presentation'):
            config=AgentTemplateGeneratorConfig();self.tx.inside(config._get_write_path())
            def apply():
                for key,value in bindings.items():config.set_hotkey(key,value)
                for key in ('float_enabled','float_expanded','float_x','float_y'):setattr(config,key,values[key])
            atomic_apply(config,revision,apply,self.root)
        return self.preferences()
