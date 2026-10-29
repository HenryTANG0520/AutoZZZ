"""真实 SDK 追踪管道与 OCR 生产者；独立工作进程硬拦截游戏、网络及输入。"""
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
    parser=argparse.ArgumentParser();parser.add_argument('--fixture-worker',action='store_true')
    parser.add_argument('--source',type=Path);parser.add_argument('--data',type=Path);parser.add_argument('--fixture-bridge',type=Path);args=parser.parse_args()
    import autozzz
    assert Path(autozzz.__file__).resolve().is_relative_to(args.fixture_bridge.resolve())
    sys.path.insert(0,str(args.source/'src'));platform.uname()
    def forbidden(*_,**__):raise AssertionError('追踪管道回放不得访问游戏、网络或输入设备。')
    with ExitStack() as scope:
        with redirect_stdout(sys.stderr):
            for target in ('subprocess.Popen','requests.sessions.Session.request','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo',
                           'autozzz.windows.find_game','autozzz.windows.user32.SendInput','autozzz.windows.user32.SetForegroundWindow',
                           'autozzz.windows.user32.ShowWindow','pynput.keyboard.Controller.press','pynput.mouse.Controller.press',
                           'pynput.keyboard.Controller.release','pynput.mouse.Controller.release'):
                scope.enter_context(patch(target,side_effect=forbidden))
            from autozzz.onedragon_worker import main
            from autozzz.onedragon_service import OneDragonService
            original_probe=OneDragonService.runtime_probe
            def full_ocr_probe(service):
                import cv2,numpy as np
                result=original_probe(service)
                image=np.full((100,320,3),255,dtype=np.uint8);cv2.putText(image,'400',(25,72),cv2.FONT_HERSHEY_SIMPLEX,2,(0,0,0),3,cv2.LINE_AA)
                # 原 probe 使用无检测单行 OCR；该源路径不产生视觉追踪。额外运行真实检测路径。
                assert '400' in service.ctx.ocr.run_ocr(image)
                return result
            scope.enter_context(patch.object(OneDragonService,'runtime_probe',full_ocr_probe))
        sys.argv=[sys.argv[0],'--source',str(args.source),'--data',str(args.data)]
        return main()


def main():
    from dataclasses import replace
    import shutil
    import subprocess
    import time
    from unittest.mock import patch
    from autozzz.config import GAME_PATHS
    from autozzz.onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending
    from autozzz.onedragon_trace_session import TraceSubscription
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--models',type=Path,required=True);parser.add_argument('--package',type=Path)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    paths=replace(RuntimePaths.discover(args.package or root),data=data);paths.prepare()
    bridge=paths.bundle/'bridge' if paths.bundle is not None else root
    for name in ('onnx_ocr','flash_classifier'):shutil.copytree(args.models/name,data/'assets/models'/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    original=subprocess.Popen;events=[];client=OneDragonClient(paths)
    subscription=TraceSubscription(lambda event:events.append(subscription.take() if event['type']=='trace' else event))
    def spawn(command,**kwargs):
        if command[:3]==['git','-C',str(paths.source)] and command[3]in ('rev-parse','diff','ls-files'):return original(command,**kwargs)
        assert str(command[0])==str(paths.python) and '-m' in command
        index=command.index('-m');assert command[index+1]=='autozzz.onedragon_worker'
        return original(command[:index]+[str(Path(__file__).resolve()),'--fixture-worker']+command[index+2:]+['--fixture-bridge',str(bridge)],**kwargs)
    def call(method,**params):
        request=client.submit(method,**params)
        while True:
            try:return client.result(request,timeout=.5)
            except RequestPending:continue
    try:
        with patch('autozzz.onedragon_runtime.subprocess.Popen',side_effect=spawn):
            try:client.start()
            except RequestPending:
                while True:
                    try:client.finish_start(timeout=.5);break
                    except RequestPending:continue
            assert not call('trace_snapshot')['enabled']
            subscription.configure(True);subscription.pump(client,call)
            def wait(job):
                while True:
                    state=call('execution_status');assert state['job_id']==job
                    if not state['busy']:return state
                    time.sleep(.05)
            state=wait(call('initialize_execution',server='cn',game_path=GAME_PATHS['cn'])['job_id']);assert state['state']=='ready',state
            state=wait(call('prepare_application',app_id='auto_battle')['job_id']);assert state['state']=='prepared',state
            probe=call('runtime_probe');assert probe['ocr_text']=='400' and not probe['window_bound'] and not probe['upstream_hotkeys_started']
            subscription.next_poll=0;subscription.pump(client,call)
            snapshot=events[-1]['snapshot'];assert snapshot['enabled']
            assert any(row['source']=='ocr' and '400' in row['label'] for row in snapshot['items']['vision']),snapshot
            assert any(row['metric']=='ocr_ms' for row in snapshot['items']['perf']),snapshot
            assert snapshot['window_identity']is None
            subscription.configure(False);subscription.pump(client,call)
            assert events[-1]['snapshot']is None and not call('trace_snapshot')['enabled']
            assert call('execution_status')['state']=='prepared','关闭采集不得停止已准备任务'
    finally:
        while True:
            try:client.close(timeout=5);break
            except RequestPending:continue
    result=dict(actual_independent_sdk=True,worker_bridge=str(bridge),actual_ocr_and_model_inference=True,actual_json_trace_pipe=True,
                disable_preserves_prepared_execution=True,original_worker_exited=not client.alive,game_accessed=False,game_input_sent=False)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':raise SystemExit(fixture_worker() if '--fixture-worker' in sys.argv else main())
