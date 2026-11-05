"""Actual fixed SDK execution + MCP HTTP + RuntimeSession reporting, no game input."""
import argparse
import http.client
import json
from pathlib import Path
import socket
import shutil
import queue
import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import patch
import uuid

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'vendor/onedragon/src')]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--tool',choices=('run_prepared_application','run_standalone_app'),default='run_prepared_application')
    args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise ValueError('请选择新的小型夹具目录')
    data.mkdir(parents=True)
    (data/'profile').mkdir()
    (data/'profile/config').mkdir()
    for path in (root/'vendor/onedragon/config').glob('*.yml'):
        shutil.copyfile(path,data/'profile/config'/path.name)
    import numpy as np
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_execution import OneDragonExecution
    from autozzz.onedragon_session import RuntimeSession
    from autozzz.onedragon_control import WindowState
    from autozzz.run_lock import RunLock
    from one_dragon.base.operation.application.application_factory import ApplicationFactory
    from one_dragon.base.operation.application_base import Application
    from one_dragon.base.operation.application_run_record import AppRunRecord
    from one_dragon.base.operation.operation_node import operation_node
    def forbidden(*_,**__):raise AssertionError('禁止游戏、外网或物理输入')
    service=OneDragonService(root/'vendor/onedragon',data/'profile')
    ctx=service.ctx;manager=OneDragonExecution(ctx);service._execution=manager
    control=manager.control;actions=[]
    class Controller:
        is_game_window_ready=True
        screenshot_method=ctx.env_config.screenshot_method
        keyboard_controller=NS(reset=lambda:None)
        def probe(self):return WindowState(True,True,(0,0,1920,1080))
        def bind(self,_):pass
        def init_before_context_run(self):return True
        def screenshot(self):return control.observe(lambda:(time.time(),np.zeros((1080,1920,3),dtype=np.uint8)))
    class RecordedApp(Application):
        def __init__(self):super().__init__(ctx,'email',op_name='MCP 无输入任务')
        @operation_node(name='离线节点',is_start_node=True)
        def step(self):actions.append('actual-sdk-node');return self.round_success('SDK fixture completed')
    class Factory(ApplicationFactory):
        def __init__(self):super().__init__(NS(APP_ID='email',APP_NAME='MCP fixture',DEFAULT_GROUP=True,NEED_NOTIFY=False))
        def create_application(self,instance_idx,group_id):return RecordedApp()
        def create_run_record(self,instance_idx):return AppRunRecord('email',instance_idx)
    class Client:
        def __init__(self,*args,**kwargs):self.requests={};self.alive=True
        def start(self):return {}
        def close(self,timeout=5):self.alive=False
        def submit(self,method,**params):
            key=uuid.uuid4().hex
            with service._dispatch_lock:
                if method=='initialize_execution':
                    # Only initialize controller/window identity with the no-input fixture.
                    manager.state='ready';manager.job_id=uuid.uuid4().hex
                    self.requests[key]=service.execution_status()
                else:self.requests[key]=getattr(service,method)(**params)
            return key
        def result(self,key,timeout):return self.requests.pop(key)
    try:
        ctx.controller=Controller();ctx.ready_for_application=True
        ctx.run_context.registry_application([Factory()])
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        events=queue.Queue()
        def wait(predicate):
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                event=events.get(timeout=max(.01,deadline-time.monotonic()))
                if predicate(event):return event
            raise AssertionError('session event timeout')
        session=None
        manager._watch=lambda stop:stop.wait()
        original_lock=RunLock
        with patch('autozzz.onedragon_session.OneDragonClient',Client),\
             patch('autozzz.onedragon_execution.RunLock',side_effect=lambda:original_lock('Local\\AutoZZZ.MCPSmoke.'+uuid.uuid4().hex)),\
             patch('autozzz.windows.find_game',side_effect=forbidden),\
             patch('autozzz.windows.user32.SendInput',side_effect=forbidden):
            session=RuntimeSession(NS(data=data/'profile'),events.put,report_root=data/'reports')
            wait(lambda e:e['type']=='connected')
            session.send('prepare',app_id='email',server='cn',game_path='fixture-only',region='cn')
            wait(lambda e:e['type']=='state' and e['state']['state']=='prepared')
            session.send('rpc',method='mcp_start',tag='mcp-start',params=dict(port=port))
            started=wait(lambda e:e['type']=='rpc_result' and e['tag']=='mcp-start')['result']
            session.send('rpc',method='mcp_grant',tag='mcp-grant',params=dict(timeout_seconds=30))
            wait(lambda e:e['type']=='rpc_result' and e['tag']=='mcp-grant')
            token=started['bearer_token'];session_id=None
            run_http(session,token,port,wait,service,manager,actions,data,args.tool)
            session.close();session.worker.join(10);assert not session.worker.is_alive()
    finally:
        service.mcp_stop();manager.close();ctx.controller=None;service.close()


def run_http(session,token,port,wait,service,manager,actions,data,tool):
        session_id=None
        def request(body):
            nonlocal session_id
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
            headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream','Content-Type':'application/json'}
            if session_id:headers['Mcp-Session-Id']=session_id
            conn.request('POST','/mcp',json.dumps(body),headers)
            response=conn.getresponse();session_id=response.getheader('Mcp-Session-Id') or session_id
            raw=response.read();assert response.status in (200,202),raw
            conn.close();return json.loads(raw) if raw else None
        request(dict(jsonrpc='2.0',id=1,method='initialize',params=dict(protocolVersion='2025-03-26')))
        request(dict(jsonrpc='2.0',method='notifications/initialized'))
        arguments=dict(app_id='email',block=False) if tool=='run_standalone_app' else {}
        response=request(dict(jsonrpc='2.0',id=2,method='tools/call',params=dict(name=tool,arguments=arguments)))
        assert not response['result']['isError'],response
        wait(lambda e:e['type']=='state' and e['state']['state']=='success')
        assert manager.state=='success',manager.status()
        assert session.job_id==manager.job_id and session.phase==''
        assert session.report['status']=='success' and session.report['request']['source']=='mcp'
        assert session.report['request']['app_id']=='email' and actions==['actual-sdk-node']
        result=dict(tool=tool,actual_sdk=True,actual_service=True,actual_loopback_http=True,original_task_and_session=True,
            report=str(session.report_dir/'report.json'),state=session.report['status'],calls=len(actions),game_input=False)
        (data/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
