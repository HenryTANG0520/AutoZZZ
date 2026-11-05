"""源码版与分发版共用的离线环境检查；不初始化游戏或输入控制器。"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys

from .onedragon_runtime import RuntimePaths,OneDragonClient,RequestPending


def check_runtime(root: Path,report: Path):
    report=report.resolve();data=report.with_name(report.stem+'.data')
    if report.exists() or data.exists():raise ValueError('检查报告或资料目录已存在，请指定新路径。')
    report.parent.mkdir(parents=True,exist_ok=True)
    paths=replace(RuntimePaths.discover(root,use_selection=False),data=data)
    client=OneDragonClient(paths)
    result=dict(state='failed',game_started=False,game_input_sent=False)
    try:
        from .process_inventory import process_inventory
        launcher=process_inventory(Path(sys.executable).name,only_pid=os.getpid())
        if len(launcher)!=1:raise RuntimeError('未能核实程序自身的 Windows 进程身份。')
        result['launcher_process_inventory']=True
        try:hello=client.start(timeout=60)
        except RequestPending:hello=client.finish_start(timeout=60)
        catalog=client.call('catalog',timeout=60)
        configuration=client.call('describe_configuration',app_id='_game_input',timeout=30)
        strategies={}
        for kind in ('auto_battle','dodge'):
            policies=client.call('policies',kind=kind,timeout=30)
            for policy in policies:client.call('policy_validate',name=policy['name'],kind=kind,timeout=30)
            strategies[kind]=len(policies)
        state=client.call('execution_status')
        if state.get('ready') or state.get('busy'):raise RuntimeError('离线检查意外进入执行状态。')
        result.update(state='success',revision=hello['revision'],python=hello['python'],
                      bundled=paths.bundle is not None,applications=len(catalog['applications']),
                      game_input_fields=len(configuration['fields']),strategies=strategies)
    except Exception as exc:
        result.update(error=str(exc))
    finally:
        while True:
            try:client.close(timeout=5);break
            except RequestPending:continue  # 原进程未退出时不删除资料或创建替代进程。
        result['worker_exited']=not client.alive
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
