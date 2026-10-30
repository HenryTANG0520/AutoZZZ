"""整目录更新事务；旧程序与用户文件始终有完整归档，不递归删除。"""
from __future__ import annotations

from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from .onedragon_bundle import digest,filesystem_path
from .program_package import MANIFEST,files,read_manifest,relative,verify_package


def write_json(path,value):
    path=Path(path);temporary=path.with_name(path.name+'.tmp')
    filesystem_path(temporary).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(filesystem_path(temporary),filesystem_path(path))


def verify_preflight(transaction):
    proof=transaction.data.get('preflight',{});path=transaction.job/'runtime-report.json'
    if (not isinstance(proof,dict) or proof.get('manifest')!=transaction.data['new_manifest'] or not path.is_file()
        or path.is_symlink() or path.stat().st_size>1024**2 or digest(path)!=proof.get('report')):
        raise ValueError('更新包尚未通过独立运行时验证。')
    report=json.loads(path.read_text(encoding='utf-8'))
    metadata=read_manifest(transaction.candidate)
    if (report.get('state')!='success' or report.get('bundled')is not True or report.get('worker_exited')is not True
        or report.get('game_started')is not False or report.get('game_input_sent')is not False
        or report.get('revision')!=metadata['runtime_revision'] or type(report.get('applications'))is not int or report['applications']<30
        or any(type(report.get('strategies',{}).get(kind))is not int or report['strategies'][kind]<1 for kind in ('auto_battle','dodge'))):
        raise ValueError('更新包的独立运行时验证未通过。')
    return report


def package_processes(root):
    """只查询可执行程序路径；不读取账号、命令行或结束进程。"""
    root=str(Path(root).resolve()).casefold()+'\\'
    shell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-Command',
        '[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; Get-CimInstance Win32_Process | Select-Object ProcessId,Name,ExecutablePath | ConvertTo-Json -Compress'],
        capture_output=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
    rows=json.loads(result.stdout or '[]');rows=rows if isinstance(rows,list) else [rows]
    return [row['ProcessId'] for row in rows if (row.get('ExecutablePath') or '').casefold().startswith(root)
            or row.get('Name','').casefold()=='autozzz.exe' and not row.get('ExecutablePath')]


