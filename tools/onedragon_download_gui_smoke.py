"""模型下载窗口布局和原任务轮询回放，实际 SDK 只读取目录。"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_download_ui import DownloadsWindow,open_downloads
from autozzz.onedragon_runtime import RuntimePaths
from onedragon_configuration_gui_smoke import pump,layout


class Session:
    snapshot=None;instances=[]
    def __init__(self,paths,emit):
        self.instances.append(self);self.emit=emit;self.catalog=copy.deepcopy(self.snapshot);self.commands=[]
        self.job=None;self.release=False;self.fail_poll=False;self.fail_start=False
        self.finished=threading.Event();self.worker=threading.Thread(target=self.work,daemon=False);self.worker.start()
        self.emit(dict(type='connected'))
    def work(self):self.finished.wait();time.sleep(.2);self.emit(dict(type='closed'))
    def close(self):self.finished.set()
    def send(self,command,**params):
        assert command=='rpc';self.commands.append(copy.deepcopy(params));method,args=params['method'],params['params']
        if method=='model_catalog':result=copy.deepcopy(self.catalog)
        elif method=='model_download_start':
            if self.fail_start:self.fail_start=False;self.emit(dict(type='error',message='fixture start rejected'));return
            assert args['revision']==self.catalog['revision'] and args['resource_id'] in {row['id'] for row in self.catalog['items']}
            self.job=dict(job_id='fixture-job',busy=True,state='downloading',detail='正在下载',progress=.25,source='cnb',installed=False,
                          downloaded_bytes=2*1024*1024,total_bytes=8*1024*1024,bytes_per_second=512*1024)
            result=copy.deepcopy(self.job)
        elif method in ('model_download_status','model_download_cancel'):
            assert args['job_id']=='fixture-job'
            if self.fail_poll:self.fail_poll=False;self.emit(dict(type='error',message='fixture transient observation failure'));return
            if method=='model_download_cancel':self.job.update(state='cancelling',detail='等待原下载退出…')
            if self.release:
                cancelled=self.job['state']=='cancelling'
                self.job.update(busy=False,state='cancelled' if cancelled else 'success',installed=not cancelled,detail='已取消' if cancelled else '模型文件已就绪')
            result=copy.deepcopy(self.job)
        elif method=='model_install_recover':self.catalog['pending']=False;result=dict(recovered=True)
        else:raise AssertionError(method)
        self.emit(dict(type='rpc_result',tag=method,result=result))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--catalog',required=True,type=Path);parser.add_argument('--preview',type=Path);args=parser.parse_args()
    Session.snapshot=json.loads(args.catalog.read_text(encoding='utf-8'))
    cases,checks=0,[]
    with tempfile.TemporaryDirectory(prefix='autozzz-download-gui-') as folder:
        config=Path(folder)/'config.json';Settings().save(config);original=config.read_bytes()
        with patch('autozzz.onedragon_download_ui.RuntimeSession',Session),patch('autozzz.gui.AutoZZZWindow._discover_report'):
            for scale in (1.,1.5,2.):
                root=tk.Tk();root.tk.call('tk','scaling',scale*96/72);errors=[]
                root.report_callback_exception=lambda kind,value,tb:errors.append(value)
                ui=AutoZZZWindow(root,config,background_helpers=False);ui.overlay.set_enabled(False)
                with patch('autozzz.onedragon_download_ui.open_downloads') as opened:
                    ui.onedragon_panel.dirty=True;ui.onedragon_panel.downloads()
                    assert not opened.called and '保存配置' in ui.onedragon_panel.status.get()
                    ui.onedragon_panel.dirty=False;ui.onedragon_panel.downloads()
                    opened.assert_called_once_with(ui,'cn')
                dialog=open_downloads(ui,'cn');pump(root,lambda:dialog.catalog is not None and not dialog.busy)
                session=dialog.session
                try:
                    assert open_downloads(ui,'cn') is dialog
                    assert len(dialog.table.get_children())==7
                    for size in ('default','minimum'):
                        if size=='minimum':dialog.window.geometry('%dx%d'%dialog.window.minsize())
                        root.update();layout(dialog.window);cases+=1
                    session.catalog['pending']=True;dialog.reload();pump(root,lambda:dialog.catalog['pending'] and not dialog.busy)
                    root.update();layout(dialog.window);cases+=1
                    assert dialog.recover_button.winfo_ismapped() and dialog.download_button.instate(['disabled'])
                    dialog.recover_button.invoke();pump(root,lambda:not dialog.catalog['pending'] and not dialog.busy)
                    session.fail_start=True;dialog.download_button.invoke();pump(root,lambda:'fixture start rejected' in dialog.status.get() and not dialog.busy)
                    assert dialog.job is None
                    session.fail_poll=True;dialog.download_button.invoke();pump(root,lambda:dialog.job is not None and not dialog.busy)
                    first=dialog.job['job_id'];root.update();layout(dialog.window);cases+=1
                    assert dialog.download_button.instate(['disabled'])
                    pump(root,lambda:'transient observation' in dialog.status.get())
                    assert dialog.job['job_id']==first
                    dialog.cancel_button.invoke();pump(root,lambda:dialog.job and dialog.job['state']=='cancelling' and not dialog.busy)
                    assert dialog.job['busy'] and dialog.job['job_id']==first
                    root.update();layout(dialog.window);cases+=1
                    session.release=True;pump(root,lambda:dialog.job is None and not dialog.busy)
                    assert dialog.status.get()=='已取消'
                    session.release=False;dialog.download_button.invoke();pump(root,lambda:dialog.job is not None and not dialog.busy)
                    session.release=True;pump(root,lambda:dialog.job is None and not dialog.busy)
                    root.update();layout(dialog.window);cases+=1
                    assert dialog.status.get()=='模型文件已就绪'
                    starts=[row for row in session.commands if row['method']=='model_download_start'];assert len(starts)==3
                    session.release=False;dialog.download_button.invoke();pump(root,lambda:dialog.job is not None and not dialog.busy)
                    if args.preview and scale==1.:
                        from PIL import ImageGrab
                        args.preview.parent.mkdir(parents=True,exist_ok=True)
                        box=(dialog.window.winfo_rootx(),dialog.window.winfo_rooty(),dialog.window.winfo_rootx()+dialog.window.winfo_width(),dialog.window.winfo_rooty()+dialog.window.winfo_height())
                        ImageGrab.grab(bbox=box).save(args.preview)
                    destroy=ui._destroy_root
                    with patch.object(ui,'_destroy_root') as root_closed:
                        ui._close();assert not dialog.closed and dialog.closing and dialog.worker.is_alive() and not root_closed.called
                        root.update();layout(dialog.window);cases+=1
                        pump(root,lambda:dialog.closed and root_closed.called)
                    dialog.worker.join(2)
                    assert not dialog.worker.is_alive() and not errors,errors
                    destroy()
                    checks.append(dict(scale=scale,transient_poll_keeps_job=True,cancel_waits_same_job=True,pending_install_recovery=True,main_window_close_waits_worker=True,unsaved_source_guard=True))
                finally:
                    dialog.close();pump(root,lambda:dialog.closed);ui._close()
                assert config.read_bytes()==original

        # 实际 UI -> 独立 SDK 进程读取七个声明版本，不启动下载。
        defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);paths=RuntimePaths(defaults.source,defaults.python,Path(folder)/'actual-sdk')
        with patch('autozzz.gui.AutoZZZWindow._discover_report'),patch('autozzz.onedragon_download_ui.RuntimePaths.discover',return_value=paths):
            root=tk.Tk();errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(value)
            ui=AutoZZZWindow(root,config,background_helpers=False);ui.overlay.set_enabled(False);dialog=open_downloads(ui,'global')
            try:
                pump(root,lambda:dialog.catalog is not None and not dialog.busy)
                assert len(dialog.table.get_children())==7 and not list(paths.data.rglob('*.onnx'))
                assert not dialog.catalog['pending'] and config.read_bytes()==original and not errors,errors
                layout(dialog.window);cases+=1
                checks.append(dict(real_sdk_process_catalog=True,network_download_not_started=True,legacy_config_unchanged=True))
            finally:dialog.close();pump(root,lambda:dialog.closed);ui._close()
    print(json.dumps(dict(layout_cases=cases,checks=checks,game_accessed=False),ensure_ascii=False),flush=True)


if __name__=='__main__':
    try:main()
    finally:
        for session in Session.instances:session.close()
