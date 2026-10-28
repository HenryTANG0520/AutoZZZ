import queue
import json
import threading
import tempfile
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from autozzz.onedragon_runtime import RequestPending,RemoteError
from autozzz.onedragon_session import RuntimeSession


class Client:
    instances=[]
    def __init__(self,paths,log=None):
        self.instances.append(self)
        self.alive=True
        self.requests={}
        self.calls=[]
        self.observed=set()
        self.state="idle"
        self.job=""
        self.query_error=True
        self.close_count=0

    def start(self):pass
    def submit(self,method,**params):
        key=str(len(self.requests))
        self.calls.append((method,params))
        if method=="initialize_execution":self.state,self.job="ready","init"
        elif method=="prepare_application":self.state,self.job="prepared","prep"
        elif method=="start_application":self.state,self.job="running","run"
        elif method=="stop_execution":self.state="stopped"
        result=dict(state=self.state,job_id=self.job,busy=self.state=="running",ready=True,prepared_app="email",uses_charge=False)
        self.requests[key]=method,result
        return key

    def result(self,key,timeout):
        if key not in self.observed:
            self.observed.add(key)
            raise RequestPending(key)
        method,result=self.requests[key]
        if method=="execution_status" and self.state=="running" and self.query_error:
            self.query_error=False
            raise RemoteError("transient query fault")
        return result

    def close(self,timeout=5):
        self.close_count+=1
        if self.close_count==1:raise RequestPending("close")
        self.alive=False


class SessionTests(unittest.TestCase):
    def test_one_client_and_job_survive_pending_replies_and_transient_query_error(self):
        Client.instances=[]
        events=queue.Queue()
        with tempfile.TemporaryDirectory() as folder,patch("autozzz.onedragon_session.OneDragonClient",Client):
            session=RuntimeSession(None,events.put,report_root=Path(folder))
            try:
                def wait(predicate):
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        event=events.get(timeout=2)
                        if predicate(event):return event
                    self.fail("session timeout")
                wait(lambda e:e['type']=='connected')
                session.send('prepare',app_id='email',server='cn',game_path='game.exe',region='cn')
                wait(lambda e:e['type']=='state' and e['state']['state']=='prepared')
                session.send('start',app_id='email',budget=None,auto_launch=False,timeout_seconds=30)
                warning=wait(lambda e:e['type']=='error')
                self.assertTrue(warning['busy'])
                state=wait(lambda e:e['type']=='state' and e['state']['state']=='running')
                self.assertEqual(state['state']['job_id'],'run')
                session.stop()
                wait(lambda e:e['type']=='state' and e['state']['state']=='stopped')
            finally:
                session.close()
                session.worker.join(5)
            self.assertFalse(session.worker.is_alive())
            report=json.loads(next(Path(folder).rglob("report.json")).read_text(encoding="utf-8"))
            self.assertEqual(report['status'],'stopped')
            self.assertEqual(report['execution']['job_id'],'run')
            self.assertFalse(report['game_input_verified'])
        self.assertEqual(len(Client.instances),1)
        client=Client.instances[0]
        self.assertEqual([m for m,p in client.calls].count('start_application'),1)
        self.assertEqual([m for m,p in client.calls].count('prepare_application'),1)
        self.assertEqual(client.close_count,2)

    def test_close_during_run_records_exit_without_claiming_completion(self):
        events=queue.Queue()
        with tempfile.TemporaryDirectory() as folder,patch("autozzz.onedragon_session.OneDragonClient",Client):
            session=RuntimeSession(None,events.put,report_root=Path(folder))
            try:
                session.send('prepare',app_id='email',server='cn',game_path='game.exe',region='cn')
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='prepared')
                session.send('start',app_id='email',budget=None,auto_launch=False,timeout_seconds=30)
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='running')
            finally:
                session.close()
                session.worker.join(5)
            self.assertFalse(session.worker.is_alive())
            report=json.loads(next(Path(folder).rglob("report.json")).read_text(encoding="utf-8"))
            self.assertEqual(report['status'],'stopped')
            self.assertTrue(report['process_exited'])

    @staticmethod
    def wait_event(events,predicate):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            event=events.get(timeout=2)
            if predicate(event):return event
        raise AssertionError("session timeout")

    def test_stop_during_bootstrap_does_not_start_application_and_keeps_bootstrap_log(self):
        entered=threading.Event()
        release=threading.Event()
        class Runner:
            def __init__(self,settings,emit):self.emit=emit
            def stop(self):release.set()
            def enter_game(self):
                entered.set()
                self.emit(dict(type='log',message='进入游戏回放'))
                if not release.wait(3):raise AssertionError('runner stop was not delivered')
                return type('Report',(),{'to_dict':lambda _:dict(status='stopped',error='已停止')})()
        events=queue.Queue()
        with tempfile.TemporaryDirectory() as folder,patch("autozzz.onedragon_session.OneDragonClient",Client),patch("autozzz.app.Runner",Runner):
            session=RuntimeSession(None,events.put,report_root=Path(folder))
            try:
                session.send('prepare',app_id='email',server='cn',game_path='game.exe',region='cn')
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='prepared')
                session.send('start',app_id='email',budget=None,auto_launch=True,timeout_seconds=30)
                self.assertTrue(entered.wait(3))
                session.stop()
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='stopped')
                self.assertNotIn('start_application',[method for method,_ in Client.instances[-1].calls])
            finally:
                session.close()
                session.worker.join(5)
            report_path=next(Path(folder).rglob('report.json'))
            report=json.loads(report_path.read_text(encoding='utf-8'))
            self.assertEqual(report['status'],'stopped')
            self.assertFalse(report['execution']['application_started'])
            self.assertIn('进入游戏回放',report_path.with_name('run.log').read_text(encoding='utf-8'))


if __name__=='__main__':unittest.main()
