import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from autozzz.onedragon_bundle import MANIFEST_NAME,digest,filesystem_path
from autozzz.program_package import MANIFEST,create_manifest,extract_package,read_manifest,verify_package,preserve_user_files
from autozzz.program_sdk_commit import ProgramSdkCommit
from autozzz.program_update import ProgramUpdate, verify_preflight, write_json
from autozzz.runtime_selection import selection_path, snapshot_path, tree_hash
from autozzz.sdk_upgrade_files import atomic_bytes, atomic_json, data_state


def package(root,version):
    root=Path(root);root.mkdir(parents=True)
    required=('python/python.exe','python/python311.dll','python/LICENSE.txt','source/LICENSE','source/src/zzz_od/context/zzz_context.py',
        'bridge/autozzz/onedragon_worker.py','bridge/autozzz/onedragon_runtime.py','bridge/autozzz/onedragon_bundle.py','bridge/autozzz/onedragon_diagnostics.py')
    entries={}
    for name in required:
        path=root/'onedragon'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(version+name,encoding='utf-8');entries[name]=digest(path)
    (root/'onedragon'/MANIFEST_NAME).write_text(json.dumps(dict(format=1,revision='a'*40,protocol=1,python_version='3.11.18',files=entries,resources=[])),encoding='utf-8')
    (root/'AutoZZZ.exe').write_text('fixture executable '+version,encoding='utf-8')
    (root/'README.md').write_text(version,encoding='utf-8')
    create_manifest(root,version,'b'*40)
    return root


class PowerLoss(BaseException):pass


class ProgramUpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup);self.root=Path(self.directory.name)
        self.old=package(self.root/'中文 安装'/'AutoZZZ','old');self.new=package(self.root/'new','new')
        (self.old/'config.json').write_text('current settings',encoding='utf-8')
        (self.old/'runs').mkdir();(self.old/'runs/report.json').write_text('history',encoding='utf-8')
        (self.old/'自定义').mkdir();(self.old/'自定义/队伍.json').write_text('custom',encoding='utf-8')

    def prepare(self):return ProgramUpdate.prepare(self.old,self.new,probe=lambda _:[])

    def test_whole_directory_update_preserves_every_user_file_and_full_old_program(self):
        transaction=self.prepare();transaction.apply()
        self.assertEqual(verify_package(self.old)['version'],'new')
        self.assertEqual(verify_package(transaction.backup)['version'],'old')
        for name,value in (('config.json','current settings'),('runs/report.json','history'),('自定义/队伍.json','custom')):
            self.assertEqual((self.old/name).read_text(encoding='utf-8'),value)
            self.assertEqual((transaction.backup/name).read_text(encoding='utf-8'),value)
        self.assertEqual(transaction.data['state'],'completed')
        with self.assertRaises(ValueError):transaction.apply()

    def test_failure_after_old_directory_move_restores_old_without_deletion(self):
        transaction=self.prepare()
        def fail(state):
            if state=='old_moved':raise OSError('fixture rename failure')
        transaction.after_state=fail
        with self.assertRaises(OSError):transaction.apply()
        self.assertEqual(verify_package(self.old)['version'],'old');self.assertTrue(transaction.candidate.exists())
        self.assertEqual(transaction.data['state'],'rolled_back')

    def test_failure_after_new_directory_move_restores_old_and_keeps_failed_new(self):
        transaction=self.prepare()
        def fail(state):
            if state=='installed':raise OSError('fixture verification failure')
        transaction.after_state=fail
        with self.assertRaises(OSError):transaction.apply()
        self.assertEqual(verify_package(self.old)['version'],'old')
        self.assertEqual(verify_package(transaction.candidate)['version'],'new')

    def test_joint_rollback_keeps_old_and_new_profile_edits_after_partial_publication(self):
        transaction = self.prepare()
        controls = {profile: self.root / 'profiles' / profile for profile in ('cn', 'global')}
        targets, snapshots, rows = {}, {}, {}
        for profile, control in controls.items():
            target = targets[profile] = snapshot_path(control, 'c' * 32)
            for root, revision in ((control, 'a' * 40), (target, 'd' * 40)):
                atomic_json(root / 'autozzz-runtime.json', dict(revision=revision, protocol=1))
                atomic_bytes(root / 'config/user.yml', b'original setting')
            rows[profile] = dict(kind='stable', control=str(control.resolve()), source=str(control.resolve()),
                                 selector='-', marker=digest(control / 'autozzz-runtime.json'),
                                 data=tree_hash(data_state(control)))
            snapshots[profile] = dict(data_id=target.name, hash=tree_hash(data_state(target)))
        binding = dict(profiles=rows, old=dict(package=transaction.data['old_manifest']),
                       new=dict(package=transaction.data['new_manifest'], revision='d' * 40, protocol=1))
        transaction.state('prepared', sdk_transition=binding)

        def interrupt_first_publish(path, raw):
            atomic_bytes(path, raw)
            if path == selection_path(controls['cn']):
                raise PowerLoss()

        with (patch('autozzz.program_sdk_commit.verify_sdk_transition', return_value=binding),
              patch('autozzz.program_sdk_commit.RuntimePaths.discover',
                    side_effect=lambda _, profile, **__: SimpleNamespace(data=controls[profile]))):
            commit = ProgramSdkCommit.prepare(transaction, snapshots)
            with patch('autozzz.program_sdk_commit.atomic_bytes', side_effect=interrupt_first_publish):
                with self.assertRaises(PowerLoss):
                    commit.after()
            atomic_bytes(targets['cn'] / 'config/user.yml', b'new snapshot edit')
            with self.assertRaisesRegex(ValueError, '目标区服快照在联合提交期间变化'):
                commit.after()
            atomic_bytes(controls['cn'] / 'config/user.yml', b'old profile edit')
            commit.before()

        self.assertEqual(verify_package(self.old)['version'], 'old')
        self.assertEqual((controls['cn'] / 'config/user.yml').read_bytes(), b'old profile edit')
        self.assertEqual((targets['cn'] / 'config/user.yml').read_bytes(), b'new snapshot edit')
        for control in controls.values():
            self.assertFalse(selection_path(control).exists())
            self.assertFalse((control / 'autozzz/sdk-activation.json').exists())

    def test_failed_completion_journal_write_restores_original_installation(self):
        transaction = self.prepare()

        def write_except_completion(path, data):
            if path == transaction.journal and data['state'] == 'completed':
                raise OSError('fixture could not commit update journal')
            write_json(path, data)

        with patch('autozzz.program_update.write_json', side_effect=write_except_completion):
            with self.assertRaisesRegex(OSError, 'could not commit'):
                transaction.apply()
        self.assertEqual(verify_package(self.old)['version'], 'old')
        self.assertEqual((self.old / 'config.json').read_bytes(), b'current settings')
        self.assertEqual(verify_package(transaction.candidate)['version'], 'new')
        self.assertFalse(transaction.backup.exists())
        self.assertEqual(ProgramUpdate(transaction.job).data['state'], 'rolled_back')

    def test_retry_after_archive_rename_denial_rechecks_candidate_user_files(self):
        transaction = self.prepare()
        rename = os.rename

        def deny_completion(path, data):
            if path == transaction.journal and data['state'] == 'completed':
                raise PermissionError('fixture completion write denied')
            write_json(path, data)

        def deny_archive_restore(source, destination):
            if source == filesystem_path(transaction.backup):
                raise PermissionError('fixture archive rename denied')
            rename(source, destination)

        with (patch('autozzz.program_update.write_json', side_effect=deny_completion),
              patch('autozzz.program_update.os.rename', side_effect=deny_archive_restore)):
            with self.assertRaisesRegex(PermissionError, 'completion write denied'):
                transaction.apply()
        self.assertFalse(self.old.exists())
        self.assertEqual(ProgramUpdate(transaction.job).data['state'], 'needs_recovery')
        self.assertEqual((transaction.backup / 'config.json').read_bytes(), b'current settings')

        updated = b'settings saved during recovery'
        (transaction.candidate / 'config.json').write_bytes(updated)
        retry = ProgramUpdate(transaction.job, probe=lambda _: [])
        with self.assertRaisesRegex(ValueError, '用户资料已变化'):
            retry.recover()
        self.assertFalse(self.old.exists())
        self.assertEqual(verify_package(transaction.backup)['version'], 'old')
        self.assertEqual((transaction.backup / 'config.json').read_bytes(), b'current settings')
        self.assertEqual(verify_package(transaction.candidate)['version'], 'new')
        self.assertEqual((transaction.candidate / 'config.json').read_bytes(), updated)
        self.assertEqual(ProgramUpdate(transaction.job).data['state'], 'needs_recovery')

        create_manifest(transaction.candidate, 'v1.2.4', 'c' * 40)
        with self.assertRaisesRegex(ValueError, '候选目录身份未核实'):
            retry.recover()
        self.assertFalse(self.old.exists())
        self.assertEqual((transaction.candidate / 'config.json').read_bytes(), updated)
        self.assertEqual(ProgramUpdate(transaction.job).data['state'], 'needs_recovery')

    def test_valid_but_different_manifest_cannot_replace_preflight_version(self):
        for stage in ('moving_new', 'installed'):
            with self.subTest(stage=stage):
                old = package(self.root / stage / 'AutoZZZ', 'v1.2.2')
                (old / 'config.json').write_bytes(b'original settings')
                source = package(self.root / stage / 'source', 'v1.2.3')
                transaction = ProgramUpdate.prepare(old, source, probe=lambda _: [])
                report = transaction.job / 'runtime-report.json'
                write_json(report, dict(state='success', bundled=True, worker_exited=True, game_started=False,
                                        game_input_sent=False, revision='a'*40, applications=30,
                                        strategies=dict(auto_battle=1, dodge=1)))
                transaction.state('prepared', preflight=dict(manifest=transaction.data['new_manifest'], report=digest(report)))
                verify_preflight(transaction)

                def change_manifest(state):
                    if state != stage:
                        return
                    path = transaction.candidate if stage == 'moving_new' else old
                    (path / 'AutoZZZ.exe').write_bytes(b'fixture from another release')
                    create_manifest(path, 'v1.2.4', 'c'*40)
                    self.assertEqual(verify_package(path)['version'], 'v1.2.4')

                transaction.after_state = change_manifest
                with self.assertRaisesRegex(ValueError, '已变化|不一致'):
                    transaction.apply()
                if stage == 'moving_new':
                    self.assertEqual(transaction.data['state'], 'rolled_back')
                    original, substituted = old, transaction.candidate
                else:
                    self.assertEqual(transaction.data['state'], 'needs_recovery')
                    original, substituted = transaction.backup, old
                self.assertEqual(verify_package(original)['version'], 'v1.2.2')
                self.assertEqual(verify_package(substituted)['version'], 'v1.2.4')
                self.assertEqual((original / 'config.json').read_bytes(), b'original settings')

    def test_late_archived_user_edit_is_restored_when_promotion_can_roll_back(self):
        transaction = self.prepare()
        updated = b'late save from the old installation'

        def save_old_settings(state):
            if state == 'installed':
                (transaction.backup / 'config.json').write_bytes(updated)

        transaction.after_state = save_old_settings
        with self.assertRaisesRegex(ValueError, '用户文件已变化'):
            transaction.apply()
        self.assertEqual(transaction.data['state'], 'rolled_back')
        self.assertEqual(verify_package(self.old)['version'], 'old')
        self.assertEqual((self.old / 'config.json').read_bytes(), updated)
        self.assertEqual(verify_package(transaction.candidate)['version'], 'new')
        self.assertEqual((transaction.candidate / 'config.json').read_bytes(), b'current settings')

    def test_process_loss_recovery_reads_actual_directories_at_each_rename_boundary(self):
        for state in ('moving_old','old_moved','moving_new','installed'):
            with self.subTest(state=state):
                transaction=self.prepare()
                def stop(current):
                    if current==state:raise PowerLoss()
                transaction.after_state=stop
                with self.assertRaises(PowerLoss):transaction.apply()
                recovered=ProgramUpdate(transaction.job,probe=lambda _:[]);self.assertTrue(recovered.recover())
                self.assertEqual(verify_package(self.old)['version'],'old')
                self.assertEqual((self.old/'config.json').read_text(),'current settings')

    def test_bad_candidate_never_moves_current_program(self):
        transaction=self.prepare();(transaction.candidate/'README.md').write_text('tampered')
        with self.assertRaises(ValueError):transaction.apply()
        self.assertEqual(verify_package(self.old)['version'],'old');self.assertFalse(transaction.backup.exists())

    def test_old_program_changed_after_preparation_is_not_overwritten(self):
        transaction=self.prepare();(self.old/'README.md').write_text('local edit')
        with self.assertRaises(ValueError):transaction.apply()
        self.assertEqual((self.old/'README.md').read_text(),'local edit');self.assertFalse(transaction.backup.exists())

    def test_user_change_during_copy_is_kept_and_blocks_update(self):
        transaction=self.prepare()
        def changed(state):
            if state=='copying_user_data':(self.old/'config.json').write_text('concurrent edit')
        transaction.after_state=changed
        with self.assertRaises(ValueError):transaction.apply()
        self.assertEqual((self.old/'config.json').read_text(),'concurrent edit');self.assertFalse(transaction.backup.exists())

    def test_user_change_during_final_process_probe_restores_old_directory(self):
        transaction=self.prepare();probes=[]
        def probe(path):
            probes.append(path)
            if probes==[self.old,transaction.candidate,self.old]:
                (self.old/'config.json').write_text('late edit',encoding='utf-8')
            return []
        transaction.probe=probe
        with self.assertRaisesRegex(ValueError,'归档时用户文件已变化'):transaction.apply()
        self.assertEqual(transaction.data['state'],'rolled_back')
        self.assertEqual(verify_package(self.old)['version'],'old')
        self.assertEqual((self.old/'config.json').read_text(encoding='utf-8'),'late edit')
        self.assertFalse(transaction.backup.exists())
        self.assertTrue(transaction.candidate.exists())

    def test_new_user_data_after_promotion_is_not_overwritten_by_rollback(self):
        transaction=self.prepare()
        def changed(state):
            if state=='installed':
                (self.old/'config.json').write_text('new edit');raise OSError('fixture failure')
        transaction.after_state=changed
        with self.assertRaises(OSError):transaction.apply()
        self.assertEqual(transaction.data['state'],'needs_recovery')
        self.assertEqual((self.old/'config.json').read_text(),'new edit')
        self.assertEqual((transaction.backup/'config.json').read_text(),'current settings')

    def test_running_or_late_process_leaves_current_installation_intact(self):
        for results in (([12],),([], [], [13])):
            with self.subTest(results=results):
                transaction=self.prepare();responses=iter(results);transaction.probe=lambda _:next(responses)
                with self.assertRaises(RuntimeError):transaction.apply()
                self.assertEqual(verify_package(self.old)['version'],'old');self.assertFalse(transaction.backup.exists())

    def test_candidate_conflicting_with_unknown_user_file_is_rejected(self):
        (self.new/'custom.txt').write_text('program');create_manifest(self.new,'new','b'*40,reindex=True)
        (self.old/'custom.txt').write_text('user')
        transaction=self.prepare()
        with self.assertRaises(ValueError):transaction.apply()
        self.assertEqual((self.old/'custom.txt').read_text(),'user');self.assertFalse(transaction.backup.exists())

    def test_corrupt_archive_is_preserved_instead_of_silently_restored(self):
        transaction=self.prepare()
        def stopped(state):
            if state=='old_moved':raise PowerLoss()
        transaction.after_state=stopped
        with self.assertRaises(PowerLoss):transaction.apply()
        (transaction.backup/'README.md').write_text('changed archive')
        with self.assertRaises(ValueError):ProgramUpdate(transaction.job,probe=lambda _:[]).recover()
        self.assertFalse(self.old.exists());self.assertTrue(transaction.backup.exists());self.assertTrue(transaction.candidate.exists())

    def test_forged_journal_cannot_move_an_outside_backup(self):
        transaction=self.prepare();value=transaction.data.copy();value['backup']=str(self.root/'outside')
        transaction.journal.write_text(json.dumps(value))
        with self.assertRaises(ValueError):ProgramUpdate(transaction.job)

    def test_source_checkout_is_not_replaced(self):
        (self.old/'.git').mkdir()
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(verify_package(self.old)['version'],'old')

    def test_release_archive_roundtrip_excludes_user_data(self):
        archive=self.root/'release.zip';data=read_manifest(self.old)
        with zipfile.ZipFile(archive,'w') as zipped:
            zipped.writestr('AutoZZZ/','')
            for name in list(data['files'])+[MANIFEST]:zipped.write(self.old/name,'AutoZZZ/'+name)
        result=self.root/'extracted';extract_package(archive,result)
        self.assertEqual(verify_package(result/'AutoZZZ',strict=True)['version'],'old')
        self.assertFalse((result/'AutoZZZ/config.json').exists());self.assertFalse((result/'AutoZZZ/runs').exists())

    def test_outer_hash_cannot_bypass_a_conflicting_sdk_manifest(self):
        path=self.new/'onedragon/python/python311.dll';path.write_text('changed runtime')
        value=read_manifest(self.new);value['files']['onedragon/python/python311.dll']=digest(path)
        (self.new/MANIFEST).write_text(json.dumps(value))
        with self.assertRaises(ValueError):verify_package(self.new)
        with self.assertRaises(ValueError):create_manifest(self.new,'forged','b'*40)

    def test_relabel_does_not_include_later_user_scripts_or_reports(self):
        create_manifest(self.old,'relabelled','b'*40)
        metadata=read_manifest(self.old)
        self.assertNotIn('自定义/队伍.json',metadata['files']);self.assertNotIn('runs/report.json',metadata['files'])
        self.assertEqual(verify_package(self.old)['version'],'relabelled')

    def test_build_publish_preserves_indexed_and_legacy_root_user_data(self):
        self.assertEqual(preserve_user_files(self.old,self.new),3)
        self.assertEqual((self.new/'自定义/队伍.json').read_text(encoding='utf-8'),'custom')
        self.assertEqual(verify_package(self.new)['version'],'new')
        (self.old/MANIFEST).unlink()
        preserve_user_files(self.old,self.new)
        self.assertEqual((self.new/'config.json').read_text(),'current settings')

    def test_unsafe_archive_paths_and_links_are_rejected_before_extracting(self):
        for index,name in enumerate(('AutoZZZ/../escape','AutoZZZ/CON.txt','AutoZZZ/a\\b','AutoZZZ/name:stream','/absolute')):
            archive=self.root/f'bad{index}.zip'
            with zipfile.ZipFile(archive,'w') as zipped:zipped.writestr(name,'bad')
            if '\\'in name:archive.write_bytes(archive.read_bytes().replace(name.replace('\\','/').encode(),name.encode()))
            target=self.root/f'out{index}'
            with self.subTest(name=name),self.assertRaises(ValueError):extract_package(archive,target)
            self.assertFalse(target.exists())
        archive=self.root/'link.zip';item=zipfile.ZipInfo('AutoZZZ/link');item.external_attr=(stat.S_IFLNK|0o777)<<16
        with zipfile.ZipFile(archive,'w') as zipped:zipped.writestr(item,'outside')
        with self.assertRaises(ValueError):extract_package(archive,self.root/'link-output')


if __name__=='__main__':unittest.main()