class ProgramUpdate:
    def __init__(self,job,*,probe=package_processes,after_state=None):
        self.job=Path(job).resolve();self.journal=self.job/'transaction.json';self.probe=probe;self.after_state=after_state
        self.data=json.loads(self.journal.read_text(encoding='utf-8'))
        self.target=Path(self.data['target']);self.candidate=self.job/'candidate';self.backup=Path(self.data['backup'])
        identifier=self.job.name.removeprefix('.autozzz-update-')
        if (len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier)
            or self.job.name!='.autozzz-update-'+identifier or self.target!=self.target.resolve()
            or self.target.parent!=self.job.parent or self.backup!=self.target.with_name(self.target.name+'.previous-'+identifier)
            or self.data.get('format')!=1 or self.data.get('product')!='AutoZZZ'
            or (self.target/'.git').exists()
            or any(type(self.data.get(key))is not str or len(self.data[key])!=64 or any(c not in '0123456789abcdef' for c in self.data[key]) for key in ('old_manifest','new_manifest'))):
            raise ValueError('更新事务路径或版本无效。')
        for path in (self.target,self.job,self.backup):
            if path.is_symlink() or path.exists() and getattr(path.lstat(),'st_file_attributes',0)&0x400:raise ValueError('更新目录被重定向。')

    @classmethod
    def prepare(cls,target,source,*,probe=package_processes):
        target,source=Path(target).resolve(),Path(source).resolve()
        if (target/'.git').exists():raise ValueError('源码目录不能替换为分发包。')
        old=read_manifest(target);new=read_manifest(source)
        if target==source or source.is_relative_to(target):raise ValueError('新程序不能放在当前安装目录中。')
        required=sum(filesystem_path(source/name).stat().st_size for name in new['files'])
        required+=sum(path.stat().st_size for name,path in files(target) if name!=MANIFEST and name not in old['files'])
        if shutil.disk_usage(target.parent).free<required+64*1024**2:raise ValueError('安装盘可用空间不足，当前程序尚未替换。')
        identifier=uuid.uuid4().hex;job=target.parent/('.autozzz-update-'+identifier);candidate=job/'candidate'
        job.mkdir();candidate.mkdir()
        for name in list(new['files'])+[MANIFEST]:
            origin=filesystem_path(source/name);destination=filesystem_path(candidate/name)
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(origin,destination)
        verify_package(candidate,strict=True)
        data=dict(format=1,product='AutoZZZ',target=str(target),backup=str(target.with_name(target.name+'.previous-'+identifier)),
            state='prepared',created=datetime.now(timezone.utc).isoformat(),old_manifest=digest(target/MANIFEST),
            new_manifest=digest(candidate/MANIFEST),old_version=old['version'],new_version=new['version'],
            program_before={name:digest(path) for name,path in files(target) if name in old['files']},user_files={},error='')
        write_json(job/'transaction.json',data)
        return cls(job,probe=probe)

    def state(self,name,**values):
        self.data.update(state=name,**values);write_json(self.journal,self.data)
        if self.after_state is not None:self.after_state(name)

    def _marker(self,path,expected):return path.is_dir() and (path/MANIFEST).is_file() and digest(path/MANIFEST)==expected

    def _check_candidate(self):
        if not self._marker(self.candidate,self.data['new_manifest']):raise ValueError('待安装程序包已变化。')
        return verify_package(self.candidate,strict=True)

    def _users(self):
        managed=read_manifest(self.target)['files']
        return {name:path for name,path in files(self.target) if name!=MANIFEST and name not in managed}

    def apply(self):
        if self.data['state']!='prepared':raise ValueError('该事务已经执行过，请查询或恢复原事务。')
        try:
            if not self._marker(self.target,self.data['old_manifest']):raise ValueError('当前程序版本已变化。')
            if self.backup.exists():raise ValueError('旧版本归档目录已存在。')
            new=self._check_candidate()
            if self.probe(self.target) or self.probe(self.candidate):raise RuntimeError('安装目录或新版本仍有程序运行，请等待原进程退出。')
            current={name:digest(path) for name,path in files(self.target) if name in self.data['program_before']}
            if current!=self.data['program_before']:raise ValueError('准备后程序文件已变化，请重新准备更新。')
            users=self._users();hashes={name:digest(path) for name,path in users.items()}
            managed_new={key.casefold() for key in new['files']}
            self.state('copying_user_data',user_files=hashes)
            for name,path in users.items():
                relative(name)
                if name.casefold()in managed_new:raise ValueError('新程序与用户文件同名：'+name)
                destination=filesystem_path(self.candidate/name)
                destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
                if digest(destination)!=hashes[name]:raise ValueError('用户文件复制未核实：'+name)
            if {name:digest(path) for name,path in self._users().items()}!=hashes:raise ValueError('复制期间用户文件已变化。')
            if self.probe(self.target):raise RuntimeError('安装目录有新进程启动，更新尚未替换。')
            self.state('moving_old');os.rename(filesystem_path(self.target),filesystem_path(self.backup));self.state('old_moved')
            self.state('moving_new');os.rename(filesystem_path(self.candidate),filesystem_path(self.target));self.state('installed')
            verify_package(self.target)
            if any(digest(filesystem_path(self.target/name))!=expected for name,expected in hashes.items()):raise ValueError('安装后用户文件核对失败。')
            self.state('completed');return dict(self.data)
        except Exception as exc:
            self.data['error']=str(exc)
            try:self.recover()
            except Exception as recovery:
                self.state('needs_recovery',error=str(exc)+'；'+str(recovery))
            raise

    def recover(self):
        if self.data['state']=='completed':return False
        old_here=self._marker(self.target,self.data['old_manifest'])
        old_backup=self._marker(self.backup,self.data['old_manifest'])
        new_here=self._marker(self.target,self.data['new_manifest'])
        if old_here and not self.backup.exists():self.state('rolled_back');return True
        if not old_backup:raise ValueError('无法核实完整旧版本，请保留事务目录。')
        before=self.data['program_before']
        archived={name:digest(path) for name,path in files(self.backup) if name in before}
        if archived!=before:raise ValueError('旧程序归档已变化，未自动回退。')
        if self.probe(self.target):raise RuntimeError('待恢复目录仍有程序运行。')
        if self.target.exists():
            if not new_here or self.candidate.exists():raise ValueError('恢复目标存在其他文件，未覆盖。')
            expected=self.data.get('user_files',{})
            actual=self._users()
            if set(actual)!=set(expected) or any(digest(actual[name])!=value for name,value in expected.items()):raise ValueError('新目录中的用户资料已变化，未自动回退。')
            os.rename(filesystem_path(self.target),filesystem_path(self.candidate))
        os.rename(filesystem_path(self.backup),filesystem_path(self.target));self.state('rolled_back');return True
