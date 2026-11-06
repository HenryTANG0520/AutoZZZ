import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
import zipfile

from autozzz.program_updates import ASSET,release_rows,ProgramUpdates
from autozzz.program_update import verify_preflight
from autozzz.onedragon_bundle import digest
from autozzz.program_package import LEGACY_UPDATE_REVISION,MANIFEST,PROGRAM_ARCHIVES,SDK_BRIDGE_PACKAGE_FORMAT,extract_package
from tests.test_program_update import package
from tools.package_release import main as package_release


class ProgramReleaseTests(unittest.TestCase):
    def release(self):
        return dict(tag_name='v1.2.3',draft=False,prerelease=False,published_at='2026-10-30T10:00:00Z',assets=[dict(id=7,name=ASSET,size=100,digest='sha256:'+'a'*64,
            browser_download_url='https://github.com/HenryTANG0520/AutoZZZ/releases/download/v1.2.3/'+ASSET)])

    def test_releases_require_exact_repository_asset_digest_and_channel(self):
        original=self.release();self.assertEqual(release_rows([original])[0]['sha256'],'a'*64)
        for changes in (dict(draft=True),dict(prerelease=True),dict(tag_name='../v1'),dict(assets=None)):
            with self.subTest(changes=changes):self.assertEqual(release_rows([dict(original,**changes)]),[])
        for changes in (dict(id=True),dict(size=-1),dict(digest=None),dict(browser_download_url='https://example.invalid/a.zip'),dict(name='source.zip')):
            value=copy.deepcopy(original);value['assets'][0].update(changes)
            with self.subTest(changes=changes):self.assertEqual(release_rows([value]),[])
        self.assertEqual(release_rows([None,dict(assets=['bad'])]),[])
        beta=self.release();beta['prerelease']=True
        self.assertEqual(len(release_rows([beta],'beta')),1)
        beta['draft']=True;self.assertEqual(release_rows([beta],'beta'),[])
        with self.assertRaises(ValueError):release_rows([],'unknown')

    def test_preflight_is_bound_to_this_manifest_and_report_and_requires_exited_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);candidate=package(root/'candidate','new');report=root/'runtime-report.json'
            value=dict(state='success',bundled=True,worker_exited=True,game_started=False,game_input_sent=False,revision=LEGACY_UPDATE_REVISION,applications=30,strategies=dict(auto_battle=8,dodge=7))
            report.write_text(json.dumps(value));tx=NS(job=root,candidate=candidate,data=dict(new_manifest='new',preflight=dict(manifest='new',report=digest(report))))
            self.assertEqual(verify_preflight(tx)['applications'],30)
            for changes in (dict(worker_exited=False),dict(game_started=True),dict(applications=29),dict(revision='other'),dict(strategies={})):
                report.write_text(json.dumps(dict(value,**changes)));tx.data['preflight']['report']=digest(report)
                with self.subTest(changes=changes),self.assertRaises(ValueError):verify_preflight(tx)
            report.write_text(json.dumps(value));tx.data['preflight']['report']='0'*64
            with self.assertRaises(ValueError):verify_preflight(tx)

    def test_release_catalog_is_offline_and_does_not_claim_source_checkout_is_installable(self):
        service=NS(source=Path('project/vendor/onedragon'))
        manager=ProgramUpdates(service)
        result=manager.catalog();self.assertFalse(result['installed']);self.assertEqual(result['current'],'开发环境')

    def test_download_rejects_stale_or_unknown_selection_before_spawning_work(self):
        manager=ProgramUpdates(NS(source=Path('project/vendor/onedragon')));manager.releases=release_rows([self.release()]);manager.revision='current'
        with patch.object(manager,'_start') as started:
            for args in (('7','old'),('other','current')):
                with self.assertRaises(ValueError):manager.download(*args)
            started.assert_not_called()

    def test_release_export_rejects_a_changed_managed_file_until_explicit_reindex(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=package(root/'package','dev-fixture');archive=root/ASSET
            original=(source/MANIFEST).read_bytes()
            (source/'AutoZZZ.exe').write_bytes(b'changed fixture executable')
            (source/'config.json').write_bytes(b'fixture user settings')
            command=['package_release.py','--package',str(source),'--version','v1.2.3','--output',str(archive)]
            with patch('sys.argv',command),self.assertRaisesRegex(ValueError,'AutoZZZ.exe'):
                package_release()
            self.assertEqual((source/MANIFEST).read_bytes(),original)
            self.assertFalse(archive.exists())
            self.assertFalse(archive.with_suffix('.zip.sha256').exists())
            with patch('sys.argv',command+['--reindex']),patch('builtins.print'):
                package_release()
            extracted=root/'extracted';data=extract_package(archive,extracted)
            self.assertEqual(data['version'],'v1.2.3')
            self.assertEqual((extracted/'AutoZZZ/AutoZZZ.exe').read_bytes(),b'changed fixture executable')
            self.assertFalse((extracted/'AutoZZZ/config.json').exists())

    def test_cancellation_and_close_wait_for_original_worker(self):
        manager=ProgramUpdates(NS(source=Path('project/vendor/onedragon')))
        manager.job=dict(job_id='same',state='working');manager.worker=NS(is_alive=lambda:True,join=Mock())
        self.assertTrue(manager.cancel('same')['busy']);self.assertTrue(manager.cancelled.is_set());self.assertEqual(manager.signal['signal'],'cancel')
        with self.assertRaises(ValueError):manager.cancel('other')
        manager.close();manager.worker.join.assert_called_once()

    def test_sdk_transition_asset_keeps_exact_url_and_digest_requirements(self):
        _,name=PROGRAM_ARCHIVES[SDK_BRIDGE_PACKAGE_FORMAT]
        release=self.release()
        asset=release['assets'][0]
        asset['name']=name
        self.assertEqual(release_rows([release]),[])
        asset['browser_download_url']=asset['browser_download_url'].removesuffix(ASSET)+name
        self.assertEqual(release_rows([release])[0]['sha256'],'a'*64)
        asset['digest']='sha256:'+'b'*63
        self.assertEqual(release_rows([release]),[])

    def test_sdk_transition_uses_separate_zip_root_and_rejects_mismatched_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=package(root/'package','dev-fixture',revision='c'*40)
            archive_root,name=PROGRAM_ARCHIVES[SDK_BRIDGE_PACKAGE_FORMAT]
            archive=root/name
            command=['package_release.py','--package',str(source),'--version','v1.2.3','--output']
            original=(source/MANIFEST).read_bytes()
            with patch('sys.argv',command+[str(root/ASSET)]),self.assertRaisesRegex(ValueError,name):
                package_release()
            self.assertEqual((source/MANIFEST).read_bytes(),original)
            self.assertFalse((root/ASSET).exists())
            with patch('sys.argv',command+[str(archive)]),patch('builtins.print'):
                package_release()
            self.assertTrue(archive.with_suffix('.zip.sha256').read_text().startswith(digest(archive)))
            extracted=root/'extracted'
            metadata=extract_package(archive,extracted)
            self.assertEqual(metadata['format'],SDK_BRIDGE_PACKAGE_FORMAT)
            self.assertTrue((extracted/'AutoZZZ/AutoZZZ.exe').is_file())
            mismatched=root/'mismatched.zip'
            with zipfile.ZipFile(archive) as original_zip,zipfile.ZipFile(mismatched,'x') as wrong:
                for member in original_zip.namelist():
                    self.assertTrue(member.startswith(archive_root+'/'))
                    wrong.writestr('AutoZZZ/'+member.split('/',1)[1],original_zip.read(member))
            with self.assertRaisesRegex(ValueError,'清单格式不符'):
                extract_package(mismatched,root/'wrong')

            with zipfile.ZipFile(archive,'a') as original_zip:
                original_zip.writestr(archive_root+'///','')
            decorated=root/'decorated'
            with patch('autozzz.program_package.filesystem_path',wraps=Path) as checked_path:
                extract_package(archive,decorated)
            self.assertIn(((decorated/'AutoZZZ'),),[call.args for call in checked_path.call_args_list])
            self.assertTrue(all(Path(call.args[0]).is_relative_to(decorated) for call in checked_path.call_args_list))


if __name__=='__main__':unittest.main()
