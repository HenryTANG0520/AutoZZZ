"""真实复制 Python、父进程退出与 Windows 任务锁交接；只替换隔离夹具目录。"""
from __future__ import annotations

import argparse
import ctypes as C
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch


def wait(predicate,timeout=60):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.05)
    raise AssertionError('更新交接仍在等待原进程。')


def fixture_worker():
    loss='--loss'in sys.argv
    if loss:sys.argv.remove('--loss')
    index=sys.argv.index('--bridge');bridge=Path(sys.argv[index+1]);del sys.argv[index:index+2];sys.argv.remove('--fixture-worker')
    sys.path.insert(0,str(bridge))
    from autozzz.program_update_handoff import worker
    from autozzz.program_update import ProgramUpdate
    original=ProgramUpdate.apply
    def gated(tx):
        (tx.job/'worker-waiting').write_text(str(__import__('os').getpid()))
        wait(lambda:(tx.job/'continue-update').exists())
        if loss:
            class ProcessLoss(BaseException):pass
            def lost(state):
                if state=='old_moved':raise ProcessLoss('simulated process loss after archiving old program')
            tx.after_state=lost
        return original(tx)
    with patch.object(ProgramUpdate,'apply',gated):return worker()


def fixture_parent():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from autozzz.program_update import ProgramUpdate
    from autozzz.program_update_handoff import UpdateHandoff
    parser=argparse.ArgumentParser();parser.add_argument('--fixture-parent',action='store_true');parser.add_argument('--job',type=Path,required=True);parser.add_argument('--loss',action='store_true');args=parser.parse_args()
    original=subprocess.Popen
    def spawn(command,**options):
        startup=str(args.job/'helper/start.py');index=command.index(startup)
        command=command[:index]+[str(Path(__file__).resolve()),'--fixture-worker','--bridge',str(args.job/'helper/bridge')]+command[index+1:]
        if args.loss:command.append('--loss')
        return original(command,**options)
    with patch('subprocess.Popen',side_effect=spawn):
        handoff=UpdateHandoff(ProgramUpdate(args.job),restart=False).start();wait(handoff.poll)
    (args.job/'parent-acknowledged').write_text('ready')
    return 0


