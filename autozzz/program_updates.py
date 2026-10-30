"""程序版本、完整包下载和实际 EXE 预检；替换由独立交接助手完成。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from .onedragon_bundle import digest
from .onedragon_environment import EnvironmentStore,validate_environment
from .onedragon_notify import redact
from .program_package import read_manifest,extract_package,verify_package
from .program_update import ProgramUpdate,verify_preflight
from .run_lock import profile_lock

REPOSITORY='HenryTANG0520/AutoZZZ'
ASSET='AutoZZZ-windows-x64.zip'


def release_rows(items,channel='stable'):
    if not isinstance(items,list):raise ValueError('版本服务返回无效清单。')
    if channel not in ('stable','beta'):raise ValueError('更新通道无效。')
    result=[]
    for release in items:
        if not isinstance(release,dict) or release.get('draft')or release.get('prerelease') and channel=='stable':continue
        tag=release.get('tag_name','')
        if type(tag)is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}',tag)is None:continue
        assets=release.get('assets',[])
        if not isinstance(assets,list):continue
        for asset in assets:
            if not isinstance(asset,dict):continue
            url=asset.get('browser_download_url','');checksum=asset.get('digest','')
            if (asset.get('name')!=ASSET or type(asset.get('id'))is not int or asset['id']<1 or type(asset.get('size'))is not int
                or not 0<asset['size']<=4*1024**3 or url!=f'https://github.com/{REPOSITORY}/releases/download/{tag}/{ASSET}'
                or type(checksum)is not str or re.fullmatch('sha256:[0-9a-f]{64}',checksum)is None):continue
            result.append(dict(id=str(asset['id']),version=tag,url=url,sha256=checksum[7:],bytes=asset['size'],
                published=str(release.get('published_at','')),page=f'https://github.com/{REPOSITORY}/releases/tag/{tag}'))
    return result


class ProgramUpdates:
    def __init__(self,service):
        self.service=service;self.root=service.source.parent.parent;self.worker=None;self.job=None
        self.cancelled=threading.Event();self.signal={};self.lock=threading.RLock();self.releases=[];self.revision=''
        self.channel=None;self.page=1;self.has_more=False;self.unavailable_reason=''

    @property
    def busy(self):return self.worker is not None and self.worker.is_alive()

    def _installed(self):
        if self.service.source.parent.name!='onedragon' or not (self.root/'autozzz-package.json').is_file():return None
        return read_manifest(self.root)

    def catalog(self):
        installed=self._installed();history=[]
        if installed:
            for path in self.root.parent.glob(self.root.name+'.previous-*'):
                try:metadata=read_manifest(path);history.append(dict(path=str(path),version=metadata['version']))
                except (ValueError,OSError):continue
        return dict(installed=bool(installed),current=installed['version'] if installed else '开发环境',
            releases=self.releases,revision=self.revision,history=history,repository='https://github.com/'+REPOSITORY+'/releases',
            channel=self.channel or ('beta' if installed and '-beta'in installed['version'] else 'stable'),
            page=self.page,has_more=self.has_more,unavailable_reason=self.unavailable_reason)

    def _start(self,kind,operation):
        self.service._assert_idle();self.service.ctx
        with self.lock:
            if self.busy:raise RuntimeError('版本操作仍在进行，请查询原任务。')
            self.cancelled.clear();self.signal={};self.job=dict(job_id=uuid.uuid4().hex,kind=kind,state='working',detail='正在处理…',progress=0.,result=None)
            def run():
                try:
                    with profile_lock(self.service.data):result=operation()
                    with self.lock:self.job.update(state='success',result=result,detail='更新包已验证，关闭程序后即可安装。' if kind in ('download','import','restore') else '版本检查完成。')
                except Exception as exc:
                    secrets=getattr(getattr(getattr(self.service,'_ctx',None),'push_service',None),'secrets',())
                    with self.lock:self.job.update(state='cancelled' if isinstance(exc,InterruptedError) else 'failed',detail=redact(str(exc),secrets))
            self.worker=threading.Thread(target=run,name='AutoZZZ-Program-Update',daemon=False);self.worker.start()
            return self.status(self.job['job_id'])

    def status(self,job_id):
        with self.lock:
            if self.job is None or self.job['job_id']!=job_id:raise ValueError('版本任务编号已变化，请查询原任务。')
            return dict(self.job,busy=self.busy)

    def cancel(self,job_id):
        with self.lock:
            self.status(job_id);self.cancelled.set();self.signal['signal']='cancel'
            return self.status(job_id)

    def _network(self):
        env=EnvironmentStore(self.service).config('_environment');validate_environment(env,self.service.ctx.repo_config)
        self.service.ctx.push_service.protect_transient(env.personal_proxy,env.gh_proxy_url)
        return env

    def check(self,channel=None,page=1):
        channel=channel or self.catalog()['channel']
        if channel not in ('stable','beta') or type(page)is not int or page<1:raise ValueError('更新通道或页码无效。')
        def operation():
            import requests
            env=self._network();proxy=env.personal_proxy if env.is_personal_proxy else None
            address=f'https://api.github.com/repos/{REPOSITORY}/releases'
            if env.is_gh_proxy:address=env.gh_proxy_url.rstrip('/')+'/'+address
            with requests.get(address,params={'per_page':30,'page':page},headers={'Accept':'application/vnd.github+json'},
                proxies={'http':proxy,'https':proxy} if proxy else None,timeout=(10,20),stream=True) as response:
                response.raise_for_status();raw=bytearray()
                for chunk in response.iter_content(65536):
                    if self.cancelled.is_set():raise InterruptedError('已取消版本检查。')
                    raw.extend(chunk)
                    if len(raw)>4*1024**2:raise ValueError('版本清单超过 4 MB。')
            items=json.loads(raw);rows=release_rows(items,channel)
            revision=hashlib.sha256(json.dumps(dict(rows=rows,channel=channel,page=page),sort_keys=True).encode()).hexdigest()
            with self.lock:
                self.releases=rows;self.revision=revision;self.channel=channel;self.page=page;self.has_more=len(items)==30
                self.unavailable_reason='' if rows else '本页未找到带有效摘要的 Windows 完整包。'
            return self.catalog()
        return self._start('check',operation)

    def _prepare(self,source,expected_version=None):
        if self._installed()is None:raise ValueError('请从完整便携包运行后更新；源码目录保持不变。')
        if self.cancelled.is_set():raise InterruptedError('已取消更新准备。')
        metadata=read_manifest(source)
        if expected_version is not None and metadata['version']!=expected_version:raise ValueError('程序包版本与所选发布不一致。')
        with self.lock:self.job.update(detail='正在准备同盘安装目录…')
        tx=ProgramUpdate.prepare(self.root,source)
        if self.cancelled.is_set():raise InterruptedError('已取消更新准备，当前程序尚未替换。')
        report=tx.job/'runtime-report.json'
        with (tx.job/'runtime-check.log').open('wb') as output:
            process=subprocess.Popen([str(tx.candidate/'AutoZZZ.exe'),'--runtime-check',str(report)],cwd=tx.candidate,
                stdout=output,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            while True:
                try:code=process.poll()
                except OSError:
                    with self.lock:self.job.update(detail='正在等待原验证进程的状态恢复…')
                    time.sleep(.1);continue
                if code is not None:break
                with self.lock:self.job.update(detail='正在验证完整运行时…' if not self.cancelled.is_set() else '正在等待原验证进程退出…')
                time.sleep(.1)
        if self.cancelled.is_set():raise InterruptedError('已取消更新准备；原验证进程已退出。')
        if process.returncode or not report.is_file():raise ValueError('新程序运行时验证失败，请查看临时目录日志。')
        tx.state('prepared',preflight=dict(manifest=tx.data['new_manifest'],report=digest(report)))
        verify_preflight(tx);verify_package(tx.candidate,strict=True)
        return dict(transaction=str(tx.job),target=str(self.root),version=metadata['version'],journal_hash=digest(tx.journal))

    def import_package(self,path):
        def operation():
            folder=self.service.data/'autozzz/program-updates'/self.job['job_id'];folder.mkdir(parents=True)
            extract_package(Path(path),folder/'extracted',self.cancelled.is_set)
            return self._prepare(folder/'extracted/AutoZZZ')
        return self._start('import',operation)

    def download(self,release_id,revision):
        if revision!=self.revision:raise ValueError('版本清单已变化，请重新检查。')
        release=next((row for row in self.releases if row['id']==release_id),None)
        if release is None:raise ValueError('所选版本不存在，请重新检查。')
        def operation():
            from one_dragon.base.web.common_downloader import CommonDownloader,CommonDownloaderParam
            env=self._network();folder=self.service.data/'autozzz/program-updates'/self.job['job_id'];folder.mkdir(parents=True)
            param=CommonDownloaderParam(str(folder),'program.zip',dict(github=release['url']))
            def progress(value):
                if max(value.downloaded_bytes,value.total_bytes)>4*1024**3:self.signal['signal']='cancel';raise ValueError('下载包超过 4 GB。')
                with self.lock:self.job.update(detail=redact(value.message,self.service.ctx.push_service.secrets),progress=max(0.,min(1.,value.progress)))
            downloaded=CommonDownloader(param).download(source_order=env.get_resource_source_order(),proxy_url=env.personal_proxy if env.is_personal_proxy else None,
                ghproxy_url=env.gh_proxy_url if env.is_gh_proxy else None,skip_if_existed=False,progress_signal=self.signal,status_callback=progress,fallback_on_slow=env.is_resource_source_auto)
            if self.cancelled.is_set():raise InterruptedError('已取消程序下载。')
            archive=folder/'program.zip'
            if not downloaded or not archive.is_file():raise ValueError('完整程序包下载失败。')
            if archive.stat().st_size!=release['bytes'] or digest(archive)!=release['sha256']:raise ValueError('发布包摘要或大小不符，未安装。')
            extract_package(archive,folder/'extracted',self.cancelled.is_set)
            return self._prepare(folder/'extracted/AutoZZZ',release['version'])
        return self._start('download',operation)

    def restore(self,path):
        backup=Path(path).resolve()
        if backup.parent!=self.root.parent or not backup.name.startswith(self.root.name+'.previous-'):raise ValueError('不是当前程序的归档版本。')
        return self._start('restore',lambda:self._prepare(backup))

    def close(self):
        if self.job is not None:self.cancel(self.job['job_id'])
        if self.worker is not None:self.worker.join()
