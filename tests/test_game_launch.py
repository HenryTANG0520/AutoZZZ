import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from threading import Event

from autozzz.game_launch import LaunchOptions,LaunchSession,HdrStore,legacy_launch_configuration,windows_arguments


class Lock:
    held=set()
    def __init__(self,name):self.name=name;self.acquired=False
    def acquire(self):
        if self.name in self.held:raise RuntimeError('busy')
        self.held.add(self.name);self.acquired=True
    def close(self):
        if self.acquired:self.held.remove(self.name);self.acquired=False
    def __enter__(self):self.acquire();return self
    def __exit__(self,*_):self.close()


class Registry:
    def __init__(self,value=None):self.value=copy.deepcopy(value or dict(exists=False));self.writes=[]
    def read(self,path):return copy.deepcopy(self.value)
    def write(self,path,value):self.writes.append(copy.deepcopy(value));self.value=copy.deepcopy(value)


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'ZenlessZoneZero.exe';self.path.write_bytes(b'fixture')
        self.registry=Registry(dict(exists=True,type=2,data='GpuPreference=2;AutoHDREnable=2097;Other=4;'))
        self.store=HdrStore(self.path,root=Path(self.folder.name)/'hdr',registry=self.registry,lock_factory=Lock)
        self.spawn=Mock()
    def session(self,**extra):
        values=dict(hdr_store=self.store,popen=self.spawn);values.update(extra)
        return LaunchSession(self.path,LaunchOptions(autozzz_temporary_hdr=True),**values)

    def test_windows_native_argv_round_trip_and_shell_metacharacters(self):
        values=['value with spaces','x"y','folder\\trailing\\','', '&', '|', '%PATH%', '$(secret)', '中文']
        self.assertEqual(windows_arguments(subprocess.list2cmdline(values)),values)

    def test_all_six_launch_fields_are_actually_consumed_without_shell(self):
        options=LaunchOptions(True,'2560x1440','1',True,'3','--label "a b" & calc')
        session=LaunchSession(self.path,options,popen=self.spawn,hdr_store=self.store);session.spawn();session.close()
        args=self.spawn.call_args.args[0]
        self.assertEqual(args[1:],['--label','a b','&','calc','-screen-width','2560','-screen-height','1440','-screen-fullscreen','1','-popupwindow','-monitor','3'])
        self.assertIs(self.spawn.call_args.kwargs['shell'],False);self.assertEqual(self.registry.writes,[])

    def test_upgrade_defaults_never_touch_registry(self):
        session=LaunchSession(self.path,popen=self.spawn,hdr_store=self.store);session.spawn();session.close()
        self.assertEqual(self.spawn.call_args.args[0],[str(self.path)]);self.assertEqual(self.registry.writes,[])

    def test_temporary_hdr_restores_full_original_type_and_other_preferences(self):
        before=self.registry.read('');session=self.session();session.spawn()
        self.assertEqual(self.registry.value,dict(exists=True,type=2,data='GpuPreference=2;Other=4;AutoHDREnable=2096;'))
        self.assertTrue(self.store.active.is_file());session.close()
        self.assertEqual(self.registry.value,before);self.assertFalse(self.store.active.exists())
        self.assertEqual(len(list((self.store.root/'history').glob('*.json'))),1)

    def test_absent_and_empty_original_values_are_preserved(self):
        for value in (dict(exists=False),dict(exists=True,type=1,data='')):
            with self.subTest(value=value):
                self.registry.value=value;session=self.session();session.spawn();session.close();self.assertEqual(self.registry.value,value)

    def test_spawn_failure_restores_and_cannot_repeat(self):
        self.spawn.side_effect=OSError('fixture launch failed');before=self.registry.read('');session=self.session()
        with self.assertRaises(OSError):session.spawn()
        self.assertEqual(self.registry.value,before)
        with self.assertRaisesRegex(Exception,'重复'):session.spawn()
        self.assertEqual(self.spawn.call_count,1)

    def test_cancel_after_journal_before_write_prevents_launch_and_restores(self):
        count=0
        def guard():
            nonlocal count
            count+=1
            if count==1:raise RuntimeError('cancelled')
        self.store.guard=guard;session=self.session();before=self.registry.read('')
        with self.assertRaisesRegex(RuntimeError,'cancelled'):session.spawn()
        self.spawn.assert_not_called();self.assertEqual(self.registry.value,before);self.assertFalse(self.store.active.exists())

    def test_stale_generation_before_spawn_restores_without_game_launch(self):
        def guard():
            if self.registry.writes:raise RuntimeError('generation changed')
        session=self.session(guard=guard);before=self.registry.read('')
        with self.assertRaisesRegex(RuntimeError,'generation'):session.spawn()
        self.spawn.assert_not_called();self.assertEqual(self.registry.value,before)
        self.assertEqual(len(self.registry.writes),2)

    def test_external_registry_change_is_preserved_and_recovery_refuses(self):
        session=self.session();session.spawn();self.registry.value=dict(exists=True,type=1,data='Other=external;')
        with self.assertRaisesRegex(ValueError,'外部修改'):session.close()
        state=self.store.status();self.assertTrue(state['conflict'])
        with self.assertRaisesRegex(ValueError,'外部修改'):self.store.recover(state['journal_digest'],True)
        self.assertEqual(self.registry.value['data'],'Other=external;');self.assertTrue(self.store.active.exists())

    def test_external_journal_change_is_not_overwritten(self):
        session=self.session();session.spawn();self.store.active.write_text('{}')
        with self.assertRaises(ValueError):session.close()
        self.assertEqual(self.store.active.read_text(),'{}')

    def test_interrupted_transaction_reopens_reviews_and_recovers_original(self):
        before=self.registry.read('');session=self.session();session.spawn()
        session.hdr_lock.close();session.hdr_lock=None  # emulate OS releasing mutex on process exit
        reopened=HdrStore(self.path,root=self.store.root,registry=self.registry,lock_factory=Lock)
        state=reopened.status();self.assertEqual(state['pending']['before'],before)
        with self.assertRaises(ValueError):reopened.recover('0'*64,True)
        reopened.recover(state['journal_digest'],True);self.assertEqual(self.registry.value,before)
        session.journal=None

    def test_pending_transaction_blocks_new_temporary_launch(self):
        session=self.session();session.spawn();session.hdr_lock.close();session.hdr_lock=None
        next_session=self.session()
        with self.assertRaisesRegex(ValueError,'尚未恢复'):next_session.spawn()
        self.assertEqual(self.spawn.call_count,1)
        state=self.store.status();self.store.recover(state['journal_digest'],True);session.journal=None

    def test_cross_session_concurrency_cannot_mutate_pref(self):
        session=self.session();session.spawn();other=self.session()
        with self.assertRaisesRegex(RuntimeError,'busy'):other.spawn()
        self.assertEqual(self.spawn.call_count,1);session.close()

    def test_manual_enable_disable_confirmation_and_revision(self):
        state=self.store.status()
        with self.assertRaises(ValueError):self.store.set(False,state['revision'])
        state=self.store.set(False,state['revision'],True)
        self.assertIn('AutoHDREnable=2096;',self.registry.value['data'])
        with self.assertRaisesRegex(ValueError,'状态已变化'):self.store.set(True,'stale',True)
        state=self.store.set(True,state['revision'],True)
        self.assertIn('GpuPreference=2;',self.registry.value['data']);self.assertTrue(state['enabled']);self.assertIsNone(state['pending'])

    def test_registry_and_journal_race_checked_after_guard(self):
        state=self.store.status()
        def guard():
            if self.store.active.exists():self.registry.value=dict(exists=True,type=1,data='External=1;')
        self.store.guard=guard
        with self.assertRaisesRegex(ValueError,'已变化'):self.store.set(False,state['revision'],True)
        self.assertEqual(self.registry.writes,[]);self.assertTrue(self.store.active.exists())

    def test_nonstring_preferences_are_not_destroyed(self):
        self.registry.value=dict(exists=True,type=3,data={'bytes':'AAEC'})
        with self.assertRaises(ValueError):self.session().spawn()
        self.assertEqual(self.registry.writes,[]);self.spawn.assert_not_called()

    def test_options_reject_unknowns_types_and_nul(self):
        for values in ({'hdr':True},{'launch_argument':1},{'screen_size':'bad'},{'launch_argument_advance':'\0'}):
            with self.subTest(values=values),self.assertRaises(ValueError):LaunchOptions.from_mapping(values)

    def startup(self,ready=False,existing=False):
        from autozzz.onedragon_startup import GameStartup
        from tests.test_onedragon_startup import Flag
        cancelled=Flag();paused=Flag()
        def wait(_):
            if not ready:cancelled.set()
        cancelled.waited=wait
        identity=dict(pid=5,created=1,path=str(self.path))
        def inventory():return [identity]if existing or self.spawn.called else[]
        def window(_):
            if ready:return type('Window',(),dict(hwnd=8))()
            from autozzz.models import AutomationError
            raise AutomationError('fixture waiting')
        with patch('autozzz.game_launch.HdrStore',return_value=self.store),patch('autozzz.game_launch.subprocess.Popen',self.spawn):
            startup=GameStartup(self.path,cancelled=cancelled,paused=paused,poll_keys=lambda:None,elapsed=lambda _:None,
                phase=lambda _:None,inventory=inventory,find_window=window,options=LaunchOptions(autozzz_temporary_hdr=True))
            return startup.run(True)

    def test_game_startup_window_ready_restores_before_return(self):
        original=self.registry.read('');self.assertEqual(self.startup(ready=True),dict(window_handle=8,process_id=5))
        self.assertEqual(self.registry.value,original);self.assertEqual(len(self.registry.writes),2)

    def test_game_startup_cancel_during_window_wait_restores(self):
        original=self.registry.read('');self.assertIsNone(self.startup())
        self.assertEqual(self.registry.value,original);self.assertEqual(len(self.registry.writes),2)

    def test_game_startup_existing_process_never_reads_or_changes_hdr(self):
        self.store.registry.read=Mock(side_effect=AssertionError('existing process must not read HDR'))
        self.assertIsNotNone(self.startup(ready=True,existing=True));self.spawn.assert_not_called()

    def test_raw_borrow_preserves_original_missing_or_typed_value(self):
        for before in (dict(exists=False),dict(exists=True,type=2,data='GpuPreference=2;')):
            with self.subTest(before=before):
                self.registry.value=before
                owner=self.session();owner.begin_hdr()
                borrowed=LaunchSession(self.path,hdr_lease=owner,hdr_store=self.store,popen=self.spawn)
                borrowed.spawn();borrowed.close();self.assertIn('2096',self.registry.value['data'])
                with self.assertRaisesRegex(RuntimeError,'busy'):
                    LaunchSession(self.path,hdr_store=self.store,popen=self.spawn).spawn()
                owner.close();self.assertEqual(self.registry.value,before)

    def test_default_launch_refuses_interrupted_raw_hdr(self):
        owner=self.session();owner.begin_hdr();owner.hdr_lock.close();owner.hdr_lock=None
        with self.assertRaisesRegex(ValueError,'尚未恢复'):
            LaunchSession(self.path,hdr_store=self.store,popen=self.spawn).spawn()
        self.spawn.assert_not_called()
        state=self.store.status();self.store.recover(state['journal_digest'],True);owner.journal=None


class LegacyGenerationTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        env=patch.dict(os.environ,LOCALAPPDATA=str(self.root));env.start();self.addCleanup(env.stop)
        self.data=self.root/'AutoZZZ/OneDragon/cn'
        self.selector=self.data/'autozzz/sdk-selection.json';self.selector.parent.mkdir(parents=True)
        self.journal=self.data/'autozzz/sdk-activation.json'

    def test_broken_selector_or_candidate_identity_uses_defaults_without_losing_generation(self):
        from autozzz.runtime_selection import FORMAT
        from autozzz.onedragon_runtime import PROTOCOL_VERSION
        cases=['broken selector',json.dumps(dict(format=FORMAT,kind='candidate',candidate_id='a'*32,
            data_id='b'*32,revision='c'*40,protocol=PROTOCOL_VERSION,identity_digest='d'*64))]
        for broken in cases:
            with self.subTest(broken=broken):
                self.selector.write_text(broken)
                options,guard,degraded=legacy_launch_configuration(self.root,'cn')
                self.assertEqual(options,LaunchOptions());self.assertTrue(degraded);guard()
                self.selector.write_text('new generation')
                with self.assertRaisesRegex(RuntimeError,'已切换'):guard()

    def test_pending_switch_is_not_a_fallback(self):
        self.selector.write_text('broken selector');self.journal.write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'待核对事务'):
            legacy_launch_configuration(self.root,'cn')

    def test_bootstrap_uses_a_generation_even_if_selector_changes_after_a_rpc(self):
        from autozzz.onedragon_session import RuntimeSession
        from autozzz.runtime_generation import selection_generation
        self.selector.write_text('A')
        session=object.__new__(RuntimeSession)
        session.emit=Mock();session._legacy_event=Mock();session.closing=Event();session.stop_requested=Event()
        client=SimpleNamespace(paths=SimpleNamespace(stable_data=self.data,data=self.data,
            generation=selection_generation(self.data),management_only=False))
        old_options=LaunchOptions(launch_argument=True,screen_size='2560x1440').to_dict()
        def fetch(_,method):
            self.assertEqual(method,'game_launch_options');self.selector.write_text('B');return old_options
        session._call=fetch
        captured={}
        class Runner:
            def __init__(inner,*_,**kwargs):captured.update(kwargs)
            def enter_game(inner):captured['runtime_guard']();raise AssertionError('stale generation passed')
        with patch('autozzz.app.Runner',Runner),self.assertRaisesRegex(RuntimeError,'已切换'):
            session._enter_game(dict(server='cn',game_path='fixture/ZenlessZoneZero.exe'),client)
        self.assertEqual(captured['launch_options'],old_options)

    def test_generation_is_checked_again_at_click_and_held_input(self):
        from autozzz.app import Context,Runner
        from autozzz.config import Settings
        from autozzz.models import Scene
        self.selector.write_text('broken A')
        _,guard,_=legacy_launch_configuration(self.root,'cn')
        runner=Runner(Settings(),runtime_guard=guard)
        context=Context(runner,Mock(),Mock())
        scene=Scene([]);context.last_scene=scene;context._input_scene=Mock()
        context.log=lambda *_:self.selector.write_text('broken B')
        with patch('autozzz.policy.guard_click'),self.assertRaisesRegex(RuntimeError,'已切换'):
            context.click(scene,(.1,.1))
        context.window.click.assert_not_called()
        with self.assertRaisesRegex(RuntimeError,'已切换'):context.input_checkpoint()


if __name__=='__main__':unittest.main()
