from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autozzz.onedragon_bundle import MANIFEST_NAME, BundleError, digest, verify_bundle,filesystem_path,tree_files
from autozzz.onedragon_runtime import RuntimePaths, RuntimeUnavailable, UPSTREAM_REVISION, PROTOCOL_VERSION


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='autozzz-bundle-')
        self.temp.name=str(filesystem_path(Path(self.temp.name)))
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'有空格的安装目录';self.bundle=self.root/'onedragon'
        files=('python/python.exe','python/python311.dll','python/LICENSE.txt','source/LICENSE',
               'source/src/zzz_od/context/zzz_context.py','source/config/project.yml',
               'bridge/autozzz/onedragon_worker.py','bridge/autozzz/onedragon_runtime.py',
               'bridge/autozzz/onedragon_bundle.py','bridge/autozzz/onedragon_diagnostics.py')
        for name in files:
            target=self.bundle/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(name,encoding='utf-8')
        self.metadata=dict(format=1,revision=UPSTREAM_REVISION,protocol=PROTOCOL_VERSION,python_version='3.11.18',
                           files={name:digest(self.bundle/name) for name in files},resources=['config/project.yml'])
        self.write()
        self.paths=RuntimePaths(self.bundle/'source',self.bundle/'python/python.exe',Path(self.temp.name)/'data',self.bundle)

    def write(self):
        (self.bundle/MANIFEST_NAME).write_text(json.dumps(self.metadata,ensure_ascii=False),encoding='utf-8')

    def test_discover_uses_distribution_without_developer_paths(self):
        with patch.dict(os.environ,{'LOCALAPPDATA':str(Path(self.temp.name)/'local')}):
            paths=RuntimePaths.discover(self.root,'global')
        self.assertEqual(paths.bundle,self.bundle)
        self.assertEqual(paths.source,self.bundle/'source')
        self.assertEqual(paths.python,self.bundle/'python/python.exe')
        self.assertEqual(paths.data,Path(self.temp.name)/'local/AutoZZZ/OneDragon/global')

    def test_package_verifies_and_initializes_without_git_or_overwriting_user_config(self):
        (self.paths.data/'config').mkdir(parents=True)
        target=self.paths.data/'config/project.yml';target.write_text('existing user setting')
        with patch('autozzz.onedragon_runtime.subprocess.run',side_effect=AssertionError('distribution must not invoke Git')):
            self.paths.prepare();self.paths.prepare()
        self.assertEqual(target.read_text(),'existing user setting')
        self.assertTrue((self.paths.data/'autozzz-runtime.json').is_file())

    def test_new_profile_gets_only_declared_resources(self):
        self.paths.prepare()
        self.assertEqual((self.paths.data/'config/project.yml').read_text(),'source/config/project.yml')
        self.assertFalse((self.paths.data/'src').exists())

    def test_tamper_is_rejected_even_with_existing_profile_marker(self):
        self.paths.prepare()
        (self.bundle/'bridge/autozzz/onedragon_worker.py').write_text('changed')
        with self.assertRaisesRegex(RuntimeUnavailable,'文件已变更'):self.paths.prepare()

    def test_extra_python_module_is_rejected(self):
        (self.bundle/'bridge/autozzz/extra.py').write_text('extra')
        with self.assertRaisesRegex(RuntimeUnavailable,'多出文件'):self.paths.verify()

    def test_missing_file_cannot_fall_back_to_development_tree(self):
        (self.bundle/'python/python.exe').unlink()
        with self.assertRaises(RuntimeUnavailable):RuntimePaths.discover(self.root).verify()

    def test_version_and_schema_mismatch_are_rejected(self):
        for name,value in (('revision','other'),('protocol',2),('protocol',True),('format',True),('python_version','3.12.0')):
            original=self.metadata[name];self.metadata[name]=value;self.write()
            with self.subTest(name=name,value=value),self.assertRaises(BundleError):verify_bundle(self.bundle,UPSTREAM_REVISION,PROTOCOL_VERSION)
            self.metadata[name]=original

    def test_unsafe_or_ambiguous_paths_are_rejected(self):
        for name in ('../external','/absolute','C:/outside','config\\file','source/../file','source//file',
                     'SOURCE/LICENSE'):
            self.metadata['files'][name]='0'*64;self.write()
            with self.subTest(name=name),self.assertRaises(BundleError):verify_bundle(self.bundle,UPSTREAM_REVISION,PROTOCOL_VERSION)
            self.metadata['files'].pop(name)

    def test_resource_list_cannot_copy_code_or_undeclared_files(self):
        for resource in ('src/zzz_od/context/zzz_context.py','config/missing.yml','config/../LICENSE'):
            self.metadata['resources']=[resource];self.write()
            with self.subTest(resource=resource),self.assertRaises(BundleError):verify_bundle(self.bundle,UPSTREAM_REVISION,PROTOCOL_VERSION)

    def test_profile_must_stay_outside_entire_distribution(self):
        for data in (self.bundle,self.bundle/'profile',self.root):
            with self.subTest(data=data),self.assertRaisesRegex(RuntimeUnavailable,'独立'):replace(self.paths,data=data).verify()

    def test_duplicate_json_keys_cannot_hide_a_different_revision(self):
        marker=self.bundle/MANIFEST_NAME
        marker.write_text('{"revision":"other","revision":"'+UPSTREAM_REVISION+'"}',encoding='utf-8')
        with self.assertRaisesRegex(BundleError,'重复字段'):verify_bundle(self.bundle,UPSTREAM_REVISION,PROTOCOL_VERSION)

    def test_interpreter_path_must_match_distribution(self):
        with self.assertRaisesRegex(RuntimeUnavailable,'路径'):replace(self.paths,python=Path('python.exe')).verify()

    def test_deep_dependency_paths_can_be_hashed_and_verified(self):
        name='python/Lib/site-packages/'+('deep-folder/'*18)+'resource.txt'
        target=filesystem_path(self.bundle/name);target.parent.mkdir(parents=True,exist_ok=True);target.write_text('deep dependency')
        self.metadata['files'][name]=digest(target);self.write()
        self.assertIn(name,dict(tree_files(self.bundle)))
        self.paths.verify()


if __name__=='__main__':unittest.main()
