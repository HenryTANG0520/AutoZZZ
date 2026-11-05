"""Reviewable upstream source checkout; never replaces the pinned execution SDK."""
from __future__ import annotations

import os
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from .run_lock import profile_lock

REMOTE='https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon.git'


def branch_name(value):
    if (type(value)is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,99}',value)
            or '..' in value or '//' in value or any(part.endswith(('.lock','.')) for part in value.split('/'))
            or value.endswith('/')):raise ValueError('分支名无效；请输入实际分支名。')
    return value


class SourceWorkspace:
    remote=REMOTE
    protocol='https'

    def __init__(self,service):
        self.service=service;self.data=Path(getattr(service,'stable_data',service.data)).resolve()
        self.root=self.data/'autozzz/upstream-source'
        self.worker=None;self.job=None;self.cancelled=threading.Event();self.lock=threading.RLock()
        self.git=shutil.which('git');self.process=None
        self.initializing=self.root.parent/'upstream-source-init.json'

    @property
    def busy(self):return self.worker is not None and self.worker.is_alive()

    def safe(self):
        path=self.root
        while path.parent!=path:
            if path.exists() and (path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400):
                raise ValueError('源码工作副本路径不能是链接。')
            path=path.parent
        if self.root.exists():
            for folder,dirs,files in os.walk(self.root):
                for name in dirs+files:
                    path=Path(folder)/name
                    if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:
                        raise ValueError('源码工作副本包含链接，已停止操作。')

    def _git(self,*args,timeout=120,binary=False):
        if not self.git:raise ValueError('未找到 Git，请先安装 Git for Windows。')
        if self.cancelled.is_set():raise InterruptedError('源码操作已取消')
        env={key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0',GIT_PAGER='cat')
        self.root.mkdir(parents=True,exist_ok=True)
        command=[self.git,'--no-pager','-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false',
            '-c','core.symlinks=false','-c','protocol.allow=never','-c','protocol.'+self.protocol+'.allow=always',
            '-C',str(self.root),*args]
        with tempfile.TemporaryFile() as output:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.process=process;deadline=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    if self.cancelled.wait(.05) or time.monotonic()>deadline:
                        if os.name=='nt':
                            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
                        else:process.terminate()
                        process.wait(timeout=10)
                        if self.cancelled.is_set():raise InterruptedError('源码操作已取消；保留工作副本，请检查状态')
                        raise TimeoutError('Git 操作超时；保留工作副本，请检查状态')
                output.seek(0);raw=output.read(512*1024+1)
                if len(raw)>512*1024:raise ValueError('Git 输出过大，请在工作副本中检查详情。')
                text=raw.decode('utf-8',errors='replace').strip()
                if process.returncode:raise ValueError(text or 'Git 操作失败')
                return raw if binary else text
            finally:self.process=None

    def verify(self,require_origin=True):
        self.safe()
        if not (self.root/'.git').is_dir():raise ValueError('尚未创建源码工作副本。')
        if (self.root/'.git/objects/info/alternates').exists():raise ValueError('不允许复用外部 Git 对象目录。')
        config=self._git('config','--local','--list')
        for line in config.splitlines():
            key=line.partition('=')[0].lower()
            if (key not in {'core.repositoryformatversion','core.filemode','core.bare','core.logallrefupdates',
                    'core.symlinks','core.ignorecase','core.protectntfs','core.longpaths','remote.origin.url','remote.origin.fetch','user.name','user.email'}
                    and not re.fullmatch(r'branch\.[a-zA-Z0-9._/-]+\.(remote|merge)',key)):
                raise ValueError('工作副本存在额外 Git 设置，未执行同步：'+key)
        if require_origin and self._git('remote','get-url','origin')!=self.remote:raise ValueError('工作副本必须使用官方仓库。')
        if self._git('rev-parse','--show-toplevel').replace('\\','/').casefold()!=str(self.root).replace('\\','/').casefold():
            raise ValueError('Git 工作副本路径不一致。')

    def _initialization_record(self):
        path=self.initializing
        if (not path.is_file() or path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400
                or path.stat().st_size>4096):raise ValueError('源码初始化记录无效，保留原目录。')
        value=json.loads(path.read_text(encoding='utf-8'));identity=self.root.stat()
        if (not isinstance(value,dict) or set(value)!={'format','repository','path','identity','nonce'}
                or value['format']!=1 or value['repository']!=self.remote or value['path']!=str(self.root)
                or value['identity']!=[identity.st_dev,identity.st_ino]
                or type(value['nonce'])is not str or re.fullmatch('[0-9a-f]{32}',value['nonce'])is None):
            raise ValueError('源码初始化记录与目录身份不符，未接管现有仓库。')
        return value

    def _begin_initialization(self):
        self.root.mkdir(parents=True,exist_ok=True)
        if self.initializing.exists():self._initialization_record();return
        identity=self.root.stat()
        value=dict(format=1,repository=self.remote,path=str(self.root),
            identity=[identity.st_dev,identity.st_ino],nonce=uuid.uuid4().hex)
        # Written before git init, outside the checkout. Exclusive creation never replaces a prior transaction.
        with self.initializing.open('x',encoding='utf-8') as stream:
            json.dump(value,stream);stream.flush();os.fsync(stream.fileno())

    def _finish_initialization(self):
        self._initialization_record()
        self.verify(require_origin=False)
        if any(self.root.joinpath('.git').glob('*.lock')):
            raise ValueError('源码初始化存在 Git 锁，已保留；请检查原 Git 操作。')
        remotes=self._git('remote')
        if remotes:
            if remotes!='origin' or self._git('remote','get-url','origin')!=self.remote:
                raise ValueError('初始化仓库已有其他远端，未覆盖。')
        else:
            # Only the owned, physically empty and unborn repository may have origin completed.
            if (any(path.name!='.git' for path in self.root.iterdir())
                    or self._git('rev-list','--all','--max-count=1')
                    or self._git('status','--porcelain=v1','--untracked-files=all')):
                raise ValueError('初始化工作副本已有文件或提交，未接管或补写远端。')
            self._git('remote','add','origin',self.remote)
        self.verify()
        self._initialization_record()
        self.initializing.unlink()

    def _catalog(self):
        self.safe()
        result=dict(path=str(self.root),repository=REMOTE,exists=(self.root/'.git').is_dir(),
            pinned_runtime_unchanged=True,branch='',head='',dirty=False,changes='',diff='',branches=[],initialization_pending=False)
        if self.initializing.exists():
            self._initialization_record()
            result.update(initialization_pending=True,
                changes='初始化尚未完成；点击“创建副本”安全续接。已有文件、提交或 Git 锁会保留并阻止补写。')
            return result
        if not result['exists']:return result
        self.verify()
        refs=self._git('for-each-ref','--format=%(refname:short)','refs/heads','refs/remotes/origin')
        result['branches']=sorted(set(row.removeprefix('origin/') for row in refs.splitlines()))
        result['branch']=self._git('symbolic-ref','--quiet','--short','HEAD')
        result['changes']=self._git('status','--porcelain=v1','--untracked-files=normal')
        result['dirty']=bool(result['changes'])
        if self._git('rev-list','--all','--max-count=1'):
            result['head']=self._git('rev-parse','HEAD')
            result['diff']=self._git('diff','--no-ext-diff','--stat','HEAD','--')
        return result

    def catalog(self):
        self.service._assert_idle()
        if self.busy:raise RuntimeError('源码操作仍在进行。')
        self.cancelled.clear()
        with profile_lock(self.data):return self._catalog()

    def _clean(self):
        if self._git('status','--porcelain=v1','--untracked-files=normal'):
            raise ValueError('工作副本有未提交或未跟踪的修改；已保留，未切换或同步。')

    def _operation(self,action,branch):
        self.safe()
        if action=='create':
            if (self.root/'.git').exists():
                if self.initializing.exists():self._finish_initialization()
                else:self.verify()
                return self._catalog()
            if self.root.exists() and any(self.root.iterdir()):raise ValueError('源码目录非空，未覆盖。')
            self._begin_initialization()
            self._git('init','--initial-branch=main')
            self._finish_initialization()
        else:
            self.verify()
            if action=='fetch':
                refs=self._git('ls-remote','--heads','origin')
                branches=[]
                for line in refs.splitlines():
                    ref=line.split('\t')[-1]
                    if ref.startswith('refs/heads/'):
                        try:branches.append(branch_name(ref.removeprefix('refs/heads/')))
                        except ValueError:pass
                result=self._catalog();result['branches']=sorted(set(result['branches']+branches));return result
            self._clean()
            if action=='sync':branch=branch_name(self._git('symbolic-ref','--short','HEAD'))
            branch=branch_name(branch)
            self._git('fetch','--no-tags','--depth=64','origin',
                'refs/heads/'+branch+':refs/remotes/origin/'+branch,timeout=300)
            self._clean();self.safe()
            local=self._git('for-each-ref','--format=%(refname:short)','refs/heads')
            # Git otherwise silently replaces ignored local files when a target starts tracking them.
            if branch in local.splitlines():self._git('switch','--no-guess','--no-overwrite-ignore',branch)
            else:self._git('switch','--no-guess','--no-overwrite-ignore','--create',branch,'refs/remotes/origin/'+branch)
            self._git('merge','--ff-only','--no-edit','--no-overwrite-ignore','refs/remotes/origin/'+branch)
        return self._catalog()

    def start(self,action,branch=None):
        if action not in ('create','fetch','select','sync'):raise ValueError('未知源码操作。')
        if action=='select':branch_name(branch)
        elif branch is not None:raise ValueError('此操作不接受分支参数。')
        self.service._assert_idle()
        with self.lock:
            if self.busy:raise RuntimeError('源码操作仍在进行，请查询原任务。')
            self.cancelled.clear();self.job=dict(job_id=uuid.uuid4().hex,state='working',action=action,result=None,error='')
            def run():
                try:
                    with profile_lock(self.data):result=self._operation(action,branch)
                    with self.lock:self.job.update(state='success',result=result)
                except Exception as exc:
                    with self.lock:self.job.update(state='cancelled' if isinstance(exc,InterruptedError) else 'failed',error=str(exc)[:8192])
            self.worker=threading.Thread(target=run,name='AutoZZZ-Source-Workspace',daemon=False);self.worker.start()
            return self.status()

    def status(self):
        with self.lock:return dict(self.job or dict(job_id='',state='idle',result=None,error=''),busy=self.busy)

    def cancel(self):self.cancelled.set();return self.status()

    def close(self):
        self.cancelled.set()
        if self.worker is not None:self.worker.join()
