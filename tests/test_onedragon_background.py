from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import threading

from autozzz.onedragon_background import ForegroundBorrow,BackgroundMouse,PhysicalForeground,background_mouse_policy
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


class PhysicalForegroundTests(unittest.TestCase):
    def setUp(self):
        self.desktop=Desktop();self.events=[]
        class Device:
            def __init__(device):device.pressed=set()
            def release(device,key):self.events.append(('release',key,self.desktop.current()));device.pressed.remove(key)
        self.keyboard,self.mouse=Device(),Device()
        self.controller=NS(_physical_input_lock=threading.RLock(),_physical_input_owner=None,_foreground_required=False,_foreground_borrowing=False,
                           keyboard_controller=NS(keyboard=self.keyboard,mouse=self.mouse),game_win=NS(bound=NS(hwnd=2)),desktop=self.desktop,
                           xbox_controller=NS(reset=lambda:self.events.append(('neutral',))),ds4_controller=None,
                           _prepare_physical_mode=lambda:self.events.append(('prepare',self.desktop.current())))
        def probe():return WindowState(self.desktop.identity(2)==(20,22),not self.controller._foreground_required or self.desktop.current()==2,(0,0,1920,1080))
        self.control=ExecutionControl(probe,lambda:None,lambda:True,lambda:None,lambda:None)
        self.controller.control=self.control;self.control.observe(lambda:None)

    def test_releases_scope_keys_before_restoring_window(self):
        with PhysicalForeground(self.controller):
            self.keyboard.pressed.add('ctrl')
            self.assertTrue(self.controller._foreground_borrowing)
        self.assertIn(('release','ctrl',2),self.events)
        self.assertEqual(self.desktop.calls,[2,1]);self.assertFalse(self.controller._foreground_borrowing)
        self.assertIsNone(self.controller._physical_input_owner)

    def test_nested_text_scope_keeps_modifier_and_foreground_until_outer_exit(self):
        with PhysicalForeground(self.controller):
            self.keyboard.pressed.add('ctrl')
            with PhysicalForeground(self.controller):self.assertEqual(self.desktop.current(),2)
            self.assertIn('ctrl',self.keyboard.pressed)
            self.assertTrue(self.controller._foreground_borrowing)
        self.assertEqual(self.desktop.calls,[2,1]);self.assertFalse(self.keyboard.pressed)

    def test_pause_before_scope_does_not_activate_or_reset_devices(self):
        self.control.set_manual_pause(True)
        with self.assertRaises(ObservationExpired),PhysicalForeground(self.controller):pass
        self.assertFalse(self.desktop.calls);self.assertFalse(self.events)

    def test_stop_inside_scope_still_releases_and_restores(self):
        with PhysicalForeground(self.controller):
            self.mouse.pressed.add('left');self.control.stop()
        self.assertEqual(self.desktop.current(),1);self.assertFalse(self.mouse.pressed)
        self.assertTrue(self.control.stopped)

    def test_user_foreground_change_blocks_remaining_input_without_overriding_choice(self):
        with self.assertRaises(ObservationExpired),PhysicalForeground(self.controller):
            self.desktop.foreground=3
            self.control.input(lambda:self.fail('input escaped foreground scope'))
        self.assertEqual(self.desktop.current(),3);self.assertEqual(self.desktop.calls,[2])

    def test_reused_target_cannot_receive_more_input_or_restore_old_owner(self):
        with PhysicalForeground(self.controller):self.desktop.ids[2]=(999,888)
        self.assertEqual(self.desktop.calls,[2]);self.control.poll();self.assertTrue(self.control.stopped)

    def test_prepare_failure_cleans_scope_and_restores_window(self):
        def fail():raise ValueError('fixture prepare rejected')
        self.controller._prepare_physical_mode=fail
        with self.assertRaises(ValueError),PhysicalForeground(self.controller):pass
        self.assertEqual(self.desktop.calls,[2,1]);self.assertIsNone(self.controller._physical_input_owner)

    def test_release_failure_stops_input_but_still_restores_window(self):
        def fail(_):raise OSError('fixture release failure')
        self.keyboard.release=fail
        with self.assertRaises(OSError),PhysicalForeground(self.controller):self.keyboard.pressed.add('ctrl')
        self.assertTrue(self.control.stopped);self.assertEqual(self.desktop.current(),1)

    def test_explicit_wait_setting_is_preserved_and_unconfigured_background_defaults_to_flash(self):
        self.assertEqual(background_mouse_policy({}),'flash')
        self.assertEqual(background_mouse_policy({'autozzz_background_mouse':'wait'}),'wait')
        with self.assertRaises(ValueError):background_mouse_policy({'autozzz_background_mouse':'unknown'})

    def test_stopped_waiting_thread_cannot_borrow_after_owner_exits(self):
        started=threading.Event();finished=[];errors=[]
        def waiter():
            started.set()
            try:
                with PhysicalForeground(self.controller):finished.append('unexpected input')
            except Exception as exc:errors.append(exc)
        with PhysicalForeground(self.controller):
            thread=threading.Thread(target=waiter);thread.start();self.assertTrue(started.wait(1));self.control.stop()
            thread.join(1);self.assertFalse(thread.is_alive())
        self.assertFalse(finished);self.assertEqual(len(errors),1);self.assertEqual(self.desktop.calls,[2,1])


if __name__=='__main__':unittest.main()
