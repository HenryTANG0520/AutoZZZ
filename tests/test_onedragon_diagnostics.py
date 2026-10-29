from contextlib import redirect_stdout,redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autozzz.onedragon_diagnostics import check_runtime
from autozzz.onedragon_runtime import RequestPending


class Client:
    def __init__(self,_paths):self.starts=0;self.closed=0;self.alive=False;self.calls=[];self.delay=False;self.failure=False
    def start(self,**_):
        self.starts+=1;self.alive=True
        if self.delay:raise RequestPending('same hello')
        return dict(revision='fixed',python='3.11.18')
    def finish_start(self,**_):return dict(revision='fixed',python='3.11.18')
    def call(self,method,**params):
        self.calls.append(method)
        if self.failure:raise RuntimeError('fixture failure')
        if method=='catalog':return dict(applications=[{},{}])
        if method=='describe_configuration':return dict(fields=[{}])
        if method=='policies':return [dict(name='fixture')]
        if method=='policy_validate':return dict(engine='actual source fixture')
        if method=='execution_status':return dict(ready=False,busy=False)
        raise AssertionError('unexpected operation: '+method)
    def close(self,**_):
        self.closed+=1
        if self.delay and self.closed==1:raise RequestPending('same close')
        self.alive=False


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.report=Path(self.temp.name)/'new-report.json';self.client=Client(None)
        self.mock=patch('autozzz.onedragon_diagnostics.OneDragonClient',return_value=self.client);self.mock.start();self.addCleanup(self.mock.stop)

    def test_offline_report_uses_isolated_profile_and_no_execution_commands(self):
        result=check_runtime(Path(self.temp.name),self.report)
        self.assertEqual(result['state'],'success');self.assertTrue(result['worker_exited'])
        self.assertFalse(result['game_input_sent']);self.assertEqual(json.loads(self.report.read_text()),result)
        self.assertEqual(set(self.client.calls),{'catalog','describe_configuration','policies','policy_validate','execution_status'})

    def test_pending_handshake_and_close_stay_on_the_same_client(self):
        self.client.delay=True;result=check_runtime(Path(self.temp.name),self.report)
        self.assertTrue(result['worker_exited']);self.assertEqual(self.client.starts,1);self.assertEqual(self.client.closed,2)

    def test_failure_is_written_and_worker_is_closed(self):
        self.client.failure=True;result=check_runtime(Path(self.temp.name),self.report)
        self.assertEqual(result['state'],'failed');self.assertIn('fixture failure',result['error'])
        self.assertTrue(result['worker_exited']);self.assertTrue(self.report.is_file())

    def test_existing_report_is_not_overwritten(self):
        self.report.write_text('previous result')
        with self.assertRaises(ValueError):check_runtime(Path(self.temp.name),self.report)
        self.assertEqual(self.report.read_text(),'previous result');self.assertEqual(self.client.starts,0)

    def test_cli_runs_diagnostic_without_loading_gui_or_game(self):
        from autozzz.__main__ import main
        with patch('autozzz.onedragon_diagnostics.check_runtime',return_value=dict(state='success')) as check,redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--runtime-check',str(self.report)]),0)
        self.assertEqual(check.call_count,1)

    def test_cli_diagnostic_rejects_battle_execution_options(self):
        from autozzz.__main__ import main
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
            main(['--runtime-check',str(self.report),'--energy-limit','20'])
        self.assertEqual(error.exception.code,2)


if __name__=='__main__':unittest.main()
