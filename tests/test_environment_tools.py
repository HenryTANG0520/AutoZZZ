from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz import environment_tools as e
from autozzz import gamepad_install as g


class Store:
    revision='initial';values={};writes=[]
    def __init__(self,service):self.service=service
    def describe(self,app):
        return dict(revision=self.revision,fields=[
            dict(name='pip_source',value=self.values.get('pip_source',e.INDEXES[0]),options=[dict(label=str(i),value=v) for i,v in enumerate(e.INDEXES)]),
            dict(name='gh_proxy_url',configured=bool(self.values.get('gh_proxy_url')))])
    def config(self,app):return NS(is_personal_proxy=False,personal_proxy='')
    def save(self,app,values,revision):
        if revision!=Store.revision:raise ValueError('配置已被其他窗口修改')
        Store.values.update(values);Store.writes.append(deepcopy(values));Store.revision='changed'


class EnvironmentToolsTests(unittest.TestCase):
    def setUp(self):
        Store.revision='initial';Store.values={};Store.writes=[]
        self.patch=patch.object(e,'EnvironmentStore',Store);self.patch.start();self.addCleanup(self.patch.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.manager=e.EnvironmentTools(NS(data=Path(self.temp.name),_assert_idle=lambda:None))
        self.addCleanup(self.manager.close)

    def run_job(self,action):
        self.manager.start(action,'initial');self.manager.worker.join(5)
        self.assertFalse(self.manager.busy);return self.manager.status()

    def test_speed_probe_reads_index_then_explicit_apply_only(self):
        with patch.object(e,'read_network',return_value=b'<a href="vgamepad-0.1.0.tar.gz">file</a>') as network:
            state=self.run_job('pip_test')
        self.assertEqual(state['state'],'success');self.assertEqual(network.call_count,3)
        self.assertTrue(all(call.args[0].endswith('/simple/vgamepad/') for call in network.call_args_list))
        self.assertEqual(Store.writes,[])
        result=self.manager.apply(state['job_id'],e.INDEXES[1],'initial')
        self.assertEqual(result['pip_source'],e.INDEXES[1]);self.assertEqual(Store.writes,[dict(pip_source=e.INDEXES[1])])

    def test_proxy_discovery_is_candidate_and_preserves_config_on_change(self):
        content=b'<a href=\\\\\\"https://proxy.example.org\\\\\\" target="_blank">Go</a>'
        with patch.object(e,'read_network',return_value=content):state=self.run_job('proxy_discover')
        self.assertEqual(state['state'],'success',state);self.assertEqual(Store.writes,[])
        self.assertEqual(state['candidate'],'https://proxy.example.org')
        Store.revision='other-window'
        with self.assertRaisesRegex(ValueError,'其他窗口'):
            self.manager.apply(state['job_id'],state['candidate'],'initial')
        self.assertEqual(Store.writes,[])

    def test_unapproved_candidate_and_stale_job_cannot_apply(self):
        with patch.object(e,'read_network',return_value=b'<a href="vgamepad-0.1.0.tar.gz">x</a>'):state=self.run_job('pip_test')
        for job,value,revision in ((state['job_id'],'https://unapproved.invalid','initial'),('old',e.INDEXES[0],'initial'),
                                    (state['job_id'],e.INDEXES[0],'old')):
            with self.subTest(job=job),self.assertRaises(ValueError):self.manager.apply(job,value,revision)
        self.assertEqual(Store.writes,[])

    def test_cancel_and_close_preserve_old_config(self):
        entered=threading.Event();release=threading.Event()
        def network(*_,**__):entered.set();release.wait(3);raise InterruptedError('取消')
        with patch.object(e,'read_network',side_effect=network):
            state=self.manager.start('pip_test','initial');self.assertTrue(entered.wait(2))
            self.manager.cancel(state['job_id']);release.set();self.manager.close()
        self.assertEqual(self.manager.status()['state'],'cancelled');self.assertFalse(self.manager.busy)
        self.assertEqual(Store.writes,[])

    def test_probe_failures_do_not_invent_success(self):
        with patch.object(e,'read_network',return_value=b'not a package index'):state=self.run_job('pip_test')
        self.assertEqual(state['state'],'failed');self.assertEqual(Store.writes,[])

    def test_proxy_parser_rejects_multiple_links_credentials_local_and_paths(self):
        for value in ('<a href="https://one.example">1</a><a href="https://two.example">2</a>',
                      '<a href="https://user:secret@proxy.example">x</a>','<a href="https://proxy.local">x</a>',
                      '<a href="https://127.0.0.1">x</a>','<a href="https://proxy.example/path">x</a>'):
            with self.subTest(value=value),self.assertRaises(ValueError):e.proxy_candidate(value.encode())

    def test_fixed_archive_resolution_and_origin_rejection(self):
        checksum='a'*64;url=e.INDEXES[1].replace('/simple','/packages')+'/vgamepad-0.1.0.tar.gz'
        page=f'<a href="{url}#sha256={checksum}">download</a>'.encode()
        with patch.object(e,'read_network',return_value=page):
            address,hosts=e.resolve_pinned_archive(e.INDEXES[1],'vgamepad-0.1.0.tar.gz',checksum,threading.Event())
        self.assertEqual(address,url);self.assertEqual(hosts,{'pypi.tuna.tsinghua.edu.cn'})
        for page in (page.replace(checksum.encode(),b'b'*64),page.replace(b'pypi.tuna.tsinghua.edu.cn',b'evil.invalid')):
            with patch.object(e,'read_network',return_value=page),self.assertRaises(ValueError):
                e.resolve_pinned_archive(e.INDEXES[1],'vgamepad-0.1.0.tar.gz',checksum,threading.Event())

    def test_download_uses_selected_index_and_keeps_hash_gate(self):
        payload=b'pinned archive';checksum=hashlib.sha256(payload).hexdigest()
        manager=g.GamepadDependencies(NS(data=Path(self.temp.name)));url='https://mirrors.aliyun.com/pypi/packages/vgamepad-0.1.0.tar.gz'
        with patch.object(e,'dependency_network',return_value=(e.INDEXES[2],'http://localhost:7890')),\
                patch.object(e,'resolve_pinned_archive',return_value=(url,{'mirrors.aliyun.com'})) as resolve,\
                patch.object(e,'read_network',return_value=payload) as read,patch.object(g,'SHA256',checksum):
            self.assertEqual(manager._download(),payload)
            self.assertEqual(resolve.call_args.args[0],e.INDEXES[2]);self.assertEqual(read.call_args.kwargs['proxy'],'http://localhost:7890')
            self.assertEqual(manager.job['index_url'],e.INDEXES[2]);self.assertEqual(manager.job['download_url'],url)
        with patch.object(e,'dependency_network',return_value=(e.INDEXES[0],None)),\
                patch.object(e,'resolve_pinned_archive',return_value=(url,{'mirrors.aliyun.com'})),patch.object(e,'read_network',return_value=b'wrong'):
            with self.assertRaisesRegex(ValueError,'摘要不符'):manager._download()

    def test_transport_honors_limits_cancel_and_redirection_policy(self):
        class Response(BytesIO):
            def geturl(self):return e.INDEXES[0]
        opened=[]
        def build(*handlers):opened.extend(handlers);return NS(open=lambda *_,**__:Response(b'12345'))
        with patch('urllib.request.build_opener',side_effect=build),self.assertRaisesRegex(ValueError,'大小'):
            e.read_network(e.INDEXES[0],threading.Event(),limit=4)
        event=threading.Event();event.set()
        with patch('urllib.request.build_opener',side_effect=build),self.assertRaises(InterruptedError):
            e.read_network(e.INDEXES[0],event)
        redirect=next(item for item in opened if isinstance(item, __import__('urllib.request',fromlist=['HTTPRedirectHandler']).HTTPRedirectHandler))
        with self.assertRaises(ValueError):redirect.redirect_request(None,None,302,'',{},'https://evil.invalid/')


if __name__=='__main__':unittest.main()
