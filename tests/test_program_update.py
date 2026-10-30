import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from autozzz.onedragon_bundle import MANIFEST_NAME,digest
from autozzz.program_package import MANIFEST,create_manifest,extract_package,read_manifest,verify_package,preserve_user_files
from autozzz.program_update import ProgramUpdate


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
