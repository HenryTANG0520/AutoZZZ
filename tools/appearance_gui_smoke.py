"""Actual Tk DPI/layout, managed image/video and close-wait acceptance; no game."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows,theme
from autozzz.appearance_preferences import AppearancePreferences,DEFAULTS
from autozzz.appearance_media import import_media
from autozzz.appearance_ui import AppearanceWindow
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.overlay import NativePresentation
from onedragon_configuration_gui_smoke import layout

AUDIT=None


class FocusAudit:
    def __init__(self,path):
        self.path=path;self.known={};self.stage='start';self.last=None;self.records=[]
        self.user32=windows.user32
        self.user32.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];self.user32.GetAncestor.restype=wintypes.HWND
        self.user32.GetWindowLongPtrW.argtypes=[wintypes.HWND,ctypes.c_int];self.user32.GetWindowLongPtrW.restype=ctypes.c_ssize_t
        self.observe('initial',force=True)

    def window(self,widget,label):
        handle=self.user32.GetAncestor(widget.winfo_id(),2)
        self.known[handle]=label
        self.observe(self.stage+':registered '+label,force=True)
        return handle

    def observe(self,stage=None,force=False):
        if stage is not None:self.stage=stage
        handle=self.user32.GetForegroundWindow()
        if force or handle!=self.last:
            pid=wintypes.DWORD();self.user32.GetWindowThreadProcessId(handle,ctypes.byref(pid))
            title=ctypes.create_unicode_buffer(512);self.user32.GetWindowTextW(handle,title,len(title))
            row=dict(time=time.monotonic(),stage=self.stage,hwnd=handle,pid=pid.value,title=title.value,
                own_process=pid.value==os.getpid(),owned_window=self.known.get(handle),
                ex_style=self.user32.GetWindowLongPtrW(handle,-20) if handle else None)
            self.records.append(row);self.last=handle
            self.path.write_text(json.dumps(dict(test_pid=os.getpid(),records=self.records,owned_windows=self.known),ensure_ascii=False,indent=2),encoding='utf-8')


def pump(root,predicate,timeout=5):
    deadline=time.monotonic()+timeout
    while not predicate():
        root.update()
        if AUDIT is not None:AUDIT.observe()
        if time.monotonic()>deadline:raise AssertionError('Appearance GUI did not settle')
        time.sleep(.02)
    root.update()
    if AUDIT is not None:AUDIT.observe()


def main():
    global AUDIT
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    AUDIT=FocusAudit(args.output/'focus-audit.json')
    initial_foreground=windows.user32.GetForegroundWindow();cases=[]
    import cv2
    import numpy as np
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix='autozzz-appearance-') as folder,patch.dict(os.environ,LOCALAPPDATA=folder):
        base=Path(folder);config=base/'config.json';Settings().save(config);original=config.read_bytes()
        image=base/'wall.png';Image.new('RGB',(100,70),(30,90,180)).save(image)
        video=base/'clip.avi';writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),10,(160,90))
        assert writer.isOpened()
        for index in range(20):writer.write(np.full((90,160,3),(index*11,55,240-index*9),np.uint8))
        writer.release()
        store=AppearancePreferences();store.load();image_asset=import_media(image,store);video_asset=import_media(video,store)
        for mode in ('dark','light'):
            for scale in (1.,1.5,2.):
                print('case',mode,scale,flush=True)
                store.load();value=deepcopy(DEFAULTS);value.update(theme=mode,background='custom',assets={'custom':image_asset});store.save(value)
                root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72)
                presentation=NativePresentation(protect_capture=False);presentation.prepare(root)
                AUDIT.window(root,f'{mode}-{scale}:main')
                errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(str(value))
                with patch.object(AutoZZZWindow,'_discover_report'):
                    ui=AutoZZZWindow(root,config,background_helpers=False)
                ui.overlay.set_enabled(False);AUDIT.observe(f'{mode}-{scale}:show main',force=True);root.deiconify();root.update();AUDIT.observe(force=True)
                try:
                    controller=ui.appearance_controller
                    pump(root,lambda:controller.frame() is not None)
                    layout(root)
                    assert theme.MODE==mode
                    # Reapplying an existing theme used to reuse element names.
                    style=theme.apply_theme(root,ui.ui_scale);root.update();layout(root)
                    from autozzz.appearance_preferences import contrast
                    for name in ('Treeview','Config.Treeview'):
                        selected_foreground=style.lookup(name,'foreground',('selected',))
                        selected_background=style.lookup(name,'background',('selected',))
                        assert contrast(selected_foreground,selected_background)>=4.5,(mode,name,selected_foreground,selected_background)
                    window=AppearanceWindow(ui);ui.appearance_window=window
                    AUDIT.window(window.root,f'{mode}-{scale}:appearance')
                    presentation.prepare(window.root);root.update();layout(window.root)
                    AUDIT.observe(f'{mode}-{scale}:show appearance',force=True)
                    for width,height in ((660,560),(600,520)):
                        window.root.geometry(f'{round(width*scale)}x{round(height*scale)}');root.update();layout(window.root)
                    window.value['assets']['custom']=video_asset
                    window.background.set(window.background_labels['custom']);window.save()
                    pump(root,lambda:controller.player.frame_number>=3)
                    frame=controller.frame().getpixel((1,1));number=controller.player.frame_number
                    pump(root,lambda:controller.player.frame_number>=number+3)
                    assert frame!=controller.frame().getpixel((1,1))
                    ui._show_page('settings');root.update();time.sleep(.2);number=controller.player.frame_number
                    for _ in range(12):root.update();time.sleep(.025)
                    assert number==controller.player.frame_number,'Hidden video advanced'
                    ui._show_page('home');pump(root,lambda:controller.player.frame_number>number)
                    AUDIT.observe(f'{mode}-{scale}:before iconify',force=True)
                    root.iconify();root.update();AUDIT.observe(f'{mode}-{scale}:after iconify',force=True);controller.visibility();time.sleep(.2);number=controller.player.frame_number
                    time.sleep(.25);assert number==controller.player.frame_number,'Minimized video advanced'
                    root.deiconify();root.update();AUDIT.observe(f'{mode}-{scale}:after restore',force=True);controller.visibility();pump(root,lambda:controller.player.frame_number>number)
                    # Save the actual image already installed on the real Tk canvas.
                    from PIL import ImageTk
                    ImageTk.getimage(ui.table._photo).save(args.output/f'{mode}-{scale}.png')
                    assert not errors,errors
                    print('closing',mode,scale,flush=True)
                    AUDIT.observe(f'{mode}-{scale}:before close',force=True)
                    close_waited=False
                    if mode=='light' and scale==2:
                        entered=threading.Event();release=threading.Event();publish=controller.player._publish
                        def delayed_publish(frame,generation):
                            entered.set();release.wait(3);publish(frame,generation)
                        controller.player._publish=delayed_publish
                        pump(root,entered.is_set)
                        window.close();ui._close()
                        assert root.winfo_exists() and controller.worker.is_alive(),'Root discarded an active decoder'
                        release.set();close_waited=True
                    else:window.close();ui._close()
                    assert controller.closed and window.closed
                    worker=controller.worker;worker.join(3);assert not worker.is_alive()
                    AUDIT.observe(f'{mode}-{scale}:after close',force=True)
                    cases.append(dict(mode=mode,scale=scale,window_sizes=2,theme_reapply=True,video_frames=True,hidden_pause=True,minimized_pause=True,closed=True,close_waited=close_waited))
                finally:
                    if not ui.appearance_controller.closed:ui.appearance_controller.close()
                    if ui.appearance_controller.worker:ui.appearance_controller.worker.join(3)
                    try:root.destroy()
                    except tk.TclError:pass
                    AUDIT.observe(f'{mode}-{scale}:destroyed',force=True)
        assert config.read_bytes()==original,'Game/task configuration changed'
    AUDIT.observe('final',force=True)
    final_foreground=windows.user32.GetForegroundWindow()
    assert final_foreground==initial_foreground,'Foreground changed; see focus-audit.json'
    owned_activations=[row for row in AUDIT.records if row['own_process']]
    assert not owned_activations,('Test fixture activated its own window',owned_activations)
    result=dict(cases=cases,game_accessed=False,input_sent=False,task_config_unchanged=True,foreground_unchanged=True,
        initial_foreground_hwnd=initial_foreground,final_foreground_hwnd=final_foreground,
        focus_samples=len(AUDIT.records),owned_activations=0)
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
