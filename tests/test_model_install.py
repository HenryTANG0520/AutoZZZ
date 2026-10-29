import io
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from autozzz.model_install import ModelInstall,extract_model


class ModelInstallTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.tx=ModelInstall(self.root)
        self.identifier='0'*32;self.family='flash_classifier';self.model='fixture-1'
        self.target=self.tx.target(self.family,self.model)
        self.target.mkdir(parents=True);(self.target/'model.onnx').write_bytes(b'old model')
        self.stage=self.tx.job_dir(self.identifier)/'content';self.stage.mkdir(parents=True)
        (self.stage/'model.onnx').write_bytes(b'new model')
        self.before=self.tx.digest(self.target)

    def test_install_retains_old_directory_and_marks_new_preparation_revision(self):
        self.tx.publish(self.family,self.model,self.identifier)
        self.assertEqual((self.target/'model.onnx').read_bytes(),b'new model')
        self.assertEqual(self.tx.digest(self.tx.archive_dir(self.identifier)/'previous'),self.before)
        self.assertIn(self.identifier,self.tx.version.read_text())
        self.assertFalse(self.tx.pending.exists())

    def crash(self,after_new=False):
        move=self.tx.move
        def interrupted(origin,target):
            if origin==self.stage and not after_new:raise SystemExit('fixture crash')
            move(origin,target)
            if origin==self.stage and after_new:raise SystemExit('fixture crash')
        with patch.object(self.tx,'move',side_effect=interrupted),self.assertRaises(SystemExit):
            self.tx.publish(self.family,self.model,self.identifier)

    def test_crash_after_moving_old_model_recovers_exact_directory(self):
        self.tx.put(self.tx.version,b'version: previous\n');self.crash()
        self.assertFalse(self.target.exists());self.assertTrue(self.tx.pending.exists())
        with self.assertRaisesRegex(ValueError,'未完成'):self.tx.assert_clear()
        self.assertTrue(self.tx.recover());self.assertFalse(self.tx.recover())
        self.assertEqual(self.tx.digest(self.target),self.before)
        self.assertEqual(self.tx.version.read_bytes(),b'version: previous\n')

    def test_crash_after_new_model_retains_unpublished_model_and_restores_original(self):
        self.crash(after_new=True);self.tx.recover()
        self.assertEqual(self.tx.digest(self.target),self.before)
        self.assertEqual((self.tx.archive_dir(self.identifier)/'unpublished/model.onnx').read_bytes(),b'new model')
        self.assertFalse(self.tx.version.exists())

    def test_replace_failure_restores_original_without_losing_error(self):
        move=self.tx.move
        def failed(origin,target):
            if origin==self.stage:raise PermissionError('fixture locked')
            move(origin,target)
        with patch.object(self.tx,'move',side_effect=failed),self.assertRaisesRegex(PermissionError,'fixture locked'):
            self.tx.publish(self.family,self.model,self.identifier)
        self.assertEqual(self.tx.digest(self.target),self.before);self.assertFalse(self.tx.pending.exists())

    def test_external_model_or_version_edit_prevents_every_recovery_write(self):
        self.crash(after_new=True)
        (self.target/'external.txt').write_bytes(b'external')
        version=b'version: external\n';self.tx.put(self.tx.version,version)
        current=self.tx.digest(self.target)
        with self.assertRaisesRegex(ValueError,'外部修改'):self.tx.recover()
        self.assertEqual(self.tx.digest(self.target),current);self.assertEqual(self.tx.version.read_bytes(),version)
        self.assertTrue(self.tx.pending.exists())

    def test_archive_tamper_is_rejected_before_any_move(self):
        self.crash();manifest=self.tx.archive_dir(self.identifier)/'manifest.json'
        manifest.write_text('{}')
        with self.assertRaisesRegex(ValueError,'归档已变化'):self.tx.recover()
        self.assertFalse(self.target.exists());self.assertTrue(self.tx.pending.exists())

    def test_unsafe_families_names_and_paths_are_refused(self):
        for family,model in (('other','fixture'),('onnx_ocr','../outside'),('onnx_ocr','x:y'),('onnx_ocr','trailing.'),('onnx_ocr','NUL'),('onnx_ocr','CON.onnx')):
            with self.assertRaises(ValueError):self.tx.target(family,model)
        with self.assertRaises(ValueError):self.tx.safe(self.root.parent/'outside')
        with self.assertRaises(ValueError):self.tx.job_dir('../outside')

    def test_unknown_temporary_file_collision_is_preserved(self):
        temporary=self.tx.version.with_name(self.tx.version.name+'.'+'1'*32+'.tmp')
        temporary.parent.mkdir(parents=True,exist_ok=True);temporary.write_bytes(b'external')
        with patch('autozzz.model_install.uuid.uuid4') as identifier:
            identifier.return_value.hex='1'*32
            with self.assertRaises(FileExistsError):self.tx.put(self.tx.version,b'new')
        self.assertEqual(temporary.read_bytes(),b'external');self.assertFalse(self.tx.version.exists())

    def test_windows_junction_cannot_redirect_model_or_staging_to_another_area(self):
        link=self.root/'redirect';destination=self.root/'owned';destination.mkdir()
        assert link.absolute().is_relative_to(self.root) and destination.resolve().is_relative_to(self.root)
        subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(destination)],check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        with self.assertRaisesRegex(ValueError,'重定向'):self.tx.safe(link/'model.onnx')
        archive=self.root/'model.zip'
        with zipfile.ZipFile(archive,'w') as package:package.writestr('model.onnx',b'model')
        with self.assertRaisesRegex(ValueError,'重定向'):extract_model(archive,link/'content',['model.onnx'],False,lambda:False)
        self.assertFalse(any(destination.iterdir()))


class ModelArchiveTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.archive=self.root/'model.zip';self.output=self.root/'content'

    def package(self,entries):
        with zipfile.ZipFile(self.archive,'w') as package:
            for name,raw in entries:
                if isinstance(name,str) and '\\' in name:
                    # Windows ZipInfo 会主动规范化分隔符，保留恶意压缩包的实际字节名。
                    info=zipfile.ZipInfo('fixture');info.filename=info.orig_filename=name;name=info
                package.writestr(name,raw)

    def test_extracts_required_files_ignores_unneeded_executables(self):
        self.package([('model.onnx',b'weights'),('labels.csv',b'labels'),('unknown.exe',b'ignored')])
        extract_model(self.archive,self.output,['model.onnx','labels.csv'],False,lambda:False)
        self.assertEqual(sorted(p.name for p in self.output.iterdir()),['labels.csv','model.onnx'])

    def test_missing_files_rejected_before_extracting_any_weight(self):
        self.package([('model.onnx',b'weights')])
        with self.assertRaisesRegex(ValueError,'缺少'):extract_model(self.archive,self.output,['model.onnx','labels.csv'],False,lambda:False)
        self.assertFalse(any(self.output.iterdir()))

    def test_ocr_needs_exactly_one_nonempty_dictionary(self):
        self.package([('det.onnx',b'model'),('a_dict.txt',b'a'),('b_dict.txt',b'b')])
        with self.assertRaisesRegex(ValueError,'字典数量'):extract_model(self.archive,self.output,['det.onnx'],True,lambda:False)

    def test_path_traversal_and_symlink_are_refused_even_if_not_selected(self):
        for name in ('../outside','C:/outside','a\\b','NUL.txt'):
            with self.subTest(name=name):
                self.package([(name,b'bad'),('model.onnx',b'good')])
                with self.assertRaisesRegex(ValueError,'无效路径'):extract_model(self.archive,self.root/name.replace('/','_').replace('\\','_').replace(':','_')/'out',['model.onnx'],False,lambda:False)
        symlink=zipfile.ZipInfo('link');symlink.external_attr=(stat.S_IFLNK|0o777)<<16
        self.package([(symlink,b'outside'),('model.onnx',b'good')])
        with self.assertRaisesRegex(ValueError,'无效路径'):extract_model(self.archive,self.output,['model.onnx'],False,lambda:False)

    def test_duplicate_case_sensitive_paths_are_refused(self):
        self.package([('model.onnx',b'a'),('MODEL.ONNX',b'b')])
        with self.assertRaisesRegex(ValueError,'重复'):extract_model(self.archive,self.output,['model.onnx'],False,lambda:False)

    def test_cancellation_prevents_publishing_a_partial_model(self):
        self.package([('model.onnx',b'weights')])
        with self.assertRaises(InterruptedError):extract_model(self.archive,self.output,['model.onnx'],False,lambda:True)
        self.assertFalse(any(self.output.iterdir()))
