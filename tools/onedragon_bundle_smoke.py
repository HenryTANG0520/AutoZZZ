"""可移动 SDK 的真实工作进程、配置与模型检查；不绑定游戏或发送输入。"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import GAME_PATHS
from autozzz.onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--data',type=Path,required=True);parser.add_argument('--models',type=Path,required=True)
    args=parser.parse_args();package=args.package.resolve();data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证资料目录。')
    paths=replace(RuntimePaths.discover(package),data=data)
    assert paths.bundle is not None
    environment=os.environ.copy()
    for name in ('PYTHONHOME','PYTHONPATH','PYTHONUSERBASE','VIRTUAL_ENV','CONDA_PREFIX'):
        environment.pop(name,None)
    environment['PYTHONUTF8']='1';environment['PYTHONNOUSERSITE']='1'
    environment['PATH']=str(paths.python.parent)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32')+os.pathsep+os.environ['SystemRoot']
    code='''import sys,json,site,cv2,numpy,onnxruntime,win32ui,pynput,PySide6
print(json.dumps(dict(prefix=sys.prefix,base_prefix=sys.base_prefix,executable=sys.executable,site=site.getsitepackages(),modules={name:sys.modules[name].__file__ for name in ("cv2","numpy","onnxruntime","win32ui","pynput","PySide6")})))'''
    result=subprocess.run([str(paths.python),'-s','-P','-c',code],cwd=package,env=environment,
                          check=True,capture_output=True,text=True,encoding='utf-8',timeout=60,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    interpreter=json.loads(result.stdout)
    assert Path(interpreter['prefix']).resolve()==paths.python.parent
    assert Path(interpreter['base_prefix']).resolve()==paths.python.parent
    assert Path(interpreter['executable']).resolve()==paths.python
    assert all(Path(value).resolve().is_relative_to(paths.python.parent) for value in interpreter['modules'].values())
    paths.prepare()
    for name in ('onnx_ocr','flash_classifier'):
        shutil.copytree(args.models/name,data/'assets/models'/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    client=OneDragonClient(paths,log=lambda message:print(message,file=sys.stderr,flush=True))
    checks=[]
    try:
        with patch.dict(os.environ,{'PATH':environment['PATH'],'PYTHONHOME':'invalid parent python','PYTHONPATH':'invalid parent modules'}):
            try:hello=client.start(timeout=60)
            except RequestPending:hello=client.finish_start(timeout=60)
        assert hello['python'].startswith('3.11.')
        catalog=client.call('catalog',timeout=60);assert len(catalog['applications'])==30
        checks.append('actual_relocated_python_and_dependencies_without_developer_or_git_path')
        description=client.call('describe_configuration',app_id='_game_input')
        assert len(description['fields'])==67
        saved=client.call('save_configuration',app_id='_game_input',values=dict(key_normal_attack='j'),revision=description['revision'])
        assert next(field for field in saved['fields'] if field['name']=='key_normal_attack')['value']=='j'
        checks.append('actual_packaged_bridge_configuration_read_and_save')
        for kind in ('auto_battle','dodge'):
            policies=client.call('policies',kind=kind)
            assert policies
            for policy in policies:
                validated=client.call('policy_validate',name=policy['name'],kind=kind)
                assert validated['engine'].endswith('AutoBattleOperator')
        checks.append('actual_packaged_battle_and_dodge_strategy_construction')
        def wait(job):
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                state=client.call('execution_status',timeout=5);assert state['job_id']==job
                if not state['busy']:return state
                time.sleep(.05)
            raise RuntimeError('原运行环境准备仍未结束；保留原进程，不新建任务。')
        initial=client.call('initialize_execution',server='cn',game_path=GAME_PATHS['cn'],timeout=30)
        state=wait(initial['job_id']);assert state['state']=='ready',state
        prepared=client.call('prepare_application',app_id='auto_battle',timeout=30)
        state=wait(prepared['job_id']);assert state['state']=='prepared',state
        probe=client.call('runtime_probe',timeout=30)
        assert probe['ocr_text']=='400' and probe['flash_model'] and probe['flash_model']['providers']
        assert not probe['window_bound'] and not probe['upstream_hotkeys_started']
        checks.append('actual_packaged_ocr_flash_and_battle_preparation_without_game_input')
    finally:
        while True:
            try:client.close(timeout=5);break
            except RequestPending:print('等待原分发验证工作进程结束。',file=sys.stderr,flush=True)
    report=dict(checks=checks,interpreter=interpreter,probe=probe,worker_exited=not client.alive,
                game_started=False,game_input_sent=False,model_downloaded=False)
    (data/'bundle-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
