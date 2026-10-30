"""专用设置的独立 Qt 进程；任务锁保留到实际进程退出。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading

from .onedragon_plugins import PluginStore,digest_tree


def settings_revision(ctx):
    from one_dragon.utils import os_utils
    root=Path(os_utils.get_work_dir())
    return PluginStore(root).generation()+':'+digest_tree(root/'plugins',code_only=True)


def has_settings(info):
    directory=getattr(info,'plugin_dir',None)
    return directory is not None and any(path.is_file() for path in Path(directory).glob('*_app_setting.py'))


def validate_target(ctx,app_id,instance_idx,revision,group_id='one_dragon'):
    if type(instance_idx)is not int or ctx.current_instance_idx!=instance_idx:
        raise ValueError('当前账号已变化，请重新读取配置。')
    if not isinstance(revision,str) or settings_revision(ctx)!=revision:
        raise ValueError('插件已变化，请重新读取配置。')
    if group_id not in ctx.app_group_manager.get_group_list(instance_idx):
        raise ValueError('应用组不存在，请重新读取配置。')
    if not isinstance(app_id,str) or app_id not in ctx.run_context._application_factory_map:
        raise ValueError('应用未注册，请重新读取配置。')
    info=ctx.factory_manager.get_plugin_info(app_id)
    if not has_settings(info):raise ValueError('该应用没有专用设置。')
    return info


class NativeSettingsSession:
    def __init__(self,paths,target,emit):
        self.paths,self.target,self.emit=paths,dict(target),emit
        self.closing=threading.Event();self.lock=threading.RLock();self.process=None
        self.worker=threading.Thread(target=self.work,name='AutoZZZ-Native-Settings',daemon=False)
        self.worker.start()

    def close(self):
        self.closing.set()
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.stdin.write(b'{"method":"close"}\n');self.process.stdin.flush()
                except (OSError,ValueError):pass

    def work(self):
        from .task_lease import RunLeaseOwner,LEASE_ENV
        lease=RunLeaseOwner();logger=None;remote_error=False
        try:
            self.paths.prepare()
            if self.closing.is_set():return
            lease.acquire()
            bridge=self.paths.bundle/'bridge' if self.paths.bundle is not None else Path(__file__).resolve().parent.parent
            environment=os.environ.copy()
            for name in ('PYTHONHOME','PYTHONUSERBASE','VIRTUAL_ENV','CONDA_PREFIX','PYTHONSTARTUP',LEASE_ENV):environment.pop(name,None)
            environment.update(PYTHONUTF8='1',PYTHONNOUSERSITE='1',PYTHONPATH=str(bridge))
            environment[LEASE_ENV]=lease.export()
            command=[str(self.paths.python),'-s','-P','-u','-m','autozzz.onedragon_settings_host',
                     '--source',str(self.paths.source.resolve()),'--data',str(self.paths.data.resolve()),
                     '--target',json.dumps(self.target,ensure_ascii=False)]
            with self.lock:
                if self.closing.is_set():return
                self.process=subprocess.Popen(command,cwd=bridge,env=environment,stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            def logs():
                for line in self.process.stderr:self.emit(dict(type='log',message=line.decode('utf-8',errors='replace').rstrip()))
            logger=threading.Thread(target=logs,name='AutoZZZ-Settings-Logs',daemon=True);logger.start()
            if self.closing.is_set():self.close()
            for line in self.process.stdout:
                try:
                    event=json.loads(line)
                    if not isinstance(event,dict) or event.get('type') not in ('ready','error'):raise ValueError('专用设置响应无效。')
                    if event['type']=='error':remote_error=True
                    self.emit(event)
                except Exception as exc:
                    remote_error=True;self.emit(dict(type='error',message=str(exc)));self.close()
            code=self.process.wait()
            if code and not remote_error:self.emit(dict(type='error',message=f'专用设置进程已退出（{code}）；请查看运行日志。'))
        except Exception as exc:self.emit(dict(type='error',message=str(exc)))
        finally:
            # EOF、等待超时或读管道失败都不能替代真实退出。
            if self.process is not None:
                self.close();self.process.wait()
                if logger is not None:logger.join()
                for stream in (self.process.stdin,self.process.stdout,self.process.stderr):stream.close()
            lease.close()
            self.emit(dict(type='closed'))
