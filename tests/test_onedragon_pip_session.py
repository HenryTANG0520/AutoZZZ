from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from autozzz.onedragon_pip_session import PipSession
from autozzz.onedragon_runtime import RuntimePaths,RequestPending


def wait(predicate,seconds=3):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('原画中画会话未在时限内响应')


class Client:
    def __init__(self,*_):
        self.alive=False;self.worker_pid=1234;self.calls=[];self.requests={};self.next=0;self.starts=0;self.closes=0
        self.revision=0;self.enabled=True;self.pending=set();self.exit_allowed=threading.Event();self.exit_allowed.set()
    def start(self,**_):self.alive=True;self.starts+=1;raise RequestPending('original hello')
    def finish_start(self,**_):return {}
    def submit(self,method,**params):
        self.next+=1;self.calls.append((method,params));self.requests[str(self.next)]=(method,params);self.pending.add(str(self.next));return str(self.next)
    def result(self,request,**_):
        if request in self.pending:self.pending.remove(request);raise RequestPending(request)
        method,params=self.requests[request]
        if method=='pip_status' and hasattr(self,'status_gate') and not self.status_gate.wait(.01):
            self.status_waiting.set();raise RequestPending(request)
        if method=='pip_start' and getattr(self,'fail_start',False):raise RuntimeError('fixture rejected target')
        if method=='pip_save':
            assert params['revision']==str(self.revision)
            self.revision+=1
            if 'enabled' in params['values']:self.enabled=params['values']['enabled']
        if method in ('pip_settings','pip_save'):return dict(revision=str(self.revision),values=dict(enabled=self.enabled,width=480,x=-1,y=-1))
        if method=='pip_status':return dict(visible=False,epoch=1,state='loading',sequence=0)
        if method=='pip_activate':return dict(requested=True,foreground=True)
        return {}
    def close(self,**_):
        self.closes+=1
        if not self.exit_allowed.is_set():raise RuntimeError('fixture original process exit unknown')
        self.alive=False


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.client=Client()
        self.mock=patch('autozzz.onedragon_pip_session.OneDragonClient',return_value=self.client);self.mock.start();self.addCleanup(self.mock.stop)
        self.session=PipSession(RuntimePaths(Path('source'),Path('python.exe'),Path('data')),'cn',r'E:\cn\ZenlessZoneZero.exe')
        self.addCleanup(self.cleanup)
        wait(lambda:any(name=='pip_start' for name,_ in self.client.calls))
    def cleanup(self):
        if hasattr(self.client,'status_gate'):self.client.status_gate.set()
        self.client.exit_allowed.set();self.session.close();self.session.worker.join(3)
        self.assertFalse(self.session.worker.is_alive())

    def test_pending_responses_keep_one_connection_and_one_start(self):
        wait(lambda:self.session.take_state() is not None)
        self.assertEqual(self.client.starts,1)
        self.assertEqual(sum(name=='pip_start' for name,_ in self.client.calls),1)
        self.assertFalse(any(name=='pip_activate' for name,_ in self.client.calls))

    def test_queued_geometry_is_saved_before_original_process_close(self):
        self.session.send('geometry',values=dict(width=600,x=1,y=2));self.session.close();self.session.worker.join(3)
        self.assertFalse(self.session.worker.is_alive())
        self.assertIn(('pip_save',dict(values=dict(width=600,x=1,y=2),revision='0')),self.client.calls)
        self.assertTrue(self.client.enabled)

    def test_new_game_target_reuses_same_worker(self):
        self.session.send('target',server='global',game_path=r'E:\global\ZenlessZoneZero.exe')
        wait(lambda:sum(name=='pip_start' for name,_ in self.client.calls)==2)
        self.assertEqual(self.client.starts,1)

    def test_focus_requires_explicit_preview_click_and_live_original_worker(self):
        self.session.send('activate',epoch=1);wait(lambda:any(name=='pip_activate' for name,_ in self.client.calls))
        self.assertEqual(sum(name=='pip_activate' for name,_ in self.client.calls),1)
        self.client.alive=False
        with patch('autozzz.windows.user32.AllowSetForegroundWindow') as allow:
            self.assertFalse(self.session.allow_user_focus());allow.assert_not_called()

    def test_unknown_exit_does_not_end_session_or_start_replacement(self):
        self.client.exit_allowed.clear();self.session.close();wait(lambda:self.client.closes>0)
        self.assertTrue(self.session.worker.is_alive());self.assertEqual(self.client.starts,1)
        self.client.exit_allowed.set();self.session.worker.join(3)
        self.assertFalse(self.session.worker.is_alive())

    def test_rejected_start_is_not_repeated_by_status_polling(self):
        self.client.fail_start=True
        self.session.send('target',server='global',game_path=r'E:\global\ZenlessZoneZero.exe')
        wait(lambda:sum(name=='pip_start' for name,_ in self.client.calls)==2);time.sleep(.1)
        self.assertEqual(sum(name=='pip_start' for name,_ in self.client.calls),2)
        self.client.fail_start=False
        self.session.send('target',server='global',game_path=r'E:\fixed\ZenlessZoneZero.exe')
        wait(lambda:sum(name=='pip_start' for name,_ in self.client.calls)==3)

    def test_close_discards_unsubmitted_focus_requests_but_waits_pending_poll(self):
        self.client.status_gate=threading.Event();self.client.status_waiting=threading.Event()
        wait(self.client.status_waiting.is_set)
        self.session.send('activate',epoch=1);self.session.close()
        self.assertTrue(self.session.worker.is_alive())
        self.client.status_gate.set();self.session.worker.join(3)
        self.assertFalse(any(name=='pip_activate' for name,_ in self.client.calls))
        self.assertFalse(self.session.worker.is_alive())


if __name__=='__main__':unittest.main()
