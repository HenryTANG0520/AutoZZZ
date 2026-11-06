from contextlib import nullcontext,contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from autozzz.onedragon_runtime import RuntimePaths,UPSTREAM_REVISION
from autozzz.onedragon_bundle import filesystem_path
from autozzz.runtime_selection import (bridge_fingerprint,candidate_selection,digest,resolve_selection,runtime_files,selection_path,tree_hash,verify_identity)
from autozzz.sdk_stable_migration import receipt_path
from autozzz.sdk_upgrade import SDKUpgradeManager
from autozzz.sdk_upgrade_files import atomic_json,data_state,pending_edits,snapshot_data,unpack_archive
import threading


class SDKUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.data=self.root/'profiles/cn';self.data.mkdir(parents=True);self.store=self.root/'SDKs'
        self.patchers=[patch('autozzz.runtime_selection.store_root',return_value=self.store),patch('autozzz.sdk_upgrade.store_root',return_value=self.store),
            patch('autozzz.sdk_upgrade.RunLock',side_effect=lambda:nullcontext()),patch('autozzz.sdk_upgrade.profile_lock',side_effect=lambda _:nullcontext())]
        for item in self.patchers:item.start();self.addCleanup(item.stop)
        self.stable_source=self.root/'stable/source';self.stable_bridge=self.root/'stable/bridge'
        for name in ('autozzz/onedragon_worker.py','autozzz/onedragon_runtime.py','autozzz/runtime_selection.py'):
            self.put(self.stable_bridge/name,b'# fixture bridge')
        self.put(self.stable_source/'config/default.sample.yml',b'old default')
        self.put(self.data/'config/account.yml',b'user account stays private')
        atomic_json(self.data/'autozzz-runtime.json',dict(revision=UPSTREAM_REVISION,protocol=1))
        self.paths=RuntimePaths(self.stable_source,self.root/'stable/python/python.exe',self.data,bridge=self.stable_bridge,product_root=self.root/'stable',stable_data=self.data)
        self.service=SimpleNamespace(data=self.data,stable_data=self.data,product_root=self.root/'stable',_assert_idle=lambda:None,_environment_changed=False)
        self.manager=SDKUpgradeManager(self.service,self.paths,'cn')
        self.candidate_id='a'*32;self.data_id='b'*32;self.revision='c'*40
        self.candidate=self.store/'candidates'/self.candidate_id;self.snapshot=self.data/'autozzz/runtime-data'/self.data_id
        self.snapshot.mkdir(parents=True);self.put(self.snapshot/'config/account.yml',(self.data/'config/account.yml').read_bytes())
        atomic_json(self.snapshot/'autozzz-runtime.json',dict(revision=self.revision,protocol=1))
        required=('source/LICENSE','source/uv.lock','source/pyproject.toml','source/src/zzz_od/context/zzz_context.py','base-python/python.exe',
            'base-python/python311.dll','env/Scripts/python.exe','bridge/autozzz/onedragon_worker.py','bridge/autozzz/onedragon_runtime.py','bridge/autozzz/runtime_selection.py')
        for name in required:self.put(self.candidate/name,b'# fixture bridge'if name.startswith('bridge/')else b'fixture')
        self.put(self.candidate/'env/pyvenv.cfg',('home = '+str(self.candidate/'base-python')).encode())
        files={name:digest(path)for name,path in runtime_files(self.candidate).items()}
        self.metadata=dict(format=1,candidate_id=self.candidate_id,revision=self.revision,protocol=1,python_version='3.11.18',branch='main',remote='fixture',groups=[],
            lock_hash=files['source/uv.lock'],bridge_hash=bridge_fingerprint(self.candidate/'bridge'),files=files,resources=[],probe=dict(compatible=True,revision=self.revision))
        atomic_json(self.candidate/'runtime.json',self.metadata)
        self.record=dict(id=self.candidate_id,state='ready',stable_data=str(self.data),revision=self.revision,branch='main',groups=[],data_id=self.data_id,
            bridge_hash=bridge_fingerprint(self.stable_bridge),identity_digest=digest(self.candidate/'runtime.json'),hello=dict(revision=self.revision),
            source_data_hash=tree_hash(data_state(self.data)),prepared_data_hash=tree_hash(data_state(self.snapshot)))
        atomic_json(self.candidate/'candidate.json',self.record)

    @staticmethod
    def put(path,raw):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    def selected(self):return candidate_selection(self.data,self.candidate_id,self.data_id,1)
    def selection(self):return dict(format=1,kind='candidate',candidate_id=self.candidate_id,data_id=self.data_id,revision=self.revision,protocol=1,identity_digest=digest(self.candidate/'runtime.json'))
    def resolve(self):return resolve_selection(self.data,stable_source=self.paths.source,stable_python=self.paths.python,stable_bridge=self.stable_bridge,stable_bundle=None,stable_revision=UPSTREAM_REVISION,protocol=1)
    def finish(self):
        self.manager.worker.join(timeout=10);self.assertFalse(self.manager.busy);return self.manager.status()

    def test_no_selection_does_not_reject_legacy_profile_path(self):
        with patch('autozzz.runtime_selection.plain',side_effect=AssertionError('legacy path must not be hardened')):self.assertIsNone(self.resolve())

    def test_exact_runtime_identity_and_private_python_home_required(self):
        self.assertEqual(self.selected().revision,self.revision)
        self.put(self.candidate/'env/pyvenv.cfg',b'home = C:/external/python')
        with self.assertRaisesRegex(ValueError,'已变化'):self.selected()

    def test_bridge_update_invalidates_old_candidate(self):
        atomic_json(selection_path(self.data),self.selection());self.assertEqual(self.resolve().revision,self.revision)
        self.put(self.stable_bridge/'autozzz/new_rpc.py',b'new API')
        with self.assertRaisesRegex(ValueError,'桥接代码已更新'):self.resolve()

    def test_selection_generation_invalidates_old_connection(self):
        atomic_json(selection_path(self.data),self.selection());selected=self.resolve()
        record=self.selection();record['data_id']='d'*32;atomic_json(selection_path(self.data),record)
        with self.assertRaisesRegex(ValueError,'另一窗口变化'):selected.verify()

    def test_failed_restart_atomically_restores_prior_selection(self):
        with patch.object(self.manager,'_handshake',side_effect=RuntimeError('actual worker hello failed')):
            self.manager.activate(self.candidate_id,'-',True);state=self.finish()
        self.assertEqual(state['state'],'failed');self.assertFalse(selection_path(self.data).exists());self.assertFalse(self.manager.activation_journal.exists())
        self.assertTrue((self.candidate/'runtime.json').exists());self.assertTrue((self.snapshot/'config/account.yml').exists())
        self.assertEqual((self.data/'config/account.yml').read_bytes(),b'user account stays private')

    def test_successful_activation_requires_current_profile_hash(self):
        self.put(self.data/'config/account.yml',b'changed after build')
        with patch.object(self.manager,'_handshake')as hello:
            self.manager.activate(self.candidate_id,'-',True);state=self.finish();hello.assert_not_called()
        self.assertEqual(state['state'],'failed');self.assertFalse(selection_path(self.data).exists())

    def test_successful_activation_uses_new_data_and_reported_revision(self):
        with patch.object(self.manager,'_handshake',return_value=dict(revision=self.revision,pid=1)):
            self.manager.activate(self.candidate_id,'-',True);state=self.finish()
        self.assertEqual(state['state'],'success');selected=self.resolve();self.assertEqual(selected.data,self.snapshot)
        self.assertEqual(selected.revision,self.revision);self.assertTrue(self.service._environment_changed)

    def test_nonreentrant_profile_mutex_released_before_child_handshake(self):
        held=set()
        @contextmanager
        def lock(path):
            path=Path(path).resolve()
            if path in held:raise RuntimeError('same profile lock cannot be reentered')
            held.add(path)
            try:yield
            finally:held.remove(path)
        def hello(selected,**kwargs):
            self.assertNotIn(selected.data,held,'real child must acquire its own profile mutex')
            self.assertIn(self.data,held,'stable control directory remains protected')
            return dict(revision=self.revision,pid=1)
        with patch('autozzz.sdk_upgrade.profile_lock',side_effect=lock),patch.object(self.manager,'_handshake',side_effect=hello):
            self.manager.activate(self.candidate_id,'-',True);state=self.finish()
        self.assertEqual(state['state'],'success',state);self.assertFalse(held)

    def test_data_changed_during_restart_rolls_selector_back(self):
        def hello(selected,**kwargs):
            self.put(selected.data/'config/account.yml',b'outside editor changed target')
            return dict(revision=self.revision,pid=1)
        with patch.object(self.manager,'_handshake',side_effect=hello):
            self.manager.activate(self.candidate_id,'-',True);state=self.finish()
        self.assertEqual(state['state'],'failed');self.assertFalse(selection_path(self.data).exists())
        self.assertEqual((self.snapshot/'config/account.yml').read_bytes(),b'outside editor changed target')

    def old_stable_receipt(self):
        revision = 'e' * 40
        files = {'config/default.sample.yml': hashlib.sha256(b'old default').hexdigest()}
        receipt = receipt_path(self.data, revision)
        atomic_json(receipt, dict(format=1, revision=revision, protocol=1, files=files,
                                  origin=dict(kind='verified_git_checkout_bytes', identity='f' * 40),
                                  resource_identity=tree_hash(files)))
        atomic_json(self.data / 'autozzz-runtime.json', dict(revision=revision, protocol=1))
        self.put(self.data / 'config/default.sample.yml', b'old default')
        self.put(self.stable_source / 'config/default.sample.yml', b'new default')
        return revision, digest(receipt)

    def test_stable_migration_updates_old_defaults_in_a_new_snapshot(self):
        revision, receipt = self.old_stable_receipt()
        original_data = data_state(self.data)
        with patch.object(self.paths.__class__, 'verify'), patch.object(
                self.manager, '_handshake', return_value=dict(revision=UPSTREAM_REVISION, pid=1)):
            self.manager.migrate_stable(revision, receipt, '-', True)
            state = self.finish()

        self.assertEqual(state['state'], 'success', state)
        selected = self.resolve()
        self.assertEqual(selected.kind, 'stable')
        self.assertEqual((selected.data / 'config/default.sample.yml').read_bytes(), b'new default')
        self.assertEqual((selected.data / 'config/account.yml').read_bytes(), b'user account stays private')
        self.assertEqual(data_state(self.data), original_data)
        self.assertEqual(json.loads((self.data / 'autozzz-runtime.json').read_bytes())['revision'], revision)

    def test_stable_migration_rolls_back_if_old_profile_changes_during_restart(self):
        revision, receipt = self.old_stable_receipt()

        def handshake(selected, **_):
            if self.manager.activation_journal.exists():
                self.put(self.data / 'config/account.yml', b'new edit from outside')
            return dict(revision=selected.revision, pid=1)

        with patch.object(self.paths.__class__, 'verify'), patch.object(self.manager, '_handshake', side_effect=handshake):
            self.manager.migrate_stable(revision, receipt, '-', True)
            state = self.finish()

        self.assertEqual(state['state'], 'failed', state)
        self.assertIn('旧稳定 SDK 资料在迁移期间变化', state['error'])
        self.assertFalse(selection_path(self.data).exists())
        self.assertEqual((self.data / 'config/account.yml').read_bytes(), b'new edit from outside')

    def test_release_prepare_keeps_original_bytes_and_seals_native_default(self):
        revision, receipt = self.old_stable_receipt()
        original = data_state(self.data)
        proof = dict(marker=digest(self.data/'autozzz-runtime.json'), data=tree_hash(original))
        self.service._sdk_control_lease = SimpleNamespace(scope=nullcontext, check=lambda:None)

        for drift in (None, 'config/account.yml', 'config/01/unexpected.yml'):
            with self.subTest(drift=drift):
                def handshake(selected, **_):
                    self.put(selected.data/'config/01/notify.yml', b'native default')
                    if drift is not None:
                        self.put(selected.data/drift, b'outside edit')
                    return dict(revision=selected.revision, pid=1)

                with patch.object(self.paths.__class__, 'verify'), patch.object(self.manager, '_handshake', side_effect=handshake):
                    self.manager.migrate_stable(revision, receipt, '-', True, source_proof=proof, prepare_only=True)
                    state = self.finish()
                if drift is None:
                    self.assertEqual(state['state'], 'success', state)
                    target = self.data/'autozzz/runtime-data'/state['result']['data_id']
                    self.assertEqual(state['result']['hash'], tree_hash(data_state(target)))
                    self.assertTrue((target/'config/01/notify.yml').is_file())
                else:
                    self.assertEqual(state['state'], 'failed', state)
                    self.assertIn('目标区服快照在探测期间变化', state['error'])
                self.assertEqual(data_state(self.data), original)
                self.assertFalse(selection_path(self.data).exists())
                self.assertFalse(self.manager.activation_journal.exists())

    def test_fast_selection_never_hashes_candidate_directories_but_full_verify_does(self):
        atomic_json(selection_path(self.data),self.selection())
        with patch('autozzz.runtime_selection.runtime_files',side_effect=AssertionError('GUI must not enumerate SDK bytes')):
            selected=resolve_selection(self.data,stable_source=self.paths.source,stable_python=self.paths.python,stable_bridge=self.stable_bridge,
                stable_bundle=None,stable_revision=UPSTREAM_REVISION,protocol=1,verify_files=False)
        self.put(self.candidate/'source/uv.lock',b'changed after fast discovery')
        with self.assertRaisesRegex(ValueError,'已变化'):selected.verify()

    def test_activation_crash_is_reviewable_and_recoverable_from_stable_control(self):
        class ProcessDeath(BaseException):pass
        with patch.object(self.manager,'_handshake',side_effect=ProcessDeath()):
            with self.assertRaises(ProcessDeath):self.manager._switch(self.selected(),self.selection(),'-')
        self.assertTrue(self.manager.activation_review()['pending'])
        with self.assertRaisesRegex(ValueError,'待核对事务'):self.resolve()
        review=self.manager.activation_review();self.manager.recover_activation(review['journal_digest'],'before',True);state=self.finish()
        self.assertEqual(state['state'],'success');self.assertFalse(selection_path(self.data).exists());self.assertFalse(self.manager.activation_journal.exists())

    def test_recovery_refuses_external_selection_changes(self):
        class ProcessDeath(BaseException):pass
        with patch.object(self.manager,'_handshake',side_effect=ProcessDeath()):
            with self.assertRaises(ProcessDeath):self.manager._switch(self.selected(),self.selection(),'-')
        record=self.selection();record['data_id']='e'*32;atomic_json(selection_path(self.data),record)
        review=self.manager.activation_review();self.assertTrue(review['conflict'])
        self.manager.recover_activation(review['journal_digest'],'before',True);state=self.finish()
        self.assertEqual(state['state'],'failed');self.assertTrue(self.manager.activation_journal.exists())

    def test_snapshot_preserves_user_override_and_refreshes_unchanged_builtin(self):
        new=self.root/'new_source';self.put(new/'config/default.sample.yml',b'new default')
        self.put(self.data/'config/default.sample.yml',b'old default');self.put(self.data/'assets/custom/raw.png',b'user PNG')
        target=self.root/'data_snapshot';before=data_state(self.data)
        report=snapshot_data(self.data,target,new,self.stable_source,self.revision,1,threading.Event(),before)
        self.assertEqual((target/'config/default.sample.yml').read_bytes(),b'new default');self.assertEqual((target/'assets/custom/raw.png').read_bytes(),b'user PNG')
        self.assertEqual(data_state(self.data),before);self.assertIn('config/default.sample.yml',report['refreshed_defaults'])

    def test_snapshot_copies_and_refreshes_assets_beyond_windows_max_path(self):
        self.addCleanup(shutil.rmtree, filesystem_path(self.root))
        new_source = self.root / 'new_source'
        target = self.data / 'autozzz/runtime-data' / ('d' * 32)
        folder = '/'.join(['assets', 'templates'] + ['nested_' + 'x' * 72] * 3)
        builtin = folder + '/builtin.png'
        custom = folder + '/custom.png'
        self.assertGreater(len(str(self.data / builtin)), 260)
        for root, name, content in (
            (self.stable_source, builtin, b'old default'),
            (self.data, builtin, b'old default'),
            (self.data, custom, b'custom asset'),
            (new_source, builtin, b'new default'),
        ):
            self.put(filesystem_path(root / name), content)

        before = data_state(self.data)
        report = snapshot_data(self.data, target, new_source, self.stable_source,
                               self.revision, 1, threading.Event(), before)

        self.assertEqual(filesystem_path(target / builtin).read_bytes(), b'new default')
        self.assertEqual(filesystem_path(target / custom).read_bytes(), b'custom asset')
        self.assertEqual(report['refreshed_defaults'], [builtin])
        self.assertEqual(data_state(self.data), before)

    def test_snapshot_carries_plugin_data_bytes_and_refuses_stale_plugin_data(self):
        name = 'plugin_data/demo/缓存/record.bin'
        raw = b'\x00\xff\r\nplugin data'
        self.put(self.data / name, raw)
        self.put(self.data / 'autozzz/plugin-state.json', b'explicit execution authorization')
        self.put(self.data / 'autozzz/plugin-archives/old/record.json', b'old archive')
        before = data_state(self.data)
        self.assertEqual(before[name], hashlib.sha256(raw).hexdigest())
        self.put(self.data / name, raw + b'changed after review')
        target = self.root / 'plugin_data_snapshot'
        with self.assertRaisesRegex(ValueError, '资料在审核后发生变化'):
            snapshot_data(self.data, target, self.stable_source, self.stable_source,
                          self.revision, 1, threading.Event(), before)
        self.assertFalse(target.exists())
        before = data_state(self.data)
        snapshot_data(self.data, target, self.stable_source, self.stable_source,
                      self.revision, 1, threading.Event(), before)
        self.assertEqual((target / name).read_bytes(), raw + b'changed after review')
        self.assertEqual(data_state(self.data), before)
        self.assertFalse((target / 'autozzz/plugin-state.json').exists())
        self.assertFalse((target / 'autozzz/plugin-archives').exists())

    def test_sdk_rollback_carries_current_plugin_data_to_new_stable_profile(self):
        name = 'plugin_data/demo/latest.bin'
        self.put(self.data / name, b'old stable bytes')
        raw = b'\x00\xffnew bytes written while candidate is active'
        self.put(self.snapshot / name, raw)
        atomic_json(selection_path(self.data), self.selection())
        with patch.object(RuntimePaths, 'verify'), patch.object(
            self.manager, '_handshake', return_value=dict(revision=UPSTREAM_REVISION, pid=1),
        ):
            self.manager.rollback(self.manager._selection_hash(), True)
            result = self.finish()
        self.assertEqual(result['state'], 'success', result)
        selected = self.resolve()
        self.assertEqual(selected.kind, 'stable')
        self.assertEqual((selected.data / name).read_bytes(), raw)
        self.assertEqual((self.snapshot / name).read_bytes(), raw)
        self.assertEqual((self.data / name).read_bytes(), b'old stable bytes')

    def test_pending_profile_edits_are_not_discarded_by_build(self):
        atomic_json(self.data/'config/autozzz-map-transaction.json',dict(id='1'*32,manifest_hash='unknown'))
        target=self.root/'blocked_snapshot';self.assertTrue(pending_edits(self.data))
        with self.assertRaisesRegex(ValueError,'待恢复修改'):snapshot_data(self.data,target,self.stable_source,self.stable_source,self.revision,1,threading.Event(),data_state(self.data))
        self.assertFalse(target.exists())

    def test_rollback_snapshot_carries_known_pending_archive_and_can_recover(self):
        from autozzz.map_transaction import MapTransaction
        road=self.data/'assets/game_data/world_patrol/test/zone/road_mask.png';self.put(road,b'old road')
        tx=MapTransaction(self.data);put=tx._put
        class ProcessDeath(BaseException):pass
        def crash(path,raw):
            put(path,raw)
            if path==road:raise ProcessDeath()
        tx._put=crash
        with self.assertRaises(ProcessDeath):tx.publish({road:b'published road'},dict(action='fixture'),lambda:True)
        target=self.root/'rollback-carry'
        report=snapshot_data(self.data,target,self.stable_source,self.stable_source,UPSTREAM_REVISION,1,threading.Event(),data_state(self.data),allow_pending=True)
        self.assertGreater(report['carried_archive_files'],0)
        MapTransaction(target).recover()
        self.assertEqual((target/road.relative_to(self.data)).read_bytes(),b'old road')
        self.assertEqual(road.read_bytes(),b'published road');self.assertTrue(tx.pending.exists())

    def test_unknown_pending_schema_blocks_rollback_without_erasing_it(self):
        marker=self.data/'config/autozzz-unknown-transaction.json';atomic_json(marker,dict(payload='unknown'))
        target=self.root/'blocked-rollback'
        with self.assertRaisesRegex(ValueError,'尚未适配'):
            snapshot_data(self.data,target,self.stable_source,self.stable_source,UPSTREAM_REVISION,1,threading.Event(),data_state(self.data),allow_pending=True)
        self.assertTrue(marker.exists());self.assertFalse(target.exists())


if __name__=='__main__':unittest.main()
