"""插件管理的真实独立 SDK 管道；自建插件且硬拦截游戏、网络与设备输入。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if '--fixture-worker' not in sys.argv:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def fixture_worker():
    from contextlib import ExitStack,redirect_stdout
    import platform
    from unittest.mock import patch
    parser=argparse.ArgumentParser();parser.add_argument('--fixture-worker',action='store_true');parser.add_argument('--source',type=Path);parser.add_argument('--data',type=Path);parser.add_argument('--fixture-bridge',type=Path);args=parser.parse_args()
    import autozzz
    assert Path(autozzz.__file__).resolve().is_relative_to(args.fixture_bridge.resolve())
    sys.path.insert(0,str(args.source/'src'));platform.uname()
    def forbidden(*_,**__):raise AssertionError('插件管道回放不得访问游戏、网络或输入设备。')
    with ExitStack() as scope:
        with redirect_stdout(sys.stderr):
            for target in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                           'autozzz.windows.find_game','autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow','autozzz.windows.user32.ShowWindow',
                           'pynput.keyboard.Controller.press','pynput.mouse.Controller.press','pynput.keyboard.Controller.release','pynput.mouse.Controller.release'):
                scope.enter_context(patch(target,side_effect=forbidden))
            from autozzz.onedragon_worker import main
        sys.argv=[sys.argv[0],'--source',str(args.source),'--data',str(args.data)]
        return main()


def main():
    from dataclasses import replace
    import subprocess,time,zipfile
    from unittest.mock import patch
    from autozzz.onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending,RemoteError
    from onedragon_plugins_smoke import write_plugin
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--package',type=Path);args=parser.parse_args()
    base=args.data.resolve()
    if base.exists():raise ValueError('请使用新的隔离验证目录。')
    root=Path(__file__).resolve().parents[1];paths=replace(RuntimePaths.discover(args.package or root),data=base/'profile');paths.prepare()
    bridge=paths.bundle/'bridge' if paths.bundle is not None else root
    fixture=base/'source/demo';write_plugin(fixture);archive=base/'fixture.zip'
    with zipfile.ZipFile(archive,'w') as zipped:
        for path in fixture.glob('*.py'):zipped.write(path,'demo/'+path.name)
    original=subprocess.Popen;client=OneDragonClient(paths);checks=[]
    def spawn(command,**kwargs):
        if command[:3]==['git','-C',str(paths.source)] and command[3]in ('rev-parse','diff','ls-files'):return original(command,**kwargs)
        assert str(command[0])==str(paths.python) and '-m' in command
        index=command.index('-m');assert command[index+1]=='autozzz.onedragon_worker'
        return original(command[:index]+[str(Path(__file__).resolve()),'--fixture-worker']+command[index+2:]+['--fixture-bridge',str(bridge)],**kwargs)
    def start():
        try:client.start()
        except RequestPending:
            while True:
                try:client.finish_start(timeout=.5);break
                except RequestPending:continue
    def call(method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=.5)
            except RequestPending:continue
    def job(method,**params):
        key=call(method,**params)['job_id']
        while True:
            state=call('plugin_status');assert state['job_id']==key
            if not state['busy']:assert state['state']=='done',state;return state['result']
            time.sleep(.02)
    def close():
        while True:
            try:client.close(timeout=5);return
            except RequestPending:continue
    try:
        with patch('autozzz.onedragon_runtime.subprocess.Popen',side_effect=spawn):
            start();catalog=call('plugin_catalog');assert not catalog['plugins'];assert Path(call('plugin_directory'))==paths.data/'plugins'
            preview=job('plugin_preview',path=str(archive),kind='zip')
            result=job('plugin_install',token=preview['token'],names=['demo'],overwrites=[],revision=preview['revision'])
            assert result['results'][0]['success'] and not (paths.data/'plugins/demo/imported.txt').exists()
            assert len(call('catalog')['applications'])==30;checks.append('actual_zip_preview_install_stays_unloaded_across_rpc')
            catalog=call('plugin_catalog');call('plugin_enabled',name='demo',enabled=True,revision=catalog['revision']);close();client=OneDragonClient(paths);start()
            catalog=call('catalog');assert len(catalog['applications'])==31 and not catalog['plugin_failures']
            description=call('describe_configuration',app_id='fixture_plugin');saved=call('save_configuration',app_id='fixture_plugin',values=dict(fixture_enabled=True,fixture_records={'routes':['RPC']}),revision=description['revision'])
            assert next(row['value'] for row in saved['fields'] if row['name']=='fixture_records')=={'routes':['RPC']}
            checks.append('actual_factory_load_relative_import_and_native_json_save_after_reconnect')
            current=call('plugin_catalog');call('plugin_enabled',name='demo',enabled=False,revision=current['revision'])
            try:call('catalog');raise AssertionError('stale context accepted')
            except RemoteError as exc:assert '插件已变化' in str(exc)
            removed=job('plugin_remove',name='demo',revision=call('plugin_catalog')['revision']);identifier=removed['archive']
            archives=call('plugin_archives');assert any(row['id']==identifier for row in archives)
            restored=job('plugin_restore',identifier=identifier,names=['demo'],revision=call('plugin_catalog')['revision'],overwrites=[])
            assert restored['results'][0]['success'] and not restored['catalog']['plugins'][0]['enabled'];checks.append('actual_disable_archive_remove_and_restore_rpc')
            close();client=OneDragonClient(paths);start();assert len(call('catalog')['applications'])==30;checks.append('persisted_disable_survives_new_actual_sdk_worker')
    finally:close()
    result=dict(checks=checks,package=str(args.package.resolve()) if args.package else None,source=str(paths.source),python=str(paths.python),bridge=str(bridge),actual_protocol=True,fixture_plugins_only=True,game_accessed=False,game_input_sent=False)
    (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':fixture_worker() if '--fixture-worker' in sys.argv else main()
