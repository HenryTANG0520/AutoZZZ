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
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--restart-client',action='store_true');parser.add_argument('--trace',action='store_true');parser.add_argument('--plugin',action='store_true');parser.add_argument('--timeout-seconds',type=int,default=30);args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise SystemExit('请使用新的隔离验证目录。')
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);paths=RuntimePaths(defaults.source,defaults.python,data);paths.prepare()
    (data/'.autozzz-rotation-flow-test').write_text('offline sdk only')
    setup=OneDragonClient(paths)
    def rpc(method,**params):return setup.call(method,timeout=30,**params)
    def action(operation,**params):
        current=rpc('account_list');return rpc('account_action',action=operation,revision=current['revision'],**params)
    try:
        setup.start()
        if args.plugin:
            from onedragon_plugins_smoke import write_plugin
            source=data.parent/(data.name+'-plugin-source')/'demo'
            if source.exists():raise ValueError('插件夹具来源目录已存在。')
            write_plugin(source)
            constants=(source/'demo_const.py').read_text(encoding='utf-8').replace('DEFAULT_GROUP = False','DEFAULT_GROUP = True');(source/'demo_const.py').write_text(constants,encoding='utf-8')
            factory='''import json
from pathlib import Path
from one_dragon.utils import os_utils
from one_dragon.base.operation.application.application_factory import ApplicationFactory
from one_dragon.base.operation.application_base import Application
from one_dragon.base.operation.operation_node import operation_node
from . import demo_const
class DemoApp(Application):
    def __init__(self,ctx):super().__init__(ctx,demo_const.APP_ID,op_name='轮换插件回放')
    @operation_node(name='记录当前账号',is_start_node=True)
    def step(self):
        path=Path(os_utils.get_work_dir())/'autozzz/fixture-game.json';value=json.loads(path.read_text());assert value['world'];value['done'].append(self.ctx.current_instance_idx)
        temporary=path.with_suffix('.plugin.tmp');temporary.write_text(json.dumps(value));temporary.replace(path);return self.round_success('fixture plugin completed')
class DemoFactory(ApplicationFactory):
    def __init__(self,ctx):super().__init__(demo_const);self.ctx=ctx
    def create_application(self,instance_idx,group_id):return DemoApp(self.ctx)
    def create_run_record(self,instance_idx):return None
'''
            (source/'demo_factory.py').write_text(factory,encoding='utf-8')
            def plugin_job(method,**params):
                import time
                key=rpc(method,**params)['job_id']
                while True:
                    state=rpc('plugin_status');assert state['job_id']==key
                    if not state['busy']:assert state['state']=='done',state;return state['result']
                    time.sleep(.02)
            review=plugin_job('plugin_preview',path=str(source),kind='directory');plugin_job('plugin_install',token=review['token'],names=['demo'],overwrites=[],revision=review['revision'])
            current=rpc('plugin_catalog');rpc('plugin_enabled',name='demo',enabled=True,revision=current['revision'])
        action('create',name='轮换账号2')
        for idx in (1,2):
            game=data/f'client-{idx if args.restart_client else 1}/ZenlessZoneZero.exe';game.parent.mkdir(exist_ok=True);game.write_bytes(b'fixture only')
            description=rpc('account_describe',idx=idx,server='cn')
            rpc('account_save',idx=idx,server='cn',revision=description['revision'],values=dict(game_path=str(game),game_region='cn',game_language='cn',account=f'fixture-account-{idx}',password=f'fixture-password-{idx}'))
            action('options',idx=idx,enabled=True,force_login=False);action('activate',idx=idx)
            group=rpc('describe_configuration',app_id='_group');rows=next(field['value'] for field in group['fields'] if field['name']=='app_list')
            for row in rows:row['enabled']=row['app_id']==('fixture_plugin' if args.plugin else 'email')
            rpc('save_configuration',app_id='_group',revision=group['revision'],values=dict(app_list=rows))
        action('activate',idx=1)
    finally:close(setup)
    events=queue.Queue();logs=[];last=None;trace_events=[]
    def emit(event):
        if event['type']=='log':logs.append(event['message'])
        if event['type']=='trace':
            event=session.take_trace()
            if event is None:return
            trace_events.append(event)
        events.put(event)
    session=RotationSession(paths,emit,report_root=data/'reports',client_factory=FixtureClient,read_key=lambda _:0)
    def wait(predicate):
        while True:
            event=events.get(timeout=90)
            if predicate(event):return event
    try:
        if args.trace:session.trace(True)
        session.send('prepare',server='cn')
        prepared=wait(lambda e:e['type']=='state' and not e['state']['busy'])['state']
        assert prepared['state']=='prepared',prepared
        if args.plugin:assert prepared['resources']['confirmation_required'] and all(row['requirements']['plugins'] for row in session.plan['instances'])
        session.send('start',budget=None,timeout_seconds=args.timeout_seconds,resource_confirmation=prepared['resources']['confirmation_id'] if args.plugin else None)
        last=wait(lambda e:e['type']=='state' and not e['state']['busy'] and e['state']['state']!='prepared')['state']
        assert last['state']=='success',last
        report=session.report;assert report['returned_to_start'] and [row['state'] for row in report['accounts']]==['success','success']
        state=json.loads((data/'autozzz/fixture-game.json').read_text());assert state['done']==[1,2] and state['world']
        methods=[row['method'] for row in report['stages']]
        assert methods.count('start_login')==3 and methods.count('start_application')==2
        assert methods.count('start_client_close')==(2 if args.restart_client else 0)
        if args.trace:
            assert any(event.get('snapshot',{}).get('items',{}).get('decision') for event in trace_events if event.get('snapshot')),trace_events
            assert sum(event.get('snapshot')is None for event in trace_events)>=2
            assert all('items' not in row['execution'] for row in report['stages'])
    finally:
        session.close();session.worker.join(15)
        (data/'parent.log').write_text('\n'.join(logs),encoding='utf-8')
        assert not session.worker.is_alive()
    result=dict(actual_sdk_operation_graphs=True,actual_pipe_and_worker_handoffs=True,accounts=2,returned_to_start=True,
                restart_client=args.restart_client,plugin_application=args.plugin,timeout_seconds=args.timeout_seconds,trace_subscribed=args.trace,trace_snapshots=sum(event.get('snapshot')is not None for event in trace_events),fixture_models_and_devices=True,game_accessed=False,game_input_sent=False)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
