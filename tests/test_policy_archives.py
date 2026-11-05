import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from contextlib import nullcontext

from autozzz.onedragon_service import OneDragonService
from autozzz.policy_archives import PolicyArchives,checksum


class PolicyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary=TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.service=SimpleNamespace(data=self.root,_name=OneDragonService._name,
            _policy_kind=OneDragonService._policy_kind,_assert_idle=Mock())
        self.store=PolicyArchives(self.service)
        self.lock=patch('autozzz.policy_archives.profile_lock',lambda *_:nullcontext());self.lock.start();self.addCleanup(self.lock.stop)
        self.file=self.store.path('测试','auto_battle');self.file.parent.mkdir(parents=True)
        self.raw=b'author: Original\r\nscenes: []\r\n';self.file.write_bytes(self.raw)
        self.revision=checksum(self.raw)

    def test_delete_restore_exact_bytes_and_never_overwrite_new_file(self):
        result=self.store.remove('测试','auto_battle',self.revision)
        self.assertFalse(self.file.exists())
        restored=self.store.restore(result['archive_id'])
        self.assertEqual(self.file.read_bytes(),self.raw);self.assertEqual(restored['revision'],self.revision)
        self.file.write_bytes(b'new external content')
        with self.assertRaisesRegex(ValueError,'同名'):self.store.restore(result['archive_id'])
        self.assertEqual(self.file.read_bytes(),b'new external content')

    def test_builtin_stale_and_busy_preserve_original(self):
        for suffix in ('.sample.yml','.merged.yml'):
            path=self.file.with_name('测试'+suffix);path.write_text('builtin')
            with self.assertRaisesRegex(ValueError,'内置'):self.store.remove('测试','auto_battle',self.revision)
            path.unlink()
        with self.assertRaisesRegex(ValueError,'变化'):self.store.remove('测试','auto_battle','stale')
        self.service._assert_idle.side_effect=RuntimeError('running')
        with self.assertRaisesRegex(RuntimeError,'running'):self.store.remove('测试','auto_battle',self.revision)
        self.assertEqual(self.file.read_bytes(),self.raw)

    def test_all_known_config_references_protect_and_other_kind_is_independent(self):
        file=self.root/'config/01/charge_plan.yml';file.parent.mkdir(parents=True)
        for key in ('auto_battle','auto_battle_config'):
            file.write_text('plan_list:\n  - '+key+': 测试\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'仍被引用'):self.store.remove('测试','auto_battle',self.revision)
            self.assertTrue(self.file.exists())
        file.write_text('dodge_assistant_config: 测试\n',encoding='utf-8')
        result=self.store.remove('测试','auto_battle',self.revision)
        self.assertTrue(result['archive_id'])

    def test_tampered_archive_and_invalid_names_are_rejected(self):
        result=self.store.remove('测试','auto_battle',self.revision)
        archive=self.root/'autozzz/policy-archives'/result['archive_id']
        (archive/'policy.yml').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'变化'):self.store.restore(result['archive_id'])
        for key in ('../escape','CON','name.merged'):
            with self.assertRaises(ValueError):self.store.path(key,'auto_battle')
        for identifier in ('../escape','0'*31,'Z'*32):
            with self.assertRaises(ValueError):self.store.restore(identifier)
        self.assertFalse(self.file.exists())

    def test_archive_failure_does_not_delete_and_corrupt_config_blocks_removal(self):
        with patch.object(Path,'write_bytes',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.store.remove('测试','auto_battle',self.revision)
        self.assertEqual(self.file.read_bytes(),self.raw)
        file=self.root/'config/broken.yml';file.write_text('items: [',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'无法核对'):self.store.remove('测试','auto_battle',self.revision)
        self.assertEqual(self.file.read_bytes(),self.raw)

    def test_windows_case_alias_references_protect_both_policy_kinds(self):
        config=self.root/'config/01/battle_assistant.yml';config.parent.mkdir(parents=True)
        for kind,key in (('auto_battle','auto_battle_config'),('dodge','dodge_assistant_config')):
            with self.subTest(kind=kind):
                target=self.store.path('MixedCase',kind);target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(self.raw)
                config.write_text(key+': mixedcase\n',encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'仍被引用'):
                    self.store.remove('MixedCase',kind,self.revision)
                self.assertEqual(target.read_bytes(),self.raw)
                # A same-spelled name in another policy namespace is independent.
                other_key='dodge_assistant_config' if kind=='auto_battle' else 'auto_battle_config'
                config.write_text(other_key+': MIXEDCASE\n',encoding='utf-8')
                archived=self.store.remove('MixedCase',kind,self.revision)
                self.assertTrue(archived['archive_id']);self.assertFalse(target.exists())

    def test_windows_uppercase_reserved_suffix_cannot_delete_builtin_alias(self):
        for suffix in ('sample','merged'):
            builtin=self.file.with_name('Builtin.'+suffix+'.yml');builtin.write_bytes(self.raw)
            for alias in ('Builtin.'+suffix.upper(),'Builtin.'+suffix.title()):
                with self.subTest(alias=alias),self.assertRaisesRegex(ValueError,'sample|merged|内置'):
                    self.store.remove(alias,'auto_battle',self.revision)
                self.assertEqual(builtin.read_bytes(),self.raw)

    def test_global_name_guard_and_restore_reject_case_alias_suffixes(self):
        archived=self.store.remove('测试','auto_battle',self.revision)
        manifest_path=self.root/'autozzz/policy-archives'/archived['archive_id']/'manifest.json'
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        for suffix in ('SAMPLE','Sample','SaMpLe','MERGED','Merged','MeRgEd'):
            name='Builtin.'+suffix
            with self.subTest(suffix=suffix),self.assertRaises(ValueError):OneDragonService._name(name)
            builtin=self.file.with_name(name+'.yml');builtin.write_bytes(self.raw)
            manifest['name']=name;manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
            with self.assertRaises(ValueError):self.store.restore(archived['archive_id'])
            self.assertEqual(builtin.read_bytes(),self.raw)


if __name__=='__main__':unittest.main()
