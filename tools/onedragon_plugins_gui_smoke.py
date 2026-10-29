"""原生插件窗口、导入确认和关闭交接回放；只使用自建数据，不接触游戏。"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_plugins_ui import PluginWindow,ArchiveWindow
from autozzz.onedragon_forms import ScalarForm
from autozzz.onedragon_pip_ui import PipPresentation
from autozzz.windows import user32
from onedragon_configuration_gui_smoke import layout,pump


CATALOG=dict(plugins=[dict(name='demo',app_name='自建插件回放',version='2.0',author='AutoZZZ fixture',enabled=False,path='fixture',app_id='fixture',description='原生管理窗口的本地回放。',error='',homepage='https://example.invalid/plugin')],revision='r1',directory='fixture')
ARCHIVE=dict(id='a'*32,action='remove',created='2026-10-30T12:00:00+08:00',plugins=[dict(name='demo',digest='0'*64)])


class Session:
    def __init__(self,paths,emit):
        self.paths,self.emit=paths,emit;self.calls=[];self.closing=threading.Event();self.exit_allowed=threading.Event();self.exit_allowed.set();self.catalog=copy.deepcopy(CATALOG)
        self.worker=threading.Thread(target=self.work,daemon=False);self.worker.start();emit(dict(type='result',method='plugin_catalog',result=copy.deepcopy(self.catalog)))
    def work(self):self.closing.wait();self.exit_allowed.wait();self.emit(dict(type='closed'))
    def close(self):self.closing.set()
    def send(self,method,**params):
        self.calls.append((method,params))
        if method=='plugin_enabled':self.catalog['plugins'][0]['enabled']=params['enabled'];result=copy.deepcopy(self.catalog)
        elif method=='plugin_archives':result=[copy.deepcopy(ARCHIVE)]
        elif method=='plugin_directory':result='fixture'
        elif method=='plugin_preview':result=dict(token='review',revision='r1',plugins=[dict(name='demo',version='1.0',old_version='2.0',author='AutoZZZ fixture',exists=True,downgrade=True)])
        elif method in ('plugin_install','plugin_restore'):result=dict(catalog=copy.deepcopy(self.catalog),results=[dict(name='demo',success=True,message='fixture success')])
        else:result=copy.deepcopy(self.catalog)
        self.emit(dict(type='result',method=method,result=result))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    cases=[];foreground=user32.GetForegroundWindow()
    for scale in (1.,1.5,2.):
        with tempfile.TemporaryDirectory(prefix='autozzz-plugin-ui-') as directory:
            config=Path(directory)/'config.json';Settings().save(config);original=config.read_bytes();root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72)
            errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(value);ui=AutoZZZWindow(root,config,background_helpers=False);ui.overlay.set_enabled(False)
            presentation=PipPresentation(protect_capture=False);presentation.prepare(root)
            with patch('autozzz.onedragon_plugins_ui.PluginSession',Session):
                ui._show_page('applications');ui.onedragon_panel.plugins_button.invoke();view=ui.plugin_window
                view.window.withdraw();presentation.prepare(view.window);root.deiconify();view.window.deiconify()
                try:
                    pump(root,lambda:view.connected);view.table.selection_set('demo');view.select();root.update()
                    assert view.enable_button.instate(['disabled']);view.accept.set(True);view.buttons();assert not view.enable_button.instate(['disabled'])
                    view.enable_button.invoke();pump(root,lambda:not view.busy);assert view.selected()['enabled'] and not view.accept.get()
                    assert not view.enable_button.instate(['disabled']);view.enable_button.invoke();pump(root,lambda:not view.busy);assert not view.selected()['enabled']
                    with patch('autozzz.onedragon_plugins_ui.filedialog.askopenfilename',return_value='fixture.zip'):view.choose('zip')
                    pump(root,lambda:not view.busy);assert view.install_button.instate(['disabled'])
                    view.overwrite.set(True);view.buttons();assert view.install_button.instate(['disabled']);view.downgrade.set(True);view.buttons();assert not view.install_button.instate(['disabled'])
                    for size in ('default','minimum'):
                        if size=='minimum':view.window.geometry('%dx%d' % view.window.minsize());root.geometry('%dx%d' % root.minsize())
                        for name,index in (('installed',0),('preview',1)):
                            view.book.select(index);root.update();layout(root);layout(view.window);cases.append([scale,size,name])
                            if scale==1. and size=='default' and name=='preview':
                                from PIL import ImageGrab
                                ImageGrab.grab(window=view.window.winfo_id()).save(args.output/'import-preview.png')
                    view.install_button.invoke();pump(root,lambda:not view.busy);assert not view.review and not view.previews.get_children()
                    assert next(params for method,params in view.session.calls if method=='plugin_install')['allow_downgrade']is True
                    view.archive_button.invoke();pump(root,lambda:view.archive_window is not None);archive=view.archive_window;archive.window.withdraw();presentation.prepare(archive.window);archive.window.deiconify()
                    for size in ('default','minimum'):
                        if size=='minimum':archive.window.geometry('%dx%d' % archive.window.minsize())
                        root.update();layout(archive.window);cases.append([scale,size,'archive'])
                    with patch('autozzz.onedragon_plugins_ui.messagebox.showerror') as message:
                        archive.restore();assert message.called and not view.busy
                    archive.overwrite.set(True);archive.restore();pump(root,lambda:not view.busy)
                    assert next(params for method,params in view.session.calls if method=='plugin_restore')['overwrites']==['demo']
                    calls=len(view.session.calls)
                    with patch('autozzz.onedragon_plugins_ui.filedialog.askopenfilename',return_value=''):view.choose('zip')
                    with patch('autozzz.onedragon_plugins_ui.messagebox.askyesno',return_value=False):view.remove()
                    assert len(view.session.calls)==calls
                    with patch('autozzz.onedragon_plugins_ui.webbrowser.open') as opened:view.url('file:///fixture');assert not opened.called
                    # JSON 原生编辑保留草稿并报告格式，未经过保存不会写入任何配置。
                    form_window=tk.Toplevel(root);form_window.withdraw();presentation.prepare(form_window);form_window.columnconfigure(0,weight=1)
                    changed=[];form=ScalarForm(form_window,[dict(name='custom',label='自定义选项',kind='json',value={'routes':[]},hint='JSON 格式')],lambda:changed.append(True),ui.ui_scale);form.frame.grid(row=0,column=0,sticky='ew');form_window.deiconify();root.update();layout(form_window)
                    text=form.controls['custom'];text.delete('1.0','end');text.insert('1.0','{"routes":');root.update()
                    try:form.values();raise AssertionError('invalid JSON accepted')
                    except ValueError as exc:assert 'JSON 格式' in str(exc)
                    assert changed and config.read_bytes()==original;text.delete('1.0','end');text.insert('1.0','{"routes":["回放"]}');assert form.values()['custom']=={'routes':['回放']};form_window.destroy()
                    # 区服切换与关闭都等待同一原工作进程，不让旧结果重新启用动作。
                    previous=view.session;previous.exit_allowed.clear();generation=view.generation;view.server.set('国际服');view.change_profile();root.update()
                    assert view.session is previous and view.busy and not view.connected
                    previous.emit(dict(type='result',method='plugin_catalog',result=copy.deepcopy(CATALOG)));root.update();assert view.enable_button.instate(['disabled'])
                    previous.exit_allowed.set();pump(root,lambda:view.profile=='global' and view.connected)
                    view.events.put(dict(type='error',message='stale',fatal=True,generation=generation));root.update();assert view.connected
                    current=view.session;current.exit_allowed.clear();view.close();root.update();assert not view.closed and current.worker.is_alive()
                    current.exit_allowed.set();pump(root,lambda:view.closed);current.worker.join(2)
                    assert not errors,errors
                    assert config.read_bytes()==original and user32.GetForegroundWindow()==foreground
                finally:
                    view.session.exit_allowed.set();view.session.close();view.worker.join(2);ui.overlay.close()
                    for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
                    root.destroy()
    result=dict(layout_cases=cases,actual_applications_entry=True,load_requires_acceptance=True,overwrite_and_downgrade_independent=True,archive_restore_explicit=True,json_draft_validated=True,switch_and_close_wait_original=True,old_profile_event_ignored=True,game_accessed=False,game_input_sent=False)
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
