"""把玩家核对的配置与每次定时运行的额度绑定；不启动游戏。"""
from __future__ import annotations

import copy
from dataclasses import asdict,replace
import hashlib
from pathlib import Path
import re

from .config import Settings
from .schedules import Schedule


def execution_fields(plan: Schedule) -> dict:
    return {key:getattr(plan,key) for key in ('mode','server','instance','app_id','timeout','allowance','battles','maximum')}


def daily_review(plan: Schedule,path: Path) -> dict:
    path=Path(path).resolve();raw=path.read_bytes() if path.is_file() else None
    settings=Settings.load(path)
    if settings.server!=plan.server:settings.select_server(plan.server)
    settings.validate('daily')
    if not Path(settings.game_path).is_file():raise ValueError('每日计划的游戏程序不存在。')
    if (path.read_bytes() if path.is_file() else None)!=raw:raise ValueError('读取期间每日配置发生变化，请重新核对。')
    return dict(format=1,kind='daily',server=plan.server,configuration_valid=True,uses_charge=False,
                settings=asdict(settings),config_path=str(path),config_hash='missing' if raw is None else hashlib.sha256(raw).hexdigest())


def requires_resources(review: dict) -> bool:
    return any(action.get('requires_confirmation')is True for row in review.get('instances',[]) for action in row.get('resources',[]))


def authorize(plan: Schedule,review: dict,resources_accepted: bool=False) -> dict:
    if type(resources_accepted)is not bool:raise ValueError('资源确认须为布尔值。')
    value=dict(format=1,execution=execution_fields(plan),review=copy.deepcopy(review),resources_accepted=resources_accepted)
    validate_authorization(plan,value);return value


def validate_authorization(plan: Schedule,value: dict|None=None) -> dict:
    value=plan.authorization if value is None else value
    replace(plan,authorization=value if type(value)is dict else {}).validate()
    if (type(value)is not dict or set(value)!={'format','execution','review','resources_accepted'} or type(value['format'])is not int or value['format']!=1
        or value['execution']!=execution_fields(plan) or type(value['review'])is not dict or type(value['resources_accepted'])is not bool):
        raise ValueError('执行内容或额度已变化，请重新核对计划。')
    review=value['review']
    if type(review.get('format'))is not int or review['format']!=1 or review.get('server')!=plan.server or review.get('configuration_valid')is not True or type(review.get('uses_charge'))is not bool:
        raise ValueError('计划配置尚未通过核对。')
    if review['uses_charge']!=(plan.allowance is not None):raise ValueError('请按核对结果填写本次总电量、次数和单次上限。')
    if plan.mode=='daily':
        if review.get('kind')!='daily' or type(review.get('settings'))is not dict or type(review.get('config_path'))is not str:
            raise ValueError('每日配置核对记录无效。')
        try:Settings(**review['settings']).validate('daily')
        except (ValueError,TypeError) as exc:raise ValueError('每日配置字段未通过校验。') from exc
        if review['settings']['server']!=plan.server:raise ValueError('每日配置区服已变化。')
        digest=review.get('config_hash')
        if type(digest)is not str or digest!='missing' and re.fullmatch('[0-9a-f]{64}',digest)is None:raise ValueError('每日配置版本无效。')
    else:
        expected=None if plan.mode=='rotation' else dict(instance_idx=plan.instance,app_id=plan.app_id)
        if review.get('selection')!=expected or type(review.get('intent'))is not str or re.fullmatch('[0-9a-f]{64}',review['intent'])is None:
            raise ValueError('原生账号或配置版本未核对。')
        if type(review.get('instances'))is not list or not review['instances']:raise ValueError('计划没有可运行的账号。')
        if any(type(row)is not dict or type(row.get('resources'))is not list
            or any(type(action)is not dict or type(action.get('requires_confirmation'))is not bool for action in row['resources']) for row in review['instances']):
            raise ValueError('计划资源清单格式无效。')
        if requires_resources(review) and not value['resources_accepted']:raise ValueError('请先确认核对清单中的资源操作。')
    return review
