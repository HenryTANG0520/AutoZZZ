from concurrent.futures import Future
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.onedragon_mcp_wait import PendingTool
from tests import test_onedragon_mcp as existing
from tests import test_mcp_session as session_fixtures


class ActionTests(unittest.TestCase):
    setUp=existing.McpTests.setUp
    http=existing.McpTests.http
    initialize=existing.McpTests.initialize

    def test_blocking_http_retry_uses_one_future_and_stop_is_not_locked(self):
        self.initialize();future=Future();entered=threading.Event();calls=[];answers=[]
        record=dict(future=future,output={})
        def dispatch(name,arguments):
            calls.append(name)
            if name=='key_tap':entered.set();return PendingTool(record=record,kind=name)
            if name=='stop_run':future.set_result(dict(state='stopped',error='stopped'));return dict(stopped=True)
        request=dict(jsonrpc='2.0',id='long',method='tools/call',params=dict(name='key_tap',arguments=dict(key='w',press_time=20)))
        def wait():answers.append(self.http(request,headers=self.session)[1])
        with patch.object(self.manager,'tool',side_effect=dispatch):
            first=threading.Thread(target=wait);second=threading.Thread(target=wait)
            first.start();self.assertTrue(entered.wait(1));second.start()
            _,stopped,_=self.http(dict(jsonrpc='2.0',id='stop',method='tools/call',params=dict(name='stop_run')),headers=self.session)
            first.join(2);second.join(2)
        self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
        self.assertEqual(calls.count('key_tap'),1);self.assertEqual(answers[0],answers[1])
        self.assertFalse(stopped['result']['isError'])

    def test_history_never_drops_unacknowledged_reports_and_stop_still_works(self):
        actions=self.manager.actions;actions.history=[dict(sequence=index)for index in range(1,65)];actions.sequence=64
        with self.assertRaisesRegex(ValueError,'报告事件尚未确认'):actions.capacity()
        self.assertEqual(len(actions.events()),64)
        self.manager.owned_job=self.service._execution.job_id
        self.manager.tool('stop_run',{})
        with self.assertRaises(ValueError):actions.ack(65)
        actions.ack(62);actions.capacity();self.assertEqual(len(actions.history),2)

    def test_action_permission_is_local_and_scope_bound(self):
        with self.assertRaisesRegex(ValueError,'本地MCP窗口'):
            self.manager.tool('click_game',dict(x=1,y=2))
        with self.assertRaises(ValueError):self.manager.tool('mcp_action_enable',dict(scopes=['input']))
        self.assertFalse(self.service.calls)

    def test_revocation_does_not_restart_or_stop_the_existing_task(self):
        self.manager.actions.session=dict(scopes=['input'])
        self.manager.action_disable()
        self.assertIsNone(self.manager.actions.session);self.assertFalse(self.service.calls)

    def test_remote_status_also_strips_local_consent_from_history(self):
        self.service.execution_status=lambda:dict(job_id='prepared-1',state='prepared',busy=False,
            resources=dict(confirmation_id='secret'),mcp_events=[dict(state=dict(resources=dict(confirmation_id='history-secret')))])
        value=self.manager.tool('get_run_status',{})
        self.assertNotIn('confirmation_id',json.dumps(value))
        self.assertNotIn('history-secret',json.dumps(value))

    def test_old_runtime_generation_blocks_new_input_but_keeps_status_and_stop(self):
        def stale():raise RuntimeError('运行时选择已变化')
        self.service._ctx=NS(autozzz_runtime_guard=stale)
        with self.assertRaisesRegex(RuntimeError,'运行时选择已变化'):
            self.manager.tool('key_tap',dict(key='f'))
        self.manager.tool('get_run_status',{})
        self.manager.owned_job=self.service._execution.job_id;self.manager.tool('stop_run',{})
        self.assertEqual(self.service.calls,[('stop',)])


class HistoryReportsTests(unittest.TestCase):
    def test_three_instant_actions_in_one_poll_each_gets_a_report_then_ack(self):
        helper=session_fixtures.McpSessionTests();session=helper.session();session.mcp_event_sequence=0
        with tempfile.TemporaryDirectory()as directory:
            session.report_root=Path(directory);session.paths=NS(data=Path(directory))
            events=[];parent='prepared'
            for index in range(3):
                job='job-'+str(index);base=dict(parent_job=parent,job_id=job,app_id='operation_debug',
                    session_id='local',budget=None,timeout_seconds=30,is_prepare=False,descriptor=dict(tool='key_tap'))
                events.append(dict(base,sequence=index*2+1,kind='start'))
                events.append(dict(base,sequence=index*2+2,kind='finish',state=dict(job_id=job,state='success',busy=False)))
                parent=job
            ack=[];state=dict(job_id=parent,state='success',busy=False,mcp_events=events)
            def call(_,method,**params):
                if method=='execution_status':return state
                ack.append(params['sequence']);return dict(acknowledged=params['sequence'])
            session._call=call;session._status(None);session._status(None)
            paths=list(Path(directory).rglob('report.json'))
            self.assertEqual(len(paths),3);self.assertEqual(ack,[6,6])
            self.assertTrue(all(json.loads(path.read_text(encoding='utf-8'))['status']=='success'for path in paths))

    def test_large_snapshot_is_preserved_in_report_without_exposing_local_consent(self):
        from autozzz.onedragon_mcp_actions import McpActions
        actions=McpActions(NS(service=None))
        record=dict(session_id='local',parent_job='prepared',job_id='large',app_id='operation_debug',
            descriptor=dict(tool='run_operation'),budget=None,timeout_seconds=30,is_prepare=False)
        snapshot=dict(job_id='large',state='success',busy=False,details='x'*100000,resources=dict(confirmation_id='local-only'))
        actions.add_event(record,'start');actions.add_event(record,'finish',snapshot)
        self.assertIn('state_zlib',actions.history[-1])
        session=session_fixtures.McpSessionTests().session();session.mcp_event_sequence=0
        with tempfile.TemporaryDirectory()as directory:
            session.report_root=Path(directory);session.paths=NS(data=Path(directory))
            session._call=lambda *_args,**_kwargs:dict(acknowledged=2)
            session._mcp_events(None,actions.history)
            report=json.loads((session.report_dir/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['execution']['details'],'x'*100000)
            self.assertNotIn('confirmation_id',report['execution']['resources'])


if __name__=='__main__':unittest.main()
