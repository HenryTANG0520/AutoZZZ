from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from autozzz.devtools_resources import ResourceArchives,resource_name
from autozzz.onedragon_devtools_host import ReadOnlyCaptureController


class DeveloperResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.tx=ResourceArchives(self.root)
        self.file=self.root/'assets/image_analysis_pipelines/自定义.yml'
        self.file.parent.mkdir(parents=True);self.file.write_bytes(b'old')

    def test_edit_records_and_restores_before_version(self):
        self.tx.mutate([self.file],lambda:self.file.write_bytes(b'new'),'fixture_edit')
        rows=self.tx.list();self.assertEqual(rows[0]['metadata']['status'],'saved')
        self.tx.restore(rows[0]['id']);self.assertEqual(self.file.read_bytes(),b'old')

    def test_external_changes_cannot_be_replaced_by_archive(self):
        self.tx.mutate([self.file],lambda:self.file.write_bytes(b'new'),'fixture_edit')
        identifier=self.tx.list()[0]['id'];self.file.write_bytes(b'external')
        with self.assertRaises(ValueError):self.tx.restore(identifier)
        self.assertEqual(self.file.read_bytes(),b'external')

    def test_failed_source_edit_is_visible_and_recoverable(self):
        def edit():self.file.write_bytes(b'partial');raise ValueError('source failure')
        with self.assertRaisesRegex(ValueError,'source failure'):self.tx.mutate([self.file],edit,'fixture_failure')
        row=self.tx.list()[0];self.assertEqual(row['metadata']['status'],'failed')
        self.tx.restore(row['id']);self.assertEqual(self.file.read_bytes(),b'old')

    def test_new_unknown_assets_are_allowed_and_restored_to_absent(self):
        target=self.root/'assets/template/自定义/引擎未注册/raw.png'
        def edit():target.parent.mkdir(parents=True);target.write_bytes(b'fixture PNG')
        self.tx.mutate([target],edit,'custom_asset')
        self.tx.restore(self.tx.list()[0]['id']);self.assertFalse(target.exists())

    def test_path_traversal_source_files_and_shared_files_rejected(self):
        for name in ('../outside.yml','config/one_dragon.yml','assets/template/test/custom/evil.py','assets/template/test/custom/raw.png/../evil.py'):
            with self.subTest(name=name),self.assertRaises(ValueError):self.tx.target(name)
        link=self.file.with_name('shared.yml');link.hardlink_to(self.file)
        with self.assertRaises(ValueError):self.tx.mutate([self.file],lambda:None,'shared')
        self.assertEqual(self.file.read_bytes(),b'old')

    def test_interrupted_journal_prevents_new_edit(self):
        self.tx.pending.parent.mkdir(parents=True,exist_ok=True);self.tx.pending.write_text('{"id":"interrupted"}')
        with self.assertRaisesRegex(ValueError,'中断'):self.tx.mutate([self.file],lambda:self.file.write_bytes(b'bad'),'new')
        self.assertEqual(self.file.read_bytes(),b'old')

    def interrupted_edit(self):
        class ProcessDeath(BaseException):pass
        second=self.file.with_name('second.yml');second.write_bytes(b'original second')
        def edit():
            self.file.write_bytes(b'first intermediate');self.file.write_bytes(b'first final')
            second.write_bytes(b'changed second');raise ProcessDeath()
        with self.assertRaises(ProcessDeath):self.tx.mutate([self.file,second],edit,'interrupted_source')
        return second

    def test_new_process_recovers_verified_interrupted_writes(self):
        second=self.interrupted_edit();new=ResourceArchives(self.root)
        review=new.interruption();self.assertTrue(review['pending']);self.assertFalse(review['conflicts'])
        self.assertEqual(len(review['remaining']),3)
        new.recover_edit(review)
        self.assertEqual(self.file.read_bytes(),b'old');self.assertEqual(second.read_bytes(),b'original second')
        self.assertFalse(new.interruption()['pending']);self.assertTrue(new.list())

    def test_recovery_rejects_external_mutation_and_stale_review(self):
        second=self.interrupted_edit();new=ResourceArchives(self.root);old_review=new.interruption()
        self.file.write_bytes(b'outside editor')
        with self.assertRaisesRegex(ValueError,'已变化'):new.recover_edit(old_review)
        review=new.interruption();self.assertTrue(review['conflicts'])
        with self.assertRaisesRegex(ValueError,'外部修改'):new.recover_edit(review)
        self.assertEqual(second.read_bytes(),b'changed second');self.assertTrue(new.edit_journal.exists())

    def test_recovery_itself_can_be_interrupted_and_retried(self):
        second=self.interrupted_edit();new=ResourceArchives(self.root);put=new._put
        class ProcessDeath(BaseException):pass
        def fail_after_first_recovery(path,raw):
            put(path,raw)
            if path==new.edit_journal:
                import json
                marker=json.loads(raw)
                if marker['state']=='recovering'and len(marker['remaining'])==2:raise ProcessDeath()
        new._put=fail_after_first_recovery
        with self.assertRaises(ProcessDeath):new.recover_edit(new.interruption())
        again=ResourceArchives(self.root);again.recover_edit(again.interruption())
        self.assertEqual(self.file.read_bytes(),b'old');self.assertEqual(second.read_bytes(),b'original second')

    def test_crash_during_known_file_publication_recovers_pending_and_group(self):
        class ProcessDeath(BaseException):pass
        put=self.tx._put
        def fail_after_publish(path,raw):
            put(path,raw)
            if path==self.file and raw==b'new':raise ProcessDeath()
        self.tx._put=fail_after_publish
        with self.assertRaises(ProcessDeath):self.tx.mutate([self.file],lambda:self.file.write_bytes(b'new'),'known_pending')
        new=ResourceArchives(self.root);review=new.interruption();self.assertFalse(review['conflicts'])
        new.recover_edit(review);self.assertEqual(self.file.read_bytes(),b'old')
        self.assertFalse(new.pending.exists());self.assertFalse(new.edit_journal.exists())

    def test_unrecorded_low_level_write_is_never_adopted_for_recovery(self):
        import os
        class ProcessDeath(BaseException):pass
        def edit():
            fd=os.open(self.file,os.O_WRONLY|os.O_TRUNC)
            try:os.write(fd,b'unknown direct descriptor')
            finally:os.close(fd)
            raise ProcessDeath()
        with self.assertRaises(ProcessDeath):self.tx.mutate([self.file],edit,'unsupported_io')
        new=ResourceArchives(self.root);review=new.interruption();self.assertTrue(review['conflicts'])
        with self.assertRaises(ValueError):new.recover_edit(review)
        self.assertEqual(self.file.read_bytes(),b'unknown direct descriptor')

    def test_parent_lease_loss_prevents_source_write(self):
        def check():raise RuntimeError('parent exited')
        self.tx.checkpoint=check
        with self.assertRaisesRegex(RuntimeError,'parent exited'):self.tx.mutate([self.file],lambda:self.file.write_bytes(b'bad'),'new')
        self.assertEqual(self.file.read_bytes(),b'old')

    def test_filename_rules_retain_unicode_unknown_ids(self):
        self.assertEqual(resource_name('自定义未知角色'),'自定义未知角色')
        for value in ('../escape','CON','foo:bar','a/b',' name','name.',''):
            with self.subTest(value=value),self.assertRaises(ValueError):resource_name(value)

    def test_native_controller_has_no_input_or_focus_fallback(self):
        controller=ReadOnlyCaptureController()
        with self.assertRaises(RuntimeError):controller.screenshot()
        for method in ('click','key_tap','activate','close_game','input_str','game_win'):
            with self.subTest(method=method),self.assertRaises(RuntimeError):getattr(controller,method)
        frame=object();controller.frame=frame
        self.assertIs(controller.screenshot()[1],frame)


if __name__=='__main__':unittest.main()
