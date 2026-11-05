"""Real Tk MCP window + tagged RuntimeSession bridge, with a no-game session."""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import Settings
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_run_ui import ExecutionWindow
from onedragon_configuration_gui_smoke import layout,pump
from onedragon_run_gui_smoke import Session


def layout_window(widget):
    # Owned Toplevels have desktop coordinates, not coordinates inside their parent.
    for child in widget.winfo_children():
        if isinstance(child,tk.Toplevel) or not child.winfo_ismapped():continue
        x,y,w,h=child.winfo_x(),child.winfo_y(),child.winfo_width(),child.winfo_height()
        if not isinstance(widget,tk.Canvas):
            assert x>=-2 and y>=-2,(child,x,y)
            assert x+w<=widget.winfo_width()+2,(child,'width',x,w,widget.winfo_width())
            assert y+h<=widget.winfo_height()+2,(child,'height',y,h,widget.winfo_height())
        if isinstance(child,(ttk.Label,ttk.Button,ttk.Checkbutton)):
            assert w>=child.winfo_reqwidth(),(child,'text width',w,child.winfo_reqwidth())
            assert h>=child.winfo_reqheight(),(child,'text height',h,child.winfo_reqheight())
        layout_window(child)


class McpSession(Session):
    def __init__(self,*args,**kwargs):
        self.mcp_running=False;self.granted=False;self.reject=False;self.editing=None;self.restored=False
        super().__init__(*args,**kwargs)
        self.state.update(prepared_app='email',uses_charge=False)
    def send(self,command,**params):
        if command!='rpc' or not params['method'].startswith('mcp_'):return super().send(command,**params)
        self.commands.append((command,params))
        method=params['method']
        if self.reject:
            self.emit(dict(type='rpc_error',tag=params['tag'],message='离线RPC拒绝'));return
        if method=='mcp_start':self.mcp_running=True
        if method=='mcp_stop':self.mcp_running=False
        if method=='mcp_grant':self.granted=True
        if method=='mcp_edit_enable':self.editing=dict(instance_idx=1,scopes=params['params']['scopes'])
        if method=='mcp_edit_disable':self.editing=None
        if method=='mcp_edit_restore':self.restored=True
        result=dict(running=self.mcp_running,url='http://127.0.0.1:23001/mcp' if self.mcp_running else '',
            execution=copy.deepcopy(self.state),editing=dict(session=self.editing,
                archives={'config':[dict(id='a'*32,metadata=dict(action='mcp_set_config'))]}),logs=[dict(message='离线服务反馈')])
        if method=='mcp_start':result['bearer_token']='fixture-token'
        self.emit(dict(type='rpc_result',tag=params['tag'],result=result))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    cases=[]
    with tempfile.TemporaryDirectory(prefix='autozzz-mcp-ui-') as temporary:
        config=Path(temporary)/'config.json';Settings().save(config);original=config.read_bytes()
        for scale in (1,1.5,2):
            root=tk.Tk();root.tk.call('tk','scaling',scale*96/72)
            errors=[];root.report_callback_exception=lambda kind,value,tb:errors.append(str(value))
            ui=AutoZZZWindow(root,config,background_helpers=False);ui.overlay.set_enabled(False)
            with patch('autozzz.onedragon_run_ui.RuntimeSession',McpSession):
                window=ExecutionWindow(ui,'email','邮件','cn');ui.execution_window=window
                try:
                    pump(root,lambda:window.connected and not window.loading_account)
                    window.prepare();pump(root,lambda:window.prepared)
                    window.mcp_button.invoke();view=window.mcp_window
                    pump(root,lambda:not view.busy and bool(view.state))
                    for size in ('default','minimum'):
                        if size=='minimum':window.window.geometry('%dx%d'%window.window.minsize())
                        root.update();layout_window(window.window);layout_window(view.window)
                        cases.append([scale,size])
                    view.start();pump(root,lambda:not view.busy and view.state.get('running'))
                    assert view.token.get()=='fixture-token'
                    view.grant();pump(root,lambda:not view.busy and window.session.granted)
                    view.edit_scopes['config'].set(True);view.enable_edit()
                    pump(root,lambda:not view.busy and window.session.editing is not None)
                    assert '已启用'in view.edit_message.get()
                    view.archive.set(next(iter(view.archive_map)));view.restore_edit()
                    pump(root,lambda:not view.busy and window.session.restored)
                    view.call('mcp_edit_disable');pump(root,lambda:not view.busy and window.session.editing is None)
                    window.session.reject=True;view.call('mcp_status')
                    pump(root,lambda:not view.busy and 'RPC拒绝' in view.message.get())
                    assert not window.mcp_requests
                    window.session.reject=False;view.close()
                    pump(root,lambda:view.closed)
                    window.show_mcp();pump(root,lambda:not window.mcp_window.busy)
                    window.close();pump(root,lambda:window.closed)
                    window.worker.join(3);assert not window.worker.is_alive()
                    assert window.mcp_window.closed and not window.mcp_requests
                    assert not errors,errors
                finally:
                    if not window.closing:window.close();pump(root,lambda:window.closed)
                    ui.overlay.close()
                    for token in root.tk.call('after','info'):root.after_cancel(token)
                    root.destroy()
        assert config.read_bytes()==original
    result=dict(cases=cases,tagged_bridge=True,rpc_error_resolves=True,close_cancels_pending=True,
        same_session=True,game_input=False,config_unchanged=True)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
