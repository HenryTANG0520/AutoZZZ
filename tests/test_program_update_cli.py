import json
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

from autozzz.program_update import ProgramUpdate
from autozzz.program_update_cli import run_update
from autozzz.onedragon_bundle import digest
from autozzz.onedragon_runtime import RequestPending
from tests.test_program_update import package


class ProgramUpdateCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup);self.base=Path(self.temporary.name)
        self.old=package(self.base/'AutoZZZ','old');new=package(self.base/'new','new');self.transaction=ProgramUpdate.prepare(self.old,new,probe=lambda _:[])
        self.plan=dict(transaction=str(self.transaction.job),journal_hash=digest(self.transaction.journal),version='new')

    def client(self,cancel=False):
        commands=[];pending=set();closed=[]
        def submit(method,**params):commands.append((method,params));return str(len(commands)-1)
        def result(identifier,timeout):
            method,params=commands[int(identifier)]
            if method=='program_import':return dict(job_id='same')
            self.assertEqual(params['job_id'],'same')
            if identifier not in pending:pending.add(identifier);raise RequestPending(identifier)
            if cancel and method=='program_status':signal.raise_signal(signal.SIGINT)
            return dict(job_id='same',busy=False,state='cancelled' if cancel else 'success',detail='cancelled' if cancel else 'ready',result=self.plan)
        client=NS(start=Mock(),submit=submit,result=result,close=lambda **_:closed.append(True))
        return client,commands,closed

    def test_client_exits_before_single_handoff_and_keeps_original_request_id(self):
        client,commands,closed=self.client();handoff=NS(start=lambda:self.assertTrue(closed),poll=Mock(side_effect=[False,True]),cancel=Mock())
        report=self.base/'report.json'
        with patch('autozzz.program_update_cli.ROOT',self.old):
            result=run_update(self.base/'package.zip',report,restart=False,client_factory=lambda _:client,handoff_factory=lambda *_,**__:handoff)
        self.assertEqual(result['state'],'handoff_ready');self.assertEqual(sum(method=='program_import' for method,_ in commands),1)
        self.assertEqual(sum(method=='program_status' for method,_ in commands),1);handoff.cancel.assert_not_called()
        self.assertEqual(json.loads(report.read_text())['transaction'],str(self.transaction.job))

    def test_existing_report_does_not_start_another_update(self):
        report=self.base/'same.json';report.write_text('original');factory=Mock()
        with patch('autozzz.program_update_cli.ROOT',self.old),self.assertRaises(FileExistsError):run_update('package',report,client_factory=factory)
        factory.assert_not_called();self.assertEqual(report.read_text(),'original')

    def test_sigint_cancels_before_handoff_and_preserves_specific_report_state(self):
        client,_,closed=self.client(cancel=True);factory=Mock();report=self.base/'cancel.json'
        with patch('autozzz.program_update_cli.ROOT',self.old),self.assertRaises(InterruptedError):
            run_update('package',report,client_factory=lambda _:client,handoff_factory=factory)
        factory.assert_not_called();self.assertTrue(closed);self.assertEqual(json.loads(report.read_text())['state'],'cancelled')

    def test_handoff_start_failure_waits_for_original_helper_cancellation(self):
        client,_,_=self.client();handoff=NS(start=Mock(side_effect=OSError('original failure')),cancel=Mock(side_effect=[False,True]))
        report=self.base/'failed.json'
        with patch('autozzz.program_update_cli.ROOT',self.old),self.assertRaises(OSError):run_update('package',report,client_factory=lambda _:client,handoff_factory=lambda *_,**__:handoff)
        self.assertEqual(handoff.cancel.call_count,2);self.assertEqual(json.loads(report.read_text())['error'],'original failure')


if __name__=='__main__':unittest.main()
