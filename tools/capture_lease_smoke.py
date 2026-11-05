"""Actual Windows revocation/parent-death/mutex handles with isolated test object names."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

project=Path(__file__).resolve().parents[1];sys.path.insert(0,str(project))
import autozzz.capture_lease as c
from autozzz.run_lock import RunLock


def child(descriptor,name):
    c.RUN_MUTEX_NAME=name
    with c.JoinedCaptureLease(descriptor) as lease:
        print('ready',flush=True)
        command=sys.stdin.readline().strip()
        if command=='wait-parent':time.sleep(.4)
        try:lease.check();print('valid',flush=True)
        except RuntimeError:print('revoked',flush=True)
        if command!='wait-parent':sys.stdin.readline()


def dying_owner(name):
    lock=RunLock(name);lock.acquire();owner=c.CaptureLeaseOwner(lock.handle,1)
    process=subprocess.Popen([sys.executable,__file__,'--child',owner.export(),'--name',name],stdin=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    process.stdin.write('wait-parent\n');process.stdin.flush()
    time.sleep(.2);os._exit(0)  # no Python cleanup: child must detect the original parent's actual exit.


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);parser.add_argument('--child');parser.add_argument('--name');parser.add_argument('--dying-owner',action='store_true')
    args=parser.parse_args()
    if args.child:return child(args.child,args.name)
    if args.dying_owner:return dying_owner(args.name)
    args.output.mkdir(parents=True,exist_ok=True)
    name='Local\\AutoZZZ.CaptureLeaseFixture.'+uuid.uuid4().hex;lock=RunLock(name);lock.acquire()
    owner=c.CaptureLeaseOwner(lock.handle,3);process=None
    try:
        process=subprocess.Popen([sys.executable,__file__,'--child',owner.export(),'--name',name],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
        assert process.stdout.readline().strip()=='ready'
        lock.close();owner.close()  # only child's original total-mutex handle remains now.
        try:
            with RunLock(name):raise AssertionError('task lock released before child finished')
        except RuntimeError:pass
        process.stdin.write('check\n');process.stdin.flush();assert process.stdout.readline().strip()=='revoked'
        process.stdin.write('exit\n');process.stdin.flush();process.wait(5);assert process.returncode==0,process.stderr.read()
        with RunLock(name):pass
    finally:
        owner.close();lock.close()
        if process is not None and process.poll() is None:
            process.stdin.write('exit\n');process.stdin.flush();process.wait(5)
    exited=subprocess.run([sys.executable,__file__,'--dying-owner','--name',name],capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    assert exited.returncode==0 and 'ready' in exited.stdout and 'revoked' in exited.stdout,(exited.stdout,exited.stderr)
    with RunLock(name):pass
    result=dict(actual_windows_handles=True,actual_ancestor_validation=True,revocation_checked=True,
        parent_exit_checked=True,total_mutex_retained_until_reader_exits=True,game_accessed=False,input_sent=False)
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
