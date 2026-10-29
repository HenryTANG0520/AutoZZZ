from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.onedragon_background import ForegroundBorrow,BackgroundMouse
from autozzz.onedragon_control import ExecutionControl,WindowState,ObservationExpired


class Desktop:
    def __init__(self):self.foreground=1;self.ids={1:(10,11),2:(20,22),3:(30,33)};self.calls=[];self.failure=None
    def current(self):return self.foreground
    def identity(self,hwnd):return self.ids.get(hwnd)
    def activate(self,hwnd):
        self.calls.append(hwnd);self.foreground=hwnd
        if self.failure==hwnd:raise OSError('fixture activation failure')


class ForegroundTests(unittest.TestCase):
    def setUp(self):
        self.desktop=Desktop();self.control=ExecutionControl(lambda:WindowState(True,True,(0,0,1920,1080)),lambda:None,lambda:True,lambda:None,lambda:None)
        self.control.observe(lambda:None)
    def test_restores_original_exact_owner(self):
        with ForegroundBorrow(self.control,2,self.desktop):self.assertEqual(self.desktop.current(),2)
        self.assertEqual(self.desktop.calls,[2,1])
    def test_pause_before_activation_has_no_side_effect(self):
        self.control.set_manual_pause(True)
        with self.assertRaises(ObservationExpired):
            with ForegroundBorrow(self.control,2,self.desktop):pass
        self.assertFalse(self.desktop.calls)
    def test_stop_inside_still_restores_previous_window(self):
        with ForegroundBorrow(self.control,2,self.desktop):self.control.stop()
        self.assertEqual(self.desktop.current(),1)
    def test_user_new_foreground_choice_is_preserved(self):
        with ForegroundBorrow(self.control,2,self.desktop):self.desktop.foreground=3
        self.assertEqual(self.desktop.current(),3);self.assertEqual(self.desktop.calls,[2])
    def test_reused_previous_handle_is_not_activated(self):
        with ForegroundBorrow(self.control,2,self.desktop):self.desktop.ids[1]=(999,888)
        self.assertEqual(self.desktop.calls,[2])
    def test_unverifiable_original_window_does_not_switch(self):
        self.desktop.ids.pop(1)
        with self.assertRaises(RuntimeError):
            with ForegroundBorrow(self.control,2,self.desktop):pass
        self.assertFalse(self.desktop.calls)
    def test_partial_activation_failure_restores_without_retry(self):
        self.desktop.failure=2
        with self.assertRaises(OSError):
            with ForegroundBorrow(self.control,2,self.desktop):pass
        self.assertEqual(self.desktop.calls,[2,1])
    def test_already_foreground_does_not_activate(self):
        self.desktop.foreground=2
        with ForegroundBorrow(self.control,2,self.desktop):pass
        self.assertFalse(self.desktop.calls)

    def test_failed_frame_releases_and_rescopes_backend(self):
        events=[]
        self.control.release=lambda:events.append(('release',self.control.epoch))
        self.control.pause_backend=lambda:events.append(('pause',self.control.task_epoch)) or True
        self.control.resume_backend=lambda:events.append(('resume',self.control.task_epoch))
        old=self.control.epoch
        with self.control.scope(old):self.control.discard_frame()
        self.assertEqual(events,[('release',old+1),('pause',old+1),('resume',old+1)])
        self.assertFalse(self.control.status()['fresh_frame'])
        with self.assertRaises(ObservationExpired):self.control.input(lambda:None)
        self.control.observe(lambda:None);self.control.input(lambda:events.append(('fresh',)))

    def test_failed_frame_does_not_cancel_manual_pause(self):
        self.control.set_manual_pause(True);self.control.discard_frame()
        self.assertTrue(self.control.status()['manual_paused'])

    def test_verified_restore_despite_api_error_is_not_retried(self):
        self.desktop.failure=1
        with ForegroundBorrow(self.control,2,self.desktop):pass
        self.assertEqual(self.desktop.calls,[2,1]);self.assertFalse(self.control.stopped)

    def test_failed_restore_stops_later_input(self):
        original=self.desktop.activate
        def activate(hwnd):
            if hwnd==1:self.desktop.calls.append(hwnd);return
            original(hwnd)
        self.desktop.activate=activate
        with self.assertRaises(RuntimeError):
            with ForegroundBorrow(self.control,2,self.desktop):pass
        self.assertTrue(self.control.stopped);self.assertIn('前台',self.control.error)


class BackgroundMouseTests(unittest.TestCase):
    def setUp(self):
        self.window=NS(bound=NS(hwnd=100),process_id=200,identity_valid=lambda:True)
        self.witness=[];self.messages=[]
        self.controller=NS(game_win=self.window,_witness_mouse=lambda:self.witness.append(True))
        self.mouse=BackgroundMouse(self.controller)
        self.control=ExecutionControl(lambda:WindowState(True,True,(0,0,1920,1080)),self.mouse.reset,lambda:True,lambda:None,lambda:None)
        self.controller.control=self.control;self.control.observe(lambda:None)
        self.patch=patch('autozzz.windows.user32.PostMessageW',side_effect=lambda *args:self.messages.append(args) or True);self.patch.start();self.addCleanup(self.patch.stop)
    def test_attempt_is_witnessed_and_stop_only_releases(self):
        self.mouse.press();self.control.stop()
        self.assertEqual(self.witness,[True]);self.assertEqual([item[1] for item in self.messages],[0x201,0x202])
        self.assertIsNone(self.mouse.down)
    def test_stopped_control_cannot_submit_mouse_down(self):
        self.control.stop()
        with self.assertRaises(RuntimeError):self.mouse.press()
        self.assertFalse(self.messages);self.assertFalse(self.witness)
    def test_replacement_window_never_receives_release(self):
        self.mouse.press();self.window.process_id=999;self.mouse.reset()
        self.assertEqual(len(self.messages),1);self.assertIsNone(self.mouse.down)
    def test_witness_rejection_has_no_post(self):
        def fail():raise ValueError('fixture authorization rejected')
        self.controller._witness_mouse=fail
        with self.assertRaises(ValueError):self.mouse.press()
        self.assertFalse(self.messages);self.assertIsNone(self.mouse.down)
    def test_failed_release_can_retry_without_second_down(self):
        self.mouse.press()
        with patch('autozzz.windows.user32.PostMessageW',return_value=False):
            with self.assertRaises(RuntimeError):self.mouse.reset()
        self.assertIsNotNone(self.mouse.down)
        with self.assertRaises(RuntimeError):self.mouse.press()
        self.mouse.reset();self.assertIsNone(self.mouse.down)


if __name__=='__main__':unittest.main()
