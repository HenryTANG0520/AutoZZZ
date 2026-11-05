"""Isolated fixed-source developer tools; parent retains the task lease until exit."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading

PAGES = ('image_analysis', 'templates', 'screens')


class DevtoolsSession:
    def __init__(self, paths, emit,profile='cn'):
        if profile not in ('cn','global'):raise ValueError('开发工具区服无效。')
        self.paths,self.emit,self.profile=paths,emit,profile
        self.closing=threading.Event();self.lock=threading.RLock();self.process=None
        self.worker=threading.Thread(target=self.work,name='AutoZZZ-Devtools',daemon=False)
        self.worker.start()

    def close(self):
        self.closing.set()
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                try:self.process.stdin.write(b'{"method":"close"}\n');self.process.stdin.flush()
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
            environment.update(PYTHONUTF8='1',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(bridge))
            environment[LEASE_ENV]=lease.export()
            command=[str(self.paths.python),'-s','-P','-u','-m','autozzz.onedragon_devtools_host',
                     '--source',str(self.paths.source.resolve()),'--data',str(self.paths.data.resolve()),'--profile',self.profile]
            with self.lock:
                if self.closing.is_set():return
                self.process=subprocess.Popen(command,cwd=bridge,env=environment,stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            def logs():
                for line in self.process.stderr:self.emit(dict(type='log',message=line.decode('utf-8',errors='replace').rstrip()))
            logger=threading.Thread(target=logs,name='AutoZZZ-Devtools-Logs',daemon=True);logger.start()
            if self.closing.is_set():self.close()
            for line in self.process.stdout:
                try:
                    event=json.loads(line)
                    if not isinstance(event,dict) or event.get('type') not in ('ready','error','status'):raise ValueError('开发工具响应无效。')
                    if event['type']=='error':remote_error=True
                    self.emit(event)
                except Exception as exc:
                    remote_error=True;self.emit(dict(type='error',message=str(exc)));self.close()
            code=self.process.wait()
            if code and not remote_error:self.emit(dict(type='error',message=f'开发工具进程已退出（{code}），请查看日志。'))
        except Exception as exc:self.emit(dict(type='error',message=str(exc)))
        finally:
            if self.process is not None:
                self.close();self.process.wait()
                if logger is not None:logger.join()
                for stream in (self.process.stdin,self.process.stdout,self.process.stderr):stream.close()
            lease.close();self.emit(dict(type='closed'))
