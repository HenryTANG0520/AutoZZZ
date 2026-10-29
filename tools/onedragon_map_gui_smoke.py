"""地图页实际 SDK 描述与图形保存回放，不连接游戏。"""
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
from autozzz.onedragon_library_ui import LibraryWindow
from autozzz.onedragon_map_ui import MapEditor
from autozzz.onedragon_runtime import RuntimePaths
from onedragon_configuration_gui_smoke import pump,layout


class Session:
    snapshot=None;instances=[]
    def __init__(self,paths,emit):
        self.instances.append(self);self.emit=emit;self.commands=[];self.data=copy.deepcopy(self.snapshot)
        self.document=copy.deepcopy(self.data['map']);self.original=copy.deepcopy(self.document)
        self.fail_save=self.fail_poll=self.release_capture=self.bad_archives=False;self.capture=None;self.previous=None
        self.finished=threading.Event();self.worker=threading.Thread(target=self.work,daemon=False);self.worker.start();self.emit(dict(type='connected'))
    def work(self):self.finished.wait();time.sleep(.15);self.emit(dict(type='closed'))
    def close(self):self.finished.set()
    def send(self,command,**params):
        assert command=='rpc';self.commands.append(copy.deepcopy(params));method,args=params['method'],params['params']
        if method=='library_catalog':result=self.data['catalog']
        elif method=='library_read':result=self.document
        elif method=='map_edit':
            assert args['data']==self.document['data'];action,value=args['action'],args.get('value')
            if action=='undo':
                version=self.document['data']['version'];self.document=copy.deepcopy(self.previous);self.document['data']['version']=version
            else:
                self.previous=copy.deepcopy(self.document);p=self.document['preview']
                if action=='scale':p['width']=round(p['width']*value/100);p['height']=round(p['height']*value/100);self.document['draft']=True
                elif action=='anchor':p['anchor']=p['candidate']=value;p['match_value']=1.
                elif action=='icons':p['icons']=value;self.document['draft']=True
                elif action=='settings':p.update(value)
                elif action in ('import','append_frame'):p['mini_png']=self.data['mini_png'];p['candidate']=None
                elif action=='captured':p['mini_png']=value['mask'];p['warning']=value['warning'];p['views']=value.get('views',[])
                elif action=='merge':self.document['draft']=True
            self.document['data']['version']+=1;self.document['preview']['undo']=self.previous is not None;result=self.document
        elif method=='library_save':
            assert args['data']==self.document['data']
            if self.fail_save:self.fail_save=False;self.emit(dict(type='error',message='地图或路线已变化，请重新读取。'));return
            self.document.update(draft=False,revision='saved-map');result=self.document
        elif method=='map_archives':
            result=dict(items=[],issues=[dict(file='archives/maps/fixture/manifest.json',error='地图归档内容已变化。')]) if self.bad_archives else dict(items=[dict(id='archive-1',key='地图',created_at='2026-10-29',route_count=7,restorable=True)],issues=[])
        elif method=='map_restore':self.document=copy.deepcopy(self.original);result=self.document
        elif method=='map_capture_start':
            self.capture=dict(kind='map',job_id='original-map-capture',busy=True,state='focus_wait',error='',result=None,control=dict(manual_paused=False))
            result=self.capture
        elif method.startswith('map_capture_'):
            assert args['job_id']=='original-map-capture'
            if method=='map_capture_status' and self.fail_poll:self.fail_poll=False;self.emit(dict(type='error',message='状态查询暂未完成。'));return
            if method=='map_capture_pause':self.capture['control']['manual_paused']=args['paused']
            elif method=='map_capture_cancel':self.capture.update(busy=False,state='cancelled')
            elif self.release_capture:self.capture.update(busy=False,state='success',result=dict(mask=self.data['mini_png'],icons=[],warning='恢复后的单帧',views=[self.data['mini_png']]))
            result=self.capture
        elif method=='map_recover':self.data['catalog']['map_pending']=False;result=dict(recovered=True,area_id=self.document['key'])
        else:raise AssertionError(method)
        self.emit(dict(type='rpc_result',tag=method,result=copy.deepcopy(result)))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--descriptions',type=Path,required=True);parser.add_argument('--preview',type=Path);args=parser.parse_args()
    from PIL import Image,ImageGrab
    import base64,io
    Session.snapshot=json.loads(args.descriptions.read_text(encoding='utf-8'))
    mini=Image.new('L',(100,100),0)
    from PIL import ImageDraw
    draw=ImageDraw.Draw(mini);draw.rectangle((10,40,90,60),fill=255);stream=io.BytesIO();mini.save(stream,format='PNG')
    Session.snapshot['mini_png']=base64.b64encode(stream.getvalue()).decode()
    cases=0;checks=[]
    with tempfile.TemporaryDirectory(prefix='autozzz-map-ui-') as folder:
        config=Path(folder)/'config.json';Settings().save(config);original=config.read_bytes()
        for scale in (1.,1.5,2.):
            root=tk.Tk();root.tk.call('tk','scaling',scale*96/72);errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(value)
            with patch('autozzz.gui.AutoZZZWindow._discover_report'),patch('autozzz.onedragon_library_ui.RuntimeSession',Session):
                ui=AutoZZZWindow(root,config);ui.overlay.set_enabled(False)
                dialog=LibraryWindow(ui,'global',kind='world_patrol_map');ui.library_window=dialog
                pump(root,lambda:isinstance(dialog.editor,MapEditor) and not dialog.busy)
                for size in ('default','minimum'):
                    if size=='minimum':dialog.window.geometry('%dx%d'%dialog.window.minsize())
                    root.update();layout(dialog.window);cases+=1
                editor=dialog.editor;editor.canvas.event_generate('<MouseWheel>',delta=120);root.update();assert editor.zoom>1
                dialog.map_action('anchor',[100,120]);pump(root,lambda:not dialog.busy)
                assert dialog.editor.zoom>1 and dialog.editor.preview['candidate']==[100,120]
                dialog.editor.resize();root.update();layout(dialog.editor.dialog.window);cases+=1
                form=dialog.editor.dialog.form;form.controls['percent'].delete(0,'end');form.controls['percent'].insert(0,'110')
                dialog.editor.dialog.confirm();pump(root,lambda:not dialog.busy and dialog.dirty)
                assert dialog.editor.preview['width']==round(Session.snapshot['map']['preview']['width']*1.1)
                editor=dialog.editor;editor.table.selection_set(editor.preview['icons'][0]['id']);editor.edit_icon()
                root.update();layout(editor.dialog.window);cases+=1
                form=editor.dialog.form;form.controls['lm_x'].delete(0,'end');form.controls['lm_x'].insert(0,str(editor.preview['width']))
                editor.dialog.confirm();assert editor.dialog.window.winfo_exists() and editor.dialog.status.get()
                form.controls['lm_x'].delete(0,'end');form.controls['lm_x'].insert(0,'100')
                form.controls['name'].delete(0,'end');form.controls['name'].insert(0,'图形编辑验证')
                editor.dialog.confirm();pump(root,lambda:not dialog.busy)
                assert dialog.editor.preview['icons'][0]['icon_name']=='图形编辑验证'
                dialog.editor.settings();root.update();layout(dialog.editor.dialog.window);cases+=1
                dialog.editor.dialog.confirm();pump(root,lambda:not dialog.busy)
                state=dialog.values();dialog.session.fail_save=True;dialog.save();pump(root,lambda:not dialog.busy)
                assert dialog.dirty and dialog.values()==state and '变化' in dialog.status.get()
                dialog.save();pump(root,lambda:not dialog.busy and not dialog.dirty)
                dialog.map_archives();pump(root,lambda:not dialog.busy and dialog.dialog is not None)
                root.update();layout(dialog.dialog.window);cases+=1
                with patch('autozzz.onedragon_library_ui.messagebox.askyesno',return_value=True):dialog.dialog.confirm()
                pump(root,lambda:not dialog.busy)
                assert dialog.editor.preview['width']==Session.snapshot['map']['preview']['width']
                dialog.session.bad_archives=True;dialog.map_archives();pump(root,lambda:not dialog.busy)
                root.update();layout(dialog.dialog.window);cases+=1
                assert 'manifest.json' in dialog.dialog.text.get('1.0','end') and dialog.catalog['issues']
                dialog.dialog.window.destroy();dialog.session.bad_archives=False
                frame=Path(folder)/'client.png';Image.effect_noise((1920,1080),20).convert('RGB').save(frame)
                with patch('autozzz.onedragon_map_ui.filedialog.askopenfilenames',return_value=(str(frame),str(frame))):dialog.editor.import_frames()
                pump(root,lambda:not dialog.busy and any(p['method']=='map_edit' and p['params']['action']=='append_frame' for p in dialog.session.commands))
                imports=[p['params'] for p in dialog.session.commands if p['method']=='map_edit' and p['params']['action'] in ('import','append_frame')]
                assert len(imports)==2 and len(imports[0]['value'])==1 and len(imports[0]['value'][0])>65536
                dialog.session.fail_poll=True;dialog.map_capture(True);pump(root,lambda:dialog.capture is not None and not dialog.busy)
                root.update();layout(dialog.window);cases+=1
                dialog.kind_var.set('巡逻路线');dialog.change_kind();assert dialog.kind=='world_patrol_map'
                pump(root,lambda:any(p['method']=='map_capture_status' for p in dialog.session.commands) and not dialog.busy)
                dialog.pause_capture();pump(root,lambda:dialog.capture['control']['manual_paused'] and not dialog.busy)
                dialog.session.release_capture=True;dialog.pause_capture()
                pump(root,lambda:dialog.capture is None and not dialog.busy and dialog.editor.preview['warning'])
                assert len([p for p in dialog.session.commands if p['method']=='map_capture_start'])==1
                assert dialog.editor.preview['mini_png'];root.update();layout(dialog.window);cases+=1
                if args.preview and scale==1.:
                    args.preview.parent.mkdir(parents=True,exist_ok=True);ImageGrab.grab(window=dialog.window.winfo_id()).save(args.preview)
                dialog.catalog['map_pending']=True;dialog.buttons()
                with patch('autozzz.onedragon_library_ui.messagebox.askyesno',return_value=True):dialog.recover_map()
                pump(root,lambda:not dialog.busy and not dialog.catalog.get('map_pending'))
                dialog.session.release_capture=False;dialog.map_capture(False);pump(root,lambda:dialog.capture is not None and not dialog.busy)
                dialog.capture_cancel_button.invoke();pump(root,lambda:dialog.capture is None and not dialog.busy)
                dialog.map_capture(False);pump(root,lambda:dialog.capture is not None and not dialog.busy)
                dialog.dirty=False;dialog.close();assert not dialog.closed
                pump(root,lambda:dialog.closed);dialog.worker.join(2)
                assert not dialog.worker.is_alive() and config.read_bytes()==original and not errors,errors
                ui._close();checks.append(dict(scale=scale,draft_conflict_preserved=True,large_dual_import_sequential=True,capture_original_job_pause_cancel=True,archive_and_recovery=True,close_waits_worker=True))

        # 真正的 UI -> SDK -> 地图与路线整组保存，再从归档恢复。
        defaults=RuntimePaths.discover(Path(__file__).resolve().parents[1]);live=RuntimePaths(defaults.source,defaults.python,Path(folder)/'actual-sdk')
        root=tk.Tk();errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(value)
        with patch('autozzz.gui.AutoZZZWindow._discover_report'),patch('autozzz.onedragon_library_ui.RuntimePaths.discover',return_value=live):
            ui=AutoZZZWindow(root,config);ui.overlay.set_enabled(False);dialog=LibraryWindow(ui,'cn',kind='world_patrol_map');ui.library_window=dialog
            try:
                pump(root,lambda:isinstance(dialog.editor,MapEditor) and not dialog.busy)
                key=Session.snapshot['map']['key']
                area=next(row for row in dialog.catalog['areas'] if row['id']==key)
                dialog.area_var.set(area['entry_name']);dialog.change_area();pump(root,lambda:not dialog.busy and dialog.document is not None)
                dialog.table.selection_set(key);dialog.select();pump(root,lambda:not dialog.busy and dialog.document['key']==key)
                scope=[path for path in live.data.rglob('*') if path.is_file() and path.parent.name==key and path.suffix in ('.yml','.png')]
                before={path:path.read_bytes() for path in scope};assert before
                old_width=dialog.editor.preview['width'];dialog.editor.resize();form=dialog.editor.dialog.form
                form.controls['percent'].delete(0,'end');form.controls['percent'].insert(0,'110');dialog.editor.dialog.confirm()
                pump(root,lambda:not dialog.busy and dialog.dirty)
                assert all(path.read_bytes()==raw for path,raw in before.items())
                dialog.save();pump(root,lambda:not dialog.busy and not dialog.dirty)
                assert dialog.editor.preview['width']==round(old_width*1.1) and any(path.read_bytes()!=raw for path,raw in before.items())
                dialog.map_archives();pump(root,lambda:not dialog.busy and dialog.dialog is not None)
                with patch('autozzz.onedragon_library_ui.messagebox.askyesno',return_value=True):dialog.dialog.confirm()
                pump(root,lambda:not dialog.busy and dialog.editor.preview['width']==old_width)
                assert all(path.read_bytes()==raw for path,raw in before.items()) and not errors,errors
                assert config.read_bytes()==original
                checks.append(dict(real_sdk_ui_map_and_route_group_save_restore=True,legacy_config_unchanged=True))
            finally:
                dialog.dirty=False;dialog.close();pump(root,lambda:dialog.closed);dialog.worker.join(2);ui._close()
    print(json.dumps(dict(layout_cases=cases,checks=checks,game_accessed=False),ensure_ascii=False),flush=True)


if __name__=='__main__':
    try:main()
    finally:
        for session in Session.instances:session.close();session.worker.join(3)
