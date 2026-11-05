"""Actual native editor and SDK, six PNG imports, preview/mask/archive and three DPIs."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import ROOT
from autozzz.agent_templates_ui import AgentTemplatesWindow
from autozzz.onedragon_runtime import RuntimePaths
from ui_layout_smoke import check_layout


def pump(root,predicate):
    end=time.monotonic()+40
    while time.monotonic()<end:
        root.update()
        if predicate():return
        time.sleep(.02)
    raise AssertionError('Template GUI did not finish')


def main():
    from PIL import Image
    checks=[];source=RuntimePaths.discover(ROOT,'cn')
    with tempfile.TemporaryDirectory(prefix='autozzz-template-gui-') as temp:
        folder=Path(temp);image=folder/'测试客户区.png';Image.new('RGB',(1920,1080),(31,91,181)).save(image)
        for scale in (1.,1.5,2.):
            root=tk.Tk();root.tk.call('tk','scaling',scale*96/72);errors=[];root.report_callback_exception=lambda kind,error,tb:errors.append(str(error))
            ui=SimpleNamespace(root=root,ui_scale=scale,server=tk.StringVar(root,'国服'),game_path=tk.StringVar(root,str(folder/'ZenlessZoneZero.exe')))
            paths=replace(source,data=folder/str(scale))
            with patch('autozzz.agent_templates_ui.RuntimePaths.discover',return_value=paths):view=AgentTemplatesWindow(ui,'cn')
            try:
                pump(root,lambda:view.preferences is not None and not view.busy)
                for size in ('default','minimum'):
                    if size=='minimum':view.window.geometry('%dx%d'%view.window.minsize())
                    for i in range(3):view.book.select(i);root.update();check_layout(view.window);checks.append(f'{scale}-{size}-{i}')
                view.open_agent('anby');pump(root,lambda:view.draft is not None and not view.busy)
                view.name.set('anby')
                for i in range(6):
                    view.selected=i;view.import_png(image);pump(root,lambda:not view.busy)
                    assert view.draft['images'][i] and view.draft['masks'][i],view.status.get()
                view.choose_step(2);pump(root,lambda:not view.busy)
                view.side.set('右侧');view.change_side();pump(root,lambda:not view.busy)
                view.mask.set(True);view.draw();root.update();check_layout(view.window)
                if view.float is not None:
                    view.toggle_expand();pump(root,lambda:not view.busy);root.update();check_layout(view.float.window)
                view.save();pump(root,lambda:not view.busy and not view.dirty and any(row['id']=='anby' for row in view.catalog['agents']))
                view.book.select(0);view.search.set('anby');root.update();assert 'anby' in view.table.get_children()
                view.table.selection_set('anby')
                with patch('autozzz.agent_templates_ui.messagebox.askokcancel',return_value=True):view.delete()
                pump(root,lambda:not view.busy and not any(row['id']=='anby' for row in view.catalog['agents']))
                deletion=next(row for row in view.catalog['archives'] if row['metadata']['action']=='delete')
                view.archives.selection_set(deletion['id'])
                with patch('autozzz.agent_templates_ui.messagebox.askokcancel',return_value=True):view.restore()
                pump(root,lambda:not view.busy and any(row['id']=='anby' for row in view.catalog['agents']))
                view.open_agent();pump(root,lambda:not view.busy);view.name.set('unsaved')
                with patch('autozzz.agent_templates_ui.messagebox.askokcancel',return_value=False):assert view.close()is False
                view.close(confirmed=True);pump(root,lambda:view.closed and not view.worker.is_alive())
                assert not errors,errors;checks.append(f'{scale}-actual-sdk-import-six-side-mask-save-delete-restore-draft-close')
            finally:
                view.close(confirmed=True)
                pump(root,lambda:view.closed and not view.worker.is_alive())
                for token in root.tk.splitlist(root.tk.call('after','info')):root.after_cancel(token)
                root.destroy()
        print(json.dumps(dict(actual_sdk=True,checks=checks,game_started=False,game_input_sent=False),ensure_ascii=False))


if __name__=='__main__':main()
