import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from contextlib import nullcontext

from autozzz import gamepad_install as g


class GamepadDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.files={'vgamepad/__init__.py':hashlib.sha256(b'fixed package').hexdigest(),'LICENSE':hashlib.sha256(b'license').hexdigest()}
        self.payload=self.archive({'vgamepad/__init__.py':b'fixed package','LICENSE':b'license','setup.py':b'raise AssertionError("must not execute")'})
        self.patches=[patch.object(g,'FILES',self.files),patch.object(g,'SHA256',hashlib.sha256(self.payload).hexdigest())]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def archive(self,files):
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
            for name,content in files.items():
                item=tarfile.TarInfo('vgamepad-0.1.0/'+name);item.size=len(content);archive.addfile(item,io.BytesIO(content))
        return buffer.getvalue()

    def test_pinned_archive_only_extracts_fixed_package_and_license(self):
        target=self.root/'target';g.extract_dependency(self.payload,target)
        self.assertTrue(g.verify_dependency(target));self.assertFalse((target/'setup.py').exists())

    def test_archive_hash_mismatch_never_creates_directory(self):
        with self.assertRaises(ValueError):g.extract_dependency(self.payload+b'x',self.root/'target')
        self.assertFalse((self.root/'target').exists())

    def test_path_traversal_rejected_without_external_write(self):
        payload=self.archive({'../escape':b'evil'})
        with patch.object(g,'SHA256',hashlib.sha256(payload).hexdigest()),self.assertRaises(ValueError):g.extract_dependency(payload,self.root/'target')
        self.assertFalse((self.root/'escape').exists())

    def test_known_file_tamper_and_unknown_dll_are_rejected(self):
        target=self.root/'target';g.extract_dependency(self.payload,target)
        (target/'evil.dll').write_bytes(b'evil')
        with self.assertRaises(ValueError):g.verify_dependency(target)
        (target/'evil.dll').unlink();(target/'vgamepad/__init__.py').write_bytes(b'evil')
        with self.assertRaises(ValueError):g.verify_dependency(target)

    def test_bytecode_and_pth_never_admitted(self):
        for name in ('evil.pth','vgamepad/__pycache__/__init__.cpython-311.pyc'):
            target=self.root/str(len(name));g.extract_dependency(self.payload,target)
            path=target/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'evil')
            with self.subTest(name=name),self.assertRaises(ValueError):g.verify_dependency(target)

    def manager(self):
        service=NS(data=self.root,_assert_idle=lambda:None,_environment_changed=False)
        return g.GamepadDependencies(service)

    def test_catalog_does_not_create_context_or_install(self):
        manager=self.manager()
        with patch.object(g,'driver_state',return_value=dict(registered=False,disabled=False)),patch.object(g,'install_msi',side_effect=AssertionError):
            state=manager.catalog()
        self.assertFalse(state['python_ready']);self.assertFalse(manager.busy)

    def test_missing_confirmation_and_stale_revision_never_start(self):
        manager=self.manager()
        with patch.object(g,'driver_state',return_value={}):
            rev=manager.catalog()['revision']
            for args in [('install',rev,False),('repair',rev,1),('other',rev,True),('install','stale',True)]:
                with self.subTest(args=args),self.assertRaises(ValueError):manager.start(*args)
        self.assertIsNone(manager.thread)

    def run_install(self,code,registered=True,action='install',bus_connects=False):
        manager=self.manager();driver=dict(registered=registered,disabled=False,game_verified=False)
        self.install_calls=[]
        with patch.object(g,'driver_state',return_value=driver),patch.object(manager,'_download',return_value=self.payload),\
            patch.object(g,'_existing_bus_connects',return_value=bus_connects),\
            patch.object(g,'install_msi',side_effect=lambda p,r,l:self.install_calls.append((p,r,l)) or code),\
            patch('autozzz.run_lock.RunLock',return_value=nullcontext()),patch('autozzz.run_lock.profile_lock',return_value=nullcontext()):
            revision=manager.catalog()['revision'];manager.start(action,revision,True);manager.thread.join(5)
        self.assertFalse(manager.busy);return manager

    def test_installer_zero_keeps_game_unverified_and_requires_worker_restart(self):
        manager=self.run_install(0)
        state=manager.status();self.assertEqual(state['state'],'installed_unverified')
        self.assertFalse(state['game_verified']);self.assertTrue(state['process_restart_required'])
        self.assertTrue(manager.service._environment_changed);self.assertEqual(len(self.install_calls),1)

    def test_installer_zero_without_driver_registration_not_installed(self):
        state=self.run_install(0,registered=False).status()
        self.assertTrue(state['installer_completed']);self.assertFalse(state['installed'])

    def test_install_reuses_connectable_bus_and_still_requires_worker_restart(self):
        manager = self.run_install(0, bus_connects=True)
        state = manager.status()
        self.assertEqual(state['state'], 'installed_unverified')
        self.assertTrue(state['installed'])
        self.assertTrue(state['driver_reused'])
        self.assertTrue(state['process_restart_required'])
        self.assertFalse(state['installer_completed'])
        self.assertFalse(state['game_verified'])
        self.assertEqual(self.install_calls, [])

    def test_reboot_code_is_preserved_not_called_ready(self):
        for code in (3010,1641):
            manager=self.run_install(code);state=manager.status()
            self.assertEqual(state['state'],'restart_required');self.assertTrue(state['restart_required']);self.assertFalse(state['installed'])

    def test_repair_uses_repair_installer_operation(self):
        manager=self.run_install(0,action='repair',bus_connects=True)
        self.assertTrue(self.install_calls[0][1]);self.assertEqual(manager.status()['action'],'repair')

    def test_installer_failure_never_returns_installed(self):
        state=self.run_install(1603).status()
        self.assertEqual(state['state'],'failed');self.assertFalse(state['installed']);self.assertIn('1603',state['detail'])

    def test_cancel_download_prevents_driver(self):
        manager=self.manager();entered=threading.Event();release=threading.Event()
        def download():
            entered.set();release.wait(5)
            if manager.cancelled.is_set():raise InterruptedError('cancelled')
            return self.payload
        with patch.object(g,'driver_state',return_value={}),patch.object(manager,'_download',side_effect=download),\
            patch.object(g,'install_msi',side_effect=AssertionError('driver must not run')),\
            patch('autozzz.run_lock.RunLock',return_value=nullcontext()),patch('autozzz.run_lock.profile_lock',return_value=nullcontext()):
            manager.start('install',manager.catalog()['revision'],True);self.assertTrue(entered.wait(3))
            manager.cancel(manager.job['job_id']);release.set();manager.close()
        self.assertEqual(manager.status()['state'],'cancelled')

    def test_driver_transaction_cannot_be_cancelled_or_report_closed_early(self):
        manager=self.manager();entered=threading.Event();release=threading.Event()
        def install(*_):entered.set();release.wait(5);return 0
        with patch.object(g,'driver_state',return_value={}),patch.object(manager,'_download',return_value=self.payload),\
            patch.object(g,'install_msi',side_effect=install),\
            patch('autozzz.run_lock.RunLock',return_value=nullcontext()),patch('autozzz.run_lock.profile_lock',return_value=nullcontext()):
            manager.start('repair',manager.catalog()['revision'],True);self.assertTrue(entered.wait(3))
            with self.assertRaises(ValueError):manager.cancel(manager.job['job_id'])
            self.assertTrue(manager.busy);release.set();manager.close();self.assertFalse(manager.busy)

    def test_cancel_after_final_home_validation_never_launches_driver(self):
        manager=self.manager();original=g.verify_dependency;cancelled=[]
        def checked(path):
            result=original(path)
            if Path(path)==g.dependency_root(self.root) and threading.current_thread() is manager.thread:
                cancelled.append(manager.cancel(manager.job['job_id']))
            return result
        with patch.object(g,'driver_state',return_value={}),patch.object(manager,'_download',return_value=self.payload),\
            patch.object(g,'verify_dependency',side_effect=checked),patch.object(g,'install_msi') as installer,\
            patch('autozzz.run_lock.RunLock',return_value=nullcontext()),patch('autozzz.run_lock.profile_lock',return_value=nullcontext()):
            manager.start('install',manager.catalog()['revision'],True);manager.thread.join(3)
            installer.assert_not_called()
        self.assertEqual(len(cancelled),1)
        self.assertEqual(manager.status()['state'],'cancelled');self.assertFalse(manager.status()['installed'])
        self.assertTrue(manager.status()['process_restart_required'])

    def test_close_after_final_home_validation_never_launches_driver(self):
        manager=self.manager();original=g.verify_dependency;validated=threading.Event();release=threading.Event()
        def checked(path):
            result=original(path)
            if Path(path)==g.dependency_root(self.root) and threading.current_thread() is manager.thread:
                validated.set();release.wait(3)
            return result
        with patch.object(g,'driver_state',return_value={}),patch.object(manager,'_download',return_value=self.payload),\
            patch.object(g,'verify_dependency',side_effect=checked),patch.object(g,'install_msi') as installer,\
            patch('autozzz.run_lock.RunLock',return_value=nullcontext()),patch('autozzz.run_lock.profile_lock',return_value=nullcontext()):
            manager.start('install',manager.catalog()['revision'],True);self.assertTrue(validated.wait(3))
            closer=threading.Thread(target=manager.close);closer.start()
            self.assertTrue(manager.cancelled.wait(3));release.set();closer.join(3)
            self.assertFalse(closer.is_alive());installer.assert_not_called()
        self.assertEqual(manager.status()['state'],'cancelled')

    def test_closed_connection_cannot_start_another_install(self):
        manager=self.manager();manager.close()
        with patch.object(g,'driver_state',return_value={}):
            with self.assertRaisesRegex(RuntimeError,'连接已关闭'):manager.start('install',manager.catalog()['revision'],True)

    def test_existing_dependency_is_archived_on_repair(self):
        home=g.dependency_root(self.root);home.mkdir(parents=True);(home/'old.txt').write_text('keep me')
        manager=self.run_install(0,action='repair')
        self.assertEqual(next(home.parent.glob('previous-*')).joinpath('old.txt').read_text(),'keep me')

    def test_unknown_job_id_does_not_cancel_original(self):
        manager=self.manager();manager.job={'job_id':'original'}
        with self.assertRaises(ValueError):manager.cancel('other')
        self.assertFalse(manager.cancelled.is_set())

    def test_restart_receipt_persists_until_next_boot(self):
        manager=self.manager();receipt=g.dependency_root(self.root).parent/'last-install.json'
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps(dict(restart_required=True,boot=123)),encoding='utf-8')
        with patch.object(g,'driver_state',return_value={}),patch.object(g,'boot_token',return_value=123):
            self.assertTrue(manager.catalog()['restart_required'])
        with patch.object(g,'driver_state',return_value={}),patch.object(g,'boot_token',return_value=124):
            self.assertFalse(manager.catalog()['restart_required'])

    def test_corrupt_receipt_is_reported_and_repair_remains_reachable(self):
        manager=self.manager();receipt=g.dependency_root(self.root).parent/'last-install.json'
        receipt.parent.mkdir(parents=True);receipt.write_text('[]',encoding='utf-8')
        with patch.object(g,'driver_state',return_value={}):
            state=manager.catalog()
        self.assertIn('记录格式无效',state['error']);self.assertIsInstance(state['revision'],str)

    def test_failed_post_publish_validation_requires_new_worker(self):
        manager=self.manager();original=g.verify_dependency
        def checked(path):
            if Path(path)==g.dependency_root(self.root) and threading.current_thread() is manager.thread:
                raise ValueError('post publication validation failed')
            return original(path)
        with patch.object(g,'driver_state',return_value={}),patch.object(manager,'_download',return_value=self.payload),\
            patch.object(g,'verify_dependency',side_effect=checked),patch.object(g,'install_msi') as installer,\
            patch('autozzz.run_lock.RunLock',return_value=nullcontext()),patch('autozzz.run_lock.profile_lock',return_value=nullcontext()):
            manager.start('repair',manager.catalog()['revision'],True);manager.thread.join(3);installer.assert_not_called()
        self.assertEqual(manager.status()['state'],'failed');self.assertTrue(manager.status()['process_restart_required'])
        self.assertTrue(manager.service._environment_changed)

    def test_global_task_lock_failure_does_not_download_or_install(self):
        manager=self.manager()
        with patch.object(g,'driver_state',return_value={}),patch.object(manager,'_download',side_effect=AssertionError),\
            patch('autozzz.run_lock.RunLock',side_effect=RuntimeError('another game task owns lease')):
            manager.start('install',manager.catalog()['revision'],True);manager.thread.join(3)
        self.assertEqual(manager.status()['state'],'failed')
        self.assertIn('lease',manager.status()['detail'])

    def test_msi_arguments_are_fixed_non_shell_and_never_force_reboot(self):
        process=NS(wait=lambda:3010)
        with patch('subprocess.Popen',return_value=process) as start:
            self.assertEqual(g.install_msi(Path('fixed.msi'),True,Path('log.txt')),3010)
        args=start.call_args.args[0]
        self.assertEqual(args[1],'/fa');self.assertIn('/norestart',args);self.assertNotIn('shell',start.call_args.kwargs)
