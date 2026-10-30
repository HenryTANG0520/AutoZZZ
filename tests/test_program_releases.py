import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

from autozzz.program_updates import ASSET,release_rows,ProgramUpdates
from autozzz.program_update import verify_preflight
from autozzz.onedragon_bundle import digest
from tests.test_program_update import package


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
            value=dict(state='success',bundled=True,worker_exited=True,game_started=False,game_input_sent=False,revision='a'*40,applications=30,strategies=dict(auto_battle=8,dodge=7))
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

    def test_cancellation_and_close_wait_for_original_worker(self):
        manager=ProgramUpdates(NS(source=Path('project/vendor/onedragon')))
        manager.job=dict(job_id='same',state='working');manager.worker=NS(is_alive=lambda:True,join=Mock())
        self.assertTrue(manager.cancel('same')['busy']);self.assertTrue(manager.cancelled.is_set());self.assertEqual(manager.signal['signal'],'cancel')
        with self.assertRaises(ValueError):manager.cancel('other')
        manager.close();manager.worker.join.assert_called_once()


if __name__=='__main__':unittest.main()
