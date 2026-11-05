import ctypes
from ctypes import wintypes as W
from contextlib import ExitStack
from types import SimpleNamespace as NS
import threading
import time
import unittest
from unittest.mock import Mock,patch

from autozzz.global_key_events import KeyboardEdgeQueue,KeyEdge,ReadOnlyKeyboardEvents
from autozzz.global_capture_hotkeys import GlobalCaptureHotkeys


class FakeEvents:
    def __init__(self):self.queue=KeyboardEdgeQueue();self.state='listening';self.error='';self.closed=False
    def configure(self,combo,generation):self.queue.configure(combo,generation)
    def drain(self):return self.queue.drain()
    def status(self):return self.queue.status()|dict(state=self.state,error=self.error)
    def close(self):self.closed=True;self.queue.close()
    def join(self,timeout=None):pass


class EventHotkeyTests(unittest.TestCase):
    def setUp(self):
        self.now=0.;self.foreground=7;self.keys=set();self.requests=[];self.messages=[];self.source=FakeEvents()
        self.clock=patch('autozzz.global_capture_hotkeys.time.monotonic',lambda:self.now);self.clock.start();self.addCleanup(self.clock.stop)
        self.hotkeys=GlobalCaptureHotkeys(NS(after=lambda *_:1,after_cancel=lambda *_:None),
            lambda code:0x8000 if code in self.keys else 0,lambda:self.foreground,
            lambda *args:self.requests.append(args),self.messages.append,event_source=self.source)
        self.addCleanup(self.hotkeys.close)
        self.state=dict(settings=dict(key_screenshot='f11'),ready=True,foreground=True,
            window_identity=dict(hwnd=7),token='a'*64)
        self.requests[-1][2](self.state)

    def captures(self):return [item for item in self.requests if item[0]=='global_capture_capture']
    def pulse(self,start=.005,end=.015,foreground=7,flags=0):
        self.source.queue.feed(122,True,foreground=foreground,created=start,flags=flags)
        self.source.queue.feed(122,False,foreground=foreground,created=end,flags=flags)

    def test_short_injected_down_up_and_tk_stall_preserve_edge(self):
        self.pulse(flags=0x12);self.now=.3;self.hotkeys.tick()
        self.assertEqual(self.requests[-1][0],'global_capture_status')
        self.requests[-1][2](self.state)
        self.assertEqual(len(self.captures()),1)
        self.assertAlmostEqual(self.hotkeys.status()['max_tick_gap_ms'],300)
        self.assertEqual(self.hotkeys.status()['edges'],1)
        self.assertTrue(any('事件监听已启用' in message for message in self.messages))

    def test_first_press_after_focus_returns_refreshes_the_epoch(self):
        self.hotkeys.update(dict(self.state,foreground=False,epoch=1))
        self.pulse();self.now=.04;self.hotkeys.tick()
        self.assertEqual(len(self.captures()),0)
        self.assertEqual(self.requests[-1][0],'global_capture_status')
        self.requests[-1][2](dict(self.state,epoch=2,token='b'*64))
        self.assertEqual(len(self.captures()),1)
        self.assertEqual(self.captures()[0][1]['token'],'b'*64)

    def test_unknown_or_stale_target_requests_one_fresh_status_then_captures(self):
        self.hotkeys.update(dict(self.state,ready=False,window_identity=None))
        self.pulse();self.now=.04;self.hotkeys.tick()
        self.assertEqual(self.requests[-1][0],'global_capture_status');self.assertTrue(self.hotkeys.queued_capture)
        count=len(self.requests);self.hotkeys.tick();self.assertEqual(len(self.requests),count)
        self.now=.1;self.requests[-1][2](self.state)
        self.assertEqual(len(self.captures()),1)
        self.assertEqual(self.captures()[0][1]['token'],'a'*64)

    def test_fresh_status_not_ready_rejects_without_retry_loop(self):
        self.pulse();self.now=.04;self.hotkeys.tick();status=self.requests[-1]
        status[2](dict(self.state,ready=False))
        self.assertFalse(self.hotkeys.queued_capture);self.assertEqual(len(self.captures()),0)
        count=len(self.requests);self.hotkeys.tick();self.assertEqual(len(self.requests),count)

    def test_key_edge_during_status_wait_replaces_pre_edge_status_once(self):
        self.hotkeys.next_status=0;self.hotkeys.tick();status=self.requests[-1]
        self.pulse();self.now=.04;self.hotkeys.tick();self.assertTrue(self.hotkeys.queued_capture)
        status[2](dict(self.state,token='b'*64))
        self.assertEqual(len(self.captures()),0)
        fresh=self.requests[-1]
        self.assertEqual(fresh[0],'global_capture_status');self.assertIsNot(fresh,status)
        fresh[2](dict(self.state,token='c'*64));self.assertEqual(len(self.captures()),1)
        self.assertEqual(self.captures()[0][1]['token'],'c'*64)

    def test_f10_short_pulse_cancels_pending_even_after_release(self):
        self.pulse();self.now=.04;self.hotkeys.tick()
        status=self.requests[-1]
        self.source.queue.feed(121,True,foreground=7,created=.05)
        self.source.queue.feed(121,False,foreground=7,created=.06)
        # Even without draining the cancel event first, the monotonic epoch rejects it.
        self.now=.1;status[2](self.state)
        self.assertEqual(len(self.captures()),0);self.assertFalse(self.hotkeys.queued_capture)

    def test_event_age_config_change_and_foreign_focus_do_not_capture(self):
        self.pulse();self.now=2;self.hotkeys.tick();self.assertEqual(len(self.captures()),0)
        if self.hotkeys.pending_kind=='status':self.requests[-1][2](self.state)
        self.pulse(2.005,2.015);self.hotkeys.update(dict(self.state,settings=dict(key_screenshot='F12')))
        self.now=2.04;self.hotkeys.tick();self.assertEqual(len(self.captures()),0)
        self.source.queue.feed(123,True,foreground=9,created=2.05);self.source.queue.feed(123,False,foreground=9,created=2.06)
        self.now=2.1;self.hotkeys.tick();self.assertEqual(len(self.captures()),0)

    def test_hook_failure_explicitly_degrades_and_polling_still_works(self):
        self.source.state='failed';self.source.error='fixture installation failure'
        self.keys.add(122);self.now=.04;self.hotkeys.tick()
        self.assertEqual(self.requests[-1][0],'global_capture_status')
        self.requests[-1][2](self.state)
        self.assertEqual(len(self.captures()),1)
        self.assertTrue(any('降级为轮询' in message and 'fixture installation failure' in message for message in self.messages))

    def test_close_rejects_late_status_and_event_and_joins_listener(self):
        self.pulse();count=len(self.requests);self.hotkeys.close();self.hotkeys.update(self.state);self.hotkeys.tick()
        self.assertEqual(len(self.requests),count);self.assertTrue(self.source.closed)
        self.hotkeys.join_listener();self.assertFalse(self.source.drain())


