"""在安装目录外运行更新助手，核实原进程退出后才替换目录。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .onedragon_bundle import digest,filesystem_path
from .program_package import read_manifest
from .program_update import ProgramUpdate,write_json,verify_preflight
from .task_lease import RunLeaseOwner,JoinedRunLease,LEASE_ENV
from .run_lock import RunLock

MODULES=('__init__.py','program_package.py','program_update.py','program_update_handoff.py','onedragon_bundle.py','run_lock.py','task_lease.py')


def copy_python(source,destination,expected=None):
    source,destination=Path(source).absolute(),Path(destination).absolute()
    if source!=source.resolve() or destination!=destination.resolve():raise ValueError('更新助手路径被重定向。')
    if destination.exists():raise ValueError('更新助手解释器目录已经存在。')
    destination.mkdir(parents=True)
    def copy(origin,relative):
        key=relative.as_posix()
        if expected is not None and key not in expected:return
        target=filesystem_path(destination/relative);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(origin,target)
        if expected is not None and digest(target)!=expected[key]:raise ValueError('更新助手解释器摘要不符：'+key)
    for path in source.iterdir():
        if path.is_file() and (path.suffix.lower()in ('.exe','.dll') or path.name=='LICENSE.txt'):
            copy(path,Path(path.name))
    for name in ('Lib','DLLs'):
        for folder,dirs,names in os.walk(filesystem_path(source/name),followlinks=False):
            for item in dirs:
                path=Path(folder)/item
                if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:raise ValueError('助手解释器包含目录链接。')
            dirs[:]=[item for item in dirs if item not in ('site-packages','__pycache__','test','tests','tkinter','idlelib','ensurepip')]
            for filename in names:
                if filename.endswith('.pyc'):continue
                origin=Path(folder)/filename
                if origin.is_symlink() or getattr(origin.lstat(),'st_file_attributes',0)&0x400:raise ValueError('助手解释器包含链接。')
                copy(origin,origin.relative_to(filesystem_path(source)))


class UpdateHandoff:
    def __init__(self,transaction,restart=True):
        self.transaction=transaction;self.restart=restart;self.process=None;self.lease=RunLeaseOwner();self.ready=False
        self.receipt=transaction.job/'helper-ready.json';self.cancel_file=transaction.job/'helper-cancel'

    def start(self):
        tx=self.transaction
        if tx.data['state']!='prepared':raise ValueError('更新包尚未准备好。')
        verify_preflight(tx)
        marker=tx.job/'helper-start.json'
        if marker.exists() or self.cancel_file.exists():raise ValueError('本事务已有交接或取消记录，请查询原任务，勿重复启动。')
        self.lease.acquire()
        try:
            with marker.open('x',encoding='utf-8') as stream:json.dump(dict(state='preparing'),stream)
            package=read_manifest(tx.target);helper=tx.job/'helper';bridge=helper/'bridge/autozzz';bridge.mkdir(parents=True)
            for name in MODULES:
                relative='onedragon/bridge/autozzz/'+name;origin=tx.target/relative
                if relative not in package['files'] or digest(origin)!=package['files'][relative]:raise ValueError('更新助手源码已变化，请修复完整程序包。')
                shutil.copyfile(origin,bridge/name)
            expected={name.removeprefix('onedragon/python/'):value for name,value in package['files'].items() if name.startswith('onedragon/python/')}
            copy_python(tx.target/'onedragon/python',helper/'python',expected)
            bootstrap=helper/'start.py'
            bootstrap.write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parent/'bridge'))\nfrom autozzz.program_update_handoff import worker\nraise SystemExit(worker())\n",encoding='utf-8')
            (tx.job/'恢复原版本.cmd').write_text('@echo off\r\npushd "%~dp0"\r\n"helper\\python\\python.exe" -I -S -X utf8 "helper\\start.py" --job . --recover\r\npopd\r\npause\r\n',encoding='utf-8')
            environment=os.environ.copy()
            for name in ('PYTHONHOME','PYTHONPATH','PYTHONUSERBASE','VIRTUAL_ENV','CONDA_PREFIX','PYTHONSTARTUP',LEASE_ENV):environment.pop(name,None)
            environment[LEASE_ENV]=self.lease.export()
            command=[str(helper/'python/python.exe'),'-I','-S','-X','utf8',str(bootstrap),'--job',str(tx.job),
                     '--target',str(tx.target),'--journal-hash',digest(tx.journal)]
            if not self.restart:command.append('--no-restart')
            with (tx.job/'helper.log').open('wb') as output:
                self.process=subprocess.Popen(command,cwd=helper,env=environment,stdin=subprocess.DEVNULL,
                    stdout=output,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            write_json(marker,dict(state='started',pid=self.process.pid))
            return self
        except BaseException:
            if self.process is None:self.lease.close()
            raise

    def poll(self):
        if self.process is None:raise RuntimeError('更新助手尚未启动。')
        try:code=self.process.poll()
        except OSError:return False
        if code is not None:
            self.lease.close()
            raise RuntimeError('更新助手提前退出，请查看 helper.log。')
        if not self.receipt.exists():return False
        receipt=json.loads(self.receipt.read_text(encoding='utf-8'))
        if receipt!={'pid':self.process.pid,'target':str(self.transaction.target),'journal_hash':digest(self.transaction.journal)}:
            raise RuntimeError('更新助手交接信息不符，原进程保持运行。')
        self.ready=True;return True

    def cancel(self):
        self.cancel_file.touch()
        try:finished=self.process is None or self.process.poll()is not None
        except OSError:return False
        if finished:self.lease.close();return True
        return False


def worker():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--job',type=Path,required=True);parser.add_argument('--target',type=Path)
    parser.add_argument('--journal-hash');parser.add_argument('--no-restart',action='store_true');parser.add_argument('--recover',action='store_true');args=parser.parse_args()
    lease=None;tx=None
    try:
        if args.recover:
            lease=RunLock();lease.acquire();tx=ProgramUpdate(args.job)
            if tx.probe(tx.target) or tx.probe(tx.backup):raise RuntimeError('原程序或归档仍有进程运行，请先正常关闭。')
            changed=tx.recover()
            write_json(tx.job/'recovery-result.json',dict(state='rolled_back' if changed else 'already_completed',target=str(tx.target)))
            print('恢复完成。' if changed else '更新已经完成，无需恢复。')
            return 0
        lease=JoinedRunLease.from_environment()
        if lease is None:raise RuntimeError('更新助手缺少已核实的原进程。')
        tx=ProgramUpdate(args.job)
        if args.target is None or tx.target!=args.target.resolve() or tx.data['state']!='prepared' or digest(tx.journal)!=args.journal_hash:raise ValueError('更新事务在交接前已变化。')
        verify_preflight(tx)
        write_json(tx.job/'helper-ready.json',dict(pid=os.getpid(),target=str(tx.target),journal_hash=args.journal_hash))
        while True:
            if (tx.job/'helper-cancel').exists():return 0
            state=lease.kernel.WaitForSingleObject(lease.parent,100)
            if state==0:break
            if state!=258:raise RuntimeError('无法核实原程序是否退出，更新尚未替换。')
        if digest(tx.journal)!=args.journal_hash:raise ValueError('等待退出期间更新事务已变化。')
        tx.apply()
        lease.close();lease=None
        if not args.no_restart:
            try:subprocess.Popen([str(tx.target/'AutoZZZ.exe')],cwd=tx.target,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except OSError as exc:tx.state('completed',restart_error=str(exc))
        write_json(tx.job/'helper-result.json',dict(state='completed',target=str(tx.target)))
        return 0
    except Exception as exc:
        import traceback
        traceback.print_exc()
        if tx is not None:write_json(tx.job/'helper-result.json',dict(state='failed',error=str(exc)))
        return 1
    finally:
        if lease is not None:lease.close()
