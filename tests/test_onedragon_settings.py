import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

from autozzz.onedragon_settings import NativeSettingsSession,has_settings,validate_target


class NativeSettingsTests(unittest.TestCase):
    def test_settings_advertising_never_imports_plugin_code(self):
        with tempfile.TemporaryDirectory() as root:
            info=NS(plugin_dir=Path(root));self.assertFalse(has_settings(info))
            (Path(root)/'custom_app_setting.py').write_text('raise AssertionError("must not import")')
            self.assertTrue(has_settings(info));self.assertFalse(has_settings(None))

    def test_target_rejects_stale_account_plugins_groups_and_missing_app(self):
        ctx=NS(current_instance_idx=1,app_group_manager=NS(get_group_list=lambda _:['one_dragon']),
               run_context=NS(_application_factory_map={'demo':object()}),factory_manager=NS(get_plugin_info=lambda _:NS()))
        with patch('autozzz.onedragon_settings.settings_revision',return_value='current'),patch('autozzz.onedragon_settings.has_settings',return_value=True):
            target=dict(app_id='demo',instance_idx=1,revision='current',group_id='one_dragon')
            self.assertIsNotNone(validate_target(ctx,**target))
            for changes in (dict(instance_idx=True),dict(instance_idx=2),dict(revision='old'),dict(app_id='other'),dict(group_id='../other')):
                with self.subTest(changes=changes),self.assertRaises(ValueError):validate_target(ctx,**dict(target,**changes))

    def session(self,process,prepare=None):
        paths=NS(prepare=prepare or Mock(),bundle=None,python=Path('sdk/python.exe'),source=Path('sdk/source'),data=Path('data'))
        lease=NS(acquire=Mock(),export=lambda:'lease',close=Mock());events=[]
        self.stack.enter_context(patch('autozzz.task_lease.RunLeaseOwner',return_value=lease))
        spawn=self.stack.enter_context(patch('autozzz.onedragon_settings.subprocess.Popen',return_value=process))
        result=NativeSettingsSession(paths,dict(app_id='demo'),events.append)
        return result,events,lease,spawn

    def setUp(self):
        from contextlib import ExitStack
        self.stack=ExitStack();self.addCleanup(self.stack.close)

    def test_eof_and_close_keep_original_process_and_lease_until_terminal(self):
        waiting=threading.Event();exit_allowed=threading.Event()
        def wait():waiting.set();exit_allowed.wait(5);return 0
        process=NS(stdin=io.BytesIO(),stdout=io.BytesIO(b'{"type":"ready"}\n'),stderr=io.BytesIO(),wait=wait,poll=lambda:None)
        session,events,lease,spawn=self.session(process)
        try:
            self.assertTrue(waiting.wait(3));session.close()
            self.assertTrue(session.worker.is_alive());lease.close.assert_not_called();self.assertEqual(spawn.call_count,1)
        finally:exit_allowed.set();session.worker.join(3)
        self.assertFalse(session.worker.is_alive());lease.close.assert_called_once();self.assertEqual(events[-1]['type'],'closed')

    def test_close_during_prepare_never_starts_child(self):
        started=threading.Event();released=threading.Event()
        def prepare():started.set();released.wait(3)
        session,events,lease,spawn=self.session(None,prepare)
        self.assertTrue(started.wait(2));session.close();released.set();session.worker.join(3)
        spawn.assert_not_called();lease.acquire.assert_not_called();self.assertEqual(events[-1]['type'],'closed')

    def test_invalid_response_does_not_restart_process(self):
        process=NS(stdin=io.BytesIO(),stdout=io.BytesIO(b'invalid\n'),stderr=io.BytesIO(),wait=lambda:0,poll=lambda:None)
        session,events,lease,spawn=self.session(process);session.worker.join(3)
        self.assertEqual(spawn.call_count,1);self.assertTrue(any(event['type']=='error' for event in events));lease.close.assert_called_once()

    def test_remote_error_reason_is_not_overwritten_by_generic_exit_code(self):
        process=NS(stdin=io.BytesIO(),stdout=io.BytesIO(b'{"type":"error","message":"account changed"}\n'),stderr=io.BytesIO(),wait=lambda:1,poll=lambda:1)
        session,events,lease,spawn=self.session(process);session.worker.join(3)
        self.assertEqual([event['message'] for event in events if event['type']=='error'],['account changed'])

    def test_packaged_launcher_uses_bundle_bridge_and_cleans_environment(self):
        paths=NS(prepare=Mock(),bundle=Path('package/onedragon'),python=Path('package/onedragon/python/python.exe'),source=Path('package/onedragon/source'),data=Path('profile'))
        process=NS(stdin=io.BytesIO(),stdout=io.BytesIO(),stderr=io.BytesIO(),wait=lambda:0,poll=lambda:0)
        lease=NS(acquire=Mock(),export=lambda:'new lease',close=Mock())
        with patch('autozzz.task_lease.RunLeaseOwner',return_value=lease),patch('autozzz.onedragon_settings.subprocess.Popen',return_value=process) as spawn,patch.dict('os.environ',{'PYTHONHOME':'bad','AUTOZZZ_INTERNAL_RUN_LEASE':'old'}):
            session=NativeSettingsSession(paths,dict(app_id='demo'),lambda _:None);session.worker.join(3)
        command=spawn.call_args.args[0];environment=spawn.call_args.kwargs['env']
        self.assertEqual(command[0],str(paths.python));self.assertIn('autozzz.onedragon_settings_host',command)
        self.assertEqual(environment['PYTHONPATH'],str(paths.bundle/'bridge'));self.assertNotIn('PYTHONHOME',environment)
        self.assertEqual(environment['AUTOZZZ_INTERNAL_RUN_LEASE'],'new lease')


if __name__=='__main__':unittest.main()
