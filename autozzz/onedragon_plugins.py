"""上游插件导入与工厂发现适配；管理目录不加载第三方代码。"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path,PureWindowsPath
import shutil
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime

from .run_lock import profile_lock,RunLock

STATE='autozzz/plugin-state.json'


def leaf(name):
    if (not isinstance(name,str) or not 1<=len(name)<=120 or name.startswith('.') or name[-1:]in ('.',' ')
            or any(char in name for char in '\\/:<>"|?*') or any(ord(char)<32 for char in name) or PureWindowsPath(name).is_reserved()
            or name.casefold()in ('autozzz','one_dragon','one_dragon_qt','zzz_od')):raise ValueError('插件目录名无效。')
    return name


def owned(path,root):
    path,root=Path(path).absolute(),Path(root).resolve()
    if path.resolve()!=path or not path.is_relative_to(root):raise ValueError('插件路径被重定向或超出数据目录。')
    return path


def files(root):
    root=Path(root)
    if not root.exists():return []
    owned(root,root.parent)
    result=[]
    for folder,dirs,names in os.walk(root,followlinks=False):
        for name in dirs:owned(Path(folder)/name,root)
        dirs[:]=sorted(name for name in dirs if name!='__pycache__')
        for name in sorted(names):
            if name.endswith(('.pyc','.pyo')):continue
            path=owned(Path(folder)/name,root)
            if path.is_file():result.append(path)
    return sorted(result)


def digest_file(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def digest_tree(root,code_only=False):
    root=Path(root);result=hashlib.sha256()
    for path in files(root):
        if code_only and path.suffix.casefold()not in ('.py','.pyd','.dll','.exe','.bat','.cmd','.ps1','.js','.vbs'):continue
        result.update(path.relative_to(root).as_posix().encode('utf-8')+b'\0'+digest_file(path).encode('ascii'))
    return result.hexdigest()


def metadata(folder):
    folder=Path(folder);factories=list(folder.glob('*_factory.py'));constants=list(folder.glob('*_const.py'))
    result=dict(name=folder.name,app_id='',app_name=folder.name,version='',author='',homepage='',description='',error='')
    try:
        if len(factories)!=1 or len(constants)!=1:raise ValueError('插件根目录须各有一个 *_factory.py 与 *_const.py。')
        path=owned(constants[0],folder)
        if path.stat().st_size>512*1024:raise ValueError('插件常量文件过大。')
        values={}
        for node in ast.parse(path.read_text(encoding='utf-8-sig')).body:
            if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant):
                for target in node.targets:
                    if isinstance(target,ast.Name):values[target.id]=node.value.value
        for field,key in (('app_id','APP_ID'),('app_name','APP_NAME'),('version','PLUGIN_VERSION'),('author','PLUGIN_AUTHOR'),('homepage','PLUGIN_HOMEPAGE'),('description','PLUGIN_DESCRIPTION')):
            if type(values.get(key))is str:result[field]=values[key][:4096]
    except (OSError,ValueError,SyntaxError,RecursionError) as exc:result['error']=str(exc)
    return result


class PluginStore:
    def __init__(self,root):self.root=Path(root).resolve();self.plugins=self.root/'plugins';self.state=self.root/STATE

    def read(self):
        owned(self.plugins,self.root);owned(self.state,self.root)
        if not self.state.exists():return dict(format=1,enabled=[],generation='')
        if self.state.stat().st_size>128*1024:raise ValueError('插件状态文件过大。')
        value=json.loads(self.state.read_text(encoding='utf-8'))
        if (not isinstance(value,dict) or value.get('format')!=1 or not isinstance(value.get('enabled'),list)
                or not isinstance(value.get('generation'),str)):raise ValueError('插件状态文件格式无效。')
        for name in value['enabled']:leaf(name)
        return value

    def generation(self):return self.read()['generation']

    def save(self,enabled):
        owned(self.state,self.root);self.state.parent.mkdir(parents=True,exist_ok=True)
        value=dict(format=1,enabled=sorted(set(leaf(name) for name in enabled)),generation=uuid.uuid4().hex)
        temporary=self.state.with_name(self.state.name+'.'+uuid.uuid4().hex+'.tmp')
        with temporary.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
        temporary.replace(self.state)

    def enabled(self,name):return name.casefold()in {item.casefold() for item in self.read()['enabled']}

    def revision(self):
        value=hashlib.sha256(json.dumps(self.read(),sort_keys=True).encode())
        value.update(digest_tree(self.plugins).encode());return value.hexdigest()

    def catalog(self):
        state=self.read();rows=[]
        if self.plugins.exists():
            for path in sorted(self.plugins.iterdir()):
                if path.name.startswith('.'):continue
                try:
                    leaf(path.name);owned(path,self.plugins)
                    if not path.is_dir():continue
                    row=metadata(path);row.update(enabled=self.enabled(path.name),path=str(path));rows.append(row)
                except ValueError as exc:rows.append(dict(name=path.name,app_name=path.name,enabled=False,error=str(exc),path='',version='',author='',homepage='',description='',app_id=''))
        return dict(plugins=rows,revision=self.revision(),generation=state['generation'],directory=str(self.plugins))


def importer_for(root):
    from one_dragon.base.operation.application.plugin_import_service import PluginImportService
    root=Path(root).resolve()
    class Importer(PluginImportService):
        @property
        def plugins_dir(self):return owned(root/'plugins',root)
        def _get_target_dir(self,name):
            leaf(name);return owned(super()._get_target_dir(name),self.plugins_dir)
        def _remove_path(self,path):
            # 源服务可能递归删除临时目录或覆盖备份，只允许当前区服插件根内的对象。
            path=owned(path,self.plugins_dir)
            if path==self.plugins_dir:raise ValueError('不能删除插件根目录。')
            return super()._remove_path(path)
    return Importer(None)


class PluginManager:
    def __init__(self,root):
        self.root=Path(root).resolve();self.store=PluginStore(root);self.importer=importer_for(root)
        self.lock=threading.RLock();self.worker=None;self.state='idle';self.job='';self.result=None;self.error='';self.closed=False
        self.review=None;self.review_root=self.root/'autozzz/plugin-imports';self.archives=self.root/'autozzz/plugin-archives'

    @property
    def busy(self):return self.worker is not None and self.worker.is_alive()

    def catalog(self):
        with profile_lock(self.root):return self.store.catalog()

    def status(self):
        with self.lock:return dict(job_id=self.job,state=self.state,busy=self.busy,result=copy.deepcopy(self.result),error=self.error)

    def _start(self,state,operation):
        with self.lock:
            if self.closed or self.busy:raise RuntimeError('原插件操作尚未完成。')
            self.state=state;self.job=uuid.uuid4().hex;self.error='';self.result=None
            def work():
                try:
                    with profile_lock(self.root),RunLock() if state!='previewing' else nullcontext():result=operation()
                    with self.lock:self.result=result;self.state='done'
                except Exception as exc:
                    with self.lock:self.state='failed';self.error=str(exc)
            self.worker=threading.Thread(target=work,name='AutoZZZ-Plugin-'+state,daemon=False);self.worker.start();return self.status()

    def _cleanup_review(self):
        if self.review is not None:
            folder=owned(Path(self.review['folder']),owned(self.review_root,self.root))
            if folder==self.review_root:raise ValueError('预览目录无效。')
            if folder.exists():shutil.rmtree(folder)
        self.review=None

    def preview(self,path,kind):
        if kind not in ('zip','directory'):raise ValueError('插件来源类型无效。')
        source=Path(path).resolve()
        if source==self.root or self.root.is_relative_to(source) or source.is_relative_to(self.root):raise ValueError('请选择数据目录之外的插件来源。')
        def prepare():
            self._cleanup_review();token=uuid.uuid4().hex;folder=owned(self.review_root/token,self.root);folder.mkdir(parents=True)
            self.review=dict(token=token,folder=str(folder),kind=kind,source=str(source))
            if kind=='zip':
                target=folder/'source.zip';shutil.copyfile(source,target)
                previews=self.importer.preview_plugins_from_zip(target)
            else:
                target=folder/'source';target.mkdir()
                for layout in self.importer._analyze_directory(source):
                    leaf(layout.plugin_dir_name);self.importer._validate_directory_symlinks(layout.source_dir)
                    shutil.copytree(layout.source_dir,target/layout.plugin_dir_name)
                previews=self.importer.preview_plugins_from_directory(target)
            if not previews:raise ValueError('未发现合法插件；插件根目录须各有一个工厂文件和常量文件。')
            current=self.store.catalog();existing={row['name'].casefold():row for row in current['plugins']}
            rows=[]
            for preview in previews:
                leaf(preview.plugin_name);old=existing.get(preview.plugin_name.casefold())
                downgrade=False
                if old and old['version'] and preview.version:
                    from packaging.version import Version,InvalidVersion
                    try:downgrade=Version(preview.version)<Version(old['version'])
                    except InvalidVersion:pass
                rows.append(dict(name=preview.plugin_name,version=preview.version or '',author=preview.author or '',exists=old is not None,old_version=old['version'] if old else '',downgrade=downgrade))
            self.review.update(path=str(target),signature=digest_file(target) if kind=='zip' else digest_tree(target),revision=current['revision'],plugins=rows)
            return dict(token=token,source=str(source),plugins=rows,revision=current['revision'])
        return self._start('previewing',prepare)

    def _archive(self,names,action):
        identifier=uuid.uuid4().hex;folder=owned(self.archives/identifier,self.root);folder.mkdir(parents=True)
        rows=[]
        for name in names:
            source=owned(self.store.plugins/leaf(name),self.store.plugins)
            if not source.exists():continue
            files(source);target=folder/name;shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo'))
            rows.append(dict(name=name,digest=digest_tree(target)))
        record=dict(id=identifier,action=action,plugins=rows,created=datetime.now().astimezone().isoformat())
        (folder/'record.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');return identifier

    def install(self,token,names,overwrites,revision,allow_downgrade=False):
        if not isinstance(names,list) or not names or not isinstance(overwrites,list):raise ValueError('请选择插件。')
        for name in names+overwrites:leaf(name)
        if type(allow_downgrade)is not bool:raise ValueError('降级选项须为布尔值。')
        def install():
            review=self.review
            if review is None or token!=review['token'] or revision!=self.store.revision() or revision!=review['revision']:raise ValueError('插件或预览已变化，请重新预览。')
            source=Path(review['path']);signature=digest_file(source) if review['kind']=='zip' else digest_tree(source)
            if signature!=review['signature']:raise ValueError('预览副本已变化，请重新预览。')
            available={row['name'].casefold() for row in review['plugins']}
            if not {name.casefold() for name in names}<=available:raise ValueError('预览不含所选插件。')
            if not allow_downgrade and any(row['downgrade'] and row['name'].casefold()in {name.casefold() for name in names} for row in review['plugins']):raise ValueError('所选插件将降级，需要明确允许降级。')
            old={row['name'].casefold():row['name'] for row in self.store.catalog()['plugins']}
            overwrite_set={name.casefold() for name in overwrites};selected={name.casefold() for name in names}
            if not overwrite_set<=selected:raise ValueError('覆盖选项与已选插件不一致。')
            required=selected&set(old)
            if not required<=overwrite_set:raise ValueError('同名插件需要明确选择覆盖。')
            archive=self._archive([old[key] for key in required],'overwrite') if required else None
            method=self.importer.import_plugins_from_zip if review['kind']=='zip' else self.importer.import_plugins_from_directory
            results=method(source,overwrite=bool(required),selected_plugin_names=set(names))
            successes=[item.plugin_name for item in results if item.success]
            if successes:
                enabled=[name for name in self.store.read()['enabled'] if name.casefold()not in {item.casefold() for item in successes}]
                self.store.save(enabled)
            return dict(results=[dict(success=item.success,name=item.plugin_name,message=item.message) for item in results],archive=archive,catalog=self.store.catalog())
        return self._start('installing',install)

    def enabled(self,name,enabled,revision):
        leaf(name)
        if type(enabled)is not bool:raise ValueError('加载开关须为布尔值。')
        with profile_lock(self.root),RunLock():
            if self.busy or revision!=self.store.revision():raise ValueError('插件列表已变化，请重新读取。')
            path=owned(self.store.plugins/name,self.store.plugins)
            if not path.is_dir():raise ValueError('插件不存在。')
            values=self.store.read()['enabled'];values=[item for item in values if item.casefold()!=name.casefold()]
            if enabled:
                if metadata(path)['error']:raise ValueError('插件结构无效，不能启用。')
                values.append(name)
            self.store.save(values);return self.store.catalog()

    def remove(self,name,revision):
        leaf(name)
        def remove():
            if revision!=self.store.revision():raise ValueError('插件列表已变化，请重新读取。')
            archive=self._archive([name],'remove');result=self.importer.delete_plugin(name)
            if result.success:self.store.save([item for item in self.store.read()['enabled'] if item.casefold()!=name.casefold()])
            return dict(results=[dict(success=result.success,name=name,message=result.message)],archive=archive,catalog=self.store.catalog())
        return self._start('removing',remove)

    def archive_list(self):
        result=[]
        if self.archives.exists():
            for folder in sorted(self.archives.iterdir()):
                try:result.append(self._record(folder.name))
                except (OSError,ValueError):continue
        return result

    def _record(self,identifier):
        if not isinstance(identifier,str) or len(identifier)!=32 or any(char not in '0123456789abcdef' for char in identifier):raise ValueError('归档编号无效。')
        folder=owned(self.archives/identifier,self.root);path=owned(folder/'record.json',folder)
        if path.stat().st_size>128*1024:raise ValueError('插件归档记录过大。')
        record=json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(record,dict) or record.get('id')!=identifier or record.get('action')not in ('remove','overwrite','restore-overwrite')
                or not isinstance(record.get('created'),str) or not isinstance(record.get('plugins'),list) or not record['plugins']):raise ValueError('插件归档记录无效。')
        names=set()
        for row in record['plugins']:
            if not isinstance(row,dict):raise ValueError('插件归档记录无效。')
            name=leaf(row.get('name'));signature=row.get('digest')
            if name.casefold()in names or not isinstance(signature,str) or len(signature)!=64 or any(char not in '0123456789abcdef' for char in signature):raise ValueError('插件归档记录无效。')
            names.add(name.casefold())
        return record

    def directory(self):
        with profile_lock(self.root):owned(self.store.plugins,self.root).mkdir(parents=True,exist_ok=True)
        return str(self.store.plugins)

    def restore(self,identifier,names,revision,overwrites):
        if not isinstance(names,list) or not names or not isinstance(overwrites,list):raise ValueError('请选择归档中的插件。')
        for name in names+overwrites:leaf(name)
        if not set(overwrites)<=set(names):raise ValueError('覆盖选项与已选插件不一致。')
        def restore():
            if revision!=self.store.revision():raise ValueError('插件列表已变化，请重新读取。')
            record=self._record(identifier);folder=owned(self.archives/identifier,self.root)
            available={row['name']:row for row in record['plugins']}
            if not names or not set(names)<=set(available):raise ValueError('归档不含所选插件。')
            for name in names:
                if digest_tree(owned(folder/name,folder))!=available[name]['digest']:raise ValueError('插件归档已变化。')
            existing=[name for name in names if (self.store.plugins/name).exists()]
            if not set(existing)<=set(overwrites):raise ValueError('恢复将覆盖同名插件，请明确选择覆盖。')
            backup=self._archive(existing,'restore-overwrite') if existing else None
            results=[]
            for name in names:results.extend(self.importer.import_plugins_from_directory(folder/name,overwrite=name in existing))
            successes={item.plugin_name.casefold() for item in results if item.success}
            if successes:self.store.save([name for name in self.store.read()['enabled'] if name.casefold()not in successes])
            return dict(results=[dict(success=item.success,name=item.plugin_name,message=item.message) for item in results],archive=backup,catalog=self.store.catalog())
        return self._start('restoring',restore)

    def close(self):
        self.closed=True
        if self.worker is not None:self.worker.join()
        self._cleanup_review()


def factory_manager(ctx):
    from one_dragon.base.operation.application.application_factory_manager import ApplicationFactoryManager
    from one_dragon.base.operation.application.plugin_info import PluginSource
    from one_dragon.utils import os_utils
    store=PluginStore(os_utils.get_work_dir())
    class Manager(ApplicationFactoryManager):
        def _scan_directory(self,directory,reload_modules=False,source=PluginSource.BUILTIN):
            offset=len(self._scan_failures);factories=super()._scan_directory(directory,reload_modules,source)
            if source==PluginSource.THIRD_PARTY:
                self._scan_failures[offset:]=[(path,error) for path,error in self._scan_failures[offset:] if not path.relative_to(directory).parts or store.enabled(path.relative_to(directory).parts[0])]
            return factories
        def _load_factory_from_file(self,path,reload_modules=False,source=PluginSource.BUILTIN,base_dir=None):
            if source==PluginSource.THIRD_PARTY:
                if not store.enabled(path.parent.name):return None
                owned(path,store.plugins)
                lease=getattr(ctx,'plugin_run_lease',None)
                with lease.scope() if lease is not None else RunLock():
                    return super()._load_factory_from_file(path,reload_modules,source,base_dir)
            return super()._load_factory_from_file(path,reload_modules,source,base_dir)
    ctx.plugin_generation=store.generation()
    return Manager(ctx,ctx.application_plugin_dirs)


def verify_context_plugins(ctx,code=False):
    generation=getattr(ctx,'plugin_generation',None)
    if generation is None:return
    from one_dragon.utils import os_utils
    store=PluginStore(os_utils.get_work_dir())
    if (store.generation()!=generation or code and getattr(ctx,'plugin_code_signature',None)is not None
            and ctx.plugin_code_signature!=digest_tree(store.plugins,code_only=True)):
        raise RuntimeError('插件已变化，请关闭原工作进程并重新读取或准备。')


def plugin_application(ctx,app_id):
    """只接受实际管理器已经注册、且当前仍允许加载的第三方工厂。"""
    from .onedragon_runtime import EXECUTABLE_APPLICATIONS
    if app_id in EXECUTABLE_APPLICATIONS:return None
    manager=getattr(ctx,'factory_manager',None)
    info=getattr(manager,'get_plugin_info',lambda _:None)(app_id)
    if info is None or getattr(info,'is_third_party',False)is not True:return None
    if app_id not in ctx.run_context._application_factory_map:return None
    verify_context_plugins(ctx,code=True)
    from one_dragon.utils import os_utils
    store=PluginStore(os_utils.get_work_dir());path=owned(info.plugin_dir,store.plugins)
    if path.parent!=store.plugins or not store.enabled(path.name):raise ValueError('插件尚未启用加载，请重新读取插件状态。')
    return info


def executable_application(ctx,app_id):
    from .onedragon_runtime import EXECUTABLE_APPLICATIONS
    return app_id in EXECUTABLE_APPLICATIONS or plugin_application(ctx,app_id)is not None
