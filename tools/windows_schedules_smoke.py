"""用独立 Windows 计划运行无游戏脚本；结束后只清理本轮任务文件夹。"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.schedules import Schedule
from autozzz.windows_schedules import WindowsSchedules


def wait(predicate,seconds=90):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.2)
    raise TimeoutError('原系统任务仍在等待；不会重新启动或强制结束。')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--worker',action='store_true');parser.add_argument('--owner');parser.add_argument('--key');parser.add_argument('--command',type=Path)
    parser.add_argument('--label');args=parser.parse_args();base=args.data.resolve();project=Path(__file__).resolve().parents[1]
    if args.worker:
        manager=WindowsSchedules(args.owner,args.command,project)
        instances=manager.call('instances',key=args.key)['instances']
        assert len(instances)==1,instances
        marker=base/'worker.json';marker.write_text(json.dumps(dict(pid=__import__('os').getpid(),instances=instances,label=args.label),ensure_ascii=False),encoding='utf-8')
        wait(lambda:(base/'finish').exists())
        return
    if base.exists():raise ValueError('请使用新的隔离验证目录。')
    base.mkdir(parents=True);owner=uuid.uuid4().hex;plan=Schedule(name='无游戏 中文 & 空格验证',trigger='once',date='2099-01-02',highest=False)
    command=Path(sys.executable).resolve();manager=WindowsSchedules(owner,command,project)
    arguments=['-X','utf8',str(Path(__file__).resolve()),'--worker','--data',str(base),'--owner',owner,'--key',plan.id,'--command',str(command),'--label','中文 "引号" & 符号']
    (base/'definition.json').write_text(json.dumps(dict(owner=owner,key=plan.id,command=str(command),working=str(project)),ensure_ascii=False,indent=2),encoding='utf-8')
    registered=False
    try:
        assert manager.call('list')['tasks']==[]
        registered=True;row=manager.register(plan,arguments)['task']
        assert row['enabled']is False and not row['conflict'],row
        for trigger in ('daily','weekly','logon','once'):
            row=manager.register(replace(plan,trigger=trigger),arguments)['task']
            assert not row['enabled'] and not row['conflict'],row
        wrong=WindowsSchedules(owner,project/'launcher.py',project)
        assert wrong.call('list')['tasks'][0]['conflict']
        try:wrong.call('enable',key=plan.id)
        except RuntimeError:pass
        else:raise AssertionError('foreign command accepted as owned task')
        assert manager.call('list')['tasks'][0]['enabled']is False
        assert manager.call('instances',key=plan.id)['instances']==[]
        try:manager.call('run',key=plan.id)
        except RuntimeError:pass
        else:raise AssertionError('disabled task unexpectedly started')
        manager.call('enable',key=plan.id)
        first=manager.call('run',key=plan.id)
        wait(lambda:(base/'worker.json').exists())
        evidence=json.loads((base/'worker.json').read_text(encoding='utf-8'))
        assert evidence['label']=='中文 "引号" & 符号'
        assert first['instance']==evidence['instances'][0]['instance']
        second=manager.call('run',key=plan.id)
        instances=manager.call('instances',key=plan.id)['instances']
        assert len(instances)==1 and instances[0]['instance']==first['instance'],(second,instances)
        for operation in ('register','remove'):
            try:
                if operation=='register':manager.register(replace(plan,name='changed'),arguments)
                else:manager.call('remove',key=plan.id)
            except RuntimeError:pass
            else:raise AssertionError('running task changed or removed')
        manager.call('disable',key=plan.id)
        assert len(manager.call('instances',key=plan.id)['instances'])==1
        (base/'finish').touch()
        wait(lambda:not manager.call('instances',key=plan.id)['instances'])
        row=manager.call('list')['tasks'][0];assert not row['enabled'] and row['result']==0,row
        manager.call('remove',key=plan.id);registered=False
        assert manager.call('list')['tasks']==[];manager.call('remove_empty_folder')
        result=dict(actual_windows_scheduler=True,registered_disabled=True,interactive_current_user=True,unicode_and_quoted_arguments=True,
            all_four_trigger_types_registered=True,foreign_command_refused_without_enabling=True,
            actual_instance_guid=first['instance'],ignore_new_instance=True,running_task_not_replaced_or_removed=True,
            disable_does_not_kill_running_process=True,worker_exited=True,task_and_owned_folder_removed=True,
            game_accessed=False,game_input_sent=False,energy_spent=0)
        (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
    finally:
        (base/'finish').touch()
        if registered:
            try:
                if any(row['id']==plan.id for row in manager.call('list')['tasks']):
                    manager.call('disable',key=plan.id)
                    wait(lambda:not manager.call('instances',key=plan.id)['instances'])
                    manager.call('remove',key=plan.id)
                manager.call('remove_empty_folder')
            except Exception as exc:print('保留本轮验证任务，需继续查询原任务：'+str(exc),file=sys.stderr)


if __name__=='__main__':main()