class KeyboardQueueTests(unittest.TestCase):
    def test_repeat_exact_chord_right_modifier_and_no_unrelated_text_retained(self):
        queue=KeyboardEdgeQueue();queue.configure((17,122),1)
        queue.feed(65,True,foreground=7)
        queue.feed(17,True,foreground=7,flags=1)  # Right Ctrl.
        queue.feed(122,True,foreground=7,created=1);queue.feed(122,True,foreground=7,created=1.1)
        queue.feed(122,False,foreground=7,created=1.2)
        self.assertEqual(len(queue.drain()),1);self.assertNotIn(65,queue.pressed)
        self.assertEqual(queue.status()['ignored_repeats'],1)
        queue.feed(160,True,foreground=7);queue.feed(122,True,foreground=7)
        self.assertFalse(queue.drain())

    def test_config_while_main_key_held_suppresses_until_release_and_queue_bounded(self):
        queue=KeyboardEdgeQueue(limit=2);queue.configure((122,),4,pressed=(122,))
        queue.feed(122,True,foreground=7);self.assertFalse(queue.drain())
        for index in range(4):
            queue.feed(122,False,foreground=7);queue.feed(122,True,foreground=7,created=index)
        self.assertEqual(len(queue.drain()),2);self.assertEqual(queue.dropped,2)
        queue.configure((123,),5);self.assertFalse(queue.drain())
        queue.close();queue.feed(123,True,foreground=7);self.assertFalse(queue.drain())


