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
    def test_login_uses_login_operations_and_never_legacy_enter_or_application_budget(self):
        class LoginClient(Client):
            def submit(self,method,**params):
                if method=='prepare_login':self.state,self.job='prepared','login-prep'
                elif method=='start_login':self.state,self.job='running','login-run'
                return super().submit(method,**params)
        Client.instances=[];events=queue.Queue()
        with tempfile.TemporaryDirectory() as folder,patch('autozzz.onedragon_session.OneDragonClient',LoginClient),patch('autozzz.app.Runner',side_effect=AssertionError('legacy entry must not run')):
            session=RuntimeSession(None,events.put,report_root=Path(folder))
            try:
                session.send('prepare',app_id='_login',server='global',game_path='fixture.exe',region='asia',login_mode='forced_enter')
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='prepared')
                session.send('start',app_id='_login',budget=None,auto_launch=True,timeout_seconds=30)
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='running')
                session.pause(True)
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='running')
                session.stop()
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='stopped')
            finally:session.close();session.worker.join(5)
            calls=Client.instances[0].calls
            self.assertEqual([params for name,params in calls if name=='prepare_login'],[dict(mode='forced_enter')])
            self.assertEqual([params for name,params in calls if name=='start_login'],[dict(mode='forced_enter',auto_launch=True,timeout_seconds=30)])
            self.assertFalse(any(name in ('prepare_application','start_application') for name,_ in calls))
            self.assertEqual(len(Client.instances),1)
            report=json.loads(next(Path(folder).rglob('report.json')).read_text(encoding='utf-8'))
            self.assertEqual(report['app_id'],'_login');self.assertEqual(report['status'],'stopped')
            self.assertEqual(report['connection']['login_mode'],'forced_enter')
            self.assertFalse(report['game_input_verified'])

    def test_route_debug_metadata_is_prepared_once_and_kept_in_report(self):
        Client.instances=[]
        events=queue.Queue()
        descriptor=dict(key='saved_route_1',revision='version',start_index=1)
        with tempfile.TemporaryDirectory() as folder,patch('autozzz.onedragon_session.OneDragonClient',Client):
            session=RuntimeSession(None,events.put,report_root=Path(folder))
            try:
                session.send('prepare',app_id='world_patrol',server='global',game_path='fixture.exe',region='asia',route_debug=descriptor)
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='prepared')
                prepares=[params for method,params in Client.instances[0].calls if method=='prepare_application']
                self.assertEqual(prepares,[dict(app_id='world_patrol',route_debug=descriptor)])
                session.send('start',app_id='world_patrol',budget=None,timeout_seconds=30)
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='running')
                saved=json.loads((session.report_dir/'report.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['connection']['route_debug'],descriptor)
                self.assertEqual(len(Client.instances),1)
            finally:session.close();session.worker.join(5)
            self.assertFalse(session.worker.is_alive())

    def test_explicit_reprepare_closes_old_process_before_new_initialization(self):
        Client.instances=[]
        observations=[]
        class OrderedClient(Client):
            def start(self):
                observations.append(('start',len(self.instances)))
                if len(self.instances)>1:
                    self.assert_previous_exited = not self.instances[-2].alive
            def close(self,timeout=5):
                observations.append(('close',self.close_count))
                return super().close(timeout)
        events=queue.Queue()
        with tempfile.TemporaryDirectory() as folder,patch('autozzz.onedragon_session.OneDragonClient',OrderedClient):
            session=RuntimeSession(None,events.put,report_root=Path(folder))
            try:
                for _ in range(2):
                    session.send('prepare',app_id='email',server='global',game_path='game.exe',region='global')
                    self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='prepared')
                self.assertEqual(len(Client.instances),2)
                self.assertTrue(Client.instances[-1].assert_previous_exited)
                self.assertEqual(Client.instances[0].close_count,2)
                self.assertEqual([m for m,_ in Client.instances[-1].calls].count('initialize_execution'),1)
            finally:
                session.close();session.worker.join(5)
            self.assertFalse(session.worker.is_alive())

    def test_stop_during_reprepare_wait_never_spawns_replacement_process(self):
        Client.instances=[]
        waiting,release=threading.Event(),threading.Event()
        class SlowClose(Client):
            def close(self,timeout=5):
                if self.close_count==0:
                    self.close_count+=1
                    waiting.set();release.wait(4)
                    raise RequestPending('close')
                self.close_count+=1;self.alive=False
        events=queue.Queue()
        with tempfile.TemporaryDirectory() as folder,patch('autozzz.onedragon_session.OneDragonClient',SlowClose):
            session=RuntimeSession(None,events.put,report_root=Path(folder))
            try:
                params=dict(app_id='email',server='global',game_path='game.exe',region='global')
                session.send('prepare',**params)
                self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='prepared')
                session.send('prepare',**params)
                self.assertTrue(waiting.wait(4))
                session.stop();release.set()
                event=self.wait_event(events,lambda e:e['type']=='state' and e['state']['state']=='stopped')
                self.assertFalse(event['state']['ready'])
                self.assertEqual(len(Client.instances),1)
                self.assertEqual([m for m,_ in Client.instances[0].calls].count('initialize_execution'),1)
            finally:
                release.set();session.close();session.worker.join(5)
            self.assertFalse(session.worker.is_alive())

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

    def test_configuration_rpc_keeps_original_request_and_does_not_create_run_report(self):
        events=queue.Queue()
        with tempfile.TemporaryDirectory() as folder,patch("autozzz.onedragon_session.OneDragonClient",Client):
            session=RuntimeSession(None,events.put,report_root=Path(folder))
            try:
                self.wait_event(events,lambda event:event['type']=='connected')
                session.send('rpc',method='account_list',tag='accounts',params={})
                event=self.wait_event(events,lambda event:event['type']=='rpc_result')
                self.assertEqual(event['tag'],'accounts')
                self.assertEqual([method for method,_ in Client.instances[-1].calls].count('account_list'),1)
                self.assertEqual(list(Path(folder).rglob('report.json')),[])
            finally:
                session.close()
                session.worker.join(5)

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
