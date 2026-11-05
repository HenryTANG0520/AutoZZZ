"""Remaining MCP tools through real fixed SDK/HTTP/session; recorded devices only."""
import argparse
from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import queue
import shutil
import socket
import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import patch
import uuid

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'vendor/onedragon/src')]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise ValueError('请选择新的夹具目录')
    profile=data/'profile';(profile/'config').mkdir(parents=True)
    for path in (root/'vendor/onedragon/config').glob('*.yml'):shutil.copyfile(path,profile/'config'/path.name)
    import numpy as np
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_execution import OneDragonExecution
    from autozzz.onedragon_session import RuntimeSession
    from autozzz.onedragon_control import WindowState
    from autozzz.run_lock import RunLock
    from one_dragon.base.operation.operation import Operation
    from zzz_od.operation.enter_game.enter_game import EnterGame
    from autozzz.clipboard_input import ClipboardPaste
    from tests.test_clipboard_input import Clipboard
    clipboard=Clipboard('原有剪贴板')
    service=OneDragonService(root/'vendor/onedragon',profile);ctx=service.ctx
    execution=OneDragonExecution(ctx);service._execution=execution;control=execution.control;calls=[]
    pressed=threading.Event()
    class Buttons:
        def reset(self):pass
        def press(self,key,press_time=None):
            calls.append(('press',key));pressed.set()
            try:
                if press_time:control.hold(press_time)
            finally:calls.append(('release',key))
        def release(self,key):calls.append(('release',key))
        def tap(self,key):calls.append(('tap',key))
        def type(self,text):calls.append(('text','masked'))
    class Window:
        bound=NS(hwnd=1,path='fixture');process_id=2
        def clear_binding(self):self.bound=None
    class Controller:
        is_game_window_ready=True;screenshot_method=ctx.env_config.screenshot_method
        keyboard_controller=Buttons();btn_controller=keyboard_controller;game_win=Window()
        def probe(self):return WindowState(True,True,(0,0,1920,1080))
        def bind(self,_):pass
        def active_window(self):pass
        def init_before_context_run(self):return True
        def screenshot(self):return control.observe(lambda:(time.time(),np.zeros((1080,1920,3),dtype=np.uint8)))
        def click(self,point,**values):return control.input(lambda:calls.append(('click',point.x,point.y))or True)
        def btn_tap(self,key):return control.input(lambda:self.btn_controller.tap(key))
        def drag_to(self,end,**values):return control.input(lambda:calls.append(('drag',end.x,end.y)))
        def input_str(self,text):return control.input(lambda:calls.append(('text','masked')))
    class Client:
        def __init__(self,*_,**__):self.results={};self.alive=True
        def start(self):return {}
        def submit(self,method,**parameters):
            identifier=uuid.uuid4().hex
            with service._dispatch_lock:
                if method=='initialize_execution':execution.state='ready';execution.job_id=uuid.uuid4().hex;value=service.execution_status()
                else:value=getattr(service,method)(**parameters)
            self.results[identifier]=value;return identifier
        def result(self,key,timeout):return self.results.pop(key)
        def close(self,timeout=5):self.alive=False
    def forbidden(*_,**__):raise AssertionError('禁止游戏启动或物理输入')
    events=queue.Queue();session=None
    def wait(test,seconds=15):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            event=events.get(timeout=max(.01,deadline-time.monotonic()))
            if event['type']=='error':raise AssertionError(event)
            if test(event):return event
        raise AssertionError('event timeout')
    with ExitStack()as stack:
        stack.enter_context(patch('autozzz.onedragon_session.OneDragonClient',Client))
        stack.enter_context(patch('autozzz.onedragon_execution.RunLock',side_effect=lambda:RunLock('Local\\AutoZZZ.MCPActions.'+uuid.uuid4().hex)))
        stack.enter_context(patch('autozzz.windows.user32.SendInput',side_effect=forbidden))
        stack.enter_context(patch('autozzz.windows.find_game',side_effect=forbidden))
        stack.enter_context(patch.object(Operation,'check_and_update_current_screen',return_value='大世界-普通'))
        stack.enter_context(patch.object(EnterGame,'check_screen_to_interact',return_value=None))
        stack.enter_context(patch('autozzz.clipboard_input.ClipboardPaste',side_effect=lambda:ClipboardPaste(clipboard)))
        # Startup and process termination are observation fixtures, not game launches.
        stack.enter_context(patch('autozzz.onedragon_mcp_actions.McpActions.startup',return_value=dict(window_handle=1,process_id=2)))
        def close(_actions,manager):manager.client_exit_result=dict(requested=True,process_exited=True);manager.state='success'
        stack.enter_context(patch('autozzz.onedragon_mcp_actions.McpActions.close_client',close))
        execution._watch=lambda stop:stop.wait()
        try:
            ctx.controller=Controller();ctx.ready_for_application=True
            session=RuntimeSession(NS(data=profile),events.put,report_root=data/'reports')
            wait(lambda event:event['type']=='connected')
            session.send('prepare',app_id='email',server='cn',game_path='fixture',region='cn')
            wait(lambda event:event['type']=='state'and event['state']['state']=='prepared')
            def local(method,**params):
                tag='mcp-'+uuid.uuid4().hex;session.send('rpc',method=method,tag=tag,params=params)
                event=wait(lambda event:event['type']in('rpc_result','rpc_error')and event['tag']==tag)
                assert event['type']=='rpc_result',event
                return event['result']
            with socket.socket()as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            started=local('mcp_start',port=port);sid=None;sequence=0
            def rpc(method,params=None,key=None):
                nonlocal sid,sequence
                sequence+=1;body=dict(jsonrpc='2.0',id=key or sequence,method=method,params=params or {})
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=20)
                headers={'Authorization':'Bearer '+started['bearer_token'],'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
                if sid:headers['Mcp-Session-Id']=sid
                conn.request('POST','/mcp',json.dumps(body),headers);response=conn.getresponse();raw=response.read()
                sid=response.getheader('Mcp-Session-Id')or sid;conn.close();assert response.status in(200,202),raw
                value=json.loads(raw)if raw else{}
                assert 'error'not in value,value
                return value.get('result')
            rpc('initialize',dict(protocolVersion='2025-03-26'))
            # A notification carries no id in the protocol; use the manager path for fixture handshake.
            service.mcp_service.sessions[sid]['initialized']=True
            def tool(name,parameters=None,key=None):
                result=rpc('tools/call',dict(name=name,arguments=parameters or{}),key)
                assert not result['isError'],result
                return json.loads(result['content'][0]['text'])
            def prepare(descriptor,scopes):
                local('mcp_action_prepare',descriptor=descriptor)
                wait(lambda event:event['type']=='state'and event['state']['state']=='prepared')
                status=local('mcp_status')
                local('mcp_action_enable',scopes=scopes,resource_confirmation=status['execution']['resources']['confirmation_id'])
            prepare(dict(mode='input'),['input','login','close'])
            tool('click_game',dict(x=100,y=200));tool('drag',dict(x1=10,y1=10,x2=100,y2=100))
            tool('key_tap',dict(key='f'));tool('input_text',dict(text='secret fixture',use_clipboard=False))
            assert tool('input_text',dict(text='clipboard secret fixture',use_clipboard=True))['method']=='clipboard'
            assert clipboard.value=='原有剪贴板'
            assert tool('open_game',dict(enter=False,block=True))=='成功打开游戏(未登录)'
            assert tool('open_game',dict(enter=False,block=False))['started']
            execution.worker.join(5)
            assert tool('open_game',dict(enter=True,block=True))=='成功打开并进入绝区零游戏'
            assert '已发送关闭' in tool('close_game')
            pressed.clear();resumed=[]
            short=threading.Thread(target=lambda:resumed.append(tool('key_tap',dict(key='e',press_time=.2))))
            short.start();assert pressed.wait(5);time.sleep(.04)
            tool('pause_run',dict(paused=True));time.sleep(.12);assert short.is_alive()
            tool('pause_run',dict(paused=False));short.join(3)
            assert not short.is_alive()and resumed[0]['success']
            # No repeated consent per input. An in-flight duplicate must be the same task.
            pressed.clear();answers=[];failures=[]
            def hold():
                try:answers.append(tool('key_tap',dict(key='w',press_time=20),key='same-inflight'))
                except Exception as exc:failures.append(str(exc))
            first=threading.Thread(target=hold);first.start();assert pressed.wait(5)
            second=threading.Thread(target=hold);second.start()
            tool('pause_run',dict(paused=True));tool('pause_run',dict(paused=False));tool('stop_run')
            first.join(5);second.join(5);assert not first.is_alive()and not second.is_alive()
            assert not failures,failures
            assert answers[0]==answers[1] and sum(row==('press','w')for row in calls)==1
            prepare(dict(mode='operation',op_id='zzz_od.operation.wait_normal_world.WaitNormalWorld',args=dict(check_once=True)),['operation'])
            assert tool('run_operation',dict(op_id='zzz_od.operation.wait_normal_world.WaitNormalWorld',args=dict(check_once=True),block=True))=='operation运行成功'
            assert tool('run_operation',dict(op_id='zzz_od.operation.wait_normal_world.WaitNormalWorld',args=dict(check_once=True),block=False))['started']
            execution.worker.join(5)
            deadline=time.monotonic()+5
            while service.mcp_service.actions.history and time.monotonic()<deadline:time.sleep(.02)
            reports=[json.loads(path.read_text(encoding='utf-8'))for path in (data/'reports').rglob('report.json')]
            assert len(reports)==13,(len(reports),reports)
            assert all(row['request']['source']=='mcp'for row in reports)
            assert sum(row['status']=='stopped'for row in reports)==1
            assert not service.mcp_service.actions.history
            # A reviewed synthesis op may carry a plan outside the saved list;
            # preserve saved-file invariants while binding exactly that plan.
            synthesis_file=profile/'config/explicit-synthesis.yml'
            synthesis_file.write_text('loop: false\nplan_list: []\n',encoding='utf-8')
            reviewed=dict(category_name='合成电池',plan_times=1,plan_id='reviewed')
            plan=NS(to_dict=lambda:dict(reviewed))
            guard=NS(path=synthesis_file,specs=[],loop=False)
            service.mcp_service.actions.bind_synthesis(guard,plan)
            operation=NS(config=NS(plan_list=[],loop=False),plan=NS(category_name='合成电池',to_dict=lambda:dict(reviewed)))
            guard.authorize(operation)
            bad=dict(reviewed,plan_times=2);operation.plan=NS(category_name='合成电池',to_dict=lambda:bad)
            try:guard.authorize(operation)
            except ValueError:pass
            else:raise AssertionError('changed synthesis op was authorized')
            operation.plan=plan;plan.category_name='合成电池';synthesis_file.write_text('loop: true\nplan_list: []\n',encoding='utf-8')
            try:guard.authorize(operation)
            except ValueError:pass
            else:raise AssertionError('changed saved synthesis config was authorized')
            session.close();session.worker.join(10);assert not session.worker.is_alive()
            for path in(data/'reports').rglob('report.json'):
                raw=path.read_text(encoding='utf-8');assert 'secret fixture'not in raw
                assert json.loads(raw)['process_exited']is True
            (data/'result.json').write_text(json.dumps(dict(success=True,tools=sorted(ACTIONS),reports=len(reports),
                repeated_request_executions=1,blocking_stop=True,game_input_sent=False,actual_sdk=True),indent=2),encoding='utf-8')
            print('PASS: remaining 7 MCP tools, blocking stop/replay, 13 original-session reports')
        finally:
            if session is not None:session.close();session.worker.join(10)
            service.mcp_stop();execution.close();ctx.controller=None;service.close()


ACTIONS={'click_game','key_tap','drag','input_text','open_game','close_game','run_operation'}
if __name__=='__main__':main()
