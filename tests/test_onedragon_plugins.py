import json
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.onedragon_plugins import PluginStore,PluginManager,metadata,leaf,digest_tree
from autozzz.onedragon_plugins_ui import PluginSession
from autozzz.onedragon_runtime import RequestPending


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup);self.root=Path(self.directory.name);self.store=PluginStore(self.root)
    def plugin(self):
        folder=self.root/'plugins/demo';folder.mkdir(parents=True);(folder/'demo_factory.py').write_text('raise AssertionError("must not import")')
        (folder/'demo_const.py').write_text("APP_ID='demo'\nAPP_NAME='演示'\nPLUGIN_VERSION='2.0'\nraise AssertionError('must not import')",encoding='utf-8');return folder
    def test_listing_parses_static_metadata_without_executing_plugin(self):
        self.plugin();row=self.store.catalog()['plugins'][0];self.assertEqual(row['app_name'],'演示');self.assertFalse(row['enabled']);self.assertFalse(self.store.state.exists())
    def test_revision_tracks_package_changes_and_loading_preferences_but_not_python_cache(self):
        folder=self.plugin();before=self.store.revision();cache=folder/'__pycache__';cache.mkdir();(cache/'generated.pyc').write_bytes(b'generated')
        self.assertEqual(self.store.revision(),before);(folder/'data.txt').write_text('new');self.assertNotEqual(self.store.revision(),before)
        old=self.store.generation();self.store.save(['demo']);self.assertNotEqual(self.store.generation(),old);self.assertTrue(self.store.enabled('DEMO'))
    def test_unknown_or_broken_metadata_is_visible_without_import(self):
        folder=self.plugin();(folder/'second_factory.py').write_text('pass');self.assertIn('一个',metadata(folder)['error'])
    def test_archive_list_skips_invalid_records_and_restore_selection_is_explicit(self):
        manager=PluginManager.__new__(PluginManager);manager.root=self.root;manager.archives=self.root/'autozzz/plugin-archives'
        folder=manager.archives/('a'*32);folder.mkdir(parents=True);path=folder/'record.json'
        for record in ([],{'id':'a'*32,'action':'remove','created':'now','plugins':[dict(name='../bad',digest='0'*64)]}):
            path.write_text(json.dumps(record));self.assertEqual(manager.archive_list(),[])
        path.write_text(json.dumps(dict(id='a'*32,action='remove',created='now',plugins=[dict(name='demo',digest='0'*64)])))
        self.assertEqual(manager.archive_list()[0]['plugins'][0]['name'],'demo')
        for names,overwrites in ((None,[]),([],[]),(['demo'],['other']),(['../bad'],[])):
            with self.subTest(names=names),self.assertRaises(ValueError):manager.restore('a'*32,names,'revision',overwrites)
    def test_protected_names_paths_and_reserved_device_names_are_rejected(self):
        for name in ('../escape','a/b','C:\\escape','nul','demo.','.hidden','autozzz','one_dragon','name:stream'):
            with self.subTest(name=name),self.assertRaises(ValueError):leaf(name)
    def test_loading_manifest_is_strict_and_malformed_file_is_not_overwritten(self):
        self.store.state.parent.mkdir();self.store.state.write_text('{"format":1,"enabled":["../bad"],"generation":"old"}')
        old=self.store.state.read_bytes()
        with self.assertRaises(ValueError):self.store.read()
        self.assertEqual(self.store.state.read_bytes(),old)
    def test_plugin_code_and_manifest_are_in_rotation_snapshot(self):
        from autozzz.rotation_snapshot import snapshot,tracked
        folder=self.plugin();before=snapshot(self.root);self.store.save(['demo']);after=snapshot(self.root)
        self.assertNotEqual(before,after);self.assertTrue(tracked('autozzz/plugin-state.json'));self.assertTrue(tracked('plugins/demo/demo_factory.py'))
        self.assertFalse(tracked('plugins/demo/__pycache__/demo.pyc'))
        (folder/'demo_factory.py').write_text('changed');self.assertNotEqual(snapshot(self.root),after)
    def test_third_party_scalar_and_json_fields_do_not_inherit_builtin_rules(self):
        from autozzz.onedragon_config import _field,ConfigurationStore
        field=_field('custom_negative',-3,plugin=True);self.assertNotIn('minimum',field)
        ConfigurationStore._validate_scalar(None,field,-4)
        field=_field('custom_options',dict(paths=['a','b']),plugin=True);self.assertEqual(field['kind'],'json')
        ConfigurationStore._validate_scalar(None,field,dict(paths=[]))
        with self.assertRaises(ValueError):ConfigurationStore._validate_scalar(None,field,dict(nan=float('nan')))
        unavailable=property(lambda self:None)
        with patch('autozzz.onedragon_config._getter_metadata',side_effect=OSError('source unavailable')):
            self.assertEqual(_field('custom',None,unavailable,plugin=True)['kind'],'json')
        with self.assertRaises(ValueError):_field('custom_unknown',False)


