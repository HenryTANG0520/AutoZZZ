from pathlib import Path
import tempfile
import unittest

from autozzz.rotation_snapshot import RotationSnapshot,snapshot


class RotationSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);(self.root/'config').mkdir();self.path=self.root/'config/account.yml';self.path.write_text('counter: 0',encoding='utf-8')
        self.guard=RotationSnapshot(self.root,snapshot(self.root))
    def test_authorized_source_counter_save_advances_version(self):
        before=self.guard.checkpoint();self.guard.authorized(self.path,lambda:self.path.write_text('counter: 1',encoding='utf-8'))
        self.assertNotEqual(self.guard.checkpoint(),before)
        RotationSnapshot(self.root,self.guard.checkpoint()).check()
    def test_external_write_before_save_is_preserved_and_latches_failure(self):
        self.path.write_text('external edit',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'外部修改'):self.guard.authorized(self.path,lambda:self.path.write_text('overwrite'))
        self.assertEqual(self.path.read_text(),'external edit')
        with self.assertRaisesRegex(ValueError,'失效'):self.guard.check()
    def test_unrelated_file_change_during_save_is_not_accepted(self):
        def save():self.path.write_text('counter: 1');(self.root/'config/other.yml').write_text('changed')
        with self.assertRaisesRegex(ValueError,'其他轮换资料'):self.guard.authorized(self.path,save)
        with self.assertRaisesRegex(ValueError,'失效'):self.guard.checkpoint()
    def test_source_new_file_and_deletion_are_explicit_changes(self):
        target=self.root/'config/new.yml'
        self.guard.authorized(target,lambda:target.write_text('new'))
        self.assertIn('config/new.yml',self.guard.checkpoint())
        self.guard.authorized(target,target.unlink);self.assertNotIn('config/new.yml',self.guard.checkpoint())
    def test_foreign_paths_and_invalid_digests_are_rejected(self):
        for value in ({'config/../other.yml':'a'*64},{'/config/a.yml':'a'*64},{'config/a.yml':'bad'}):
            with self.subTest(value=value),self.assertRaises(ValueError):RotationSnapshot(self.root,value)
    def test_run_records_are_not_configuration_mutations(self):
        folder=self.root/'records';folder.mkdir();(folder/'done.yml').write_text('done')
        self.guard.check()
    def test_shared_hardlink_is_rejected_before_save(self):
        other=self.root/'other.yml';other.hardlink_to(self.path)
        with self.assertRaisesRegex(ValueError,'硬链接'):self.guard.check()
