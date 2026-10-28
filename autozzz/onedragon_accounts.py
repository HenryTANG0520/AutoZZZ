"""上游账号清单与登录设置；不打开游戏、不输出密码。"""
from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
from typing import Any
import uuid

from .config_transaction import atomic_apply,config_revision
from .run_lock import profile_lock


class AccountStore:
    def __init__(self,service:Any):
        self.service,self.ctx,self.root=service,service.ctx,service.data

    def _registry(self):
        from one_dragon.base.config.one_dragon_config import OneDragonConfig
        from one_dragon.base.config.yaml_operator import cached_yaml_data
        cached_yaml_data.clear()
        return OneDragonConfig()

    def _snapshot(self,registry):
        return dict(revision=config_revision(registry),current=registry.current_active_instance.idx,
                    instances=[dict(idx=i.idx,name=i.name,active=i.active,enabled=i.active_in_od,
                                    force_login=i.force_login_before_run) for i in registry.instance_list])

    def list(self):
        self.service._assert_idle()
        with profile_lock(self.root):return self._snapshot(self._registry())

    @staticmethod
    def _instance(registry,idx):
        if type(idx) is not int or not any(i.idx==idx for i in registry.instance_list):raise ValueError("账号配置不存在。")
        return next(i for i in registry.instance_list if i.idx==idx)

    def _refresh(self,registry):
        self.ctx.__dict__["one_dragon_config"]=registry
        active=registry.current_active_instance
        if active is None:raise ValueError("没有当前账号配置。")
        self.ctx.current_instance_idx=active.idx
        self.ctx.reload_instance_config()
        self.ctx.run_context.clear_application_cache()
        self.ctx.app_group_manager.clear_config_cache()
        self.ctx.ready_for_application=False
        if self.service._execution is not None:
            self.service._execution.prepared_app=self.service._execution.prepared_hash=""
            self.service._execution.prepared_requirements={}
            self.service._execution.prepared_applications=[]
        if self.ctx.controller is not None:self.ctx.controller.game_win.clear_binding()

    def action(self,action:str,revision:str,idx:int|None=None,name:str|None=None,enabled:bool|None=None,force_login:bool|None=None):
        self.service._assert_idle()
        if action not in ("create","rename","activate","options","archive"):raise ValueError("未知账号操作。")
        if action in ("create","rename") and (not isinstance(name,str) or not name.strip() or len(name)>80 or any(c in name for c in "\r\n\0")):
            raise ValueError("账号名称须为 1—80 字，不能含换行。")
        if action=="options" and (type(enabled) is not bool or type(force_login) is not bool):raise ValueError("账号选项必须为开关值。")
        with profile_lock(self.root):
            registry=self._registry()
            if action!="create":self._instance(registry,idx)
            rows=copy.deepcopy(registry.dict_instance_list)
            archive=source=None
            created=[]
            if action=="archive":
                if len(rows)==1:raise ValueError("至少保留一个账号配置。")
                source=(self.root/"config"/f"{idx:02d}").resolve()
                base=(self.root/"config").resolve()
                if source.parent!=base:raise ValueError("账号目录不在配置目录中。")
                archive=(self.root/"autozzz"/"archived-accounts"/(datetime.now().strftime("%Y%m%d_%H%M%S_")+uuid.uuid4().hex)).resolve()
                if not archive.is_relative_to(self.root.resolve()):raise ValueError("归档目录不在实例目录中。")
            def apply():
                if action=="create":
                    new=registry.create_new_instance(False)
                    new.name=name.strip()
                    new.active_in_od=False
                    created.append(new.idx)
                    registry.dict_instance_list=[vars(i) for i in registry.instance_list]
                else:
                    for row in rows:
                        if action=="activate":row["active"]=row["idx"]==idx
                        if row["idx"]==idx:
                            if action=="rename":row["name"]=name.strip()
                            if action=="options":row.update(active_in_od=enabled,force_login_before_run=force_login)
                    if action=="archive":
                        removed=next(row for row in rows if row["idx"]==idx)
                        rows[:]=[row for row in rows if row["idx"]!=idx]
                        if removed["active"]:rows[0]["active"]=True
                    registry.dict_instance_list=rows
            moved=False
            # 先检验修订，再归档；发布失败时原目录恢复。没有递归删除。
            if config_revision(registry)!=revision:raise ValueError("账号清单已变化，请重新读取。")
            try:
                if source is not None and source.exists():
                    archive.parent.mkdir(parents=True,exist_ok=True)
                    source.rename(archive)
                    moved=True
                    archive.with_suffix(".json").write_text(json.dumps(dict(instance=next(row for row in rows if row["idx"]==idx),original_path=str(source)),ensure_ascii=False,indent=2),encoding="utf-8")
                atomic_apply(registry,revision,apply,self.root)
            except Exception:
                if moved:
                    archive.with_suffix(".json").unlink(missing_ok=True)
                    archive.rename(source)
                raise
            registry._init_instance_list()
            self._refresh(registry)
            result=self._snapshot(registry)
            if moved:result["archive"]=str(archive)
            if created:result["created"]=created[0]
            return result

    def describe(self,idx:int,server:str):
        self.service._assert_idle()
        if server not in ("cn","global"):raise ValueError("区服无效。")
        with profile_lock(self.root):
            registry=self._registry()
            self._instance(registry,idx)
            from one_dragon.base.config.game_account_config import GameAccountConfig,GameRegionEnum
            from .config import GAME_PATHS
            config=GameAccountConfig(idx)
            regions=[dict(label=e.value.label,value=e.value.value) for e in GameRegionEnum
                     if (e.value.value in ("cn","cn_b"))==(server=="cn")]
            values=dict(game_path=config.game_path or GAME_PATHS[server],
                        game_region=config.data.get("game_region","cn" if server=="cn" else "asia"),game_language=config.game_language,
                        account=config.account,bilibili_account_name=config.bilibili_account_name,
                        use_custom_win_title=config.use_custom_win_title,custom_win_title=config.custom_win_title)
            return dict(idx=idx,revision=config_revision(config),values=values,regions=regions,password_set=bool(config.password),path_configured=bool(config.game_path))

    def save(self,idx:int,server:str,revision:str,values:dict):
        self.service._assert_idle()
        if server not in ("cn","global"):raise ValueError("区服无效。")
        allowed={"game_path","game_region","game_language","account","bilibili_account_name","use_custom_win_title","custom_win_title","password","clear_password"}
        if not isinstance(values,dict) or set(values)-allowed:raise ValueError("登录设置包含未知字段。")
        for key,value in values.items():
            expected=bool if key in ("use_custom_win_title","clear_password") else str
            if type(value) is not expected:raise ValueError("登录设置类型无效。")
            if isinstance(value,str) and (len(value)>4096 or any(c in value for c in "\r\n\0")):raise ValueError("登录设置不能含换行或过长文本。")
        if values.get("clear_password") and values.get("password"):raise ValueError("不能同时设置和清除密码。")
        if "game_region" in values and values["game_region"] not in (("cn","cn_b") if server=="cn" else ("us","eu","asia","twhkmo")):
            raise ValueError("所选服务器与客户端不符。")
        if values.get("game_language","cn") not in ("cn","en"):raise ValueError("游戏语言无效。")
        if "game_path" in values and values["game_path"] and Path(values["game_path"]).name.casefold()!="zenlesszonezero.exe":
            raise ValueError("请选择 ZenlessZoneZero.exe。")
        with profile_lock(self.root):
            registry=self._registry()
            self._instance(registry,idx)
            from one_dragon.base.config.game_account_config import GameAccountConfig
            config=GameAccountConfig(idx)
            def apply():
                for key,value in values.items():
                    if key=="clear_password":
                        if value:config.password=""
                    elif key=="password":
                        if value:config.password=value
                    else:setattr(config,key,value)
            atomic_apply(config,revision,apply,self.root)
            self._refresh(registry)
        return self.describe(idx,server)