class HookLifecycleTests(unittest.TestCase):
    def test_controller_worker_waits_for_listener_join_and_does_not_close_borrowed_task(self):
        from autozzz.global_capture_ui import GlobalCaptureController
        from tests.test_global_capture_routes import Client
        class BlockingEvents(FakeEvents):
            def __init__(self):super().__init__();self.entered=threading.Event();self.release=threading.Event()
            def join(self,timeout=None):self.entered.set();self.release.wait(2)
        source=BlockingEvents();ui=NS(root=NS(after=lambda *_:1,after_cancel=lambda *_:None),server=NS(get=lambda:'国服'),
            game_path=NS(get=lambda:'cn.exe'),_log=Mock(),running=False,runner=None)
        with patch('autozzz.global_key_events.ReadOnlyKeyboardEvents',return_value=source),\
                patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0),\
                patch('autozzz.global_capture_ui.OneDragonClient',Client),\
                patch('autozzz.global_capture_ui.RuntimePaths.discover',return_value='fixture'):
            controller=GlobalCaptureController(ui)
            try:
                controller.close();self.assertTrue(source.entered.wait(2))
                self.assertTrue(controller.worker.is_alive());self.assertTrue(source.closed)
                source.release.set();controller.worker.join(2);self.assertFalse(controller.worker.is_alive())
            finally:source.release.set();controller.close();controller.worker.join(2)

    def api(self,install=True):
        user=NS(**{name:Mock() for name in ('SetWindowsHookExW','CallNextHookEx','UnhookWindowsHookEx','GetForegroundWindow',
            'PeekMessageW','TranslateMessage','DispatchMessageW','PostThreadMessageW','MsgWaitForMultipleObjects','GetAsyncKeyState')})
        user.SetWindowsHookExW.return_value=41 if install else 0;user.CallNextHookEx.return_value=88
        user.UnhookWindowsHookEx.return_value=True;user.GetForegroundWindow.return_value=7
        user.PeekMessageW.return_value=False;user.PostThreadMessageW.return_value=False;user.GetAsyncKeyState.return_value=0
        user.MsgWaitForMultipleObjects.side_effect=lambda *_:time.sleep(.005) or 258
        kernel=NS(GetCurrentThreadId=Mock(return_value=42),GetModuleHandleW=Mock(return_value=1))
        return user,kernel

    def test_hook_always_calls_next_for_press_release_unknown_negative_and_bad_data(self):
        user,kernel=self.api()
        with patch('autozzz.global_key_events.ctypes.WinDLL',side_effect=lambda name,**_:user if name=='user32' else kernel):
            source=ReadOnlyKeyboardEvents()
            try:
                self.assertTrue(source.ready.wait(2));self.assertTrue(source.status()['installed']);source.configure((122,),1)
                class Data(ctypes.Structure):_fields_=[('vkCode',W.DWORD),('scanCode',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('extra',ctypes.c_size_t)]
                data=Data(122,0,0x10,1,0);address=ctypes.addressof(data)
                self.assertEqual(source._callback(0,0x100,address),88)
                self.assertEqual(source._callback(0,0x101,address),88)
                self.assertEqual(source._callback(-1,0x100,0),88)
                self.assertEqual(source._callback(0,0x100,0),88)
                data.vkCode=65;self.assertEqual(source._callback(0,0x100,address),88)
                self.assertEqual(user.CallNextHookEx.call_count,5);self.assertEqual(len(source.drain()),1)
                self.assertIn('事件处理失败',source.status()['error'])
            finally:source.close();source.join(2)
            self.assertFalse(source.worker.is_alive());user.UnhookWindowsHookEx.assert_called_once_with(41)
            source.close();self.assertIsNone(source._thread_id)
            user.PostThreadMessageW.assert_called_once();self.assertEqual(source.status()['state'],'closed')

    def test_failed_install_is_visible_and_close_finishes(self):
        user,kernel=self.api(install=False)
        with patch('autozzz.global_key_events.ctypes.WinDLL',side_effect=lambda name,**_:user if name=='user32' else kernel):
            source=ReadOnlyKeyboardEvents();self.assertTrue(source.ready.wait(2));source.close();source.join(2)
        self.assertEqual(source.status()['state'],'failed');self.assertTrue(source.status()['error'])
        self.assertFalse(source.worker.is_alive());user.UnhookWindowsHookEx.assert_not_called()


if __name__=='__main__':unittest.main()
