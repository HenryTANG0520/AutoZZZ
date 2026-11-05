"""Native Tk SDK manager forms and close lifecycle, with the backend deliberately absent."""
import json
from pathlib import Path
import queue
import sys
import tkinter as tk
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.sdk_upgrade_ui import SDKUpgradeWindow
from autozzz import theme


class NoBackendThread:
    def __init__(self,*args,**kwargs):pass
    def start(self):pass
    def join(self,*args,**kwargs):pass
    def is_alive(self):return False


def main():
    results=[]
    for scale in (1.,1.5,2.):
        root=tk.Tk();root.geometry('320x100+30+30');theme.apply_theme(root,scale)
        owner_window=tk.Toplevel(root);owner_window.title('SDK manager offline fixture')
        owner=NS(window=owner_window,ui=NS(root=root,ui_scale=scale),profile='cn')
        window=None
        try:
            with patch('autozzz.sdk_upgrade_ui.threading.Thread',NoBackendThread):window=SDKUpgradeWindow(owner)
            catalog=dict(stable_revision='f'*40,selected=None,selection_error='',selection_revision='-',activation_pending=False,
                stable_data=str(Path.home()/'sdk-fixture'),candidates=[dict(id='a'*32,state='ready',revision='c'*40,branch='feature/full-sdk',groups=['dev'],data_id='b'*32,
                bridge_hash='0'*64,probe=dict(compatible=True),error='',path='fixture',requirements_path='fixture')])
            window.events.put(('sdk_catalog',catalog));window.poll();root.update()
            for width,height in ((940,700),(760,560)):
                window.window.geometry(f'{round(width*scale)}x{round(height*scale)}');root.update_idletasks();root.update()
                x,y=window.window.winfo_rootx(),window.window.winfo_rooty();right=x+window.window.winfo_width();bottom=y+window.window.winfo_height()
                for control in window.controls:
                    assert control.winfo_rootx()>=x and control.winfo_rootx()+control.winfo_width()<=right+2,(scale,width,control.cget('text'))
                    assert control.winfo_rooty()+control.winfo_height()<=bottom+2
                results.append(dict(scale=scale,width=width,height=height,controls_inside=True))
            window.table.selection_set('a'*32);window.show_candidate();assert 'feature/full-sdk'in window.detail.get('1.0','end')
            window.review_source();method,params=window.commands.get_nowait();assert method=='sdk_review'and params['groups']==['dev']
            window.request_close();assert window.closing
            window.events.put(('closed',None));window.poll();assert window.closed
        finally:
            if window is not None and not window.closed:window.window.destroy()
            root.destroy()
    print(json.dumps(dict(native_tk=True,layouts=results,backend_started=False,game_input_sent=False),ensure_ascii=False))


if __name__=='__main__':main()
