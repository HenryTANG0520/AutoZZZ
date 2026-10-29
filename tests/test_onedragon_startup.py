from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from autozzz.models import AutomationError
from autozzz.onedragon_startup import GameStartup


class Flag:
    def __init__(self,wait=lambda seconds:None):self.value=False;self.waited=wait
    def set(self):self.value=True
    def clear(self):self.value=False
    def is_set(self):return self.value
    def wait(self,seconds):self.waited(seconds);return self.value


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'ZenlessZoneZero.exe';self.path.write_bytes(b'fixture')
        self.time=0.;self.active=0.;self.ticks=0;self.launches=[];self.phases=[]
        self.cancel=Flag(self.advance);self.pause=Flag()
        self.process=dict(pid=20,created=123.,path=str(self.path))
    def advance(self,seconds):self.time+=seconds
    def elapsed(self,seconds):
        self.active+=seconds
        if self.active>=.4:self.cancel.set()
    def poll(self):
        self.ticks+=1
        if self.ticks>30:raise AssertionError('fixture startup did not finish')
    def missing(self,path):raise AutomationError('fixture missing window')
    def start(self,**extra):
        values=dict(cancelled=self.cancel,paused=self.pause,poll_keys=self.poll,elapsed=self.elapsed,phase=self.phases.append,
                    inventory=lambda:[],find_window=self.missing,launch=self.launches.append,clock=lambda:self.time)
        values.update(extra);return GameStartup(self.path,**values)

    def test_existing_matching_window_never_launches_or_activates(self):
        result=self.start(inventory=lambda:[self.process],find_window=lambda _:SimpleNamespace(hwnd=200)).run(True)
        self.assertEqual(result,dict(window_handle=200,process_id=20));self.assertFalse(self.launches)

    def test_existing_process_without_window_is_waited_without_relaunch(self):
        def window(_):
            if self.ticks<3:return self.missing(_)
            return SimpleNamespace(hwnd=200)
        self.assertIsNotNone(self.start(inventory=lambda:[self.process],find_window=window).run(True))
        self.assertFalse(self.launches)

    def test_new_launch_is_issued_once_even_when_window_never_appears(self):
        startup=self.start()
        self.assertIsNone(startup.run(True));self.assertTrue(startup.launched)
        self.assertEqual(self.launches,[str(self.path)])

    def test_started_game_must_match_window_before_returning_identity(self):
        startup=self.start(inventory=lambda:[self.process] if self.launches else [],find_window=lambda _:SimpleNamespace(hwnd=200))
        self.assertEqual(startup.run(True),dict(window_handle=200,process_id=20))
        self.assertEqual(len(self.launches),1)

    def test_different_client_and_duplicate_processes_refuse_launch(self):
        for rows in ([dict(self.process,path=str(self.path.parent/'other/ZenlessZoneZero.exe'))],[self.process,self.process]):
            with self.subTest(rows=rows),self.assertRaises(AutomationError):self.start(inventory=lambda:rows).run(True)
        self.assertFalse(self.launches)

    def test_process_exit_or_identity_change_does_not_relaunch(self):
        def inventory():return [self.process] if self.ticks==1 else [dict(self.process,created=456.)]
        with self.assertRaisesRegex(AutomationError,'进程已退出或变化'):self.start(inventory=inventory).run(True)
        self.assertFalse(self.launches)

    def test_stop_during_inventory_prevents_launch(self):
        def inventory():self.cancel.set();return []
        self.assertIsNone(self.start(inventory=inventory).run(True));self.assertFalse(self.launches)

    def test_pause_and_rejected_action_are_retained_without_spending_active_time(self):
        attempts=[]
        def poll():
            self.poll()
            if self.ticks==1:self.pause.set()
            elif self.ticks==4:self.pause.clear()
        def attempt(action):
            attempts.append(self.active)
            if len(attempts)==1:return False
            action();return True
        startup=self.start(poll_keys=poll,attempt=attempt)
        self.assertIsNone(startup.run(True));self.assertIn('已暂停',self.phases)
        self.assertEqual(attempts[:2],[0.,0.]);self.assertEqual(len(self.launches),1)
        self.assertGreater(self.time,self.active)

    def test_unchecked_auto_launch_does_not_start_missing_game(self):
        with self.assertRaisesRegex(AutomationError,'游戏尚未启动'):self.start().run(False)
        self.assertFalse(self.launches)

    def test_unknown_launch_result_is_not_retried(self):
        calls=[]
        def launch(_):calls.append(1);raise RuntimeError('fixture uncertain launch')
        startup=self.start(launch=launch)
        with self.assertRaisesRegex(RuntimeError,'uncertain'):startup.run(True)
        self.assertTrue(startup.launched);self.assertEqual(calls,[1])
