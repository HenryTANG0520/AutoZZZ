import copy
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.onedragon_mcp import McpService


class Service:
    def __init__(self):
        self.calls=[]
        self._execution=NS(state='prepared',busy=False,prepared_app='charge_plan',prepared_hash='config',
            job_id='prepared-1',configuration_digest=lambda:'config',uses_charge=True,
            ctx=NS(game_account_config=NS(game_region='cn')),
            resource_confirmation=NS(verify=self.confirm))
    def confirm(self,token,digest):
        if token!='consented' or digest!='config':raise ValueError('资源未确认')
    def _assert_idle(self):
        if self._execution.busy:raise ValueError('busy')
    def execution_status(self):
        return dict(state=self._execution.state,busy=self._execution.busy,job_id=self._execution.job_id,
            resources=dict(confirmation_id='local-secret'))
    def start_application(self,app_id,**params):
        self.calls.append((app_id,params));self._execution.state='running';self._execution.busy=True;self._execution.job_id='run-1'
        return self.execution_status()
    def pause_execution(self,paused):self.calls.append(('pause',paused));return self.execution_status()
    def stop_execution(self):self.calls.append(('stop',));self._execution.busy=False;self._execution.state='stopped';return self.execution_status()


class McpTests(unittest.TestCase):
    def setUp(self):
        self.service=Service();self.manager=McpService(self.service)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.service.data=self.root
        self.patcher=patch('autozzz.battle_budget.budget_directory',return_value=self.root);self.patcher.start();self.addCleanup(self.patcher.stop)
        self.budget=dict(id='existing',allowance=100,battle_limit=1,max_per_battle=100)
        self.path=self.root/'existing.json';self.path.write_text(json.dumps(dict(version=1,id='existing',server='cn',allowance=100,battle_limit=1,entries=[])))
        self.original=self.path.read_bytes()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        started=self.manager.start(self.port);self.token=started['bearer_token']
        self.addCleanup(self.manager.close)

    def grant(self):return self.manager.authorize(self.budget,resource_confirmation='consented')

    def http(self,body=None,method='POST',headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        values={'Authorization':'Bearer '+self.token,'Accept':'application/json, text/event-stream','Content-Type':'application/json'}
        values.update(headers or {})
        connection.request(method,'/mcp',body=None if body is None else json.dumps(body),headers=values)
        response=connection.getresponse();status=response.status;result=response.read();headers=dict(response.getheaders());connection.close()
        return status,json.loads(result) if result else None,headers

    def initialize(self):
        status,body,headers=self.http(dict(jsonrpc='2.0',id=1,method='initialize',params=dict(protocolVersion='2025-11-25')))
        self.assertEqual(status,200);self.assertEqual(body['result']['protocolVersion'],'2025-03-26')
        session=headers['Mcp-Session-Id'];self.session={'Mcp-Session-Id':session,'MCP-Protocol-Version':'2025-03-26'}
        self.assertEqual(self.http(dict(jsonrpc='2.0',method='notifications/initialized'),headers=self.session)[0],202)
        return session

    def test_real_http_negotiation_listing_and_get405(self):
        self.initialize()
        code,data,_=self.http(dict(jsonrpc='2.0',id=2,method='tools/list'),headers=self.session)
        self.assertEqual(code,200);self.assertEqual(len(data['result']['tools']),30)
        self.assertIn('click_game',str(data));self.assertEqual(self.http(method='GET')[0],405)

    def test_auth_origin_host_and_protocol_rejections(self):
        request=dict(jsonrpc='2.0',id=1,method='ping')
        for headers,expected in [({'Authorization':'Bearer wrong'},401),({'Origin':'https://example.com'},403),
            ({'Host':'attacker.example'},403),({'MCP-Protocol-Version':'unknown'},400),({'Accept':'application/json'},406)]:
            self.assertEqual(self.http(request,headers=headers)[0],expected)
        self.assertFalse(self.service.calls)

    def test_single_use_authorization_calls_real_service_interface_with_pinned_budget(self):
        self.grant();self.manager.tool('run_prepared_application',{})
        self.assertEqual(self.service.calls[0][0],'charge_plan')
        self.assertEqual(self.service.calls[0][1]['budget'],self.budget)
        with self.assertRaises(ValueError):self.manager.tool('run_prepared_application',{})
        self.assertEqual(len(self.service.calls),1);self.assertEqual(self.path.read_bytes(),self.original)

    def test_budget_cannot_be_created_increased_or_pending(self):
        self.path.unlink()
        with self.assertRaises(ValueError):self.grant()
        self.assertFalse(self.path.exists())
        self.path.write_bytes(self.original);self.budget['allowance']=200
        with self.assertRaises(ValueError):self.grant()
        self.budget['allowance']=100
        value=json.loads(self.original);value['entries']=[dict(status='pending')];self.path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.grant()
        self.assertFalse(self.service.calls)

    def test_stale_preparation_and_resource_consent_rejected(self):
        with self.assertRaises(ValueError):self.manager.authorize(self.budget)
        self.grant();self.service._execution.job_id='different-preparation'
        with self.assertRaises(ValueError):self.manager.tool('run_prepared_application',{})
        self.assertFalse(self.service.calls)

    def test_http_request_replay_returns_original_job_without_second_start(self):
        self.initialize();self.grant()
        request=dict(jsonrpc='2.0',id=2,method='tools/call',params=dict(name='run_prepared_application',arguments={}))
        first=self.http(request,headers=self.session);again=self.http(request,headers=self.session)
        self.assertEqual(first[1],again[1]);self.assertEqual(len(self.service.calls),1)
        request['params']['name']='stop_run';self.assertEqual(self.http(request,headers=self.session)[0],400)

    def test_remote_cannot_supply_another_budget_or_arbitrary_tool(self):
        self.grant()
        for name,args in [('run_prepared_application',dict(budget=self.budget)),('click_game',dict(x=1,y=1))]:
            with self.assertRaises(ValueError):self.manager.tool(name,args)
        self.assertFalse(self.service.calls)

    def test_pause_stop_do_not_control_foreign_job_and_stop_keeps_original_handle(self):
        with self.assertRaises(ValueError):self.manager.tool('stop_run',{})
        self.grant();self.manager.tool('run_prepared_application',{})
        self.manager.tool('pause_run',{'paused':True});self.manager.tool('stop_run',{})
        self.assertIn(('pause',True),self.service.calls);self.assertIn(('stop',),self.service.calls)
        self.service._execution.job_id='gui-task'
        with self.assertRaises(ValueError):self.manager.tool('stop_run',{})

    def test_revoke_does_not_restart_or_stop_running_job(self):
        self.grant();self.manager.tool('run_prepared_application',{});self.manager.revoke()
        self.assertEqual(len(self.service.calls),1);self.assertTrue(self.service._execution.busy)

    def test_session_termination_and_notification_batch(self):
        old_id=self.initialize()
        rows=[dict(jsonrpc='2.0',method='notifications/progress',params={})]*2
        self.assertEqual(self.http(rows,headers=self.session)[0],202)
        self.assertEqual(self.http(method='GET',headers=self.session)[0],405)
        self.assertEqual(self.http(method='DELETE',headers=self.session)[0],200)
        ping=dict(jsonrpc='2.0',id=3,method='ping')
        tool=dict(jsonrpc='2.0',id=4,method='tools/call',params=dict(name='run_prepared_application'))
        self.assertEqual(self.http(ping,headers=self.session)[0],404)
        self.assertEqual(self.http(tool,headers=self.session)[0],404)
        self.assertEqual(self.http(rows,headers=self.session)[0],404)
        self.assertEqual(self.http(method='GET',headers=self.session)[0],404)
        self.assertEqual(self.http(method='DELETE',headers=self.session)[0],404)
        self.assertEqual(self.http(ping,headers={**self.session,'Authorization':'Bearer wrong'})[0],401)
        self.assertEqual(self.http(ping,headers={**self.session,'Origin':'https://example.com'})[0],403)
        self.assertEqual(self.http(ping)[0],400)
        self.assertNotEqual(self.initialize(),old_id)
        self.assertEqual(self.http(ping,headers=self.session)[0],200)
        self.assertFalse(self.service.calls)

    def test_status_does_not_expose_local_consent_or_bearer(self):
        self.grant()
        self.assertNotIn('bearer_token',self.manager.status())
        self.assertNotIn('confirmation_id',self.manager.tool('get_run_status',{})['autozzz']['resources'])
        self.assertNotIn(self.token,json.dumps(self.manager.status()['logs']))

    def test_service_log_tracks_authenticated_lifecycle_without_poll_noise(self):
        session=self.initialize()
        notifications=dict(jsonrpc='2.0',method='notifications/initialized')
        self.assertEqual(self.http(notifications,headers=self.session)[0],202)
        ping=dict(jsonrpc='2.0',id=2,method='ping')
        self.assertEqual(self.http(ping,headers=self.session)[0],200)
        self.assertEqual(self.http(ping,headers={**self.session,'Authorization':'Bearer rejected-secret'})[0],401)
        remote=dict(jsonrpc='2.0',id=3,method='tools/call',params=dict(name='get_run_status',arguments={}))
        self.assertNotIn('logs',json.loads(self.http(remote,headers=self.session)[1]['result']['content'][0]['text']))
        self.assertEqual(self.http(method='DELETE',headers=self.session)[0],200)
        self.assertEqual(self.http(method='DELETE',headers=self.session)[0],404)
        logs=self.manager.status()['logs']
        messages=[row['message'] for row in logs]
        self.assertEqual(len(messages),3)
        self.assertEqual(messages[1:],['MCP客户端已连接。','MCP客户端已断开。'])
        self.assertTrue(all(isinstance(row['time'],float) for row in logs))
        for value in (self.token,session,'rejected-secret'):
            self.assertNotIn(value,str(logs))

    def test_current_run_summary_uses_control_state_without_copying_raw_error(self):
        self.service.execution_status=lambda:dict(state='running',busy=True,job_id='other',prepared_app='email',
            control={'state':'focus_wait'},run=dict(state='running',source='autozzz',app='email',current_node='检查邮件',
            duration_seconds=12.5),error='fixture-password')
        before=self.manager.status()
        self.assertEqual(before['run_status'],dict(state='focus_wait',source='autozzz',app='email',step='检查邮件',duration_seconds=12.5))
        self.assertEqual(before['logs'],self.manager.status()['logs'])
        self.assertNotIn('fixture-password',str(before['logs'])+str(before['run_status']))

    def test_log_ring_is_bounded_copied_and_redacts_the_current_bearer(self):
        for index in range(205):self.manager.log(str(index))
        self.manager.log('unexpected secret '+self.token)
        rows=self.manager.status()['logs']
        self.assertEqual(len(rows),200)
        self.assertEqual(rows[0]['message'],'6')
        self.assertNotIn(self.token,str(rows))
        rows[0]['message']='changed'
        self.assertEqual(self.manager.status()['logs'][0]['message'],'6')

    def test_stop_then_start_rotates_bearer_and_invalidates_old_sessions(self):
        old_session=self.initialize()
        old_token=self.token
        self.assertFalse(self.manager.stop()['running'])
        self.token=self.manager.start(self.port)['bearer_token']
        self.assertNotEqual(self.token,old_token)
        ping=dict(jsonrpc='2.0',id=2,method='ping')
        self.assertEqual(self.http(ping,headers={**self.session,'Authorization':'Bearer '+old_token})[0],401)
        self.assertEqual(self.http(ping,headers=self.session)[0],404)
        self.assertNotEqual(self.initialize(),old_session)
        self.assertEqual(self.http(ping,headers=self.session)[0],200)

    def test_delayed_body_on_old_listener_cannot_initialize_after_restart(self):
        entered=threading.Event()
        original=self.manager.session_state
        def observe(session):
            result=original(session)
            entered.set()
            return result

        body=json.dumps(dict(jsonrpc='2.0',id=1,method='initialize',
                            params=dict(protocolVersion='2025-03-26'))).encode()
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        try:
            with patch.object(self.manager,'session_state',side_effect=observe):
                connection.putrequest('POST','/mcp')
                connection.putheader('Authorization','Bearer '+self.token)
                connection.putheader('Accept','application/json, text/event-stream')
                connection.putheader('Content-Type','application/json')
                connection.putheader('Content-Length',str(len(body)))
                connection.endheaders()
                self.assertTrue(entered.wait(2))
                self.manager.stop()
                self.token=self.manager.start(self.port)['bearer_token']
                connection.send(body)
                response=connection.getresponse()
                response.read()
                self.assertEqual(response.status,404)
                self.assertEqual(self.manager.sessions,{})
        finally:
            connection.close()
        self.initialize()

    def test_start_failure_is_logged_without_partial_token_or_native_error(self):
        self.manager.stop()
        with patch('autozzz.onedragon_mcp.ThreadingHTTPServer',side_effect=OSError('fixture native bind detail')):
            with self.assertRaises(OSError):self.manager.start(self.port)
        status=self.manager.status()
        self.assertFalse(status['running'])
        self.assertEqual(self.manager.token,'')
        self.assertEqual(status['logs'][-1]['message'],'MCP服务启动失败，监听端口不可用。')

    def test_close_stops_only_owned_job_and_releases_port(self):
        self.grant();self.manager.tool('run_prepared_application',{});self.manager.close()
        self.assertFalse(self.manager.status()['running']);self.assertIn(('stop',),self.service.calls)
        with socket.socket() as sock:sock.bind(('127.0.0.1',self.port))

    def test_grant_expiry_and_configuration_change_never_start(self):
        self.grant()
        self.manager.grant['expires_at']=0
        with self.assertRaises(ValueError):self.manager.tool('run_prepared_application',{})
        self.grant();self.service._execution.configuration_digest=lambda:'changed'
        with self.assertRaises(ValueError):self.manager.tool('run_prepared_application',{})
        self.assertFalse(self.service.calls)

    def test_dispatch_lock_serializes_local_mutation_and_mcp_start(self):
        self.manager.close()
        self.service._dispatch_lock=threading.RLock()
        self.manager=McpService(self.service)
        self.manager.start(self.port);self.grant()
        begun=threading.Event();finished=threading.Event();errors=[]
        def remote():
            begun.set()
            try:self.manager.tool('run_prepared_application',{})
            except Exception as exc:errors.append(str(exc))
            finally:finished.set()
        with self.service._dispatch_lock:
            thread=threading.Thread(target=remote);thread.start();self.assertTrue(begun.wait(1))
            self.assertFalse(finished.wait(.05))
            self.service._execution.prepared_hash='changed-by-local-config'
        thread.join(2)
        self.assertTrue(errors);self.assertFalse(self.service.calls)
        self.manager.close()

    def test_close_rejects_already_authenticated_waiting_request(self):
        self.initialize();self.grant()
        self.manager.close()
        with self.assertRaises(ValueError):
            self.manager.rpc(dict(jsonrpc='2.0',id=4,method='tools/call',params=dict(name='run_prepared_application',arguments={})),self.session['Mcp-Session-Id'])
        self.assertFalse(self.service.calls)

    def test_invalid_body_and_before_initialize_never_run(self):
        self.assertEqual(self.http([{}]*17)[0],400)
        self.assertEqual(self.http(dict(jsonrpc='2.0',id=1,method='tools/list'))[0],400)
        self.assertEqual(self.http(dict(jsonrpc='2.0',method='initialize',params=dict(protocolVersion='2025-03-26')))[0],400)
        self.assertFalse(self.service.calls)


if __name__=='__main__':unittest.main()
