"""真实父协调器、管道、SDK 操作图与进程换代回放；不访问游戏。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending
from autozzz.onedragon_rotation_session import RotationSession


class FixtureClient(OneDragonClient):
    def start(self,timeout=30):
        original=subprocess.Popen
        def spawn(command,**kwargs):
            if '-m' in command and 'autozzz.onedragon_worker' in command:
                marker=command.index('-m')
                command=command[:marker]+[str(Path(__file__).with_name('rotation_fixture_worker.py'))]+command[marker+2:]
            return original(command,**kwargs)
        with patch('autozzz.onedragon_runtime.subprocess.Popen',side_effect=spawn):return super().start(timeout)


def close(client):
    while True:
        try:client.close(timeout=1);return
        except RequestPending:continue


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--restart-client',action='store_true');args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise SystemExit('请使用新的隔离验证目录。')
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);paths=RuntimePaths(defaults.source,defaults.python,data);paths.prepare()
    (data/'.autozzz-rotation-flow-test').write_text('offline sdk only')
    setup=OneDragonClient(paths)
    def rpc(method,**params):return setup.call(method,timeout=30,**params)
    def action(operation,**params):
        current=rpc('account_list');return rpc('account_action',action=operation,revision=current['revision'],**params)
    try:
        setup.start();action('create',name='轮换账号2')
        for idx in (1,2):
            game=data/f'client-{idx if args.restart_client else 1}/ZenlessZoneZero.exe';game.parent.mkdir(exist_ok=True);game.write_bytes(b'fixture only')
            description=rpc('account_describe',idx=idx,server='cn')
            rpc('account_save',idx=idx,server='cn',revision=description['revision'],values=dict(game_path=str(game),game_region='cn',game_language='cn',account=f'fixture-account-{idx}',password=f'fixture-password-{idx}'))
            action('options',idx=idx,enabled=True,force_login=False);action('activate',idx=idx)
            group=rpc('describe_configuration',app_id='_group');rows=next(field['value'] for field in group['fields'] if field['name']=='app_list')
            for row in rows:row['enabled']=row['app_id']=='email'
            rpc('save_configuration',app_id='_group',revision=group['revision'],values=dict(app_list=rows))
        action('activate',idx=1)
    finally:close(setup)
    events=queue.Queue();logs=[];last=None
    def emit(event):
        if event['type']=='log':logs.append(event['message'])
        events.put(event)
    session=RotationSession(paths,emit,report_root=data/'reports',client_factory=FixtureClient,read_key=lambda _:0)
    def wait(predicate):
        while True:
            event=events.get(timeout=90)
            if predicate(event):return event
    try:
        session.send('prepare',server='cn')
        prepared=wait(lambda e:e['type']=='state' and not e['state']['busy'])['state']
        assert prepared['state']=='prepared',prepared
        session.send('start',budget=None,timeout_seconds=30,resource_confirmation=None)
        last=wait(lambda e:e['type']=='state' and not e['state']['busy'] and e['state']['state']!='prepared')['state']
        assert last['state']=='success',last
        report=session.report;assert report['returned_to_start'] and [row['state'] for row in report['accounts']]==['success','success']
        state=json.loads((data/'autozzz/fixture-game.json').read_text());assert state['done']==[1,2] and state['world']
        methods=[row['method'] for row in report['stages']]
        assert methods.count('start_login')==3 and methods.count('start_application')==2
        assert methods.count('start_client_close')==(2 if args.restart_client else 0)
    finally:
        session.close();session.worker.join(15)
        (data/'parent.log').write_text('\n'.join(logs),encoding='utf-8')
        assert not session.worker.is_alive()
    result=dict(actual_sdk_operation_graphs=True,actual_pipe_and_worker_handoffs=True,accounts=2,returned_to_start=True,
                restart_client=args.restart_client,fixture_models_and_devices=True,game_accessed=False,game_input_sent=False)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
