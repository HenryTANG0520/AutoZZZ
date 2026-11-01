from pathlib import Path
import tempfile
import threading
import time
import unittest

from autozzz.program_update_check import MetadataCheck,result_message
from autozzz.program_update_preferences import UpdatePreferences,UpdatePreferencesStore
from autozzz.onedragon_runtime import RequestPending


class Client:
    def __init__(self,_):self.calls=[];self.waits={};self.alive=True;self.closed=0;self.starts=0;self.held=threading.Event();self.release=threading.Event()
    def start(self,timeout):self.starts+=1;raise RequestPending('hello')
    def finish_start(self,timeout):return {}
    def submit(self,method,**params):
        self.calls.append((method,params));return str(len(self.calls))
    def result(self,key,timeout):
        count=self.waits.get(key,0);self.waits[key]=count+1
        if count==0:raise RequestPending(key)
        method=self.calls[int(key)-1][0]
        if method=='program_check':return dict(job_id='original',busy=True,state='working',detail='querying')
        if method=='program_cancel':return dict(job_id='original',busy=False,state='cancelled',detail='cancelled')
        if method=='program_status':return dict(job_id='original',busy=False,state='success',detail='done',result=dict(releases=[],current='dev',unavailable_reason='没有完整发布包'))
        raise AssertionError(method)
    def close(self,timeout):
        self.closed+=1
        if self.closed==1:raise RequestPending('process-shutdown')
        self.alive=False


class UpdateCheckTests(unittest.TestCase):
    def test_original_request_retained_through_timeouts_and_close(self):
        client=Client(None);events=[]
        check=MetadataCheck(None,'stable',events.append,client_factory=lambda _:client)
        check.worker.join(5);self.assertFalse(check.alive);self.assertTrue(check.process_exited)
        self.assertEqual([row[0] for row in client.calls],['program_check','program_status'])
        self.assertEqual(client.starts,1);self.assertEqual(client.closed,2)
        self.assertEqual(events[-1]['state'],'success');self.assertEqual(events[-1]['message'],'没有完整发布包')
        self.assertTrue(all(count==2 for count in client.waits.values()))

    def test_cancel_after_original_job_received_sends_one_cancel(self):
        client=Client(None);events=[];entered=threading.Event();release=threading.Event()
        old=client.result
        def wait_result(key,timeout):
            if client.calls[int(key)-1][0]=='program_check':
                entered.set();release.wait(3)
            return old(key,timeout)
        client.result=wait_result
        check=MetadataCheck(None,'beta',events.append,client_factory=lambda _:client)
        self.assertTrue(entered.wait(2));check.cancel();release.set();check.worker.join(5)
        self.assertFalse(check.alive);self.assertEqual([row[0] for row in client.calls],['program_check','program_cancel'])
        self.assertEqual(events[-1]['state'],'cancelled');self.assertTrue(events[-1]['process_exited'])

    def test_failed_query_still_waits_original_process(self):
        client=Client(None)
        def fail(*args,**kwargs):raise ValueError('network failed')
        client.submit=fail;events=[];check=MetadataCheck(None,'stable',events.append,client_factory=lambda _:client)
        check.worker.join(3);self.assertEqual(events[-1]['state'],'failed');self.assertTrue(check.process_exited)

    def test_different_tag_is_not_claimed_newer(self):
        text=result_message(dict(current='v9',releases=[dict(version='v1')]))
        self.assertIn('最新发布',text);self.assertNotIn('发现新版',text)


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=UpdatePreferencesStore(self.root,self.root/'prefs.json')
    def test_missing_read_has_no_side_effect_and_is_off(self):
        self.assertEqual(self.store.load(),UpdatePreferences());self.assertFalse(self.store.path.exists())
    def test_save_and_revision_conflict(self):
        before=self.store.load();value=UpdatePreferences(True,'beta');self.store.save(value,before)
        self.assertEqual(self.store.load(),value)
        with self.assertRaises(ValueError):self.store.save(UpdatePreferences(),before)
    def test_invalid_values_do_not_save(self):
        for enabled,channel in ((1,'stable'),(True,'other')):
            with self.assertRaises(ValueError):self.store.save(UpdatePreferences(enabled,channel),UpdatePreferences())
        self.assertFalse(self.store.path.exists())
    def test_corrupt_or_shared_file_is_preserved(self):
        self.store.path.write_text('{broken')
        with self.assertRaises(ValueError):self.store.load()
        self.assertEqual(self.store.path.read_text(),'{broken')
        self.store.path.write_text('{"enabled":false,"channel":"stable"}')
        (self.root/'link').hardlink_to(self.store.path)
        with self.assertRaises(ValueError):self.store.load()


if __name__=='__main__':unittest.main()
