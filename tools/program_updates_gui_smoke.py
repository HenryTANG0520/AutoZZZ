"""版本窗口三种 DPI、取消原任务、安装确认与正常关闭；不访问游戏或网络。"""
from __future__ import annotations

import argparse
import copy
import gc
import json
from pathlib import Path
import sys
import tempfile
import threading
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.program_updates_ui import open_program_updates
from onedragon_configuration_gui_smoke import pump,layout

CATALOG=dict(installed=True,current='dev-fixture',revision='r1',channel='stable',page=1,has_more=True,releases=[dict(id='7',version='v1.2.3',published='2026-10-30T10:00:00Z',bytes=200000000)],history=[dict(version='v1.1.0',path='fixture/AutoZZZ.previous-archive')])


class Session:
    def __init__(self,paths,emit):
        self.emit=emit;self.calls=[];self.job=None;self.release=False;self.fail_poll=False
        self.closing=threading.Event();self.exit_allowed=threading.Event();self.exit_allowed.set()
        def work():self.closing.wait();self.exit_allowed.wait();emit(dict(type='closed'))
        self.worker=threading.Thread(target=work,daemon=False);self.worker.start();emit(dict(type='connected'))
    def close(self):self.closing.set()
    def send(self,command,**request):
        assert command=='rpc';method,params=request['method'],request['params'];self.calls.append((method,copy.deepcopy(params)))
        if method=='program_catalog':result=copy.deepcopy(CATALOG)
        elif method=='program_check':
            catalog=copy.deepcopy(CATALOG);catalog.update(channel=params['channel'],page=params['page'],has_more=params['page']==1)
            result=dict(job_id='check',busy=False,kind='check',state='success',result=catalog,detail='检查完成',progress=1.)
        elif method in ('program_download','program_import','program_restore'):
            if method=='program_download':assert params==dict(release_id='7',revision='r1')
            kind=method.removeprefix('program_');self.job=dict(job_id='same',kind=kind,state='working',detail='正在验证',progress=.5,result=None,busy=True);result=copy.deepcopy(self.job)
        elif method in ('program_status','program_cancel'):
            assert params['job_id']=='same'
            if self.fail_poll:self.fail_poll=False;self.emit(dict(type='error',message='观察暂时失败'));return
            if method=='program_cancel':self.job.update(state='cancelling',detail='等待原验证退出')
            if self.release:
                cancelled=self.job['state']=='cancelling'
                self.job.update(busy=False,state='cancelled' if cancelled else 'success',detail='已取消' if cancelled else '已验证',
                    result=None if cancelled else dict(version='v1.2.3',transaction='fixture',journal_hash='hash'))
            result=copy.deepcopy(self.job)
        else:raise AssertionError(method)
        self.emit(dict(type='rpc_result',tag=method,result=result))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    cases=[]
    with tempfile.TemporaryDirectory(prefix='autozzz-program-gui-') as temporary:
        path=Path(temporary)/'config.json';Settings().save(path);original=path.read_bytes()
        with patch('autozzz.program_updates_ui.RuntimeSession',Session),patch('autozzz.gui.AutoZZZWindow._discover_report'):
            for scale in (1.,1.5,2.):
                root=tk.Tk();root.tk.call('tk','scaling',scale*96/72);errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(value)
                ui=AutoZZZWindow(root,path,background_helpers=False);ui.overlay.set_enabled(False);ui._show_page('settings');ui.show_settings_section('updates')
                view=open_program_updates(ui);pump(root,lambda:view.catalog is not None and not view.busy);session=view.session
                try:
                    assert open_program_updates(ui)is view
                    assert view.previous_button.instate(['disabled']) and view.next_button.instate(['!disabled'])
                    view.next_button.invoke();pump(root,lambda:not view.busy and view.catalog['page']==2)
                    assert view.previous_button.instate(['!disabled']) and view.next_button.instate(['disabled'])
                    view.previous_button.invoke();pump(root,lambda:not view.busy and view.catalog['page']==1)
                    view.channel.set('测试版');view.channel_box.event_generate('<<ComboboxSelected>>')
                    pump(root,lambda:not view.busy and view.catalog['channel']=='beta')
                    assert ('program_check',dict(channel='beta',page=1))in session.calls
                    for size in ('default','minimum'):
                        if size=='minimum':view.window.geometry('%dx%d'%view.window.minsize())
                        for tab in (0,1):view.book.select(tab);root.update();layout(view.window);cases.append([scale,size,tab])
                    view.book.select(0);view.tables['release'].selection_set('7');root.update();view.operation_button.invoke()
                    pump(root,lambda:view.job is not None and not view.busy)
                    assert view.apply_button.instate(['disabled'])
                    session.fail_poll=True;view.next_poll=0;pump(root,lambda:'观察' in view.status.get())
                    assert view.job['job_id']=='same' and len([c for c in session.calls if c[0]=='program_download'])==1
                    view.cancel_button.invoke();view.next_poll=0;pump(root,lambda:view.job is not None and view.job['state']=='cancelling')
                    assert view.job['busy'];session.release=True;view.next_poll=0;pump(root,lambda:view.job is None and not view.busy)
                    assert view.plan is None
                    session.release=True;view.operation_button.invoke();pump(root,lambda:view.plan is not None and not view.busy)
                    root.update();layout(view.window);cases.append([scale,'prepared'])
                    with patch('autozzz.program_updates_ui.messagebox.askokcancel',return_value=False),patch.object(ui,'_close') as close:
                        view.apply_button.invoke();close.assert_not_called()
                    with patch('autozzz.program_updates_ui.messagebox.askokcancel',return_value=True),patch.object(ui,'_close',side_effect=lambda:setattr(ui,'closing',True)):
                        view.apply_button.invoke();assert ui.pending_program_update['version']=='v1.2.3'
                    ui.closing=False;ui.pending_program_update=None
                    if scale==1.:
                        from PIL import ImageGrab
                        ImageGrab.grab(window=view.window.winfo_id()).save(args.output/'prepared.png')
                    session.exit_allowed.clear();view.close();root.update();assert not view.closed and session.worker.is_alive()
                    session.exit_allowed.set();pump(root,lambda:view.closed and not session.worker.is_alive())
                    assert path.read_bytes()==original and not errors,errors
                finally:
                    session.exit_allowed.set();view.close();pump(root,lambda:view.closed)
                    ui.overlay.close()
                    for token in root.tk.splitlist(root.tk.call('after','info')):root.after_cancel(token)
                    root.destroy()
                ui=view=session=None;gc.collect()
    result=dict(layout_cases=cases,transient_observation_does_not_restart=True,cancel_waits_original_job=True,
                apply_requires_verified_plan_and_confirmation=True,channel_and_paging_controls=True,close_waits_original_worker=True,legacy_config_unchanged=True,game_accessed=False)
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