def main():
    project=Path(__file__).resolve().parents[1];sys.path.insert(0,str(project))
    from autozzz.program_update_handoff import copy_python,MODULES
    from autozzz.program_update import ProgramUpdate,package_processes,write_json
    from autozzz.program_package import MANIFEST,create_manifest,files,verify_package
    from autozzz.onedragon_bundle import digest
    from autozzz.run_lock import RunLock
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args();base=args.data.resolve()
    if base.exists():raise ValueError('请使用新的隔离验证目录。')
    target=base/'中文 空格'/'AutoZZZ';target.mkdir(parents=True);bundle=target/'onedragon'
    copy_python(project/'dist/AutoZZZ/onedragon/python',bundle/'python')
    for name in MODULES:
        path=bundle/'bridge/autozzz'/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(project/'autozzz'/name,path)
    for name in ('source/LICENSE','source/src/zzz_od/context/zzz_context.py','bridge/autozzz/onedragon_worker.py','bridge/autozzz/onedragon_runtime.py','bridge/autozzz/onedragon_diagnostics.py'):
        path=bundle/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('updater fixture only; not an SDK runtime probe')
    entries={name:digest(path) for name,path in files(bundle)}
    (bundle/'autozzz-bundle.json').write_text(json.dumps(dict(format=1,revision='a'*40,protocol=1,python_version='3.11.18',files=entries,resources=[])))
    (target/'AutoZZZ.exe').write_text('fixture; never executed');(target/'README.md').write_text('old')
    create_manifest(target,'fixture-old','b'*40)
    (target/'config.json').write_text('current account configuration');(target/'runs').mkdir();(target/'runs/report.json').write_text('preserved report')
    candidate=base/'new';shutil.copytree(target,candidate);(candidate/'README.md').write_text('new');create_manifest(candidate,'fixture-new','c'*40)
    tx=ProgramUpdate.prepare(target,candidate)
    report=tx.job/'runtime-report.json';write_json(report,dict(state='success',bundled=True,worker_exited=True,game_started=False,game_input_sent=False,revision='a'*40,applications=30,strategies=dict(auto_battle=8,dodge=7)))
    tx.state('prepared',preflight=dict(manifest=tx.data['new_manifest'],report=digest(report)))
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[C.c_ulong,C.c_int,C.c_ulong];kernel.OpenProcess.restype=C.c_void_p
    kernel.WaitForSingleObject.argtypes=[C.c_void_p,C.c_ulong];kernel.WaitForSingleObject.restype=C.c_ulong
    kernel.CloseHandle.argtypes=[C.c_void_p]
    transactions=[]
    for loss in (False,True):
        if loss:
            (candidate/'README.md').write_text('next');create_manifest(candidate,'fixture-next','d'*40)
            tx=ProgramUpdate.prepare(target,candidate)
            report=tx.job/'runtime-report.json';write_json(report,dict(state='success',bundled=True,worker_exited=True,game_started=False,game_input_sent=False,revision='a'*40,applications=30,strategies=dict(auto_battle=8,dodge=7)))
            tx.state('prepared',preflight=dict(manifest=tx.data['new_manifest'],report=digest(report)))
        transactions.append(str(tx.job));handle=None
        command=[sys.executable,'-X','utf8',str(Path(__file__).resolve()),'--fixture-parent','--job',str(tx.job)]
        if loss:command.append('--loss')
        with (tx.job/'parent.log').open('wb') as output:
            parent=subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT)
            try:
                wait(lambda:parent.poll()is not None)
                assert parent.returncode==0,(tx.job/'parent.log').read_text(encoding='utf-8',errors='replace')
                wait(lambda:(tx.job/'worker-waiting').exists())
                pid=int((tx.job/'worker-waiting').read_text());handle=kernel.OpenProcess(0x100000,False,pid)
                assert handle and kernel.WaitForSingleObject(handle,0)==258
                assert pid in package_processes(tx.job/'helper')
                try:
                    with RunLock():raise AssertionError('helper did not retain total task lock after actual parent exit')
                except RuntimeError:pass
                (tx.job/'continue-update').touch()
                wait(lambda:kernel.WaitForSingleObject(handle,0)==0)
                if loss:
                    assert not target.exists() and tx.backup.is_dir()
                    recovery=subprocess.Popen([str(tx.job/'helper/python/python.exe'),'-I','-S','-X','utf8',str(tx.job/'helper/start.py'),'--job',str(tx.job),'--recover'],stdout=output,stderr=subprocess.STDOUT)
                    wait(lambda:recovery.poll()is not None)
                    assert recovery.returncode==0
                    assert json.loads((tx.job/'recovery-result.json').read_text())['state']=='rolled_back'
                    assert verify_package(target)['version']=='fixture-new'
                    assert verify_package(tx.candidate)['version']=='fixture-next'
                else:
                    result=json.loads((tx.job/'helper-result.json').read_text())
                    assert result['state']=='completed',result
                    assert verify_package(target)['version']=='fixture-new'
                    assert verify_package(tx.backup)['version']=='fixture-old'
                assert (target/'config.json').read_text()=='current account configuration'
                assert (target/'runs/report.json').read_text()=='preserved report'
                with RunLock():pass
            finally:
                (tx.job/'continue-update').touch()
                if handle:kernel.CloseHandle(handle)
    result=dict(actual_copied_python=True,actual_windows_parent_identity_and_exit=True,total_lock_survives_parent_exit=True,
                whole_directory_swap=True,user_configuration_and_reports_preserved=True,actual_game_access=False,
                candidate_exe_is_fixture=True,sdk_runtime_probe=False,standalone_copied_helper_recovery_after_process_loss=True,transactions=transactions)
    (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    if '--fixture-worker'in sys.argv:raise SystemExit(fixture_worker())
    if '--fixture-parent'in sys.argv:raise SystemExit(fixture_parent())
    main()
