"""实际 SDK 管道、源下载器和完整 EXE 环境预检；只使用自建资料及构建预览。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
from unittest.mock import patch


def fixture_worker():
    parser=argparse.ArgumentParser();parser.add_argument('--fixture-worker',action='store_true');parser.add_argument('--source',type=Path);parser.add_argument('--data',type=Path);parser.add_argument('--bridge',type=Path)
    args=parser.parse_args();sys.path.insert(0,str(args.bridge));sys.path.insert(0,str(args.source/'src'));platform.uname()
    import requests
    from autozzz.onedragon_worker import main as worker_main
    from autozzz.program_updates import ASSET,REPOSITORY
    original=subprocess.Popen
    payload=b'fixture archive with wrong checksum'
    def forbidden(*_,**__):raise AssertionError('版本回放不能访问游戏、网络或物理输入。')
    def spawn(command,**options):
        assert len(command)==3 and Path(command[0]).name=='AutoZZZ.exe' and command[1]=='--runtime-check',command
        candidate=Path(command[0]).resolve().parent
        assert candidate.name=='candidate' and candidate.parent.name.startswith('.autozzz-update-')
        assert candidate.parent.parent==args.source.resolve().parents[2]
        return original(command,**options)
    def request(_session,method,url,**options):
        assert method=='get' and url==f'https://api.github.com/repos/{REPOSITORY}/releases',url
        asset=dict(name=ASSET,id=7,size=len(payload),digest='sha256:'+'f'*64,
            browser_download_url=f'https://github.com/{REPOSITORY}/releases/download/fixture-download/{ASSET}')
        response=requests.Response();response.status_code=200;response._content=json.dumps([dict(tag_name='fixture-download',draft=False,prerelease=False,assets=[asset])]).encode();response._content_consumed=True
        return response
    class Response(io.BytesIO):
        headers={'Content-Length':str(len(payload))}
    def opened(_opener,request,*_,**__):
        assert request.full_url==f'https://github.com/{REPOSITORY}/releases/download/fixture-download/{ASSET}'
        return Response(payload)
    with ExitStack() as scope:
        for name in ('socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo','autozzz.windows.find_game',
                     'autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow','autozzz.windows.user32.ShowWindow',
                     'pynput.keyboard.Controller.press','pynput.mouse.Controller.press','pynput.keyboard.Controller.release','pynput.mouse.Controller.release'):
            scope.enter_context(patch(name,side_effect=forbidden))
        scope.enter_context(patch('subprocess.Popen',side_effect=spawn));scope.enter_context(patch('requests.sessions.Session.request',request))
        scope.enter_context(patch('urllib.request.OpenerDirector.open',opened))
        sys.argv=[sys.argv[0],'--source',str(args.source),'--data',str(args.data)]
        return worker_main()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--installed',type=Path,required=True);parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--packaged',action='store_true');parser.add_argument('--catalog-only',action='store_true');args=parser.parse_args()
    base=args.data.resolve()
    if base.exists():raise ValueError('请使用新的隔离验证目录。')
    project=Path(__file__).resolve().parents[1];sys.path.insert(0,str(project))
    from autozzz.program_package import create_manifest,read_manifest
    from autozzz.program_update import ProgramUpdate,verify_preflight
    from autozzz.onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending
    installed=args.installed.resolve();archive=args.archive.resolve()
    if not installed.is_relative_to(project/'build') or not archive.is_relative_to(project/'build'):raise ValueError('只能使用本项目构建预览。')
    assert archive.parent==installed.parent and archive.name.startswith(installed.name+'.previous-')
    if not args.packaged:
        create_manifest(installed,'fixture-installed','b'*40);create_manifest(archive,'fixture-archive','c'*40)
    installed_version=read_manifest(installed)['version']
    paths=replace(RuntimePaths.discover(installed),data=base/'profile');original=subprocess.Popen;client=OneDragonClient(paths);checks=[]
    def spawn(command,**options):
        assert str(command[0])==str(paths.python) and '-m'in command
        index=command.index('-m');assert command[index+1]=='autozzz.onedragon_worker'
        bridge=installed/'onedragon/bridge' if args.packaged else project
        return original(command[:index]+[str(Path(__file__).resolve()),'--fixture-worker']+command[index+2:]+['--bridge',str(bridge)],**options)
    def call(method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=.5)
            except RequestPending:continue
    def complete(method,**params):
        key=call(method,**params)['job_id'];deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            state=call('program_status',job_id=key);assert state['job_id']==key
            if not state['busy']:return state
            time.sleep(.1)
        raise AssertionError('原版本任务仍在运行，请保留工作进程。')
    try:
        with patch('subprocess.Popen',side_effect=spawn):
            try:client.start()
            except RequestPending:
                while True:
                    try:client.finish_start(.5);break
                    except RequestPending:continue
            catalog=call('program_catalog');assert catalog['installed'] and catalog['current']==installed_version
            checked=complete('program_check');assert checked['state']=='success',checked
            release=checked['result']['releases'][0];checks.append('actual_sdk_http_release_selection_and_persistent_catalog')
            failed=complete('program_download',release_id=release['id'],revision=checked['result']['revision'])
            assert failed['state']=='failed' and '摘要' in failed['detail'],failed
            assert read_manifest(installed)['version']==installed_version
            checks.append('actual_source_common_downloader_rejects_changed_asset_before_installation')
            transaction=None;report=None
            if not args.catalog_only:
                ready=complete('program_restore',path=str(archive));assert ready['state']=='success',ready
                transaction=ProgramUpdate(ready['result']['transaction']);report=verify_preflight(transaction)
                assert report['bundled'] and report['worker_exited'] and report['applications']==30
                assert read_manifest(installed)['version']==installed_version and transaction.data['state']=='prepared'
                checks.append('actual_complete_noadmin_exe_and_bundled_sdk_preflight_without_replacing_current_program')
            result=dict(checks=checks,actual_sdk=True,actual_frozen_exe=not args.catalog_only,actual_bundled_bridge=args.packaged,network_metadata_and_download_are_fixtures=True,
                prepared_transaction=str(transaction.job) if transaction else None,runtime=report,game_accessed=False,game_input_sent=False)
            (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
    finally:
        while True:
            try:client.close(timeout=5);break
            except RequestPending:continue


if __name__=='__main__':
    if '--fixture-worker'in sys.argv:raise SystemExit(fixture_worker())
    main()
