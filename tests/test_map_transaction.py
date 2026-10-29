import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from autozzz.map_transaction import MapTransaction


class MapTransactionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.tx=MapTransaction(self.root)
        self.map=self.root/'assets/game_data/world_patrol/entry/area/road_mask.png'
        self.route=self.root/'config/world_patrol_route/system/entry/area/01.yml'
        for path,raw in ((self.map,b'old map'),(self.route,b'old route')):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)

    def test_publish_retains_exact_before_and_after_group(self):
        identifier=self.tx.publish({self.map:b'new map',self.route:b'new route'},dict(area='area'),lambda:True)
        manifest,files,_=self.tx.load(identifier)
        self.assertEqual(manifest['metadata'],dict(area='area'))
        self.assertEqual([old for _,old,_ in files],[b'old map',b'old route'])
        self.assertEqual([new for _,_,new in files],[b'new map',b'new route'])
        self.assertEqual(self.map.read_bytes(),b'new map');self.assertEqual(self.route.read_bytes(),b'new route')
        self.assertFalse(self.tx.pending.exists())

    def test_failed_second_publish_restores_both_originals(self):
        put=self.tx._put;failed=[False]
        def fail(path,raw):
            if path==self.route and raw==b'new route' and not failed[0]:failed[0]=True;raise OSError('fixture locked')
            put(path,raw)
        with patch.object(self.tx,'_put',side_effect=fail),self.assertRaises(OSError):
            self.tx.publish({self.map:b'new map',self.route:b'new route'},{},lambda:True)
        self.assertEqual(self.map.read_bytes(),b'old map');self.assertEqual(self.route.read_bytes(),b'old route')
        self.assertFalse(self.tx.pending.exists())

    def test_interrupted_process_keeps_marker_until_known_versions_restored(self):
        put=self.tx._put
        def interrupted(path,raw):
            if path==self.route:raise SystemExit('fixture crash')
            put(path,raw)
        with patch.object(self.tx,'_put',side_effect=interrupted),self.assertRaises(SystemExit):
            self.tx.publish({self.map:b'new map',self.route:b'new route'},dict(area='area'),lambda:True)
        self.assertTrue(self.tx.pending.exists())
        with self.assertRaisesRegex(ValueError,'未完成'):self.tx.assert_clear()
        self.assertEqual(self.tx.recover(),dict(area='area'))
        self.assertEqual(self.map.read_bytes(),b'old map');self.assertFalse(self.tx.pending.exists())

    def test_external_edit_prevents_recovery_without_touching_any_file(self):
        put=self.tx._put
        def interrupted(path,raw):
            if path==self.route:raise SystemExit()
            put(path,raw)
        with patch.object(self.tx,'_put',side_effect=interrupted),self.assertRaises(SystemExit):self.tx.publish({self.map:b'new',self.route:b'new'},{},lambda:True)
        self.route.write_bytes(b'external edit')
        with self.assertRaisesRegex(ValueError,'外部修改'):self.tx.recover()
        self.assertEqual(self.map.read_bytes(),b'new');self.assertEqual(self.route.read_bytes(),b'external edit')
        self.assertTrue(self.tx.pending.exists())

    def test_new_file_removed_and_deleted_file_restored_on_failure(self):
        new=self.map.with_name('icon.yml');put=self.tx._put
        def interrupted(path,raw):
            if path==self.route:raise SystemExit()
            put(path,raw)
        with patch.object(self.tx,'_put',side_effect=interrupted),self.assertRaises(SystemExit):self.tx.publish({new:b'icons',self.map:None,self.route:b'new'},{},lambda:True)
        self.tx.recover()
        self.assertFalse(new.exists());self.assertEqual(self.map.read_bytes(),b'old map')

    def test_unsafe_paths_and_stale_guard_rejected_before_publish(self):
        for name in ('../outside.yml','config/accounts.yml','assets/game_data/world_patrol/entry/area/other.png','C:/outside/road_mask.png'):
            with self.subTest(name=name),self.assertRaises(ValueError):self.tx.target(name)
        with self.assertRaises(ValueError):self.tx.publish({self.map:b'new'},{},lambda:False)
        self.assertEqual(self.map.read_bytes(),b'old map');self.assertFalse(self.tx.pending.exists())

    def test_archive_payload_and_pending_manifest_hash_are_verified(self):
        identifier=self.tx.publish({self.map:b'new'},{},lambda:True)
        directory=self.root/'archives/maps'/identifier
        (directory/'0000-old.bin').write_bytes(b'altered')
        with self.assertRaisesRegex(ValueError,'变化'):self.tx.load(identifier)
        (directory/'0000-old.bin').write_bytes(b'old map')
        self.tx.pending.write_text(json.dumps(dict(id=identifier,manifest_hash='wrong')),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'信息已变化'):self.tx.recover()
        self.assertEqual(self.map.read_bytes(),b'new')

    def junction(self,link,target):
        self.assertTrue(link.absolute().is_relative_to(self.root.resolve()))
        self.assertTrue(target.resolve().is_relative_to(self.root.resolve()))
        target.mkdir(parents=True,exist_ok=True);link.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(target)],check=True,capture_output=True,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def test_target_and_pending_junctions_cannot_redirect_to_other_profile_files(self):
        protected=self.root/'config/protected/road_mask.png';protected.parent.mkdir(parents=True);protected.write_bytes(b'keep')
        link=self.map.parent/'redirect';self.junction(link,protected.parent)
        with self.assertRaisesRegex(ValueError,'只能发布'):self.tx.target((link/'road_mask.png').relative_to(self.root).as_posix())
        other=self.map.parent.parent/'other-area';other.mkdir();(other/'road_mask.png').write_bytes(b'other map')
        alias=self.map.parent/'other-map';self.junction(alias,other)
        with self.assertRaisesRegex(ValueError,'重定向'):self.tx.target((alias/'road_mask.png').relative_to(self.root).as_posix())
        nested=self.root/'pending-root';self.junction(nested/'config',nested/'other')
        marker=nested/'other/autozzz-map-transaction.json';marker.write_bytes(b'keep marker')
        with self.assertRaisesRegex(ValueError,'标记'):MapTransaction(nested)
        self.assertEqual(protected.read_bytes(),b'keep');self.assertEqual(marker.read_bytes(),b'keep marker')

    def test_archive_reparse_point_cannot_read_outside_its_own_snapshot(self):
        identifier=self.tx.publish({self.map:b'new'},{},lambda:True)
        protected=self.root/'config/protected.yml';protected.write_bytes(b'old map')
        original=self.root/'archives/maps'/identifier/'0000-old.bin'
        self.assertTrue(original.resolve().is_relative_to(self.root));original.unlink()
        self.junction(original,protected.parent)
        with self.assertRaisesRegex(ValueError,'归档目录外'):self.tx.load(identifier)
        self.assertEqual(protected.read_bytes(),b'old map')

    def test_preexisting_temporary_file_is_never_removed_or_overwritten(self):
        temporary=self.map.with_name(self.map.name+'.fixed.tmp');temporary.write_bytes(b'other file')
        with patch('autozzz.map_transaction.uuid.uuid4',return_value=SimpleNamespace(hex='fixed')),self.assertRaises(FileExistsError):self.tx._put(self.map,b'new')
        self.assertEqual(temporary.read_bytes(),b'other file');self.assertEqual(self.map.read_bytes(),b'old map')
