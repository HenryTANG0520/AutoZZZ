"""真实工作进程换代和共享任务锁；SDK 登录/关闭回放仍使用替身设备。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending
from autozzz.run_lock import RunLock
from autozzz.task_lease import RunLeaseOwner,LEASE_ENV


def close(client):
    while True:
        try:client.close(timeout=1);return
        except RequestPending:continue


def excluded():
    lock=RunLock()
    try:lock.acquire()
    except RuntimeError:return
    else:lock.close();raise AssertionError('another game job entered rotation')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise SystemExit('请使用新的隔离验证目录。')
    data.mkdir(parents=True);(data/'.autozzz-transition-test').write_text('owned transition fixtures',encoding='utf-8')
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1])
    paths=RuntimePaths(defaults.source,defaults.python,data/'rpc');paths.prepare()
    checks=[];pids=[]
    with RunLeaseOwner() as owner:
        for index in range(2):
            with (data/f'worker-{index}.log').open('w',encoding='utf-8') as log:
                client=OneDragonClient(paths,log=lambda text:log.write(text+'\n'),run_lease=owner)
                try:
                    hello=client.start();assert hello['task_lease'] and not hello['input_enabled']
                    pids.append(hello['pid']);catalog=client.call('catalog')
                    assert len(catalog['applications'])>=30
                    excluded()
                finally:close(client)
                assert not client.alive and client.worker_exit_code==0
            excluded()  # 前一真实工作进程已退出，后一尚未创建，总锁仍应保持。
        assert len(set(pids))==2
    with RunLock():pass
    checks.append('two_actual_sdk_workers_exit_and_rebuild_under_one_continuous_total_lock_including_the_gap_between_workers')

    with RunLeaseOwner() as owner:
        environment=os.environ.copy();environment['PYTHONUTF8']='1';environment[LEASE_ENV]=owner.export()
        with (data/'sdk.log').open('w',encoding='utf-8') as stream:
            process=subprocess.Popen([str(defaults.python),'-u','tools/onedragon_login_smoke.py','--data',str(data/'sdk')],
                                     cwd=Path(__file__).resolve().parents[1],env=environment,stdout=stream,stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            while True:
                try:code=process.wait(timeout=.5);break
                except subprocess.TimeoutExpired:continue
        assert code==0,'SDK fixture failed; inspect sdk.log'
        result=json.loads((data/'sdk/result.json').read_text(encoding='utf-8'))
        assert result['leased_worker'] and result['count']==26 and not result['game_accessed'] and result['network_blocked']
        excluded()
    with RunLock():pass
    checks.append('actual_login_switch_and_verified_client_exit_operations_execute_under_the_parent_lease_with_normal_input_guards')
    summary=dict(checks=checks,sdk_checks=result['count'],worker_processes=len(pids),game_accessed=False,game_input_sent=False)
    (data/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
