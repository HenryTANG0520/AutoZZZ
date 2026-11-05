"""Locally enabled, revision-bound MCP configuration and screen edits."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
import types
import uuid

from .config_transaction import atomic_apply, config_revision
from .devtools_resources import NativeResourceGuard, ResourceArchives, resource_name
from .onedragon_mcp_compat import load_registrations, to_plain
from .run_lock import profile_lock
from .map_transaction import MapTransaction, fingerprint

CONFIG_TOOLS=frozenset(('set_config','add_config_item','delete_config_item'))
SCREEN_TOOLS=frozenset(('upsert_screen_area','delete_screen_area'))
EDIT_TOOLS=CONFIG_TOOLS|SCREEN_TOOLS


class ConfigArchives(MapTransaction):
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.pending=self.inside(self.root/'config/autozzz-mcp-config-transaction.json')

    def inside(self,path):
        path=Path(path).absolute()
        if path!=path.resolve() or path.exists()and path.is_file()and path.stat().st_nlink!=1:
            raise ValueError('MCP配置路径被重定向或共享。')
        return super().inside(path)

    @staticmethod
    def _allowed(relative):
        return len(relative.parts)>=3 and relative.parts[0].casefold()=='config' and relative.suffix.casefold()=='.yml'

    def archive_dir(self,identifier):
        if type(identifier)is not str or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):
            raise ValueError('MCP配置归档编号无效。')
        return self.inside(self.root/'archives/mcp-config'/identifier)

    def list(self):
        folder=self.inside(self.root/'archives/mcp-config');rows=[]
        if folder.exists():
            for path in sorted(folder.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
                try:
                    manifest,_,_=self.load(path.name)
                    rows.append(dict(id=path.name,metadata=manifest['metadata']))
                except (OSError,ValueError):continue
        return rows

    def restore(self,identifier):
        _,files,_=self.load(identifier)
        return self.publish({path:old for path,old,new in files},dict(action='restore',source=identifier),
            lambda:all(fingerprint(self.read(path))==fingerprint(new)for path,old,new in files))

    def assert_clear(self):
        if self.pending.exists():raise ValueError('存在未完成的MCP配置保存，请先在本地恢复中断归档。')


class EditBackend:
    def __init__(self,service):self.service=service
    @property
    def ctx(self):return self.service.ctx
    def __getattr__(self,name):
        if name not in SCREEN_TOOLS:raise ValueError('编辑通道不开放运行或游戏输入。')
        from zzz_od.backend.backend_context import ZzzBackendContext
        backend=object.__new__(ZzzBackendContext);backend._ctx=self.ctx
        return getattr(backend,name)


class McpEditing:
    def __init__(self,manager):
        self.manager=manager;self.service=manager.service;self.session=None;self.registry=None

    def archives(self,kind):
        if kind=='config':return ConfigArchives(self.service.data)
        if kind=='screens':return ResourceArchives(self.service.data)
        raise ValueError('归档类型须为config或screens。')

    def revision(self):
        root=Path(self.service.data).resolve();transaction=ResourceArchives(root)
        paths=list((root/'config').rglob('*.yml'))
        screen=root/'assets/game_data/screen_info'
        if screen.exists():paths+=list(screen.rglob('*.yml'))
        plugins=root/'plugins'
        if plugins.exists():paths += [p for p in plugins.rglob('*.yml') if 'screen_info' in p.relative_to(plugins).parts]
        if len(paths)>5000:raise ValueError('本次编辑资料文件过多。')
        records=[];total=0
        for path in sorted(set(paths)):
            path=transaction.inside(path);raw=transaction.read(path);total+=len(raw or b'')
            if total>transaction.LIMIT:raise ValueError('本次编辑资料过大。')
            records.append((path.relative_to(root).as_posix(),hashlib.sha256(raw or b'').hexdigest()))
        return hashlib.sha256(json.dumps(records,ensure_ascii=False).encode()).hexdigest()

    def status(self):
        state=copy.deepcopy(self.session)
        archives={kind:self.archives(kind).list()[:20] for kind in ('config','screens')}
        config=self.archives('config')
        if config.pending.exists():
            archives['config'].insert(0,dict(id='pending:'+fingerprint(config.read(config.pending)),
                metadata=dict(action='恢复中断配置保存')))
        return dict(session=state,archives=archives)

    def enable(self,scopes):
        if not isinstance(scopes,list) or not scopes or set(scopes)-{'config','screens'}:
            raise ValueError('编辑范围须为config和/或screens。')
        self.service._assert_idle();self.manager.compatibility.verify_source()
        with profile_lock(self.service.data):
            ctx=self.service.ctx
            for kind in ('config','screens'):self.archives(kind).assert_clear()
            self.session=dict(scopes=sorted(set(scopes)),instance_idx=ctx.current_instance_idx,
                revision=self.revision(),enabled_at=time.time())
        return self.status()

    def disable(self):
        self.session=None
        return self.status()

    def check(self,kind):
        state=self.session
        if state is None or kind not in state['scopes']:raise ValueError('请在本地MCP窗口启用本次'+kind+'编辑会话。')
        self.service._assert_idle();self.manager.compatibility.verify_source()
        if self.service.ctx.current_instance_idx!=state['instance_idx'] or self.revision()!=state['revision']:
            self.session=None
            raise ValueError('账号或配置/画面资料已被外部修改；本次编辑已关闭，请在本地重新启用。')
        for scope in ('config','screens'):self.archives(scope).assert_clear()

    def refresh(self):
        from one_dragon.base.config.yaml_operator import cached_yaml_data
        cached_yaml_data.clear()
        ctx=self.service.ctx;ctx.run_context.clear_application_cache();ctx.app_group_manager.clear_config_cache()
        for key in ('standalone_app_config','team_config','battle_assistant_config'):ctx.__dict__.pop(key,None)

    def call(self,name,arguments):
        if name not in EDIT_TOOLS:raise ValueError('未知编辑工具。')
        kind='config' if name in CONFIG_TOOLS else 'screens'
        values=self.manager.compatibility.validate(name,arguments)
        with profile_lock(self.service.data):
            self.check(kind)
            try:
                result=self.config_edit(name,values) if kind=='config' else self.screen_edit(name,values)
                self.manager.grant=None
                self.session['revision']=self.revision()
                self.manager.log('MCP编辑已保存并归档：'+name+'；已撤销未用运行授权。')
                return result
            except BaseException:
                self.session=None
                if kind=='screens':self.reload_screens()
                else:self.refresh()
                raise

    def reload_screens(self):
        loader=self.service.ctx.screen_loader
        extras={(path.parent,getattr(loader._id_2_screen.get(identifier),'app_id',''))
                for identifier,path in loader._extra_screen_file_path_map.items()}
        loader.reload()
        for directory,app_id in extras:loader.load_extra_screen_dir(str(directory),default_app_id=app_id)

    def config_edit(self,name,values):
        from zzz_od.backend.config_router import get_entry, _ro_item_fields_for
        from .onedragon_config import ConfigurationStore
        entry=get_entry(values['app_id'])
        if entry is None:raise ValueError('不支持的app_id。')
        idx=values.get('instance_idx')
        if idx is not None and idx!=self.session['instance_idx']:raise ValueError('本次编辑仅授权当前账号。')
        if values.get('group_id')is not None:resource_name(values['group_id'])
        required={'set_config':'set','add_config_item':'add','delete_config_item':'delete'}[name]
        if required not in entry.supported_ops:raise ValueError('该配置不支持此操作。')
        if name=='set_config':
            metadata=(entry.field_schema or {}).get(values['key'])
            if metadata:
                field=dict(kind=metadata['type'],label=values['key'])
                if metadata['type']=='enum':
                    field['options']=[dict(value=item.value.value)for item in metadata['enum_cls']]
                ConfigurationStore(self.service)._validate_scalar(field,values['value'])
        if name=='add_config_item' and set(values['item_dict'])&set(_ro_item_fields_for(values['app_id'])):
            raise ValueError('列表项包含只读身份或运行控制字段。')
        self.refresh()
        config=entry.get_config(self.service.ctx,idx,values.get('group_id'))
        tx=ConfigArchives(self.service.data)
        target=tx.inside(Path(config._get_write_path()))
        account_root=(Path(self.service.data)/'config'/f'{self.session["instance_idx"]:02d}').resolve()
        if not target.is_relative_to(account_root):raise ValueError('配置写入目标不属于当前账号。')
        tx.target(target.relative_to(tx.root).as_posix())
        # The original route validator and domain methods operate on this exact
        # config instance; atomic_apply suppresses intermediate saves.
        registry=self.manager.compatibility.load();function=registry.tools[name][0]
        bound_entry=replace(entry,get_config=lambda *_:config)
        globals_=dict(function.__globals__,get_entry=lambda app_id:bound_entry if app_id==entry.app_id else None)
        invoke=types.FunctionType(function.__code__,globals_,function.__name__,function.__defaults__,function.__closure__)
        result={}
        def apply():
            result.update(asyncio.run(invoke(**values)))
            if not result.get('ok'):raise ValueError(result.get('error','原工具拒绝保存。'))
        original=tx.read(target)
        stage=tx.inside(target.with_name('.mcp-stage-'+uuid.uuid4().hex+'.yml'))
        try:
            if original is not None:
                with stage.open('xb')as stream:stream.write(original)
            # Validate and serialize with the established atomic config writer,
            # then publish known bytes via the durable archive transaction. The
            # real profile file is never the upstream function's write target.
            config._get_write_path=lambda:str(stage)
            atomic_apply(config,config_revision(config),apply,self.service.data)
            raw=stage.read_bytes()
        finally:stage.unlink(missing_ok=True)
        archive=tx.publish({target:raw},dict(action='mcp_'+name),
            lambda:fingerprint(tx.read(target))==fingerprint(original))
        self.refresh()
        from .config_transaction import config_revision_from_path
        return dict(result,revision=config_revision_from_path(target),archive=archive)

    def screen_edit(self,name,values):
        ctx=self.service.ctx;guard=NativeResourceGuard(ctx,self.service.data)
        screen=ctx.screen_loader.get_screen(values['screen_name'])
        resource_name(screen.screen_id)
        paths=guard.screen_paths(screen)
        if self.registry is None:self.registry=load_registrations(self.manager.compatibility.source,EditBackend(self.service))
        function=self.registry.tools[name][0]
        result={}
        def apply():
            result.update(to_plain(function(**values)))
            if not result.get('success'):raise ValueError(result.get('error','原画面工具拒绝保存。'))
        guard.tx.mutate(paths,apply,'mcp_'+name)
        return dict(result,archive=guard.tx.list()[0]['id'])

    def restore(self,kind,identifier):
        with profile_lock(self.service.data):
            if kind=='config' and isinstance(identifier,str)and identifier.startswith('pending:'):
                # Explicit local restore action is also available before an
                # edit session can start; never exposed as a remote tool.
                self.service._assert_idle();self.manager.compatibility.verify_source()
                tx=self.archives(kind)
                if identifier!='pending:'+fingerprint(tx.read(tx.pending)):raise ValueError('中断保存记录已变化，请刷新后重新选择。')
                marker=json.loads(tx.read(tx.pending))
                _,files,_=tx.load(marker['id'])
                allowed=(Path(self.service.data)/'config'/f'{self.service.ctx.current_instance_idx:02d}').resolve()
                if any(not path.is_relative_to(allowed)for path,_,_ in files):raise ValueError('中断归档不属于当前账号。')
                tx.recover();self.refresh();self.manager.grant=None;self.session=None
                self.manager.log('已按已知旧/新哈希恢复中断MCP配置保存；请重新启用编辑。')
                return dict(editing=self.status())
            self.check(kind)
            tx=self.archives(kind)
            if kind=='config':
                _,files,_=tx.load(identifier)
                allowed=(Path(self.service.data)/'config'/f'{self.session["instance_idx"]:02d}').resolve()
                if any(not path.is_relative_to(allowed)for path,_,_ in files):raise ValueError('归档不属于当前账号。')
            result=tx.restore(identifier)
            if kind=='screens':self.reload_screens()
            self.refresh();self.manager.grant=None;self.session['revision']=self.revision()
            self.manager.log('MCP编辑归档已恢复：'+kind+'/'+identifier)
            return dict(restored=result,editing=self.status())
