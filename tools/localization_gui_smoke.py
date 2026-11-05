"""Real Tk, both languages at three DPI scales; no SDK/game/input access."""
from pathlib import Path
import argparse,ctypes,json,sys,tempfile,tkinter as tk
from tkinter import ttk
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.localization import language_scope,tr
from autozzz.localization_tk import PresentationVar
from autozzz.appearance_ui import AppearanceWindow
from autozzz.onedragon_run_ui import ExecutionWindow
from onedragon_run_gui_smoke import Session
from mcp_gui_smoke import layout_window
from onedragon_configuration_gui_smoke import pump


def walk(widget):
    yield widget
    for child in widget.winfo_children():yield from walk(child)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True);cases=[];missing=set();clipped=[]
    foreground=windows.user32.GetForegroundWindow()
    with tempfile.TemporaryDirectory(prefix='autozzz-language-')as folder:
        config=Path(folder)/'config.json';Settings().save(config);original=config.read_bytes()
        for language in ('zh','en'):
            for scale in (1.,1.5,2.):
                with language_scope(language),patch('autozzz.gui.AutoZZZWindow._discover_report'):
                    root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72)
                    from autozzz.overlay import NativePresentation
                    native=NativePresentation(protect_capture=False);handle=native.prepare(root)
                    from ctypes import wintypes as W
                    native.user32.SetLayeredWindowAttributes.argtypes=[W.HWND,W.DWORD,W.BYTE,W.DWORD]
                    native.user32.SetLayeredWindowAttributes(handle,0,255,2)
                    ui=AutoZZZWindow(root,config,background_helpers=False);ui.overlay.set_enabled(False)
                    try:
                        root.deiconify();root.update()
                        for size in ('default','minimum'):
                            if size=='minimum':root.geometry('%dx%d'%root.minsize())
                            for page in ui.pages:
                                ui._show_page(page);root.update()
                                for child in walk(root):
                                    if not child.winfo_ismapped():continue
                                    if isinstance(child,(ttk.Label,ttk.Button,ttk.Checkbutton)):
                                        text=str(child.cget('text'))
                                        if language=='en'and any('\u4e00'<=c<='\u9fff'for c in text):missing.add(text)
                                        # Wrapped labels may have width constrained by their container.
                                        if isinstance(child,(ttk.Button,ttk.Checkbutton))and child.winfo_width()+2<child.winfo_reqwidth():
                                            clipped.append((language,scale,size,page,text,child.winfo_width(),child.winfo_reqwidth()))
                                cases.append((language,scale,size,page))
                        assert ui.server.get()=='国服'and ui.promoter.get()==''
                        assert ui.status.get()=='准备就绪'
                        assert ui.status.display==('Ready'if language=='en'else'准备就绪')
                        assert ui.nav_buttons['tasks'].cget('text')==tr('目标')
                        assert root.title()==tr('AutoZZZ · 快捷手册')
                        ui.promoter.set('坏女人 蕾米埃尔');assert ui.promoter.get()=='坏女人 蕾米埃尔'
                        sample=PresentationVar(root,'正在执行：坏女人');assert sample.get()=='正在执行：坏女人'
                        assert sample.display==('Running: 坏女人'if language=='en'else'正在执行：坏女人')
                        assert not sample.trace_info()
                        tree=ttk.Treeview(root,columns=['team']);tree.insert('','end',iid='x',text='设置',values=['坏女人'])
                        assert tree.item('x','text')=='设置'and tree.item('x','values')==('坏女人',)
                        tree.destroy()
                        appearance=AppearanceWindow(ui);ui.appearance_window=appearance
                        native.prepare(appearance.root);root.update()
                        for size in ('default','minimum'):
                            if size=='minimum':appearance.root.geometry('%dx%d'%appearance.root.minsize())
                            root.update();layout_window(appearance.root);cases.append((language,scale,size,'appearance'))
                        appearance.close()
                        with patch('autozzz.onedragon_run_ui.RuntimeSession',Session):
                            execution=ExecutionWindow(ui,'charge_plan','体力刷本','cn');ui.execution_window=execution
                            native.prepare(execution.window)
                            pump(root,lambda:execution.connected and not execution.loading_account)
                            for size in ('default','minimum'):
                                if size=='minimum':execution.window.geometry('%dx%d'%execution.window.minsize())
                                root.update();layout_window(execution.window);cases.append((language,scale,size,'runtime'))
                            assert execution.region.get()=='cn'and execution.timeout.get()=='600'
                            execution.close();pump(root,lambda:execution.closed);execution.worker.join(3)
                            assert not execution.worker.is_alive()
                        if scale==1. and language=='en':
                            ui._show_page('tasks');root.attributes('-topmost',True);root.update()
                            from PIL import ImageGrab
                            ImageGrab.grab((root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())).save(args.output/'english.png')
                            root.attributes('-topmost',False)
                    finally:
                        with patch('autozzz.gui.messagebox.askyesnocancel',return_value=False):ui._close()
        assert config.read_bytes()==original
    assert foreground==windows.user32.GetForegroundWindow(),'Changed foreground'
    result=dict(cases=cases,clipped=clipped,untranslated=sorted(missing),config_unchanged=True,raw_values_unchanged=True,game_accessed=False)
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(cases=len(cases),clipped=clipped,untranslated=sorted(missing))))
    assert not clipped,clipped


if __name__=='__main__':main()
