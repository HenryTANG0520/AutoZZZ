"""真实 Windows 任务调用正式 CLI、实例记录及合作停止；实际游戏执行用无输入夹具。"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import Settings
from autozzz.schedule_authorization import authorize,daily_review
from autozzz.schedule_manager import ScheduleManager
from autozzz.schedules import Schedule,ScheduleStore,revision


def command():
    python=Path(sys.executable).resolve();windowed=python.with_name('pythonw.exe')
    return (windowed if windowed.is_file() else python),['-X','utf8',str(Path(__file__).resolve()),'--worker']


def wait(predicate,seconds=90):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.1)
    raise TimeoutError('原系统任务仍在等待，请查询原实例；不会重新启动或强制结束。')


def worker():
    sys.argv.remove('--worker')
    from autozzz import schedule_runtime
    from autozzz.__main__ import main as cli_main
    original=schedule_runtime.run_scheduled
    def execute(plan,review,stop,emit,report_root):
        base=Path(review['config_path']).parent
        assert base.is_relative_to(Path(__file__).resolve().parents[1]/'runs')
        (base/'started.json').write_text(json.dumps(dict(record=str(report_root.parent/'record.json'),pid=os.getpid())),encoding='utf-8')
        emit(dict(type='log',message='无游戏执行器已进入正式定时入口。'))
        emit(dict(type='phase',phase='fixture running'))
        if (base/'complete').exists():return dict(state='success',report='fixture success')
        assert stop.wait(60)
        wait(lambda:(base/'allow-cleanup').exists())
        return dict(state='stopped',report='fixture original cleanup finished')
    def run(*args,**kwargs):return original(*args,**kwargs,execute_task=execute)
    def forbidden(*_,**__):raise AssertionError('定时入口测试禁止访问游戏、网络和实际输入。')
    from contextlib import ExitStack
    with ExitStack() as scope:
        for name in ('autozzz.windows.find_game','autozzz.windows.launch','autozzz.windows.user32.SendInput','autozzz.windows.user32.PostMessageW',
                     'autozzz.windows.user32.SetForegroundWindow','socket.socket.connect','socket.socket.connect_ex','socket.getaddrinfo'):
            scope.enter_context(patch(name,side_effect=forbidden))
        scope.enter_context(patch('autozzz.schedule_manager.launcher',side_effect=lambda _:command()))
        scope.enter_context(patch('autozzz.schedule_runtime.run_scheduled',side_effect=run))
        return cli_main(sys.argv[1:])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args();base=args.data.resolve()
    project=Path(__file__).resolve().parents[1]
    if base.exists() or not base.is_relative_to(project/'runs'):raise ValueError('请使用 runs 中新的隔离目录。')
    base.mkdir(parents=True);game=base/'ZenlessZoneZero.exe';game.write_bytes(b'not executable; no game test fixture')
    settings=Settings(game_path=str(game));settings.save(base/'daily.json')
    store=ScheduleStore(project,base/'plans.json')
    with patch('autozzz.schedule_manager.launcher',side_effect=lambda _:command()):
        manager=ScheduleManager(store)
        plan=Schedule(name='正式定时入口无游戏验证',trigger='once',date='2099-01-02',highest=False)
        plan.authorization=authorize(plan,daily_review(plan,base/'daily.json'));plan.enabled=True
        try:
            catalog=manager.save(plan,revision(store.read()))
            (base/'catalog-initial.json').write_text(json.dumps(dict(catalog=catalog,command=str(manager.command),arguments=subprocess.list2cmdline(manager.arguments(plan))),ensure_ascii=False,indent=2),encoding='utf-8')
            assert catalog['plans'][0]['bound']
            first=manager.run(plan.id,catalog['revision']);wait(lambda:(base/'started.json').exists())
            started=json.loads((base/'started.json').read_text());record=Path(started['record'])
            second=manager.run(plan.id,catalog['revision']);assert second['instance']==first['instance']
            catalog=manager.disable(plan.id,catalog['revision']);assert catalog['plans'][0]['bound']
            assert manager.backend.call('instances',key=plan.id)['instances']
            (record.parent/'stop').touch();time.sleep(.25)
            running=json.loads(record.read_text());assert running['state']=='running' and 'finished_at'not in running
            (base/'allow-cleanup').touch();wait(lambda:not manager.backend.call('instances',key=plan.id)['instances'])
            stopped=json.loads(record.read_text());assert stopped['state']=='stopped' and 'finished_at'in stopped
            (base/'complete').touch()
            plan=replace(store.get(plan.id),enabled=True)
            catalog=manager.save(plan,revision(store.read()));next_run=manager.run(plan.id,catalog['revision'])
            assert next_run['instance']!=first['instance']
            wait(lambda:not manager.backend.call('instances',key=plan.id)['instances'])
            records=list((store.path.parent/'runs'/plan.id).glob('*/record.json'))
            assert len(records)==2 and sorted(json.loads(path.read_text())['state'] for path in records)==['stopped','success']
            settings.action_delay=2;settings.save(base/'daily.json')
            manager.run(plan.id,catalog['revision']);wait(lambda:not manager.backend.call('instances',key=plan.id)['instances'])
            records=list((store.path.parent/'runs'/plan.id).glob('*/record.json'))
            assert len(records)==3
            failed=[json.loads(path.read_text()) for path in records if json.loads(path.read_text())['state']=='failed']
            assert len(failed)==1 and '配置已变化'in failed[0]['error']
            manager.remove(plan.id,revision(store.read()));manager.backend.call('remove_empty_folder')
            assert not manager.catalog()['plans']
            result=dict(actual_windows_scheduler=True,actual_product_cli_and_recording=True,executor_is_fixture=True,
                actual_desktop_check=True,same_instance_not_restarted=True,disable_keeps_original_run=True,
                cooperative_stop_waits_cleanup=True,new_instance_gets_new_record=True,changed_daily_configuration_rejected=True,
                records_preserved=len(records),owned_task_removed=True,game_accessed=False,game_input_sent=False,energy_spent=0)
            (base/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
        finally:
            (base/'allow-cleanup').touch()
            for path in (store.path.parent/'runs'/plan.id).glob('*/record.json'):(path.parent/'stop').touch()
            rows=manager.backend.call('list')['tasks']
            if rows:
                manager.backend.call('disable',key=plan.id)
                wait(lambda:not manager.backend.call('instances',key=plan.id)['instances'])
                manager.backend.call('remove',key=plan.id);manager.backend.call('remove_empty_folder')


if __name__=='__main__':
    if '--worker'in sys.argv:raise SystemExit(worker())
    main()
