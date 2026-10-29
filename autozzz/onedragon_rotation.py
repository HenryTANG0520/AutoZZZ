"""多账号轮换预检；使用实际实例起点、应用组及依赖收集。"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from .map_transaction import MapTransaction
from .model_install import ModelInstall
from .onedragon_accounts import AccountStore
from .onedragon_notify import credential_fragments,redact
from .run_lock import profile_lock


def rotation_revision(root: Path) -> str:
    """当前选中标志可由轮换改变；其余配置和资料都必须保持原版本。"""
    from one_dragon.utils.yaml_utils import safe_load
    root=Path(root).resolve();paths=list((root/'config').rglob('*.yml'))
    maps=root/'assets/game_data/world_patrol'
    paths.extend(path for path in maps.rglob('*') if path.is_file() and path.suffix in ('.png','.yml'))
    if len(paths)>20000:raise ValueError('轮换配置文件过多。')
    digest=hashlib.sha256();total=0
    from .onedragon_plugins import PluginStore
    digest.update(PluginStore(root).revision().encode('ascii'))
    for path in sorted(paths):
        if path.relative_to(root).as_posix()=='config/pip.yml':continue
        if path.resolve()!=path.absolute() or not path.resolve().is_relative_to(root):raise ValueError('轮换资料路径被重定向。')
        raw=path.read_bytes();total+=len(raw)
        if total>256*1024*1024:raise ValueError('轮换配置资料过大。')
        relative=path.relative_to(root).as_posix()
        if relative=='config/one_dragon.yml':
            value=safe_load(raw)
            if not isinstance(value,dict):raise ValueError('账号清单格式无效。')
            value=copy.deepcopy(value)
            rows=value.get('instance_list',[])
            if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):raise ValueError('账号清单格式无效。')
            for row in rows:row.pop('active',None)
            raw=json.dumps(value,ensure_ascii=False,sort_keys=True).encode('utf-8')
        digest.update(relative.encode('utf-8')+b'\0'+hashlib.sha256(raw).digest())
    return digest.hexdigest()


def instance_order(registry):
    """复用实际初始化方法求起点，替身上下文只记录选择，不切换账号。"""
    from one_dragon.base.config.one_dragon_config import InstanceRun
    from one_dragon.base.operation.one_dragon_app import OneDragonApp
    current=registry.current_active_instance
    if current is None:raise ValueError('没有当前账号配置。')
    ids=[item.idx for item in registry.instance_list]
    if len(ids)!=len(set(ids)) or any(type(idx)is not int or idx<=0 for idx in ids):raise ValueError('账号编号无效或重复。')
    if any(type(item.active)is not bool for item in registry.instance_list) or sum(item.active for item in registry.instance_list)!=1:
        raise ValueError('账号清单须且只能有一个当前账号。')
    if any(type(item.active_in_od)is not bool or type(item.force_login_before_run)is not bool for item in registry.instance_list):raise ValueError('账号参与或重新登录开关无效。')
    config=SimpleNamespace(instance_run=InstanceRun.ALL.value.value,instance_list_in_od=registry.instance_list_in_od,current_active_instance=current)
    context=SimpleNamespace(one_dragon_config=config,current_instance_idx=current.idx)
    context.switch_instance=lambda idx:setattr(context,'current_instance_idx',idx)
    operation=object.__new__(OneDragonApp);operation.ctx=context
    OneDragonApp.handle_init(operation)
    items=operation._instance_list;start=operation._instance_start_idx
    return items[start:]+items[:start],current.idx


class RotationPlans:
    def __init__(self,service):
        self.service,self.root=service,Path(service.data).resolve()
        self.plan=None

    def _clear_transactions(self):
        MapTransaction(self.root).assert_clear();ModelInstall(self.root).assert_clear()

    def preview(self,server):
        if server not in ('cn','global'):raise ValueError('区服无效。')
        self.service._assert_idle();self._clear_transactions()
        ctx=self.service.ctx
        with profile_lock(self.root):
            self.plan=None
            from .onedragon_config import ConfigurationStore
            from .onedragon_requirements import ApplicationRequirements
            from .onedragon_plugins import executable_application
            from one_dragon.base.config.game_account_config import GameAccountConfig
            registry=AccountStore(self.service)._registry();order,original=instance_order(registry)
            old_index=ctx.current_instance_idx
            for item in registry.instance_list:
                folder=self.root/'config'/f'{item.idx:02d}'
                if folder.resolve()!=folder.absolute():raise ValueError('账号配置目录被重定向。')
                for path in folder.rglob('*.yml'):
                    if path.resolve()!=path.absolute():raise ValueError('账号配置文件被重定向。')
            configs={item.idx:GameAccountConfig(item.idx) for item in registry.instance_list}
            secrets=credential_fragments([value for config in configs.values() for value in (config.account,config.password,config.bilibili_account_name)])
            issues,instances=[],[]
            def issue(idx,message):issues.append(dict(instance_idx=idx,message=redact(message,secrets)))
            first=order[0].idx
            try:
                for item in order:
                    idx=item.idx;account=configs[idx]
                    login_required=len(order)>1 or idx!=original or item.force_login_before_run
                    path=Path(account.game_path) if account.game_path else None
                    valid_path=bool(path and path.name.casefold()=='zenlesszonezero.exe' and path.is_file())
                    if not valid_path:issue(idx,'请配置存在的 ZenlessZoneZero.exe。')
                    region=account.game_region
                    if region not in (('cn','cn_b') if server=='cn' else ('us','eu','asia','twhkmo')):issue(idx,'账号服务器与本轮客户端不符。')
                    if account.game_language not in ('cn','en'):issue(idx,'游戏语言无效。')
                    if login_required and not account.has_login_info:issue(idx,'自动换号需要完整登录资料。')
                    elif login_required and region!='cn_b' and (len(account.account.strip())<=3 or len(account.password.strip())<=3):
                        issue(idx,'登录账号与密码均须超过 3 个字符。')
                    row=dict(idx=idx,name=item.name,force_login=item.force_login_before_run,login_required=login_required,
                             login_configured=bool(account.has_login_info),game_path=str(path.resolve()) if valid_path else account.game_path,
                             region=region,language=account.game_language,applications=[],uses_charge=False,resources=[],requirements={})
                    instances.append(row)
                    ctx.current_instance_idx=idx;ctx.reload_instance_config()
                    try:
                        store=ConfigurationStore(self.service)
                        applications=[app.app_id for app in store.get('_group').app_list if app.enabled]
                        row['applications']=[dict(id=key,name=ctx.run_context.get_application_name(key)) for key in applications]
                        if not applications:issue(idx,'该账号没有启用一条龙应用。');continue
                        unsupported=[key for key in applications if not executable_application(ctx,key)]
                        if unsupported:issue(idx,'存在尚未完成执行适配的应用：'+'、'.join(unsupported));continue
                        requirements=ApplicationRequirements(ctx,self.root,store);requirements.collect(applications)
                        requirements.validate_policy_files()
                        summary=requirements.summary()
                        row['uses_charge']=requirements.uses_charge
                        row['resources']=[dict(action,instance_idx=idx,instance_name=item.name,id=f'{idx}:'+action['id']) for action in requirements.resource_actions]
                        row['requirements']={key:summary[key] for key in ('policies','models','challenges','routes','uses_hunt','plugins','restore_charge')}
                    except Exception as exc:issue(idx,str(exc))
            finally:
                ctx.current_instance_idx=old_index;ctx.reload_instance_config()
                ctx.app_group_manager.clear_config_cache()
                ctx.push_service.reload_config()
            transitions=[]
            targets=([first] if first!=original else [])+[item.idx for item in order[1:]]+([first] if len(order)>1 else [])
            previous=original
            for index,target in enumerate(targets):
                old,new=configs[previous],configs[target]
                left=Path(old.game_path).resolve() if old.game_path else None
                right=Path(new.game_path).resolve() if new.game_path else None
                changed=left!=right or old.game_client!=new.game_client
                transitions.append(dict(from_idx=previous,to_idx=target,restart_client=changed,
                                        returning=index==len(targets)-1 and target==first and len(order)>1))
                previous=target
            self.plan=dict(plan_id=uuid.uuid4().hex,server=server,original_current=original,start_idx=first,return_idx=first,
                           origin=dict(idx=original,game_path=configs[original].game_path,region=configs[original].game_region,language=configs[original].game_language),
                           revision=rotation_revision(self.root),instances=instances,transitions=transitions,issues=issues,
                           configuration_valid=not issues,uses_charge=any(row['uses_charge'] for row in instances))
            return copy.deepcopy(self.plan)

    def validate(self,plan_id):
        self.service._assert_idle();self._clear_transactions()
        with profile_lock(self.root):
            if self.plan is None or plan_id!=self.plan['plan_id']:raise ValueError('轮换预检编号已变化，请重新读取。')
            if not self.plan['configuration_valid']:raise ValueError('请先修复账号轮换预检中的问题。')
            registry=AccountStore(self.service)._registry()
            _,current=instance_order(registry)
            if current!=self.plan['original_current']:
                raise ValueError('当前账号已变化，请重新预检。')
            if rotation_revision(self.root)!=self.plan['revision']:raise ValueError('轮换配置或资料已变化，请重新预检。')
            return copy.deepcopy(self.plan)
