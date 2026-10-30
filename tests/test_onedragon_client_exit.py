import threading
import time
from types import SimpleNamespace
import unittest

from autozzz.models import AutomationError
from autozzz.onedragon_client_exit import ClientExit
from autozzz.onedragon_control import ExecutionControl,WindowState,ObservationExpired


class ClientExitTests(unittest.TestCase):
    def setUp(self):
        self.focused=True;self.valid=True;self.posts=0;self.attempts=0;self.polls=0;self.waits=0;self.closed=False
        self.exits_after=3;self.interrupt=False;self.uncertain=False
        self.cancelled=threading.Event();self.on_wait=lambda:None
        self.window=SimpleNamespace(hwnd=100,request_close=self.request)
        self.controller=SimpleNamespace(game_win=SimpleNamespace(bound=self.window,process_id=20),probe=lambda:WindowState(self.valid,self.focused,(0,0,1920,1080)))
        self.control=ExecutionControl(lambda:self.exit.probe(),lambda:None,lambda:None,lambda:None,lambda:None)
        self.exit=ClientExit(self.controller,self.control);self.control.input_validator=self.exit.input_guard
    def post(self):self.posts+=1;self.valid=False;return True
    def exited(self):self.polls+=1;return self.polls>=self.exits_after
    def close(self):self.closed=True
    def request(self,checkpoint,submit):
        self.attempts+=1;checkpoint()
        if self.interrupt:
            self.interrupt=False;self.focused=False;self.control.poll()
        submit(self.post)
        if self.uncertain:raise ObservationExpired('fixture interruption after close post')
        return SimpleNamespace(exited=self.exited,close=self.close)
    def wait(self,_):
        self.waits+=1
        if self.waits>20:raise AssertionError('fixture close did not finish')
        self.on_wait()
    def run_exit(self):return self.exit.run(self.cancelled,self.wait)

    def test_window_disappearance_waits_exact_process_and_sends_only_one_close(self):
        self.assertTrue(self.run_exit())
        self.assertEqual(self.posts,1);self.assertEqual(self.polls,3);self.assertTrue(self.closed)
        self.assertFalse(self.control.error);self.assertTrue(self.exit.status()['process_exited'])
        with self.assertRaisesRegex(RuntimeError,'禁止后续'):self.exit.input_guard()

    def test_focus_loss_before_submission_discards_observation_and_retries_without_duplicate_post(self):
        self.interrupt=True
        self.on_wait=lambda:setattr(self,'focused',True)
        self.assertTrue(self.run_exit());self.assertEqual(self.attempts,2);self.assertEqual(self.posts,1)

    def test_focus_return_does_not_clear_manual_pause_before_close(self):
        self.focused=False;self.control.set_manual_pause(True)
        def resume():
            self.assertEqual(self.posts,0)
            self.focused=True
            if self.waits<3:self.assertTrue(self.control.manual_paused)
            else:self.control.set_manual_pause(False);self.on_wait=lambda:None
        self.on_wait=resume
        self.assertTrue(self.run_exit());self.assertEqual(self.posts,1)

    def test_stop_before_submission_does_not_close_window(self):
        self.focused=False;self.on_wait=self.cancelled.set
        self.assertFalse(self.run_exit());self.assertEqual(self.posts,0)
        self.assertFalse(self.exit.status()['requested'])

    def test_stop_after_submission_preserves_unverified_process_exit(self):
        self.exits_after=100;self.on_wait=self.cancelled.set
        self.assertFalse(self.run_exit());self.assertEqual(self.posts,1);self.assertTrue(self.closed)
        self.assertEqual(self.exit.status()['process_exited'],False)

    def test_unknown_exit_query_is_not_success_and_keeps_one_submission(self):
        def failed():raise AutomationError('fixture unknown process state')
        self.exited=failed
        with self.assertRaisesRegex(AutomationError,'unknown process'):self.run_exit()
        self.assertEqual(self.posts,1);self.assertTrue(self.closed)
        self.assertFalse(self.exit.status()['process_exited'])

    def test_uncertain_post_does_not_repeat_and_does_not_claim_process_exit(self):
        self.uncertain=True
        with self.assertRaisesRegex(RuntimeError,'未取得原进程'):self.run_exit()
        self.assertEqual(self.posts,1);self.assertFalse(self.exit.status()['process_exited'])

    def test_invalid_original_window_never_receives_request(self):
        self.valid=False
        self.assertFalse(self.run_exit());self.assertFalse(self.posts);self.assertFalse(self.attempts)

    def test_window_closing_before_post_returns_does_not_race_the_control_watcher(self):
        closed=threading.Event();watching=threading.Event()
        def observe():
            self.assertTrue(closed.wait(2));watching.set();self.control.poll()
        observer=threading.Thread(target=observe)
        def post():
            self.posts+=1;self.valid=False;closed.set();self.assertTrue(watching.wait(2));time.sleep(.05);return True
        self.post=post;observer.start()
        try:self.assertTrue(self.run_exit())
        finally:observer.join(2)
        self.assertFalse(observer.is_alive());self.assertFalse(self.control.error)
        self.assertEqual(self.posts,1);self.assertTrue(self.exit.status()['process_exited'])
