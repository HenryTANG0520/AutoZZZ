"""Actual pinned GameConfig and operation metadata, isolated data/fake OS only."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def child(source,data):
    sys.path.insert(0,str(source/'src'))
    from one_dragon.utils import os_utils
    os_utils.set_work_dir(data)
    from autozzz.onedragon_game_input import GameInputStore
    from autozzz.game_launch import LaunchOptions,HdrStore,LaunchSession,legacy_launch_configuration
    from autozzz.onedragon_startup_control import StartupOperationControl
    from autozzz.onedragon_startup import GameStartup
    from tests.test_game_launch import Registry,Lock
    import yaml
    game=data/'ZenlessZoneZero.exe';game.write_bytes(b'not executable: offline fixture')
    ctx=SimpleNamespace(current_instance_idx=1,game_account_config=SimpleNamespace(game_path=str(game)))
    service=SimpleNamespace(ctx=ctx,data=data,_assert_idle=lambda:None,runtime_guard=SimpleNamespace(check_current=lambda:None))
    store=GameInputStore(service);description=store.describe()
    expected=set(LaunchOptions.__dataclass_fields__)
    assert expected<={field['name']for field in description['fields']}
    values=dict(launch_argument=True,screen_size='2560x1440',full_screen='1',popup_window=True,monitor='2',
                launch_argument_advance='--label "a b" & calc',autozzz_temporary_hdr=True)
    saved=store.save(values,description['revision']);assert store.launch_options()==values
    reloaded=store.config();assert LaunchOptions.from_config(reloaded).to_dict()==values
    (data/'config/one_dragon.yml').write_text(yaml.safe_dump(dict(instance_list=[dict(idx=1,active=True)])),encoding='utf-8')
    paths=SimpleNamespace(data=data,stable_data=data,generation='-')
    with patch('autozzz.onedragon_runtime.RuntimePaths.discover',return_value=paths):
        assert legacy_launch_configuration(ROOT,'cn')[0].to_dict()==values
    registry=Registry(dict(exists=True,type=1,data='GpuPreference=2;AutoHDREnable=2097;'))
    hdr=HdrStore(game,root=data/'hdr',registry=registry,lock_factory=Lock);spawns=[]
    session=LaunchSession(game,store.launch_options(),hdr_store=hdr,popen=lambda argv,**kwargs:spawns.append((argv,kwargs)))
    session.spawn();assert '2096'in registry.value['data'];session.close()
    assert '2097'in registry.value['data']and spawns[0][1]['shell']is False
    cancel=SimpleNamespace(is_set=lambda:False,wait=lambda _:None)
    existing=GameStartup(game,cancelled=cancel,paused=cancel,poll_keys=lambda:None,elapsed=lambda _:None,phase=lambda _:None,
        options=values,inventory=lambda:[dict(path=str(game),pid=5,created=1)],find_window=lambda _:SimpleNamespace(hwnd=8))
    existing.launch_session=session
    before=len(registry.writes)
    assert existing.run(True)==dict(window_handle=8,process_id=5)
    assert len(registry.writes)==before and len(spawns)==1
    execution=SimpleNamespace(cancelled=cancel,manual_pause=cancel,_poll_task_keys=lambda:None,_validate_task_input=lambda:None,
                              active_seconds=0,time_limit=600,startup=None,preparation_step='',job_id='initial')
    adapter=StartupOperationControl(ctx,execution)
    from zzz_od.operation.enter_game.open_game import OpenGame
    from zzz_od.operation.enter_game.auto_hdr import DisableAutoHDR,EnableAutoHDR
    original=OpenGame.open_game
    try:
        adapter.install()
        assert OpenGame.open_game.operation_node_annotation.op_method is OpenGame.open_game
        result=SimpleNamespace(round_success=lambda *args:'ok',round_fail=lambda message:('failed',message))
        class Startup:
            def __init__(self,*args,**kwargs):assert kwargs['options'].to_dict()==values
            def run(self,_):return dict(window_handle=8,process_id=5)
        with patch('autozzz.onedragon_startup.GameStartup',Startup):assert OpenGame.open_game(result)=='ok'
        with patch('autozzz.game_launch.HdrStore',return_value=hdr):
            before=registry.read('');count=len(registry.writes)
            assert EnableAutoHDR.enable_auto_hdr(result)=='ok';assert len(registry.writes)==count
            assert DisableAutoHDR.disable_auto_hdr(result)=='ok';assert '2096'in registry.value['data']
            assert DisableAutoHDR.disable_auto_hdr(result)=='ok';assert len(registry.writes)==count+1
            owned=adapter.hdr_lease(game)
            assert owned is not None
            foreign=LaunchSession(game,popen=lambda *_args,**_kwargs:None)
            try:foreign.spawn()
            except RuntimeError as exc:assert str(exc)=='busy'
            else:raise AssertionError('Foreign launch used the active HDR transaction')
            borrowed=LaunchSession(game,values,popen=lambda argv,**kwargs:spawns.append((argv,kwargs)),hdr_lease=owned)
            borrowed.spawn();borrowed.close();assert '2096'in registry.value['data']
            assert EnableAutoHDR.enable_auto_hdr(result)=='ok';assert registry.value==before
            assert not hdr.active.exists()

            # The actual decorated upstream node is invoked twice while the first
            # Popen is blocked, then retried after Windows returns an error.
            execution.job_id='uncertain';entered=Event();release=Event();outputs=[];attempts=[]
            def uncertain_spawn(argv,**kwargs):
                attempts.append(argv);entered.set()
                assert release.wait(5)
                raise OSError('fixture result unknown')
            def startup_fixture(*args,**kwargs):
                kwargs.update(inventory=lambda:[],find_window=lambda _:None)
                return GameStartup(*args,**kwargs)
            with patch('autozzz.onedragon_startup.GameStartup',startup_fixture),patch('autozzz.game_launch.subprocess.Popen',uncertain_spawn):
                thread=Thread(target=lambda:outputs.append(OpenGame.open_game(result)))
                thread.start()
                try:
                    assert entered.wait(5),outputs
                    assert OpenGame.open_game(result)[0]=='failed'
                finally:release.set();thread.join(5)
                assert not thread.is_alive()and outputs==[('failed','fixture result unknown')],outputs
                assert OpenGame.open_game(result)[0]=='failed'and len(attempts)==1
                assert registry.value==before and not hdr.active.exists()
            execution.job_id='new-user-job'
            with patch('autozzz.onedragon_startup.GameStartup',Startup):assert OpenGame.open_game(result)=='ok'
            assert DisableAutoHDR.disable_auto_hdr(result)=='ok'
    finally:adapter.close()
    assert OpenGame.open_game is original
    assert registry.value==before and not hdr.active.exists()
    return dict(actual_sdk_config_roundtrip=True,launch_fields=sorted(expected),actual_upstream_nodes_routed=True,
                native_windows_argv=True,existing_process_not_relaunched=True,registry='fake only',
                raw_hdr_restores_original=True,raw_lease_owned_launch_and_close=True,raw_spawn_retry_event_checked=True,
                source=str(source),game_started=False,real_hdr_read_or_written=False,game_input_sent=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--child',action='store_true');parser.add_argument('--source',type=Path);parser.add_argument('--data',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'runs/game-launch-sdk-20261105.json');args=parser.parse_args()
    if args.child:print(json.dumps(child(args.source,args.data),ensure_ascii=False));return
    from autozzz.onedragon_runtime import RuntimePaths
    paths=RuntimePaths.discover(ROOT,use_selection=False)
    with tempfile.TemporaryDirectory(prefix='autozzz-launch-sdk-')as folder:
        env=os.environ.copy()
        for key in ('PYTHONHOME','PYTHONPATH','PYTHONUSERBASE','VIRTUAL_ENV','CONDA_PREFIX'):env.pop(key,None)
        env.update(PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
        result=subprocess.run([str(paths.python),'-s','-P',str(Path(__file__).resolve()),'--child','--source',str(paths.source),'--data',folder],
            capture_output=True,text=True,encoding='utf-8',env=env,cwd=folder,timeout=90,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:raise RuntimeError(result.stdout+'\n'+result.stderr)
        data=json.loads(result.stdout.strip().splitlines()[-1]);args.output.parent.mkdir(exist_ok=True,parents=True)
        args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(data,ensure_ascii=False))


if __name__=='__main__':main()
