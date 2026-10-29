"""实际 SDK 描述的原生资料库布局与操作回放，不连接游戏或网络。"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_library_ui import LibraryWindow, RouteEditor, GroupEditor, open_library
from autozzz.onedragon_runtime import RuntimePaths
from onedragon_configuration_gui_smoke import layout, pump


class Session:
    snapshot = None
    instances=[]
    def __init__(self, paths, emit):
        self.instances.append(self)
        self.emit, self.commands = emit, []
        self.data = copy.deepcopy(self.snapshot)
        self.documents = {(d['kind'], d['key']): d for d in (self.data['route'], self.data['group'], self.data['challenge'], self.data['hollow_challenge'], self.data['repair'])}
        self.catalogs = 0
        self.fail_save = self.hold = False
        self.held = None
        self.finished = threading.Event()
        self.worker = threading.Thread(target=self.work, daemon=False); self.worker.start()
        self.emit(dict(type='connected'))

    def work(self): self.finished.wait(); time.sleep(.15); self.emit(dict(type='closed'))
    def close(self): self.finished.set()

    def send(self, command, **params):
        assert command == 'rpc'
        self.commands.append((command, copy.deepcopy(params)))
        method, args = params['method'], params['params']
        if self.hold: self.held=(method, args); return
        if method == 'library_catalog': self.catalogs+=1; result=self.data['catalog']
        elif method == 'library_map': result=self.data['map']
        elif method == 'library_save':
            if self.fail_save:
                self.fail_save=False; self.emit(dict(type='error', message='资料已变化，请重新读取后保存。', busy=False)); return
            source = self.documents.get((args['kind'], args['key']), self.data['challenge'])
            result=dict(copy.deepcopy(source), kind=args['kind'], key=args['key'], name=args['key'], data=args['data'], revision='saved-revision', draft=False)
            self.documents[(args['kind'], args['key'])]=result
        elif method == 'library_draft':
            source = self.documents.get((args['kind'], args.get('source_key')), next(d for d in self.documents.values() if d['kind']==args['kind']))
            key = args.get('key') or source['key']
            result=dict(copy.deepcopy(source), key=key, name=key, draft=True, builtin=False, data=args.get('data') or source['data'])
        elif method == 'library_archive_read': result=dict(copy.deepcopy(self.data['challenge']), draft=True, archive_id=args['identifier'])
        elif method == 'library_export': result=dict(text='author: fixture\n')
        elif method == 'library_rename': result=dict(copy.deepcopy(self.documents[(args['kind'],args['key'])]), key=args['name'], name=args['name'])
        elif method == 'library_remove': result=dict(archive_id='fixture-archive', restored_template=False)
        else:
            result=self.documents.get((args['kind'],args['key']))
            if result is None:
                result=next(d for d in self.documents.values() if d['kind']==args['kind'])
        self.emit(dict(type='rpc_result', tag=method, result=copy.deepcopy(result)))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--descriptions', type=Path, required=True)
    parser.add_argument('--preview', type=Path)
    args=parser.parse_args()
    Session.snapshot=json.loads(args.descriptions.read_text(encoding='utf-8'))
    checks, cases= [], 0
    with tempfile.TemporaryDirectory(prefix='autozzz-library-ui-') as folder:
        config=Path(folder)/'config.json'; Settings().save(config); original=config.read_bytes()
        for scale in (1.,1.5,2.):
            root=tk.Tk(); root.tk.call('tk','scaling',scale*96/72)
            errors=[]; root.report_callback_exception=lambda kind,value,tb: errors.append(value)
            with patch('autozzz.gui.AutoZZZWindow._discover_report'), patch('autozzz.onedragon_library_ui.RuntimeSession',Session):
                ui=AutoZZZWindow(root,config); ui.overlay.set_enabled(False)
                dialog=LibraryWindow(ui,'global'); ui.library_window=dialog
                pump(root,lambda: dialog.document is not None and not dialog.busy)
                dialog.catalog['issues']=[dict(file='config/100/one_dragon/lost_void.yml', error='应用引用格式无效；修复后重新读取。')]
                dialog.buttons()
                assert '1' in dialog.menu.entrycget(7,'label')
                dialog.diagnostics();root.update();layout(dialog.dialog.window);cases+=1
                assert 'config/100/' in dialog.dialog.text.get('1.0','end') and dialog.dialog.text.cget('state')=='disabled'
                dialog.dialog.window.destroy();dialog.catalog['issues']=[];dialog.buttons()
                fixtures=list(dialog.session.documents.values())
                for size in ('default','minimum'):
                    if size=='minimum': dialog.window.geometry('%dx%d' % dialog.window.minsize())
                    for document in fixtures:
                        dialog.render(copy.deepcopy(document), dialog.session.data['map'] if document['kind']=='world_patrol_route' else None)
                        root.update(); layout(dialog.window); dialog.values(); cases+=1
                        if isinstance(dialog.editor,RouteEditor):
                            editor=dialog.editor; initial=len(editor.rows)
                            editor.canvas.event_generate('<MouseWheel>',delta=120); root.update()
                            assert editor.zoom > 1., '外层滚动截断了地图缩放'
                            x,y=editor.preview['width']//3,editor.preview['height']//3
                            event=SimpleNamespace(x=12+x*editor.ratio-editor.canvas.canvasx(0), y=12+y*editor.ratio-editor.canvas.canvasy(0))
                            editor.add_at(event);root.update()
                            assert len(editor.rows)==initial+1
                            actual=list(map(int,editor.rows[-1]['data']))
                            assert all(abs(a-b) <= 1/editor.ratio+1 for a,b in zip(actual,(x,y))), (actual,(x,y),editor.ratio)
                            assert dialog.dirty
                            editor.move(-1); editor.remove()
                            assert len(editor.rows)==initial
                            editor.edit(True);root.update();layout(editor.dialog.window);cases+=1
                            editor.dialog.form.controls['x'].delete(0,'end');editor.dialog.form.controls['x'].insert(0,str(editor.preview['width']))
                            editor.dialog.confirm(); assert editor.dialog.window.winfo_exists() and editor.dialog.status.get()
                            editor.dialog.window.destroy()
                        elif isinstance(dialog.editor,GroupEditor):
                            editor=dialog.editor
                            before=list(editor.rows);editor.table.selection_set('0');editor.move(1)
                            assert editor.rows[:2]==before[:2][::-1]
                            editor.add(True); assert len(editor.rows)==len(set(editor.rows))
                        dialog.dirty=False
                document=copy.deepcopy(dialog.session.data['route'])
                dialog.render(document,dialog.session.data['map']);root.update()
                editor=dialog.editor
                editor.rows.append(dict(op_type='move',data=['10','20']));dialog.changed()
                draft=copy.deepcopy(dialog.values())
                dialog.session.fail_save=True
                dialog.save_button.invoke()
                assert dialog.busy and not editor.enabled
                pump(root,lambda:not dialog.busy)
                assert dialog.dirty and dialog.values()==draft and editor.enabled and '变化' in dialog.status.get()
                dialog.save_button.invoke();pump(root,lambda:not dialog.busy and not dialog.dirty)
                sent=next(p[1]['params'] for p in reversed(dialog.session.commands) if p[1]['method']=='library_save')
                assert sent['data']==draft
                assert dialog.document['revision']=='saved-revision'
                document=copy.deepcopy(dialog.session.data['challenge']);dialog.render(document)
                field=next(f for f in document['fields'] if f['kind']=='bool')
                dialog.editor.controls[field['name']].invoke();assert dialog.dirty
                with patch('autozzz.onedragon_library_ui.simpledialog.askstring',return_value='草稿另存'):
                    dialog.copy()
                pump(root,lambda:dialog.document is not None and dialog.document['key']=='草稿另存' and not dialog.busy)
                assert dialog.dirty
                request=next(p[1]['params'] for p in reversed(dialog.session.commands) if p[1]['method']=='library_draft')
                assert request['data'][field['name']] != field['value']
                with patch('autozzz.onedragon_library_ui.messagebox.askyesno',return_value=False):
                    assert not dialog.close() and not dialog.closing
                dialog.dirty=False
                dialog.archives();pump(root,lambda:dialog.dialog is not None and dialog.dialog.window.winfo_exists() and not dialog.busy)
                root.update();layout(dialog.dialog.window);cases+=1
                dialog.dialog.confirm();pump(root,lambda:dialog.document.get('archive_id') is not None and not dialog.busy)
                assert dialog.dirty and '草稿' in dialog.status.get()
                dialog.dirty=False
                if args.preview and scale==1.:
                    from PIL import ImageGrab
                    dialog.render(copy.deepcopy(dialog.session.data['route']),dialog.session.data['map']);root.update()
                    args.preview.parent.mkdir(parents=True,exist_ok=True)
                    ImageGrab.grab(window=dialog.window.winfo_id()).save(args.preview)
                dialog.session.hold=True;dialog.reload()
                assert dialog.busy and dialog.session.held is not None
                dialog.close();assert not dialog.closed and dialog.worker.is_alive()
                pump(root,lambda:dialog.closed);dialog.worker.join(2)
                assert not dialog.worker.is_alive()
                assert not errors,errors
                assert config.read_bytes()==original
                checks.append(dict(scale=scale, coordinate_roundtrip=True, draft_copy=True, save_conflict_keeps_draft=True, close_waits=True))
                if scale==1.:
                    first=open_library(ui,'cn');pump(root,lambda:first.document is not None and not first.busy)
                    first.dirty=True
                    with patch('autozzz.onedragon_library_ui.messagebox.askyesno',return_value=False):
                        assert open_library(ui,'global') is first and ui.library_window is first and not first.closing
                    first.dirty=False
                    open_library(ui,'global')
                    pump(root,lambda:ui.library_window is not first and ui.library_window.document is not None and not ui.library_window.busy)
                    assert first.closed and not first.worker.is_alive() and ui.library_window.profile=='global'
                    current=ui.library_window
                    current.session.hold=True;current.reload()
                    destroyed=threading.Event()
                    root.bind('<Destroy>',lambda e: destroyed.set() if e.widget is root else None,add='+')
                    ui._close()
                    assert not destroyed.is_set() and current.worker.is_alive()
                    pump(root,destroyed.is_set)
                    current.worker.join(2);assert not current.worker.is_alive()
                    checks.append(dict(region_switch_waits_original_worker=True, rejected_switch_keeps_draft=True, parent_close_waits_worker=True))
                    continue
                ui._close()
        defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1])
        live_paths=RuntimePaths(defaults.source,defaults.python,Path(folder)/'actual-sdk')
        root=tk.Tk();errors=[]
        root.report_callback_exception=lambda kind,value,tb: errors.append(value)
        with patch('autozzz.gui.AutoZZZWindow._discover_report'), patch('autozzz.onedragon_library_ui.RuntimePaths.discover',return_value=live_paths):
            ui=AutoZZZWindow(root,config);ui.overlay.set_enabled(False)
            dialog=LibraryWindow(ui,'cn');ui.library_window=dialog
            try:
                pump(root,lambda:dialog.document is not None and not dialog.busy)
                with patch('autozzz.onedragon_library_ui.simpledialog.askstring',return_value='界面实际方案'):
                    dialog.copy()
                pump(root,lambda:dialog.document is not None and dialog.document['key']=='界面实际方案' and not dialog.busy)
                assert dialog.dirty and not (live_paths.data/'config/lost_void_challenge/界面实际方案.yml').exists()
                field=next(f for f in dialog.document['fields'] if f['kind']=='bool')
                dialog.editor.controls[field['name']].invoke()
                dialog.save_button.invoke();pump(root,lambda:not dialog.busy and not dialog.dirty)
                target=live_paths.data/'config/lost_void_challenge/界面实际方案.yml'
                assert target.is_file() and dialog.document['data'][field['name']] != field['value']
                with patch('autozzz.onedragon_library_ui.messagebox.askyesno',return_value=True): dialog.remove()
                pump(root,lambda:not dialog.busy and dialog.document is not None)
                assert not target.exists() and dialog.catalog['archives']
                dialog.archives();pump(root,lambda:not dialog.busy and dialog.dialog is not None)
                dialog.dialog.confirm();pump(root,lambda:not dialog.busy and dialog.document.get('archive_id'))
                assert dialog.dirty and not target.exists()
                dialog.save_button.invoke();pump(root,lambda:not dialog.busy and not dialog.dirty)
                assert target.is_file()
                assert config.read_bytes()==original and not errors,errors
                checks.append(dict(real_sdk_process=True, copy_save_archive_restore_via_ui=True, legacy_config_unchanged=True))
            finally:
                dialog.dirty=False;dialog.close();pump(root,lambda:dialog.closed)
                dialog.worker.join(2);assert not dialog.worker.is_alive()
                ui._close()
    print(json.dumps(dict(layout_cases=cases,checks=checks,game_accessed=False,external_operations=False),ensure_ascii=False))


if __name__=='__main__':
    try: main()
    finally:
        for session in Session.instances:
            session.close(); session.worker.join(3)
