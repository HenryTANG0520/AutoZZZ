"""定时计划的原生配置核对；只返回摘要和资源清单，不输出登录凭据。"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path,PurePosixPath

from .rotation_snapshot import snapshot
from .run_lock import profile_lock


def normalize_intent(name: str,value: object) -> object:
    """只忽略固定上游明确保存的进度；策略、次数、来源和插件配置照常核对。"""
    value=copy.deepcopy(value);parts=PurePosixPath(name).parts
    if name=='config/one_dragon.yml' and isinstance(value,dict):
        rows=value.get('instance_list',[])
        if isinstance(rows,list):
            for row in rows:
                if isinstance(row,dict):row.pop('active',None)
    native_plan=(len(parts)==4 and parts[0]=='config' and parts[1].isdigit() and parts[2]=='one_dragon')
    if native_plan and parts[-1]in ('charge_plan.yml','notorious_hunt.yml') and isinstance(value,dict):
        value.pop('last_daily_reset_dt',None);value.pop('skip_plan',None)
        plans=value.get('plan_list',[])
        if isinstance(plans,list):
            for row in plans:
                if isinstance(row,dict):row.pop('run_times',None)
        double=value.get('combat_simulation_double_reward_config')
        if isinstance(double,dict):double.pop('run_times',None)
    if len(parts)==3 and parts[0]=='config' and parts[1].isdigit() and parts[-1]=='game_account.yml' and isinstance(value,dict):
        # 原生初始化会补写这项默认值，不改变已核对的登录语言。
        if value.get('game_language')=='cn':value.pop('game_language')
    return value


def intent_entries(root: Path,instances: list[int]|None=None) -> dict:
    from one_dragon.utils.yaml_utils import safe_load
    root=Path(root).resolve();entries=snapshot(root);result={}
    for name,checksum in entries.items():
        parts=PurePosixPath(name).parts
        if instances is not None and len(parts)>=3 and parts[0]=='config' and parts[1].isdigit() and int(parts[1])not in instances:continue
        if len(parts)==4 and parts[0]=='config' and parts[1].isdigit() and parts[2]=='app_run_record':continue
        if parts[0]=='config' and name.endswith('.yml'):
            value=normalize_intent(name,safe_load((root/name).read_bytes()))
            checksum=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode('utf-8')).hexdigest()
        result[name]=checksum
    return result


def intent_revision(root: Path,instances: list[int]|None=None) -> str:
    return hashlib.sha256(json.dumps(intent_entries(root,instances),sort_keys=True).encode('utf-8')).hexdigest()


def review(service,server: str,instance_idx: int|None=None,app_id: str='one_dragon') -> dict:
    plan=service._rotation_plans().preview(server,instance_idx,app_id)
    if plan['configuration_valid']:service._rotation_plans().validate(plan['plan_id'])
    with profile_lock(service.data):
        from .onedragon_rotation import rotation_revision
        from .onedragon_accounts import AccountStore
        if (rotation_revision(service.data)!=plan['revision']
            or AccountStore(service)._registry().current_active_instance.idx!=plan['original_current']):
            raise ValueError('核对期间账号或配置发生变化，请重新读取。')
        instances=[row['idx'] for row in plan['instances']]
        before=intent_revision(service.data,instances)
        result=dict(format=1,server=server,selection=None if instance_idx is None else dict(instance_idx=instance_idx,app_id=app_id),
            intent=before,configuration_valid=plan['configuration_valid'],issues=plan['issues'],uses_charge=plan['uses_charge'],
            instances=[{key:row[key] for key in ('idx','name','game_path','region','language','applications','uses_charge','resources','requirements')} for row in plan['instances']])
        if intent_revision(service.data,instances)!=before:raise ValueError('核对期间配置发生变化，请重新读取。')
        return result
