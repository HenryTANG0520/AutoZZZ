from concurrent.futures import Future
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.onedragon_run_ui import ExecutionWindow
from autozzz.onedragon_session import RuntimeSession


class McpSessionTests(unittest.TestCase):
    def session(self):
        session=RuntimeSession.__new__(RuntimeSession)
        session.mcp_active=True;session.job_id='prepared';session.phase=''
        session.parameters=dict(app_id='email',server='cn',game_path='fixture',region='cn')
        session.report=None;session.report_dir=None;session.report_lock=threading.RLock()
        session.events=[];session.emit=session.events.append
        return session

    def test_foreign_job_and_changed_app_never_adopted(self):
        session=self.session()
        for remote in ({},dict(job_id='new',prepared_job='prepared',app_id='charge_plan')):
            state=dict(job_id='new',busy=False,state='success',mcp_run=remote)
            session._call=lambda *_:state
            with self.assertRaisesRegex(RuntimeError,'任务编号'):session._status(None)
        self.assertIsNone(session.report)

    def test_completed_fast_remote_job_creates_one_terminal_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            session=self.session();session.report_root=Path(temporary);session.paths=NS(data=Path(temporary))
            state=dict(job_id='new',busy=False,state='success',mcp_run=dict(job_id='new',prepared_job='prepared',app_id='email',timeout_seconds=30,budget=None))
            session._call=lambda *_:state
            session._status(None);first=session.report_dir
            self.assertEqual(session.report['status'],'success')
            self.assertEqual(session.report['request']['source'],'mcp')
            session._status(None);self.assertEqual(session.report_dir,first)
            self.assertEqual(len(list(Path(temporary).rglob('report.json'))),1)

    def test_tagged_ui_bridge_close_wakes_original_request_without_retry(self):
        window=ExecutionWindow.__new__(ExecutionWindow)
        window.mcp_requests_lock=threading.Lock();window.mcp_requests={};window.closed=window.closing=False
        sent=queue.Queue();window.session=NS(send=lambda *args,**kw:sent.put((args,kw)))
        errors=[]
        def request():
            try:window.mcp_rpc('mcp_status',{})
            except RuntimeError as exc:errors.append(str(exc))
        thread=threading.Thread(target=request);thread.start()
        _,params=sent.get(timeout=2)
        self.assertTrue(params['tag'].startswith('mcp-'))
        window.closing=True;window.cancel_mcp_requests();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertTrue(errors);self.assertTrue(sent.empty())
        self.assertEqual(window.mcp_requests,{})

    def test_rpc_errors_preserve_tag_and_do_not_hang_ui(self):
        class Client:
            alive=True
            def __init__(self,*_,**__):pass
            def start(self):return {}
            def submit(self,*_,**__):raise ValueError('fixture rpc rejected')
            def close(self,timeout=5):self.alive=False
        events=queue.Queue()
        with tempfile.TemporaryDirectory() as temporary,patch('autozzz.onedragon_session.OneDragonClient',Client):
            session=RuntimeSession(None,events.put,report_root=Path(temporary))
            try:
                events.get(timeout=2)
                session.send('rpc',method='mcp_grant',params={},tag='mcp-fixture')
                event=events.get(timeout=2)
                self.assertEqual(event['type'],'rpc_error');self.assertEqual(event['tag'],'mcp-fixture')
                self.assertIn('fixture rpc rejected',event['message'])
            finally:session.close();session.worker.join(5)
            self.assertFalse(session.worker.is_alive())


if __name__=='__main__':unittest.main()
