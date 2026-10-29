"""复用实际下载服务，保持可查询、可取消的唯一模型安装任务。"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import threading
import uuid

from .model_install import ModelInstall, MAX_ARCHIVE_BYTES, extract_model
from .onedragon_environment import EnvironmentStore, validate_environment
from .onedragon_notify import redact
from .run_lock import profile_lock


class ModelDownloads:
    def __init__(self, service):
        self.service, self.root = service, Path(service.data).resolve()
        self.install = ModelInstall(self.root)
        self.lock = threading.RLock()
        self.thread, self.job = None, None
        self.signal, self.limit_error = {}, ''

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def _entries(self):
        from one_dragon.base.config.basic_model_config import get_ocr_opts
        from one_dragon.base.matcher.ocr.onnx_ocr_matcher import DEFAULT_OCR_MODEL_NAME
        store = EnvironmentStore(self.service)
        config, env = store.config('_models'), store.config('_environment')
        validate_environment(env, self.service.ctx.repo_config)
        items = []
        for resource in config.get_model_resources():
            for model in dict.fromkeys((resource.default_model, getattr(config,resource.config_key+'_backup'))):
                self.install.target(resource.config_key,model)
                param = config._build_model_download_param(resource,model)
                items.append((resource.config_key,resource.display_name,model,config.get_model_current(resource),model==resource.default_model,param,False))
        self.install.target('onnx_ocr',DEFAULT_OCR_MODEL_NAME)
        for option in get_ocr_opts():
            items.append(('onnx_ocr','文字识别',option.label,config.ocr,True,option.value,True))
        entries = []
        for family,label,model,current,recommended,param,ocr in items:
            target = self.install.target(family,model)
            if Path(param.save_file_path).resolve() != target:
                raise ValueError('上游模型目录与本区服不一致。')
            required = [Path(name).name for name in param.check_existed_list]
            if any(self.install.safe(Path(name)).parent != target for name in param.check_existed_list):
                raise ValueError('上游必要模型文件路径无效。')
            if ocr: required = [name for name in required if not name.endswith('_dict.txt')]
            files = [target/name for name in required]
            dictionaries = list(target.glob('*_dict.txt')) if ocr else []
            for path in files+dictionaries: self.install.safe(path)
            complete = all(path.is_file() and path.stat().st_size > 0 for path in files) and (not ocr or len(dictionaries)==1 and dictionaries[0].is_file() and dictionaries[0].stat().st_size>0)
            entries.append(dict(id=family+':'+model,family=family,label=label,model=model,current=current,recommended=recommended,
                                ready=complete,required=required,param=param,ocr=ocr,target=target,
                                bytes=sum(path.stat().st_size for path in files+dictionaries if path.is_file())))
        return entries,env

    def _revision(self, entries):
        digest = hashlib.sha256()
        for name in ('env.yml','model.yml','model_files.yml'):
            path = self.install.safe(self.root/'config'/name)
            digest.update(name.encode());digest.update(self.install.read(path) or b'-')
        for row in entries:
            target = row['target']
            digest.update(row['id'].encode())
            if target.exists():
                for path in sorted(target.rglob('*')):
                    checked = self.install.safe(path)
                    if checked.is_file():
                        state = checked.stat()
                        digest.update(str(path.relative_to(target)).encode())
                        digest.update(f'{state.st_size}:{state.st_mtime_ns}'.encode())
        return digest.hexdigest()

    def catalog(self):
        self.service._assert_idle()
        if not self.install.pending.exists():self.service.ctx
        with profile_lock(self.root):
            if self.install.pending.exists():
                # 上游参数构造会创建空模型目录；恢复前不能重建已移走的原目录。
                return dict(items=[],revision='',pending=True,sources=[],source='')
            entries,env = self._entries()
            return dict(items=[{key:value for key,value in row.items() if key not in ('param','target','ocr')} for row in entries],
                        revision=self._revision(entries),pending=self.install.pending.exists(),
                        sources=env.get_resource_source_order(),source=env.resource_source)

    def start(self, resource_id, revision):
        self.service._assert_idle()
        self.install.assert_clear()
        self.service.ctx
        lease = profile_lock(self.root)
        lease.acquire()
        try:
            self.install.assert_clear()
            entries,env = self._entries()
            if not isinstance(resource_id,str) or revision != self._revision(entries):
                raise ValueError('模型或下载配置已变化，请重新读取。')
            entry = next((row for row in entries if row['id']==resource_id),None)
            if entry is None: raise ValueError('请选择固定版本声明中的模型。')
            old = self.install.digest(entry['target'])
            self.service.ctx.push_service.protect_transient(env.personal_proxy,env.gh_proxy_url)
            with self.lock:
                self.signal, self.limit_error = {}, ''
                self.job = dict(job_id=uuid.uuid4().hex,resource_id=resource_id,state='connecting',detail='正在连接下载源…',
                                source='',downloaded_bytes=0,total_bytes=0,bytes_per_second=0.,progress=0.,installed=False,
                                started_at=datetime.now().astimezone().isoformat())
                self.thread = threading.Thread(target=self._run,args=(lease,entry,env,revision,old),name='AutoZZZ-Model-Download',daemon=False)
                try: self.thread.start()
                except BaseException:
                    self.thread = None
                    self.job.update(state='failed',detail='无法启动下载线程。')
                    raise
                return self.status(self.job['job_id'])
        except BaseException:
            lease.close()
            raise

    def _progress(self, progress):
        with self.lock:
            if max(progress.downloaded_bytes,progress.total_bytes) > MAX_ARCHIVE_BYTES:
                self.limit_error = '模型压缩包超过 512 MB。'
                self.signal['signal'] = 'cancel'
                raise ValueError(self.limit_error)
            if self.signal.get('signal') == 'cancel': return
            self.job.update(state=progress.phase,source=progress.source_id,
                            downloaded_bytes=progress.downloaded_bytes,total_bytes=progress.total_bytes,
                            bytes_per_second=progress.bytes_per_second,progress=max(0.,min(1.,progress.progress)),
                            detail=redact(progress.message,self.service.ctx.push_service.secrets))

    def _run(self, lease, entry, env, revision, old):
        try:
            from one_dragon.base.web.common_downloader import CommonDownloader,CommonDownloaderParam
            job = self.install.job_dir(self.job['job_id'])
            job.mkdir(parents=True,exist_ok=False)
            param = CommonDownloaderParam(str(job),'model.zip',entry['param'].download_urls)
            downloaded = CommonDownloader(param).download(source_order=env.get_resource_source_order(),
                proxy_url=env.personal_proxy if env.is_personal_proxy else None,
                ghproxy_url=env.gh_proxy_url if env.is_gh_proxy else None,skip_if_existed=False,
                progress_signal=self.signal,status_callback=self._progress,fallback_on_slow=env.is_resource_source_auto)
            if self.limit_error: raise ValueError(self.limit_error)
            if self.signal.get('signal') == 'cancel': raise InterruptedError('已取消模型下载。')
            if not downloaded: raise RuntimeError('下载失败，请检查下载源或代理后重试。')
            with self.lock: self.job.update(state='extracting',detail='正在校验并解压模型…')
            extract_model(job/'model.zip',job/'content',entry['required'],entry['ocr'],lambda:self.signal.get('signal')=='cancel')
            entries,_ = self._entries()
            if self._revision(entries) != revision or self.install.digest(entry['target']) != old:
                raise ValueError('下载期间模型或配置被修改，原文件保持不变。')
            with self.lock:
                if self.signal.get('signal') == 'cancel': raise InterruptedError('已取消模型下载。')
                self.job.update(state='installing',detail='正在替换模型，等待安装完成…')
            self.install.publish(entry['family'],entry['model'],self.job['job_id'])
            self.service._environment_changed = True
            with self.lock: self.job.update(state='success',detail='模型文件已就绪，请重新准备任务。',installed=True,progress=1.)
        except Exception as exc:
            with self.lock:
                cancelled = isinstance(exc,InterruptedError)
                self.job.update(state='cancelled' if cancelled else 'failed',detail=redact(str(exc),self.service.ctx.push_service.secrets))
        finally:
            try: lease.close()
            except Exception:
                with self.lock: self.job.update(state='failed',detail='配置锁清理失败，请等待工作进程退出。')
            with self.lock: self.job['finished_at'] = datetime.now().astimezone().isoformat()

    def status(self, job_id):
        with self.lock:
            if self.job is None or job_id != self.job['job_id']:
                raise ValueError('模型任务编号不匹配，请查询原任务。')
            return dict(self.job,busy=self.busy)

    def cancel(self, job_id):
        with self.lock:
            self.status(job_id)
            if self.busy and self.job['state'] not in ('success','failed','cancelled','installing'):
                self.signal['signal'] = 'cancel'
                self.job.update(state='cancelling',detail='正在取消，等待原下载退出…')
            return self.status(job_id)

    def recover(self):
        self.service._assert_idle()
        with profile_lock(self.root):
            restored = self.install.recover()
            if restored: self.service._environment_changed = True
            return dict(recovered=restored)

    def close(self):
        if self.job is not None: self.cancel(self.job['job_id'])
        if self.thread is not None: self.thread.join()