class Client:
    def __init__(self,*_):
        self.alive=False;self.protocol_available=True;self.calls=[];self.requests={};self.next=0;self.pending=set();self.finished=threading.Event();self.status_waiting=threading.Event();self.close_allowed=threading.Event();self.close_allowed.set()
    def start(self):self.alive=True;raise RequestPending('hello')
    def finish_start(self,**_):return {}
    def submit(self,method,**params):
        self.next+=1;key=str(self.next);self.calls.append((method,params));self.requests[key]=method;self.pending.add(key);return key
    def result(self,key,**_):
        if key in self.pending:self.pending.remove(key);raise RequestPending(key)
        method=self.requests[key]
        if method=='plugin_catalog':return dict(plugins=[],revision='r',directory='fixture')
        if method=='plugin_preview':return dict(job_id='original',busy=True)
        if method=='plugin_status':
            self.status_waiting.set();return dict(job_id='original',busy=not self.finished.is_set(),state='done' if self.finished.is_set() else 'previewing',result=dict(token='ready'))
        return {}
    def close(self,**_):
        if not self.close_allowed.wait(.01):raise RequestPending('original exit')
        self.alive=False


def wait(predicate):
    end=time.monotonic()+3
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('原插件会话未在时限内响应')


class SessionTests(unittest.TestCase):
    def test_broken_plugin_configuration_does_not_hide_builtin_configuration(self):
        from autozzz.onedragon_apps_ui import ApplicationsPanel
        panel=ApplicationsPanel.__new__(ApplicationsPanel)
        def call(_client,method,**params):
            if method=='catalog':return dict(failures=[],applications=[dict(id='email',name='邮件',configurable=True),dict(id='fixture',name='插件',configurable=True,third_party=True)])
            if method=='configuration_options':return dict(challenges=[])
            if params['app_id']=='fixture':raise ValueError('fixture property failed')
            return dict(fields=[],revision='r')
        with patch.object(panel,'call',side_effect=call):result=panel._initial(None)
        self.assertIn('email',result['descriptions']);self.assertEqual(result['plugin_failures'][0]['error'],'fixture property failed')
        self.assertEqual(result['apps'][1]['configuration_error'],'fixture property failed')
    def test_close_waits_original_job_and_process_without_restarting_or_replaying_mutation(self):
        client=Client();events=queue.Queue()
        with patch('autozzz.onedragon_plugins_ui.OneDragonClient',return_value=client):
            session=PluginSession(None,events.put)
            try:
                wait(lambda:any(name=='plugin_catalog' for name,_ in client.calls));session.send('plugin_preview',path='fixture.zip',kind='zip')
                wait(client.status_waiting.is_set);client.close_allowed.clear();session.close();self.assertTrue(session.worker.is_alive())
                client.finished.set();time.sleep(.15);self.assertTrue(session.worker.is_alive());client.close_allowed.set();session.worker.join(3)
                self.assertFalse(session.worker.is_alive());self.assertEqual(sum(method=='plugin_preview' for method,_ in client.calls),1)
                self.assertFalse(client.alive)
            finally:client.finished.set();client.close_allowed.set();session.close();session.worker.join(3)


if __name__=='__main__':unittest.main()
