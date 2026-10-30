"""完整普通权限 EXE 经正式 CLI 更新自身；只操作独立 build 包和资料目录。"""
from __future__ import annotations

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.onedragon_bundle import digest,filesystem_path
from autozzz.program_package import MANIFEST,read_manifest,verify_package
from autozzz.program_update import ProgramUpdate,package_processes,write_json
from autozzz.run_lock import RunLock


def wait(predicate,seconds=1800):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.1)
    raise TimeoutError('观察等待到期；请查询原进程，勿重新启动更新。')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--data',type=Path,required=True)
    args=parser.parse_args();project=Path(__file__).resolve().parents[1]
    target=args.package.resolve();base=args.data.resolve()
    if not target.is_relative_to(project/'build'):raise ValueError('只允许验证项目 build 下的独立完整包。')
    if base.exists():raise ValueError('请使用新的隔离资料目录。')
    if package_processes(target):raise ValueError('目标完整包仍有进程。')
    original=verify_package(target,strict=True);base.mkdir(parents=True)
    new=json.loads(json.dumps(original));new['version']='verify-next-'+uuid.uuid4().hex[:12]
    readme=(target/'README.md').read_bytes()+b'\nFrozen update acceptance marker.\n'
    import hashlib
    new['files']['README.md']=hashlib.sha256(readme).hexdigest()
    archive=base/'AutoZZZ-windows-x64.zip'
    print('Creating complete release ZIP from managed program files.',flush=True)
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as output:
        output.writestr('AutoZZZ/','')
        for name in original['files']:
            if name=='README.md':output.writestr('AutoZZZ/'+name,readme)
            else:output.write(filesystem_path(target/name),'AutoZZZ/'+name)
        output.writestr('AutoZZZ/'+MANIFEST,json.dumps(new,ensure_ascii=False,sort_keys=True,indent=2))
    users={'config.json':'{"verification":"isolated configuration"}',
           'runs/保留报告.json':'{"report":"preserve"}',
           '我的策略/坏女人.json':'{"strategy":"preserve custom file"}'}
    for name,value in users.items():
        path=target/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8') as output:output.write(value)
    user_hashes={name:digest(target/name) for name in users}
    environment=os.environ.copy();environment['LOCALAPPDATA']=str(base/'appdata')
    for name in ('PYTHONHOME','PYTHONPATH','PYTHONUSERBASE','VIRTUAL_ENV','CONDA_PREFIX','PYTHONSTARTUP','AUTOZZZ_INTERNAL_RUN_LEASE'):
        environment.pop(name,None)
    report=base/'update-report.json';handle=None
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[C.c_ulong,C.c_int,C.c_ulong];kernel.OpenProcess.restype=C.c_void_p
    kernel.WaitForSingleObject.argtypes=[C.c_void_p,C.c_ulong];kernel.WaitForSingleObject.restype=C.c_ulong
    kernel.CloseHandle.argtypes=[C.c_void_p]
    print('Starting actual frozen update CLI; no game task is requested.',flush=True)
    with (base/'cli.log').open('wb') as output:
        process=subprocess.Popen([str(target/'AutoZZZ.exe'),'--program-update',str(archive),'--update-report',str(report),'--server','cn','--no-restart'],
            cwd=target,env=environment,stdout=output,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        write_json(base/'original-process.json',dict(pid=process.pid,target=str(target),report=str(report)))
        wait(lambda:process.poll()is not None)
        result=json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
        assert process.returncode==0 and result.get('state')=='handoff_ready',(process.returncode,result)
    transaction=ProgramUpdate(result['transaction'])
    receipt=json.loads((transaction.job/'helper-ready.json').read_text(encoding='utf-8'))
    pid=receipt['pid'];handle=kernel.OpenProcess(0x100000,False,pid)
    try:
        if handle:
            def terminal():
                state=kernel.WaitForSingleObject(handle,0)
                if state not in (0,258):raise OSError(C.get_last_error(),'Cannot observe original helper')
                return state==0
            wait(terminal)
        else:
            # A fast helper may have exited before OpenProcess; query actual process state.
            assert pid not in package_processes(transaction.job/'helper')
        final=json.loads((transaction.job/'helper-result.json').read_text(encoding='utf-8'))
        assert final['state']=='completed',final
        transaction=ProgramUpdate(transaction.job);assert transaction.data['state']=='completed'
        assert verify_package(target)['version']==new['version']
        assert verify_package(transaction.backup)['version']==original['version']
        assert (target/'README.md').read_bytes()==readme
        for name,checksum in user_hashes.items():
            assert digest(target/name)==digest(transaction.backup/name)==checksum,name
        with RunLock():pass
        preflight=json.loads((transaction.job/'runtime-report.json').read_text(encoding='utf-8'))
        assert preflight['state']=='success' and preflight['worker_exited']
        print('Whole-directory replacement succeeded; verifying the installed EXE again.',flush=True)
        with (base/'installed-runtime.log').open('wb') as output:
            diagnostic=subprocess.Popen([str(target/'AutoZZZ.exe'),'--runtime-check',str(base/'installed-runtime.json')],cwd=target,
                env=environment,stdout=output,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            wait(lambda:diagnostic.poll()is not None)
            assert diagnostic.returncode==0
        runtime=json.loads((base/'installed-runtime.json').read_text(encoding='utf-8'))
        assert runtime['state']=='success' and runtime['bundled'] and runtime['worker_exited']
        assert runtime['game_started']is False and runtime['game_input_sent']is False
        evidence=dict(actual_frozen_update_cli=True,actual_frozen_candidate_preflight=True,actual_copied_helper=True,
            actual_parent_exit=True,whole_directory_replacement=True,original_process_exited=True,helper_exited=True,
            user_files_preserved=user_hashes,old_version=original['version'],new_version=new['version'],
            transaction=str(transaction.job),backup=str(transaction.backup),installed_runtime=runtime,
            network_used=False,game_accessed=False,game_input_sent=False)
        write_json(base/'result.json',evidence);print(json.dumps(evidence,ensure_ascii=False),flush=True)
    finally:
        if handle:kernel.CloseHandle(handle)


if __name__=='__main__':main()
