import json
from pathlib import Path
import tempfile
import unittest

from autozzz.schedule_history import history, request_stop, record_path
from autozzz.schedules import ScheduleStore


class ScheduleHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=ScheduleStore(self.root,self.root/'plans.json')
        self.key='a'*32;self.instance='b'*32
        self.path=record_path(self.store,self.key,self.instance);self.path.parent.mkdir(parents=True)
        self.value=dict(format=1,owner=self.store.owner,schedule_id=self.key,instance=self.instance,
            name='原计划',started_at='2026-11-01',state='running',process=dict(pid=2,created=3))
        self.write()

    def write(self):self.path.write_text(json.dumps(self.value),encoding='utf-8')

    def test_history_survives_deleted_plan(self):
        self.assertEqual(history(self.store)[0]['name'],'原计划')
        self.assertFalse(self.store.path.exists())

    def test_stop_only_signals_original_instance(self):
        seen=[]
        request_stop(self.store,self.key,self.instance,alive=lambda p:seen.append(p) or True)
        self.assertEqual(seen,[dict(pid=2,created=3)])
        self.assertEqual((self.path.parent/'stop').read_text(),'stop\n')
        self.assertEqual(json.loads(self.path.read_text())['state'],'running')
        request_stop(self.store,self.key,self.instance,alive=lambda p:True)

    def test_terminal_or_exited_process_cannot_stop(self):
        with self.assertRaises(ValueError):request_stop(self.store,self.key,self.instance,alive=lambda p:False)
        self.value['state']='success';self.write()
        with self.assertRaises(ValueError):request_stop(self.store,self.key,self.instance,alive=lambda p:True)
        self.assertFalse((self.path.parent/'stop').exists())

    def test_owner_mismatch_is_visible_without_stop_capability(self):
        self.value['owner']='c'*32;self.write()
        self.assertTrue(history(self.store)[0]['invalid'])
        with self.assertRaises(ValueError):request_stop(self.store,self.key,self.instance,alive=lambda p:True)

    def test_reject_traversal_and_hardlinked_record(self):
        with self.assertRaises(ValueError):record_path(self.store,'../oops',self.instance)
        link=self.root/'other';link.hardlink_to(self.path)
        with self.assertRaises(ValueError):request_stop(self.store,self.key,self.instance,alive=lambda p:True)

    def test_missing_or_invalid_display_fields_are_unreadable(self):
        original=dict(self.value)
        for changes in ({'name':None},{'started_at':0},{'state':[]},{'state':'unknown'},{'format':True}):
            self.value=dict(original,**changes);self.write()
            self.assertTrue(history(self.store)[0]['invalid'])
            with self.assertRaises(ValueError):request_stop(self.store,self.key,self.instance,alive=lambda p:True)

    def test_hardlinked_stop_does_not_write_elsewhere(self):
        other=self.root/'target';other.write_text('unchanged')
        (self.path.parent/'stop').hardlink_to(other)
        with self.assertRaises(ValueError):request_stop(self.store,self.key,self.instance,alive=lambda p:True)
        self.assertEqual(other.read_text(),'unchanged')


if __name__=='__main__':unittest.main()
