"""定时计划格式、安装隔离和原子保存；不创建系统任务或运行游戏。"""
from __future__ import annotations

import copy
from dataclasses import asdict,dataclass,field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .battle_budget import validate_runtime_budget
from .onedragon_control import validate_runtime_timeout
from .run_lock import profile_lock


def identifier(value: str) -> str:
    if type(value)is not str or re.fullmatch('[0-9a-f]{32}',value)is None:
        raise ValueError('计划编号无效。')
    return value


def revision(value: object) -> str:
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def text(value: str,label: str,maximum: int=200) -> str:
    if type(value)is not str or not value.strip() or value!=value.strip() or len(value)>maximum or any(ord(c)<32 for c in value):
        raise ValueError(label+'无效。')
    return value


@dataclass
class Schedule:
    id: str=field(default_factory=lambda:uuid.uuid4().hex)
    name: str='新计划'
    enabled: bool=False
    trigger: str='daily'
    time: str='09:00'
    date: str=''
    weekdays: list[int]=field(default_factory=lambda:[0,1,2,3,4,5,6])
    missed: bool=False
    wake: bool=False
    highest: bool=True
    mode: str='daily'
    server: str='cn'
    instance: int|None=None
    app_id: str='one_dragon'
    timeout: int=600
    allowance: int|None=None
    battles: int|None=None
    maximum: int|None=None
    authorization: dict=field(default_factory=dict)
    binding_error: str=''

    def validate(self) -> None:
        identifier(self.id);text(self.name,'计划名称',80)
        for key in ('enabled','missed','wake','highest'):
            if type(getattr(self,key))is not bool:raise ValueError('计划开关须为布尔值。')
        if self.trigger not in ('once','daily','weekly','logon'):raise ValueError('触发方式无效。')
        if type(self.time)is not str or re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',self.time)is None:
            raise ValueError('时间须为 24 小时制 HH:MM。')
        if type(self.date)is not str:raise ValueError('单次运行日期无效。')
        if self.date:
            try:parsed=datetime.strptime(self.date,'%Y-%m-%d')
            except ValueError as exc:raise ValueError('日期须为 YYYY-MM-DD。') from exc
            if parsed.strftime('%Y-%m-%d')!=self.date:raise ValueError('日期须为 YYYY-MM-DD。')
        if self.trigger=='once' and not self.date:raise ValueError('单次任务需要日期。')
        if (type(self.weekdays)is not list or not self.weekdays or len(self.weekdays)>7
            or any(type(day)is not int or not 0<=day<=6 for day in self.weekdays) or len(set(self.weekdays))!=len(self.weekdays)):
            raise ValueError('星期须从周一至周日选择，不能重复。')
        if self.mode not in ('daily','native','rotation'):raise ValueError('执行内容无效。')
        if self.server not in ('cn','global'):raise ValueError('区服无效。')
        if self.mode=='native' and (type(self.instance)is not int or self.instance<=0):raise ValueError('请选择固定的账号配置。')
        if self.instance is not None and (type(self.instance)is not int or self.instance<=0):raise ValueError('账号配置编号无效。')
        text(self.app_id,'应用编号',512);validate_runtime_timeout(self.timeout)
        values=(self.allowance,self.battles,self.maximum)
        if any(value is not None for value in values):
            if self.mode=='daily':raise ValueError('每日计划不使用战斗电量。')
            validate_runtime_budget(dict(id='schedule-'+self.id,allowance=self.allowance,battle_limit=self.battles,max_per_battle=self.maximum))
        try:authorization_size=len(json.dumps(self.authorization,ensure_ascii=False,allow_nan=False).encode('utf-8'))
        except (TypeError,ValueError) as exc:raise ValueError('计划确认资料格式无效。') from exc
        if type(self.authorization)is not dict or authorization_size>1024**2:
            raise ValueError('计划确认资料无效或过大。')
        if self.enabled and not self.authorization:raise ValueError('启用前请核对并保存运行配置。')
        if type(self.binding_error)is not str or len(self.binding_error)>4000:raise ValueError('系统任务错误记录无效。')

    def dump(self) -> dict:
        self.validate();return asdict(self)

    @classmethod
    def load(cls,value: dict) -> Schedule:
        if type(value)is not dict or set(value)!=set(cls.__dataclass_fields__):raise ValueError('计划资料格式无效。')
        result=cls(**copy.deepcopy(value));result.validate();return result

    @property
    def fingerprint(self) -> str:
        value=self.dump();value.pop('binding_error');return revision(value)


class ScheduleStore:
    def __init__(self,root: Path,path: Path|None=None):
        self.root=Path(root).resolve()
        local=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))
        key=revision(str(self.root).casefold())[:24]
        self.path=(local/'AutoZZZ/schedules'/key/'plans.json' if path is None else Path(path)).absolute()
        if self.path!=self.path.resolve():raise ValueError('计划资料路径被重定向。')
        self.owner=revision([str(self.root).casefold(),str(self.path).casefold()])[:32]

    def read(self) -> dict:
        self._assert_path()
        if not self.path.exists():return dict(format=1,root=str(self.root),plans=[])
        if self.path.stat().st_size>16*1024**2:raise ValueError('计划清单过大。')
        result=json.loads(self.path.read_text(encoding='utf-8'))
        if (type(result)is not dict or set(result)!={'format','root','plans'} or type(result['format'])is not int
            or result['format']!=1 or type(result['root'])is not str or result['root'].casefold()!=str(self.root).casefold() or type(result['plans'])is not list or len(result['plans'])>1000):
            raise ValueError('计划清单不属于当前安装目录或格式无效。')
        plans=[Schedule.load(value) for value in result['plans']]
        if len({plan.id for plan in plans})!=len(plans):raise ValueError('计划编号重复。')
        return result

    def get(self,key: str) -> Schedule:
        identifier(key)
        for value in self.read()['plans']:
            if value['id']==key:return Schedule.load(value)
        raise ValueError('计划不存在或已经移除。')

    def _assert_path(self) -> None:
        if self.path!=self.path.resolve() or (self.path.exists() and (self.path.stat().st_nlink>1 or getattr(self.path.lstat(),'st_file_attributes',0)&0x400)):
            raise ValueError('计划资料被重定向或共享，未读取或覆盖。')

    def _save(self,value: dict) -> None:
        self._assert_path()
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)
        if len(raw.encode('utf-8'))>16*1024**2:raise ValueError('计划清单超过 16 MB，未保存。')
        temporary=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(raw,encoding='utf-8');os.replace(temporary,self.path)
        finally:temporary.unlink(missing_ok=True)

    def put(self,plan: Schedule,expected: str) -> dict:
        value=plan.dump();self.path.parent.mkdir(parents=True,exist_ok=True)
        with profile_lock(self.path.parent):
            current=self.read()
            if revision(current)!=expected:raise ValueError('计划清单已变化，请重新读取。')
            current['plans']=[item for item in current['plans'] if item['id']!=plan.id]+[value]
            if len(current['plans'])>1000:raise ValueError('计划不能超过 1000 项。')
            self._save(current);return current

    def remove(self,key: str,expected: str) -> dict:
        identifier(key);self.path.parent.mkdir(parents=True,exist_ok=True)
        with profile_lock(self.path.parent):
            current=self.read()
            if revision(current)!=expected:raise ValueError('计划清单已变化，请重新读取。')
            if not any(item['id']==key for item in current['plans']):raise ValueError('计划不存在。')
            current['plans']=[item for item in current['plans'] if item['id']!=key]
            self._save(current);return current
