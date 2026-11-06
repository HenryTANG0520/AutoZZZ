from contextlib import contextmanager, nullcontext
from pathlib import Path
import tempfile
from threading import Lock, get_native_id
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


class WindowAPI:
    def __init__(self):
        self.foreground=100
        self.owners={100:10,200:20}
        self.threads={100:101,200:202}
        self.requests=[]
        self.attachments=[]
        self.brought=[]
        self.on_probe=lambda _:None
        self.on_request=lambda _:False
        self.on_attach=lambda _thread,_join:False
        self.on_bring=lambda _:False

    def GetForegroundWindow(self):
        return self.foreground

    def GetWindowThreadProcessId(self,hwnd,pid):
        self.on_probe(hwnd)
        if hwnd not in self.owners:
            return 0
        pid._obj.value=self.owners[hwnd]
        return self.threads.get(hwnd,hwnd)

    def SetForegroundWindow(self,hwnd):
        self.requests.append(hwnd)
        return self.on_request(hwnd)

    def AttachThreadInput(self,current,thread,join):
        self.attachments.append((current,thread,join))
        return self.on_attach(thread,join)

    def BringWindowToTop(self,hwnd):
        self.brought.append(hwnd)
        return self.on_bring(hwnd)

    def activate(self,hwnd):
        self.foreground=hwnd
        return True


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

    def new_client(self,*,launch_pid=20,exit_code=None,poll_process=None,**extra):
        process=SimpleNamespace(pid=launch_pid,poll=poll_process or (lambda:exit_code))
        def launch(path):
            self.launches.append(path)
            return process
        api=WindowAPI()
        values=dict(launch=launch,window_api=api,inventory=lambda:[self.process] if self.launches else [],
                    find_window=lambda _:SimpleNamespace(hwnd=200))
        values.update(extra)
        return self.start(**values),api

    def test_existing_matching_window_never_launches_or_activates(self):
        api=WindowAPI()
        result=self.start(inventory=lambda:[self.process],find_window=lambda _:SimpleNamespace(hwnd=200),window_api=api).run(True)
        self.assertEqual(result,dict(window_handle=200,process_id=20));self.assertFalse(self.launches)
        self.assertEqual(api.requests,[])

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

    def test_actual_launch_session_result_can_authorize_one_foreground_request(self):
        startup,api=self.new_client(launch=None)
        api.on_request=api.activate
        def popen(argv,*,cwd,shell):
            self.assertEqual(cwd,str(self.path.parent))
            self.assertFalse(shell)
            self.launches.extend(argv)
            return SimpleNamespace(pid=20,poll=lambda:None)
        startup.launch_session.store=SimpleNamespace(lock=nullcontext,active=self.path.parent/'hdr.json')
        startup.launch_session.popen=popen
        self.assertEqual(startup.run(True),dict(window_handle=200,process_id=20))
        self.assertEqual(self.launches,[str(self.path)])
        self.assertEqual(api.requests,[200])
        self.assertEqual(api.attachments,[])
        self.assertEqual(startup.focus_observation['foreground_before'],100)
        self.assertTrue(startup.focus_observation['primary_accepted'])

    def test_foreground_request_crossing_deadline_stops_before_fallback_or_window_handoff(self):
        for accepted in (False,True):
            with self.subTest(accepted=accepted):
                self.launches.clear();self.cancel.clear();self.active=0.
                startup,api=self.new_client()
                def request(hwnd):
                    self.advance(.5)
                    return api.activate(hwnd) if accepted else False
                api.on_request=request
                api.on_attach=lambda *_:True
                self.assertIsNone(startup.run(True))
                self.assertTrue(self.cancel.is_set())
                self.assertEqual(api.requests,[200])
                self.assertEqual(api.attachments,[])

    def test_inventory_crossing_deadline_never_launches(self):
        def inventory():
            self.advance(.5)
            return []
        self.assertIsNone(self.start(inventory=inventory).run(True))
        self.assertTrue(self.cancel.is_set())
        self.assertEqual(self.launches,[])

    def test_launch_guard_crossing_deadline_never_submits_popen(self):
        @contextmanager
        def lock():
            self.advance(.5)
            yield

        startup,_=self.new_client(launch=None)
        startup.launch_session.store=SimpleNamespace(lock=lock,active=self.path.parent/'hdr.json')
        startup.launch_session.popen=lambda *args,**kwargs:self.fail('late Popen')
        self.assertIsNone(startup.run(True))
        self.assertTrue(self.cancel.is_set())

    def test_refused_foreground_request_is_not_repeated_after_pause(self):
        def poll():
            self.poll()
            if self.ticks==5:self.pause.clear()
        startup,api=self.new_client(poll_keys=poll)
        api.on_request=lambda _:self.pause.set()
        self.assertEqual(startup.run(True),dict(window_handle=200,process_id=20))
        self.assertIn('已暂停',self.phases)
        self.assertEqual(api.requests,[200])
        self.assertEqual(api.attachments,[])

    def test_foreground_request_leaves_launch_serialization_available_to_stop(self):
        launch_lock = Lock()

        def attempt(action):
            with launch_lock:
                action()
                return True

        startup, api = self.new_client(attempt=attempt)

        def request(_hwnd):
            self.assertFalse(launch_lock.locked())
            self.assertEqual(startup.focus_observation['phase'], 'primary_pending')
            self.cancel.set()
            return False

        api.on_request = request
        self.assertIsNone(startup.run(True))
        self.assertEqual(self.launches, [str(self.path)])
        self.assertEqual(api.requests, [200])
        self.assertEqual(api.attachments, [])

    def test_scoped_fallback_activates_once_and_detaches_both_threads(self):
        startup,api=self.new_client()
        api.on_attach=lambda _thread,_join:True
        current=get_native_id()
        joins=[(current,101,True),(current,202,True)]
        def request(hwnd):
            if len(api.requests)==1:
                self.assertEqual(api.attachments,[])
                return False
            self.assertEqual(api.attachments,joins)
            return api.activate(hwnd)
        api.on_request=request
        self.assertEqual(startup.run(True),dict(window_handle=200,process_id=20))
        self.assertEqual(api.foreground,200)
        self.assertEqual(api.requests,[200,200])
        self.assertEqual(api.brought,[200])
        self.assertEqual(api.attachments,joins+[(current,202,False),(current,101,False)])
        self.assertFalse(startup.focus_observation['primary_accepted'])
        self.assertTrue(startup.focus_observation['fallback_accepted'])

    def test_fallback_does_not_reactivate_after_bring_to_top_focuses_game(self):
        startup,api=self.new_client()
        api.on_attach=lambda _thread,_join:True
        api.on_bring=api.activate
        startup.run(True)
        self.assertEqual(api.foreground,200)
        self.assertEqual(api.requests,[200])
        self.assertEqual(api.brought,[200])
        self.assertEqual([row[2] for row in api.attachments],[True,True,False,False])

    def test_generation_change_during_first_attach_stops_and_detaches(self):
        generation_valid = True

        def guard():
            if not generation_valid:
                raise ValueError('stale runtime generation')

        startup, api = self.new_client(guard=guard)

        def attach(_thread, join):
            nonlocal generation_valid
            if join:
                generation_valid = False
            return True

        api.on_attach = attach
        with self.assertRaisesRegex(ValueError, 'stale runtime generation'):
            startup.run(True)
        current = get_native_id()
        self.assertEqual(api.attachments, [(current, 101, True), (current, 101, False)])
        self.assertEqual(api.requests, [200])
        self.assertEqual(api.brought, [])

    def test_fallback_refusal_returns_bound_identity_without_more_requests(self):
        startup,api=self.new_client()
        api.on_attach=lambda _thread,_join:True
        self.assertEqual(startup.run(True),dict(window_handle=200,process_id=20))
        self.assertEqual(api.foreground,100)
        self.assertEqual(api.requests,[200,200])
        self.assertEqual(api.brought,[200])
        self.assertEqual([row[2] for row in api.attachments],[True,True,False,False])

    def test_user_switch_before_or_during_attachment_abandons_fallback(self):
        for stage in ('ordinary','attach','bring'):
            with self.subTest(stage=stage):
                self.launches.clear()
                startup,api=self.new_client()
                def switch():api.foreground=300
                def attach(thread,join):
                    if stage=='attach' and join:switch()
                    return True
                api.on_request=lambda _:switch() if stage=='ordinary' else False
                api.on_bring=lambda _:switch() if stage=='bring' else False
                api.on_attach=attach
                startup.run(True)
                self.assertEqual(api.foreground,300)
                self.assertEqual(api.requests,[200])
                self.assertEqual(api.brought,[200] if stage=='bring' else [])
                pairs=[(thread,join) for _,thread,join in api.attachments]
                expected={'ordinary':[],'attach':[(101,True),(101,False)],
                          'bring':[(101,True),(202,True),(202,False),(101,False)]}
                self.assertEqual(pairs,expected[stage])

    def test_fallback_refuses_reused_foreground_and_exited_game(self):
        for change in ('foreground_owner','game_exited','game_window_owner'):
            with self.subTest(change=change):
                self.launches.clear()
                exited=Flag()
                startup,api=self.new_client(poll_process=lambda:0 if exited.is_set() else None)
                def request(_):
                    if change=='foreground_owner':api.threads[100]=303
                    elif change=='game_exited':exited.set()
                    else:api.owners[200]=21
                    return False
                api.on_request=request
                startup.run(True)
                self.assertEqual(api.requests,[200])
                self.assertEqual(api.attachments,[])
                self.assertEqual(api.brought,[])

    def test_fallback_rechecks_process_creation_path_and_generation(self):
        for change in ('creation','path','generation'):
            with self.subTest(change=change):
                self.launches.clear()
                def inventory():
                    if not self.launches:return []
                    if not api.requests:return [self.process]
                    if change=='creation':return [dict(self.process,created=456.)]
                    if change=='path':return [dict(self.process,path=str(self.path.parent/'other'/self.path.name))]
                    return [self.process]
                def guard():
                    if change=='generation' and api.requests:raise ValueError('fixture stale generation')
                startup,api=self.new_client(inventory=inventory,guard=guard)
                if change=='generation':
                    with self.assertRaisesRegex(ValueError,'stale generation'):startup.run(True)
                else:
                    startup.run(True)
                self.assertEqual(api.requests,[200])
                self.assertEqual(api.attachments,[])
                self.assertEqual(api.brought,[])

    def test_failed_second_attachment_detaches_first_and_avoids_input(self):
        startup,api=self.new_client()
        api.on_attach=lambda thread,join:not (thread==202 and join)
        startup.run(True)
        self.assertEqual(api.requests,[200])
        self.assertEqual(api.brought,[])
        self.assertEqual([(thread,join) for _,thread,join in api.attachments],[(101,True),(202,True),(101,False)])

    def test_fallback_detaches_both_threads_if_native_call_raises(self):
        startup,api=self.new_client()
        api.on_attach=lambda _thread,_join:True
        def fail(_):raise OSError('fixture window failure')
        api.on_bring=fail
        with self.assertRaisesRegex(OSError,'window failure'):startup.run(True)
        self.assertEqual(api.requests,[200])
        self.assertEqual([(thread,join) for _,thread,join in api.attachments],
                         [(101,True),(202,True),(202,False),(101,False)])

    def test_stop_during_attachment_detaches_before_returning(self):
        startup,api=self.new_client()
        def attach(thread,join):
            if thread==202 and join:self.cancel.set()
            return True
        api.on_attach=attach
        self.assertIsNone(startup.run(True))
        self.assertEqual(api.requests,[200])
        self.assertEqual(api.brought,[])
        self.assertEqual([(thread,join) for _,thread,join in api.attachments],
                         [(101,True),(202,True),(202,False),(101,False)])

    def test_new_process_already_in_foreground_is_left_in_its_current_window(self):
        startup,api=self.new_client()
        api.foreground=201
        api.owners[201]=20
        self.assertEqual(startup.run(True),dict(window_handle=200,process_id=20))
        self.assertEqual(api.requests,[])

    def test_other_same_path_process_and_exited_launch_are_never_activated(self):
        for options in (dict(launch_pid=21),dict(exit_code=0)):
            with self.subTest(options=options):
                self.launches.clear()
                startup,api=self.new_client(**options)
                self.assertEqual(startup.run(True),dict(window_handle=200,process_id=20))
                self.assertEqual(api.requests,[])

    def test_hwnd_owned_by_another_process_cannot_be_activated(self):
        startup,api=self.new_client()
        api.owners[200]=21
        startup.run(True)
        self.assertEqual(api.requests,[])

    def test_inventory_creation_and_path_are_rechecked_before_activation(self):
        replacements=(dict(self.process,created=456.),dict(self.process,path=str(self.path.parent/'other'/self.path.name)))
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                self.launches.clear()
                rechecks=[]
                def inventory():
                    if not self.launches:
                        return []
                    rechecks.append(True)
                    return [self.process if len(rechecks)==1 else replacement]
                startup,api=self.new_client(inventory=inventory)
                startup.run(True)
                self.assertEqual(len(rechecks),2)
                self.assertEqual(api.requests,[])

    def test_stop_key_is_polled_again_before_foreground_request(self):
        def poll():
            self.poll()
            if self.ticks==3:self.cancel.set()
        startup,api=self.new_client(poll_keys=poll)
        self.assertIsNone(startup.run(True))
        self.assertEqual(api.requests,[])

    def test_pause_and_stop_after_window_verification_prevent_activation(self):
        for signal in (self.pause,self.cancel):
            with self.subTest(signal=signal):
                self.launches.clear()
                self.cancel.clear();self.pause.clear()
                self.ticks=0
                def poll():
                    self.poll()
                    if self.ticks==4:self.cancel.set()
                startup,api=self.new_client(poll_keys=poll)
                def notify(hwnd):
                    if hwnd==200:signal.set()
                api.on_probe=notify
                self.assertIsNone(startup.run(True))
                self.assertEqual(api.requests,[])

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
