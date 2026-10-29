"""原生画中画的显示、拖拽、缩放、切换与关闭等待；画面和 SDK 连接为替身。"""
from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.config import Settings,GAME_PATHS
from autozzz.gui import AutoZZZWindow
from autozzz.onedragon_pip_ui import PipController,PipPresentation
from autozzz.windows import user32
from onedragon_configuration_gui_smoke import pump,layout


image=None


class Session:
    def __init__(self,paths,server,path):
        self.paths=paths;self.server=server;self.path=path;self.events=queue.Queue();self.closing=False;self.commands=[];self.done=threading.Event()
        self.settings=dict(revision='fixture',values=dict(enabled=True,width=480,x=24,y=32))
        self.events.put(dict(type='settings',settings=self.settings.copy()));self.latest=None;self.worker=threading.Thread(target=self.work,daemon=False);self.worker.start();self.publish()
    def work(self):self.done.wait();time.sleep(.15)
    def publish(self):self.latest=dict(enabled=True,state='visible',visible=True,epoch=1,sequence=1,target=self.path,image=image)
    def take_state(self):result=self.latest;self.latest=None;return result
    def send(self,command,**params):
        if self.closing:return
        self.commands.append((command,params))
        if command=='target':self.path=params['game_path'];self.publish()
        elif command in ('enabled','geometry'):
            self.settings['values'].update(dict(enabled=params['enabled']) if command=='enabled' else params['values'])
            self.events.put(dict(type='settings',settings=self.settings,version=params.get('version')))
        elif command=='activate':self.events.put(dict(type='activated',foreground=True,requested=True))
    def allow_user_focus(self):self.commands.append(('focus_permission',{}));return True
    def close(self):self.closing=True;self.done.set()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    from PIL import Image as PilImage,ImageGrab
    global image
    pixels=PilImage.new('RGB',(640,360),(230,100,40));stream=BytesIO();pixels.save(stream,format='JPEG')
    image=dict(width=640,height=360,format='jpeg',data=base64.b64encode(stream.getvalue()).decode('ascii'))
    foreground=user32.GetForegroundWindow();cases=[]
    for scale in (1.,1.5,2.):
        with tempfile.TemporaryDirectory(prefix='autozzz-pip-ui-') as directory:
            config=Path(directory)/'config.json';Settings().save(config);original=config.read_bytes()
            root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72)
            callback_errors=[]
            root.report_callback_exception=lambda kind,value,tb:callback_errors.append((kind,value))
            ui=AutoZZZWindow(root,config,background_helpers=False);ui.overlay.set_enabled(False)
            root_presentation=PipPresentation(protect_capture=False);root_handle=root_presentation.prepare(root)
            root.deiconify();root.update();layout(root)
            sessions=[]
            def factory(paths,server,path):
                assert all(not item.worker.is_alive() for item in sessions),'旧画中画连接尚未退出'
                session=Session(paths,server,path);sessions.append(session);return session
            controller=PipController(ui,session_factory=factory,presentation=PipPresentation(protect_capture=False));ui.pip_controller=controller
            try:
                pump(root,lambda:controller.view.visible)
                view=controller.view;first=view.bounds
                assert ui.pip_enabled.get() and not ui.dirty
                # 内部拖拽不请求切回游戏；负坐标在后续帧中保留。
                view.down(NS(x=100,y=60,x_root=first[0]+100,y_root=first[1]+60))
                view.motion(NS(x_root=first[0],y_root=first[1]+80));view.up(NS())
                assert view.bounds[0]<0 and not any(name=='activate' for name,_ in sessions[-1].commands)
                saved=view.bounds;view.update(dict(visible=True,image=image));assert view.bounds==saved
                width=view.bounds[2]
                view.down(NS(x=width-2,y=50,x_root=100,y_root=100));view.motion(NS(x_root=180,y_root=100));view.up(NS())
                assert view.bounds[2]>width
                view.wheel(NS(delta=120));assert view.bounds[3]==round((view.bounds[2]-2)*view.aspect)+2
                width=view.bounds[2];view.wheel(NS(delta=0));assert view.bounds[2]==width
                # 单击才发送用户焦点许可和一次激活请求。
                view.down(NS(x=100,y=60,x_root=100,y_root=60));view.up(NS())
                assert sum(name=='activate' for name,_ in sessions[-1].commands)==1
                assert sum(name=='focus_permission' for name,_ in sessions[-1].commands)==1
                view.down(NS(x=100,y=60,x_root=100,y_root=60))
                view.update(dict(visible=True,image=image,epoch=2,identity=dict(hwnd=9876,pid=1234)))
                view.up(NS())
                assert sum(name=='activate' for name,_ in sessions[-1].commands)==1,'绑定换代不能继承旧鼠标手势'
                with patch('autozzz.onedragon_pip_ui.user32.GetForegroundWindow',return_value=root_handle):
                    view.update(dict(visible=True,image=image,epoch=3,identity=dict(hwnd=root_handle,pid=1234)))
                assert not view.visible
                sessions[-1].publish();pump(root,lambda:view.visible)
                view.hide_by_user();assert not view.visible and sessions[-1].commands[-1][0]=='dismiss'
                sessions[-1].publish();pump(root,lambda:view.visible)
                # 连续轮换的当前客户端优先于静态主页面选择。
                ui.execution_window=NS(busy=True,profile='cn',game_path=tk.StringVar(root,GAME_PATHS['cn']),last_state=dict(window_path=GAME_PATHS['global']),worker=NS(is_alive=lambda:False))
                pump(root,lambda:controller.target==('global',GAME_PATHS['global']))
                assert len(sessions)==1
                ui.execution_window=None;ui.server.set('国际服');ui.game_path.set(GAME_PATHS['global'])
                previous=sessions[-1];root.update();assert previous.worker.is_alive()
                pump(root,lambda:len(sessions)==2 and controller.view.visible)
                assert not previous.worker.is_alive() and sessions[-1].paths.data.name=='global'
                assert user32.GetForegroundWindow()==foreground,'自动显示或变换窃取了前台焦点'
                if scale==1.:
                    root.update();time.sleep(.1)
                    rendered=ImageGrab.grab(window=controller.view.handle)
                    red,green,blue=rendered.convert('RGB').getpixel((rendered.width//2,rendered.height//2))
                    assert red>200 and 75<green<125 and blue<70,(red,green,blue)
                    rendered.save(args.output/'pip.png')
                cases.append(dict(scale=scale,main_layout=True,drag=True,edge_resize=True,wheel=True,explicit_focus_request=True,
                                  dismiss=True,rotation_client_follow=True,profile_change_waits_original_worker=True))
                ui._saved_form=ui._form_signature();ui._close()
                assert root.winfo_exists() and sessions[-1].worker.is_alive()
                pump(root,lambda:not sessions[-1].worker.is_alive());root.update()
                assert config.read_bytes()==original
                assert not callback_errors,callback_errors
            finally:
                controller.close()
                for session in sessions:session.close();session.worker.join(2)
                try:ui._destroy_root()
                except tk.TclError:pass
    result=dict(cases=cases,actual_native_pip_no_activate=True,fixture_game_images_and_sessions=True,game_started=False,game_input_sent=False)
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
