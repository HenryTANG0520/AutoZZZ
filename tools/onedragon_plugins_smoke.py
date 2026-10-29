"""实际 SDK 的插件导入、禁用发现、加载、配置与归档回放；只使用自建插件。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import sys
import time
import zipfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

FACTORY='''from pathlib import Path
from one_dragon.base.operation.application.application_factory import ApplicationFactory
from one_dragon.base.config.yaml_config import YamlConfig
from . import demo_const
Path(__file__).with_name('imported.txt').write_text('factory imported')
class DemoConfig(YamlConfig):
    def __init__(self,idx):super().__init__('fixture_plugin',instance_idx=idx)
    @property
    def fixture_enabled(self):return self.get('fixture_enabled',False)
    @fixture_enabled.setter
    def fixture_enabled(self,value):self.update('fixture_enabled',value)
    @property
    def fixture_offset(self):return self.get('fixture_offset',-2)
    @fixture_offset.setter
    def fixture_offset(self,value):self.update('fixture_offset',value)
    @property
    def fixture_records(self):return self.get('fixture_records',{'routes':[]})
    @fixture_records.setter
    def fixture_records(self,value):self.update('fixture_records',value)
class DemoFactory(ApplicationFactory):
    def __init__(self,ctx):super().__init__(demo_const);self.ctx=ctx
    def create_application(self,instance_idx,group_id):raise AssertionError('fixture application must not run')
    def create_config(self,instance_idx,group_id):return DemoConfig(instance_idx)
'''


def write_plugin(folder,name='fixture_plugin',version='2.0'):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    (folder/'demo_const.py').write_text(f"APP_ID = '{name}'\nAPP_NAME = '插件回放'\nDEFAULT_GROUP = False\nNEED_NOTIFY = False\nPLUGIN_VERSION = '{version}'\nPLUGIN_AUTHOR = 'AutoZZZ fixture'\nPLUGIN_HOMEPAGE = 'https://example.invalid/plugin'\n",encoding='utf-8')
    (folder/'demo_factory.py').write_text(FACTORY,encoding='utf-8')


def wait(service,start):
    while True:
        status=service.plugin_status();assert status['job_id']==start['job_id']
        if not status['busy']:return status
        time.sleep(.01)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',required=True,type=Path);args=parser.parse_args();base=args.data.resolve()
    if base.exists():raise ValueError('请使用新的隔离验证目录。')
    from autozzz.onedragon_runtime import RuntimePaths
    defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);root=base/'profile';RuntimePaths(defaults.source,defaults.python,root).prepare();sys.path.insert(0,str(defaults.source/'src'));platform.uname()
    from autozzz.onedragon_service import OneDragonService
    from autozzz.onedragon_plugins import PluginStore
    from autozzz.run_lock import RunLock
    sources=base/'sources';write_plugin(sources/'collection/demo');write_plugin(sources/'collection/demo2','fixture_plugin_2')
    zip_path=sources/'collection.zip'
    with zipfile.ZipFile(zip_path,'w') as archive:
        for path in (sources/'collection').rglob('*.py'):archive.write(path,path.relative_to(sources).as_posix())
    def forbidden(*_,**__):raise AssertionError('插件回放不得访问游戏、网络或输入设备。')
    checks=[];service=None
    with ExitStack() as scope:
        for name in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo','autozzz.windows.find_game','autozzz.windows.user32.SendInput','pynput.keyboard.Controller.press','pynput.mouse.Controller.press'):
            scope.enter_context(patch(name,side_effect=forbidden))
        try:
            service=OneDragonService(defaults.source,root)
            review=wait(service,service.plugin_preview(str(zip_path),'zip'));assert review['state']=='done',review;review=review['result']
            assert len(review['plugins'])==2 and service._ctx is None
            zip_path.write_bytes(b'changed after preview')
            installed=wait(service,service.plugin_install(review['token'],['demo'],[],review['revision']))
            assert installed['state']=='done' and installed['result']['results'][0]['success'],installed
            assert not (root/'plugins/demo/imported.txt').exists() and not (root/'plugins/demo2').exists()
            catalog=service.catalog();assert len(catalog['applications'])==30 and not catalog['failures'] and not catalog['plugin_failures']
            assert not (root/'plugins/demo/imported.txt').exists();checks.append('actual_collection_zip_preview_install_and_disabled_scan_without_code_execution')
            current=service.plugin_catalog();service.plugin_enabled('demo',True,current['revision']);service.close();service=None
            with RunLock():
                blocked=OneDragonService(defaults.source,root)
                try:
                    status=blocked.catalog();assert status['plugin_failures'] and not (root/'plugins/demo/imported.txt').exists()
                finally:blocked.close()
            checks.append('plugin_code_load_obeys_shared_game_task_mutex')
            service=OneDragonService(defaults.source,root);catalog=service.catalog();assert len(catalog['applications'])==31 and not catalog['plugin_failures'],catalog
            assert (root/'plugins/demo/imported.txt').exists();description=service.describe_configuration('fixture_plugin')
            saved=service.save_configuration('fixture_plugin',dict(fixture_enabled=True,fixture_offset=-4,fixture_records={'routes':['回放']}),description['revision'])
            assert next(row for row in saved['fields'] if row['name']=='fixture_enabled')['value']is True
            assert next(row for row in saved['fields'] if row['name']=='fixture_offset')['value']==-4
            assert next(row for row in saved['fields'] if row['name']=='fixture_records')['value']=={'routes':['回放']}
            checks.append('actual_factory_relative_import_metadata_and_native_configuration_save')
            current=service.plugin_catalog();service.plugin_enabled('demo',False,current['revision'])
            try:service.catalog();raise AssertionError('旧上下文必须重新连接')
            except RuntimeError as exc:assert '插件已变化' in str(exc)
            service.close();service=OneDragonService(defaults.source,root)
            current=service.plugin_catalog();removed=wait(service,service.plugin_remove('demo',current['revision']))
            assert removed['state']=='done' and not (root/'plugins/demo').exists(),removed
            identifier=removed['result']['archive'];current=service.plugin_catalog()
            restored=wait(service,service.plugin_restore(identifier,['demo'],current['revision'],[]));assert restored['state']=='done',restored
            assert (root/'plugins/demo/demo_factory.py').exists() and not PluginStore(root).enabled('demo')
            checks.append('disable_invalidates_old_context_and_remove_restore_preserves_archive')
            low=sources/'low/demo';write_plugin(low,version='1.0')
            review=wait(service,service.plugin_preview(str(low),'directory'))['result'];assert review['plugins'][0]['downgrade']
            old=(root/'plugins/demo/demo_const.py').read_bytes();private=Path(service.plugins.review['path'])/'demo/demo_const.py';private.write_text('changed')
            rejected=wait(service,service.plugin_install(review['token'],['demo'],['demo'],review['revision'],True))
            assert rejected['state']=='failed' and '预览副本已变化' in rejected['error'] and (root/'plugins/demo/demo_const.py').read_bytes()==old
            review=wait(service,service.plugin_preview(str(low),'directory'))['result'];PluginStore(root).save([])
            rejected=wait(service,service.plugin_install(review['token'],['demo'],['demo'],review['revision'],True))
            assert rejected['state']=='failed' and '预览已变化' in rejected['error'] and (root/'plugins/demo/demo_const.py').read_bytes()==old
            checks.append('private_preview_snapshot_and_staged_tamper_or_stale_revision_protection')
            review=wait(service,service.plugin_preview(str(low),'directory'))['result']
            rejected=wait(service,service.plugin_install(review['token'],['demo'],['demo'],review['revision']))
            assert rejected['state']=='failed' and '降级' in rejected['error']
            installed=wait(service,service.plugin_install(review['token'],['demo'],['demo'],review['revision'],True));assert installed['state']=='done',installed
            assert PluginStore(root).catalog()['plugins'][0]['version']=='1.0' and not PluginStore(root).enabled('demo')
            checks.append('actual_directory_import_downgrade_ack_and_overwrite_archive')
            bad=sources/'bad.zip'
            with zipfile.ZipFile(bad,'w') as archive:archive.writestr('../escape.py','raise AssertionError()')
            rejected=wait(service,service.plugin_preview(str(bad),'zip'));assert rejected['state']=='failed'
            assert not (base/'escape.py').exists();checks.append('source_archive_validation_rejects_traversal')
        finally:
            if service is not None:service.close()
    result=dict(checks=checks,actual_sdk_importer_and_factory_manager=True,fixture_plugins_only=True,game_accessed=False,game_input_sent=False)
    (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
