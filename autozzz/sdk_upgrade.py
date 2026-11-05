"""Reviewed source -> isolated locked dependencies -> real bridge probe -> atomic SDK selection."""
from __future__ import annotations

from dataclasses import replace
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import uuid

from .onedragon_bundle import digest,tree_files
from .run_lock import RunLock,profile_lock
from .runtime_selection import (FORMAT,RuntimeSelection,bridge_files,bridge_fingerprint,candidate_root,candidate_selection,
    commit,identifier,plain,read_json,resolve_selection,runtime_files,selection_path,snapshot_path,store_root,tree_hash,verify_identity)
from .sdk_upgrade_files import (atomic_bytes,atomic_json,copy_base_python,copy_files,data_state,pending_edits,resource_files,snapshot_data,unpack_archive)
from .sdk_upgrade_tools import prepare_uv,verified_uv,UV_VERSION


class SDKUpgradeManager:
    def __init__(self,service,stable_paths=None,profile=None):
        from .onedragon_runtime import RuntimePaths
        self.service=service;self.data=Path(getattr(service,'stable_data',service.data)).resolve()
        if stable_paths is None:
            product=Path(service.product_root)
            matches=[(name,RuntimePaths.discover(product,name,use_selection=False))for name in ('cn','global')]
            matches=[(name,paths)for name,paths in matches if paths.data.resolve()==self.data]
            if len(matches)!=1:raise ValueError('无法定位稳定区服控制目录。')
            profile,stable_paths=matches[0]
        self.paths=replace(stable_paths,data=self.data,stable_data=self.data);self.profile=profile or 'cn'
        self.root=store_root(self.data);self.lock=threading.RLock();self.cancelled=threading.Event();self.worker=None;self.process=None;self.job=None;self.closed=False
        from .source_workspace import SourceWorkspace
        self.workspace=SourceWorkspace(service)

    @property
    def busy(self):return self.worker is not None and self.worker.is_alive()

    def _selection_hash(self):
        path=selection_path(self.data);return digest(path)if path.exists()else '-'

    def _data_locks(self,*roots):
        # profile_lock is an existence mutex, not a recursive/owned Windows mutex.
        stack=ExitStack();unique={Path(root).resolve()for root in roots}-{self.data}
        try:
            for root in sorted(unique,key=str):stack.enter_context(profile_lock(root))
            return stack
        except BaseException:stack.close();raise

    def _current(self):
        """Data remains reachable from the stable control plane even if candidate code is broken."""
        path=selection_path(self.data)
        if not path.exists():return self.data,self.paths.source
        value=read_json(path,8192)
        if (type(value)is not dict or value.get('format')!=1 or value.get('kind')not in ('candidate','stable')):
            raise ValueError('SDK选择记录无效，未猜测账号资料位置。')
        data=snapshot_path(self.data,value.get('data_id'))
        if value['kind']=='stable':return data,self.paths.source
        root=candidate_root(self.data,value.get('candidate_id'))
        try:
            verify_identity(root/'runtime.json',value.get('identity_digest'),source=root/'source',python=root/'env/Scripts/python.exe',bridge=root/'bridge',
                revision=value.get('revision'),protocol=value.get('protocol'))
            source=root/'source'
        except (OSError,ValueError):source=self.paths.source
        return data,source

    def catalog(self):
        candidates=[];directory=plain(self.root/'candidates')
        if directory.exists():
            for child in sorted(directory.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True):
                try:
                    record=read_json(child/'candidate.json',256*1024);identifier(child.name)
                    if record.get('stable_data')!=str(self.data):continue
                    candidates.append({key:record.get(key)for key in ('id','state','revision','branch','groups','data_id','probe','error','bridge_hash','dependency_plan_hash','prepared_data_hash','snapshot','hello')}|
                        dict(path=str(child),requirements_path=str(child/'requirements-reviewed.txt')))
                except (ValueError,OSError):continue
        selection=None;error=''
        try:
            active=resolve_selection(self.data,stable_source=self.paths.source,stable_python=self.paths.python,stable_bridge=self.paths.bridge_root,
                stable_bundle=self.paths.bundle,stable_revision=self.paths.revision,protocol=1)
            if active is not None:selection=dict(kind=active.kind,candidate_id=active.candidate_id,revision=active.revision,data=str(active.data))
        except (ValueError,OSError)as exc:error=str(exc)
        try:uv=str(verified_uv())
        except ValueError:uv=None
        return dict(stable_data=str(self.data),stable_revision=self.paths.revision,selected=selection,selection_error=error,selection_revision=self._selection_hash(),
            candidates=candidates,uv=uv,uv_version=UV_VERSION,busy=self.busy,activation_pending=(self.data/'autozzz/sdk-activation.json').exists())

    def review(self,groups=None,source_candidate=None):
        self.service._assert_idle()
        if self.busy:raise RuntimeError('SDK操作仍在进行。')
        with profile_lock(self.data):
            self.cancelled.clear();source_mode='workspace'
            if source_candidate is None:
                self.workspace.verify();self.workspace._clean();catalog=self.workspace._catalog()
                revision=commit(catalog['head']);branch=catalog['branch'];source=self.workspace.root
                lock=self.workspace._git('cat-file','blob',self.workspace._git('rev-parse',revision+':uv.lock'),binary=True)
                project_raw=self.workspace._git('cat-file','blob',self.workspace._git('rev-parse',revision+':pyproject.toml'),binary=True)
            else:
                source_mode='candidate';root=candidate_root(self.data,source_candidate);metadata=read_json(root/'runtime.json')
                verify_identity(root/'runtime.json',digest(root/'runtime.json'),source=root/'source',python=root/'env/Scripts/python.exe',bridge=root/'bridge',
                    revision=metadata['revision'],protocol=metadata['protocol'])
                revision,branch,source=metadata['revision'],metadata['branch'],root/'source'
                lock=(source/'uv.lock').read_bytes();project_raw=(source/'pyproject.toml').read_bytes()
            project=tomllib.loads(project_raw.decode('utf-8'))
            available=project.get('dependency-groups',{})
            groups=[]if groups is None else groups
            if type(groups)is not list or any(type(g)is not str or g not in available or g=='gamepad'for g in groups)or len(groups)!=len(set(groups)):
                raise ValueError('请选择锁文件已有可选组；gamepad绑定包与驱动继续由独立安装入口管理。')
            source_data,old_source=self._current();issues=pending_edits(source_data)
            facts=dict(id=uuid.uuid4().hex,format=1,source_mode=source_mode,source_candidate=source_candidate,source=str(source),revision=revision,branch=branch,
                groups=groups,available_groups=[g for g in available if g!='gamepad'],lock_hash=hashlib.sha256(lock).hexdigest(),
                bridge_hash=bridge_fingerprint(self.paths.bridge_root),data_source=str(source_data),old_source=str(old_source),data_state=data_state(source_data),
                pending=issues,selection_revision=self._selection_hash(),python_constraint=project.get('project',{}).get('requires-python',''))
            path=plain(self.root/'reviews'/(facts['id']+'.json'));atomic_json(path,facts)
            return {key:value for key,value in facts.items()if key!='data_state'}|dict(review_digest=digest(path),data_hash=tree_hash(facts['data_state']),data_files=len(facts['data_state']))

    def _run(self,command,*,cwd,environment=None,timeout=900):
        if self.cancelled.is_set():raise InterruptedError('SDK操作已取消，候选与快照保留。')
        with tempfile.TemporaryFile()as output:
            process=subprocess.Popen(command,cwd=cwd,env=environment,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            with self.lock:self.process=process
            deadline=time.monotonic()+timeout
            try:
                while process.poll()is None:
                    if self.cancelled.wait(.05)or time.monotonic()>deadline:
                        if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)
                        else:process.terminate()
                        process.wait(timeout=20)
                        if self.cancelled.is_set():raise InterruptedError('SDK子进程已取消并退出；候选保留。')
                        raise TimeoutError('SDK子进程超时；未激活。')
                output.seek(0);raw=output.read(2*1024**2+1)
                if len(raw)>2*1024**2:raise ValueError('SDK子进程输出过大，未激活。')
                result=raw.decode('utf-8',errors='replace')
                if process.returncode:raise ValueError('SDK子进程失败：'+result[-16000:])
                return result
            finally:
                with self.lock:self.process=None

    def _environment(self):
        environment={key:value for key,value in os.environ.items()if not key.startswith(('UV_','PIP_','GIT_'))}
        for key in ('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE','PYTHONSTARTUP','VIRTUAL_ENV','CONDA_PREFIX','AUTOZZZ_INTERNAL_RUN_LEASE'):environment.pop(key,None)
        environment.update(PYTHONUTF8='1',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
        return environment

    def _save_candidate(self,root,record,**changes):
        record.update(changes);atomic_json(root/'candidate.json',record)
        with self.lock:
            if self.job is not None:self.job['phase']=record['state']

    def _handshake(self,selected,*,deep=False):
        from .onedragon_runtime import OneDragonClient,RequestPending
        paths=self.paths.with_selection(selected);client=OneDragonClient(paths);hello=None
        try:
            try:hello=client.start(timeout=.5)
            except RequestPending:
                while hello is None:
                    if self.cancelled.is_set():raise InterruptedError('SDK握手已取消。')
                    try:hello=client.finish_start(timeout=.5)
                    except RequestPending:pass
            request=client.submit('catalog')
            while True:
                if self.cancelled.is_set():raise InterruptedError('SDK目录核对已取消。')
                try:catalog=client.result(request,timeout=.5);break
                except RequestPending:continue
            if hello['revision']!=selected.revision:raise ValueError('重启后实际SDK版本与选择不符。')
            if catalog['failures']or catalog['plugin_failures']:raise ValueError('实际SDK存在应用工厂加载失败。')
            if deep:
                def call(method,**params):
                    request=client.submit(method,**params)
                    while True:
                        if self.cancelled.is_set():raise InterruptedError('兼容核对已取消。')
                        try:return client.result(request,timeout=.5)
                        except RequestPending:pass
                for app in catalog['applications']:
                    if app['configurable']:
                        config=call('configuration',app_id=app['id'])
                        if config['failures']:raise ValueError('稳定SDK无法读取携带资料：'+app['id'])
                for kind in ('auto_battle','dodge'):
                    for policy in call('policies',kind=kind):call('policy_validate',name=policy['name'],kind=kind)
                state=call('execution_status')
                if state.get('ready')or state.get('busy'):raise ValueError('兼容核对意外初始化了游戏。')
            return dict(revision=hello['revision'],pid=hello['pid'],application_count=len(catalog.get('applications',[])))
        finally:
            while True:
                try:client.close(timeout=.5);break
                except RequestPending:continue

    def _build(self,plan,offline):
        from .onedragon_runtime import PROTOCOL_VERSION
        self.paths.verify()
        if self._selection_hash()!=plan['selection_revision']:raise ValueError('所选SDK已变化，请重新审核。')
        if bridge_fingerprint(self.paths.bridge_root)!=plan['bridge_hash']:raise ValueError('AutoZZZ桥接代码已变化，请重新审核。')
        if plan['pending']:raise ValueError('存在待恢复修改，先处理后再升级：'+'、'.join(plan['pending']))
        uv=verified_uv();candidate_id=uuid.uuid4().hex;root=candidate_root(self.data,candidate_id);root.mkdir(parents=True,exist_ok=False)
        data_id=uuid.uuid4().hex;target_data=snapshot_path(self.data,data_id)
        record=dict(id=candidate_id,state='exporting_source',revision=plan['revision'],branch=plan['branch'],groups=plan['groups'],data_id=data_id,
            bridge_hash=plan['bridge_hash'],probe=None,error='',review_id=plan['id'],source_data=plan['data_source'],source_data_hash=tree_hash(plan['data_state']),stable_data=str(self.data),profile=self.profile)
        self._save_candidate(root,record)
        try:
            if plan['source_mode']=='workspace':
                self.workspace.verify();self.workspace._clean()
                if self.workspace._git('rev-parse','HEAD')!=plan['revision']:raise ValueError('源码提交在审核后变化。')
                archive=root/'source.tar';self.workspace._git('archive','--format=tar','--output='+str(archive),plan['revision'],timeout=300)
                unpack_archive(archive,root/'source',self.cancelled);archive.unlink()
            else:
                previous=candidate_root(self.data,plan['source_candidate']);metadata=read_json(previous/'runtime.json')
                verify_identity(previous/'runtime.json',digest(previous/'runtime.json'),source=previous/'source',python=previous/'env/Scripts/python.exe',bridge=previous/'bridge',revision=plan['revision'],protocol=PROTOCOL_VERSION)
                names=[name.removeprefix('source/')for name in metadata['files']if name.startswith('source/')]
                (root/'source').mkdir();copy_files(previous/'source',root/'source',names,self.cancelled)
            if digest(root/'source/uv.lock')!=plan['lock_hash']:raise ValueError('候选锁文件与审核版本不符。')
            self._save_candidate(root,record,state='copying_python_bridge')
            facts=self._run([str(self.paths.python),'-s','-P','-B','-c','import sys,json;print(json.dumps(dict(version=sys.version.split()[0],base=sys.base_prefix)))'],cwd=root,environment=self._environment(),timeout=30)
            facts=json.loads(facts.strip().splitlines()[-1])
            if not re.fullmatch(r'3\.11\.\d+',facts['version']):raise ValueError('稳定SDK不是可复用的Python3.11。')
            copy_base_python(facts['base'],root/'base-python',self.cancelled)
            (root/'bridge').mkdir();files=bridge_files(self.paths.bridge_root);copy_files(self.paths.bridge_root,root/'bridge',files,self.cancelled)
            if bridge_fingerprint(root/'bridge')!=plan['bridge_hash']:raise ValueError('冻结桥接文件在复制时发生变化。')
            environment=self._environment();environment.update(UV_PROJECT_ENVIRONMENT=str(root/'env'),VIRTUAL_ENV=str(root/'env'),UV_LINK_MODE='copy',
                UV_CACHE_DIR=str(self.paths.source/'.install/uv_cache')if (self.paths.source/'.install/uv_cache').is_dir()else str(self.root/'uv-cache'))
            options=['--frozen','--no-default-groups']
            for group in plan['groups']:options+=['--group',group]
            if offline:options+=['--offline']
            exported=self._run([str(uv),'export','--format','requirements-txt','--no-emit-project',*options],cwd=root/'source',environment=environment,timeout=120)
            if re.search(r'(?mi)^vgamepad(?:\[[^\]]*\])?\s*==',exported):raise ValueError('选中依赖包含可能安装驱动的vgamepad；未执行uv同步。')
            (root/'requirements-reviewed.txt').write_text(exported,encoding='utf-8')
            self._save_candidate(root,record,state='syncing_locked_dependencies',dependency_plan_hash=digest(root/'requirements-reviewed.txt'))
            command=[str(uv),'sync','--no-install-project','--no-python-downloads','--python',str(root/'base-python/python.exe'),*options]
            self._run(command,cwd=root/'source',environment=environment,timeout=1200)
            self._run(command+['--check'],cwd=root/'source',environment=environment,timeout=120)
            if digest(root/'source/uv.lock')!=plan['lock_hash']:raise ValueError('uv更改了冻结锁文件，未激活。')
            self._save_candidate(root,record,state='snapshotting_profile')
            with self._data_locks(plan['data_source']):snapshot=snapshot_data(plan['data_source'],target_data,root/'source',plan['old_source'],plan['revision'],PROTOCOL_VERSION,self.cancelled,plan['data_state'])
            self._save_candidate(root,record,snapshot=snapshot,state='probing_source_bridge')
            checksums={name:digest(path)for name,path in runtime_files(root).items()}
            metadata=dict(format=FORMAT,candidate_id=candidate_id,revision=plan['revision'],protocol=PROTOCOL_VERSION,python_version=facts['version'],
                branch=plan['branch'],remote=self.workspace.remote,groups=plan['groups'],lock_hash=plan['lock_hash'],bridge_hash=plan['bridge_hash'],files=checksums,
                resources=sorted(resource_files(root/'source')),probe=dict(compatible=False,revision=plan['revision']))
            atomic_json(root/'runtime.json',metadata)
            probe_env=self._environment();probe_env['PYTHONPATH']=str(root/'bridge')
            probe_raw=self._run([str(root/'env/Scripts/python.exe'),'-s','-P','-B','-u','-m','autozzz.sdk_upgrade_probe',
                '--runtime',str(root/'runtime.json'),'--digest',digest(root/'runtime.json'),'--data',str(target_data)],cwd=root/'bridge',environment=probe_env,timeout=300)
            reports=[json.loads(line)for line in probe_raw.splitlines()if line.startswith('{')];probe=reports[-1]
            atomic_json(root/'probe.json',probe)
            if probe.get('compatible')is not True:raise ValueError('候选未通过完整桥接契约：'+json.dumps(probe.get('errors',[]),ensure_ascii=False))
            metadata['probe']=probe;atomic_json(root/'runtime.json',metadata)
            selected=candidate_selection(self.data,candidate_id,data_id,PROTOCOL_VERSION)
            self._save_candidate(root,record,state='restarting_verified_worker',probe=probe)
            hello=self._handshake(selected)
            self._save_candidate(root,record,state='ready',hello=hello,identity_digest=digest(root/'runtime.json'),prepared_data_hash=tree_hash(data_state(target_data)))
            return {key:value for key,value in record.items()if key not in ('source_data',)}
        except Exception as exc:
            self._save_candidate(root,record,state='cancelled'if isinstance(exc,InterruptedError)else'failed',error=str(exc)[-16000:]);raise

    def _start(self,action,operation):
        self.service._assert_idle()
        with self.lock:
            if self.closed:raise RuntimeError('SDK管理器已关闭。')
            if self.busy:raise RuntimeError('SDK作业仍在进行，请查询原编号。')
            self.cancelled.clear();self.job=dict(job_id=uuid.uuid4().hex,state='working',phase=action,action=action,result=None,error='')
            job_path=plain(self.root/'jobs'/(self.job['job_id']+'.json'));atomic_json(job_path,self.job)
            def work():
                try:
                    with RunLock(),profile_lock(self.data):result=operation()
                    with self.lock:self.job.update(state='success',result=result)
                except Exception as exc:
                    with self.lock:self.job.update(state='cancelled'if isinstance(exc,InterruptedError)else'failed',error=str(exc)[-16000:])
                finally:
                    with self.lock:atomic_json(job_path,self.job)
            self.worker=threading.Thread(target=work,name='AutoZZZ-SDK-Upgrade',daemon=False);self.worker.start();return self.status()

    def build(self,review_id,review_digest,confirmed=False,offline=True):
        if confirmed is not True or type(offline)is not bool:raise ValueError('请明确确认源码、依赖组与资料快照后构建。')
        path=plain(self.root/'reviews'/(identifier(review_id)+'.json'))
        if digest(path)!=review_digest:raise ValueError('SDK审核记录已变化。')
        plan=read_json(path);return self._start('build',lambda:self._build(plan,offline))

    def prepare_tool(self):return self._start('prepare_uv',lambda:dict(path=prepare_uv(self.cancelled)))

    def _candidate(self,candidate_id):
        root=candidate_root(self.data,candidate_id);record=read_json(root/'candidate.json',256*1024)
        if record.get('id')!=candidate_id or record.get('stable_data')!=str(self.data)or record.get('state')!='ready':
            raise ValueError('候选尚未就绪或不属于本区服。')
        if record.get('hello',{}).get('revision')!=record['revision']:raise ValueError('候选尚未通过真实重启握手。')
        selected=candidate_selection(self.data,candidate_id,record['data_id'],1)
        if digest(selected.identity_file)!=record['identity_digest']:raise ValueError('候选身份在握手后变化。')
        if bridge_fingerprint(self.paths.bridge_root)!=record['bridge_hash']:raise ValueError('AutoZZZ桥接已更新，请审核该候选并重建，而非沿用旧探测结论。')
        return root,record,selected

    def refresh(self,candidate_id,confirmed=False):
        if confirmed is not True:raise ValueError('请确认携带当前资料重新探测。')
        def refresh():
            root,record,selected=self._candidate(candidate_id);source_data,old_source=self._current()
            data_id=uuid.uuid4().hex;target=snapshot_path(self.data,data_id);expected=data_state(source_data)
            with self._data_locks(source_data):
                snapshot=snapshot_data(source_data,target,selected.source,old_source,selected.revision,1,self.cancelled,expected)
            new=candidate_selection(self.data,candidate_id,data_id,1);hello=self._handshake(new,deep=True)
            self._save_candidate(root,record,data_id=data_id,hello=hello,snapshot=snapshot,source_data=str(source_data),source_data_hash=tree_hash(expected),
                prepared_data_hash=tree_hash(data_state(target)),state='ready')
            return dict(candidate_id=candidate_id,data_id=data_id,hello=hello,snapshot=snapshot)
        return self._start('refresh_profile',refresh)

    @property
    def activation_journal(self):return plain(self.data/'autozzz/sdk-activation.json')

    @staticmethod
    def _payload(record):return json.dumps(record,ensure_ascii=False,sort_keys=True,indent=2).encode()

    def _restore_selector(self,journal):
        path=selection_path(self.data)
        if journal['before_text']is None:path.unlink(missing_ok=True)
        else:atomic_bytes(path,journal['before_text'].encode())

    def _switch(self,selected,new_record,expected_revision):
        if self.activation_journal.exists():raise ValueError('先核对并恢复上次SDK切换。')
        if self._selection_hash()!=expected_revision:raise ValueError('所选SDK已变化，请刷新后再操作。')
        if self.cancelled.is_set():raise InterruptedError('切换前已取消，未改动SDK选择。')
        path=selection_path(self.data);before_text=path.read_text(encoding='utf-8')if path.exists()else None
        after_hash=hashlib.sha256(self._payload(new_record)).hexdigest()
        journal=dict(format=1,id=uuid.uuid4().hex,before_text=before_text,before_hash=expected_revision,after=new_record,after_hash=after_hash,
            after_data_hash=tree_hash(data_state(selected.data)),state='prepared')
        atomic_json(self.activation_journal,journal)
        try:
            if self._selection_hash()!=expected_revision:raise ValueError('发布SDK选择前原记录发生变化。')
            atomic_json(path,new_record);journal['state']='verifying_restart';atomic_json(self.activation_journal,journal)
            hello=self._handshake(selected,deep=True)
            if self._selection_hash()!=after_hash:raise ValueError('重启核对期间SDK选择发生外部变化。')
            with self._data_locks(selected.data):
                if tree_hash(data_state(selected.data))!=journal['after_data_hash']:raise ValueError('候选资料在重启核对期间变化；已保留快照并回退SDK选择。')
            journal.update(state='complete',hello=hello);atomic_json(self.root/'activations'/(journal['id']+'.json'),journal)
            self.activation_journal.unlink();self.service._environment_changed=True
            return dict(revision=selected.revision,kind=selected.kind,candidate_id=selected.candidate_id,data=str(selected.data),hello=hello,selection_revision=after_hash)
        except Exception as exc:
            current=self._selection_hash()
            if current not in (journal['before_hash'],journal['after_hash']):raise RuntimeError('SDK切换结果待核对，选择记录已被外部改动；保留切换journal与所有快照。')from exc
            self._restore_selector(journal);journal.update(state='rolled_back',error=str(exc))
            atomic_json(self.root/'activations'/(journal['id']+'.json'),journal);self.activation_journal.unlink()
            raise

    def activate(self,candidate_id,selection_revision,confirmed=False):
        if confirmed is not True:raise ValueError('请确认候选源码、依赖与资料快照后激活。')
        def activate():
            _,record,selected=self._candidate(candidate_id);current_data,_=self._current()
            new=dict(format=FORMAT,kind='candidate',candidate_id=candidate_id,data_id=record['data_id'],revision=selected.revision,protocol=1,identity_digest=selected.identity_digest)
            pointer=selection_path(self.data)
            if pointer.exists()and read_json(pointer,8192)==new:
                if self._selection_hash()!=selection_revision:raise ValueError('SDK选择已变化，请刷新。')
                return dict(already_active=True,revision=selected.revision,data=str(selected.data))
            with self._data_locks(current_data):
                if tree_hash(data_state(current_data))!=record['source_data_hash']:raise ValueError('当前资料已变化，请先“刷新资料并探测”，不能切到陈旧快照。')
                with self._data_locks(selected.data):
                    if tree_hash(data_state(selected.data))!=record['prepared_data_hash']:raise ValueError('候选快照在探测后被修改，请重新探测。')
                return self._switch(selected,new,selection_revision)
        return self._start('activate',activate)

    def rollback(self,selection_revision,confirmed=False):
        if confirmed is not True:raise ValueError('请确认把当前资料携带回稳定SDK；当前候选和旧资料均会保留。')
        def rollback():
            self.paths.verify();source_data,old_source=self._current()
            if self._selection_hash()!=selection_revision:raise ValueError('SDK选择已变化，请刷新后回退。')
            data_id=uuid.uuid4().hex;target=snapshot_path(self.data,data_id);expected=data_state(source_data)
            with self._data_locks(source_data):
                snapshot=snapshot_data(source_data,target,self.paths.source,old_source,self.paths.revision,1,self.cancelled,expected,allow_pending=True)
            selected=RuntimeSelection(self.paths.source,self.paths.python,target,self.paths.bridge_root,self.paths.revision,1,None,None,None,'stable',self.data,None)
            self._handshake(selected,deep=True)
            if data_state(source_data)!=expected:raise ValueError('当前资料在回退探测时变化，请重新审核；快照保留。')
            new=dict(format=FORMAT,kind='stable',candidate_id=None,data_id=data_id,revision=self.paths.revision,protocol=1,identity_digest=None)
            result=self._switch(selected,new,selection_revision);result['snapshot']=snapshot;return result
        return self._start('rollback',rollback)

    def activation_review(self):
        if not self.activation_journal.exists():return dict(pending=False)
        raw=read_json(self.activation_journal,256*1024)
        required={'format','id','before_text','before_hash','after','after_hash','after_data_hash','state'}
        if type(raw)is not dict or set(raw)!=required or raw['format']!=1 or raw['state']not in ('prepared','verifying_restart'):
            raise ValueError('SDK切换journal格式无效，已保留。')
        identifier(raw['id'])
        before_hash='-'if raw['before_text']is None else hashlib.sha256(raw['before_text'].encode()).hexdigest()
        if before_hash!=raw['before_hash']or hashlib.sha256(self._payload(raw['after'])).hexdigest()!=raw['after_hash']:raise ValueError('SDK切换journal摘要不符，已保留。')
        current=self._selection_hash()
        return dict(pending=True,journal_digest=digest(self.activation_journal),current_revision=current,
            before_revision=raw['before_hash'],after_revision=raw['after_hash'],after=raw['after'],conflict=current not in (raw['before_hash'],raw['after_hash']),
            after_data_changed=tree_hash(data_state(snapshot_path(self.data,raw['after']['data_id'])))!=raw['after_data_hash'])

    def recover_activation(self,journal_digest,choice='before',confirmed=False):
        if confirmed is not True or choice not in ('before','after'):raise ValueError('请确认恢复到切换前或继续切换后版本。')
        def recover():
            review=self.activation_review()
            if not review['pending']or review['journal_digest']!=journal_digest:raise ValueError('SDK切换现场已变化，请重新核对。')
            if review['conflict']:raise ValueError('SDK选择存在外部变更，已保留，未覆盖。')
            journal=read_json(self.activation_journal)
            if choice=='before':
                self._restore_selector(journal);result=dict(restored='before')
            else:
                after=journal['after']
                if review['after_data_changed']:raise ValueError('切换后的资料已变化，仅可恢复切换前选择；资料现场保留。')
                if after['kind']=='candidate':
                    self._candidate(after['candidate_id'])
                    selected=candidate_selection(self.data,after['candidate_id'],after['data_id'],1)
                    if selected.identity_digest!=after['identity_digest']:raise ValueError('切换目标身份已变化。')
                elif after['kind']=='stable':
                    selected=RuntimeSelection(self.paths.source,self.paths.python,snapshot_path(self.data,after['data_id']),self.paths.bridge_root,self.paths.revision,1,None,None,None,'stable',self.data,None)
                else:raise ValueError('切换目标无效。')
                if self._selection_hash()!=review['current_revision']:raise ValueError('恢复前SDK选择发生变化。')
                atomic_json(selection_path(self.data),after);hello=self._handshake(selected,deep=True);result=dict(restored='after',hello=hello)
            if self._selection_hash()not in (journal['before_hash'],journal['after_hash']):raise ValueError('恢复期间SDK选择被外部改动，journal保留。')
            journal.update(state='recovered',choice=choice);atomic_json(self.root/'activations'/(journal['id']+'.json'),journal)
            self.activation_journal.unlink();self.service._environment_changed=True;return result
        return self._start('recover_activation',recover)

    def status(self,job_id=None):
        with self.lock:
            if job_id is not None and (self.job is None or self.job['job_id']!=job_id):
                saved=read_json(self.root/'jobs'/(identifier(job_id)+'.json'),512*1024)
                return dict(saved,busy=False,persisted=True,result_needs_review=saved['state']=='working')
            return dict(self.job or dict(job_id='',state='idle',phase='',result=None,error=''),busy=self.busy)

    def cancel(self):self.cancelled.set();return self.status()
    def close(self):
        with self.lock:self.closed=True;self.cancelled.set();worker=self.worker
        if worker is not None:worker.join()
