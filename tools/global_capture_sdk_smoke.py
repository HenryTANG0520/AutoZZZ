"""Real SDK/Win32 capture of an owned non-activating window; clipboard API replay."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace as NS
from unittest.mock import patch
import uuid

project=Path(__file__).resolve().parents[1];sys.path.insert(0,str(project));sys.path.insert(0,str(project/'vendor/onedragon/src'))
from autozzz import windows
from autozzz.overlay import NativePresentation
from autozzz.global_capture import GlobalCaptureRuntime
from autozzz.onedragon_environment import EnvironmentStore
from autozzz.run_lock import RunLock as RealRunLock
from autozzz.trace_preferences import DEFAULTS
from one_dragon.utils import os_utils,debug_utils
from one_dragon.envs.repo_config import RepoConfig
from PIL import Image
from io import BytesIO
import numpy as np


def legacy_reader(args):
    """A real SDK descendant borrowing the actual parent Runner's read-only capability."""
    import autozzz.capture_lease as lease_module
    lease_module.RUN_MUTEX_NAME=args.lease_mutex;os_utils.set_work_dir(args.data)
    game=args.data/'ZenlessZoneZero.exe'
    for cached in (debug_utils.get_debug_dir_path,debug_utils.get_debug_image_dir_path):cached.cache_clear()
    runtime=GlobalCaptureRuntime(NS(data=args.data,_ctx=None,_execution=None))
    with patch('autozzz.windows.find_game',return_value=NS(hwnd=args.hwnd,path=str(game))),\
            patch('autozzz.windows.process_path',return_value=str(game)),\
            patch.object(windows.user32,'GetForegroundWindow',return_value=args.hwnd),\
            patch.object(windows.user32,'GetAsyncKeyState',return_value=0),\
            patch.object(debug_utils,'copy_image_to_clipboard',side_effect=AssertionError('fixture must not touch system clipboard')):
        state=runtime.status(str(game));result=runtime.capture(state['token'],str(game),legacy_lease=args.reader_lease)
        assert result['mode']=='legacy' and not result['copy_requested']
        print(json.dumps(result),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path)
    parser.add_argument('--reader-lease');parser.add_argument('--lease-mutex');parser.add_argument('--hwnd',type=int);parser.add_argument('--data',type=Path)
    args=parser.parse_args()
    if args.reader_lease:return legacy_reader(args)
    if args.output is None:parser.error('--output is required')
    args.output.mkdir(parents=True,exist_ok=True)
    native=NativePresentation(protect_capture=False);foreground=native.user32.GetForegroundWindow()
    root=tk.Tk();root.withdraw();root.title('AutoZZZ · owned screenshot fixture');root.geometry('640x360+40+40')
    root.configure(background='#1e46c8');canvas=tk.Canvas(root,background='#1e46c8',highlightthickness=0);canvas.pack(fill='both',expand=True)
    handle=native.prepare(root)
    # An opaque native fixture is required; layered Tk windows render blank to PrintWindow.
    native.set_style(handle,-20,native.get_style(handle,-20)&~0x80000)
    root.deiconify();root.update();native.show(handle,(40,40,640,360));root.update()
    root_errors=[];root.report_callback_exception=lambda *error:root_errors.append(str(error))
    clipboard={};copy_calls=[];frames=[]
    try:
        with tempfile.TemporaryDirectory(prefix='autozzz-global-capture-sdk-') as temporary:
            data=Path(temporary);(data/'config').mkdir();shutil.copyfile(project/'vendor/onedragon/config/repository.yml',data/'config/repository.yml')
            game=data/'ZenlessZoneZero.exe';game.write_bytes(b'owned fixture, never executed')
            os_utils.set_work_dir(data)
            for cached in (debug_utils.get_debug_dir_path,debug_utils.get_debug_image_dir_path):cached.cache_clear()
            repo=RepoConfig(prefer_bundled_config=True)
            service=NS(data=data,_ctx=None,_execution=None,_assert_idle=lambda:None,
                ctx=NS(repo_config=repo,push_service=NS(_refresh_secrets=lambda:None)))
            env=EnvironmentStore(service);initial=env.describe('_environment')
            fields={field['name']:field for field in initial['fields']}
            assert fields['key_screenshot']['value']=='f11' and fields['copy_screenshot']['value'] is True
            saved=env.save('_environment',dict(key_screenshot='Ctrl+Shift+P',copy_screenshot=True),initial['revision'])
            for value in ('F9','Ctrl+F10','Ctrl+Shift+F8','Ctrl+1'):
                try:env.save('_environment',dict(key_screenshot=value),saved['revision']);raise AssertionError('reserved/conflicting key accepted')
                except ValueError:pass
            (args.output/'fields.json').write_text(json.dumps([field for field in saved['fields'] if field.get('group')=='截图'],ensure_ascii=False,indent=2),encoding='utf-8')
            runtime=GlobalCaptureRuntime(service);name='Local\\AutoZZZ.CaptureFixture.'+uuid.uuid4().hex
            from autozzz.onedragon_host import WindowCapture,BoundGameWindow
            original_capture=WindowCapture.get_screenshot
            def observed(source,*args,**kwargs):
                result=original_capture(source,*args,**kwargs)
                if result is not None:frames.append(result.copy())
                return result
            def set_clipboard(format,data):clipboard[format]=bytes(data);copy_calls.append(format)
            def forbidden(*_,**__):raise AssertionError('capture must not activate/input/start game')
            with ExitStack() as stack:
                stack.enter_context(patch('autozzz.windows.find_game',return_value=NS(hwnd=handle,path=str(game))))
                stack.enter_context(patch('autozzz.windows.process_path',return_value=str(game)))
                stack.enter_context(patch.object(windows.user32,'GetForegroundWindow',return_value=handle))
                stack.enter_context(patch.object(windows.user32,'GetAsyncKeyState',return_value=0))
                stack.enter_context(patch('autozzz.run_lock.RunLock',side_effect=lambda *args:RealRunLock(args[0] if args else name)))
                stack.enter_context(patch.object(WindowCapture,'get_screenshot',observed))
                stack.enter_context(patch.object(BoundGameWindow,'active',side_effect=forbidden))
                stack.enter_context(patch.object(windows.user32,'SetForegroundWindow',side_effect=forbidden))
                stack.enter_context(patch('autozzz.trace_preferences.TracePreferences.load',return_value=DEFAULTS.copy()))
                for method,call in (('OpenClipboard',lambda:None),('EmptyClipboard',clipboard.clear),('SetClipboardData',set_clipboard),('CloseClipboard',lambda:None)):
                    stack.enter_context(patch.object(debug_utils.win32clipboard,method,side_effect=call))
                state=runtime.status(str(game));assert state['ready'] and state['foreground'] and state['mode']=='standalone',state
                assert service._ctx is None and service._execution is None
                result=runtime.capture(state['token'],str(game));assert result['copied'] and frames
                raw=Image.open(result['path']).convert('RGB');np.testing.assert_array_equal(np.asarray(raw),frames[-1])
                assert np.asarray(raw).std()>2,'Owned native fixture capture was blank'
                dib=clipboard[8];offset=14+int.from_bytes(dib[:4],'little')
                bmp=b'BM'+struct.pack('<IHHI',14+len(dib),0,0,offset)+dib
                np.testing.assert_array_equal(np.asarray(Image.open(BytesIO(bmp)).convert('RGB')),frames[-1])
                with RealRunLock(name):
                    try:runtime.capture(state['token'],str(game));raise AssertionError('task-lock collision accepted')
                    except RuntimeError:pass
                count=len(frames)
                with patch.object(windows.user32,'GetAsyncKeyState',return_value=0x8000):
                    try:runtime.capture(state['token'],str(game));raise AssertionError('F10 ignored')
                    except ValueError:pass
                assert len(frames)==count
                saved=env.save('_environment',dict(copy_screenshot=False),saved['revision']);count=len(copy_calls)
                result=runtime.capture(runtime.status(str(game))['token'],str(game));assert not result['copy_requested']
                assert len(copy_calls)==count
                # Real SDK source + actual mutex handle retained while the original simulated owner exits.
                window=BoundGameWindow(1920,1080);window.bound=NS(hwnd=handle,path=str(game));window.process_id=os.getpid()
                window.process_handle=windows.kernel32.OpenProcess(0x101000,False,os.getpid());window.init_win()
                source=WindowCapture(NS(using_background=False),window,1920,1080)
                control=NS(epoch=7,status=lambda:dict(state='running',fresh_frame=True))
                trace=dict(created=time.time(),epoch=7,generation=1,standard_size=[640,360],
                    items=dict(vision=[dict(source='ocr',label='owned trace',created=time.time(),x1=10,y1=10,x2=100,y2=60)],decision=[],timeline=[],perf=[]))
                service._ctx=NS(controller=NS(game_win=window,screenshot_controller=source),execution_control=control,
                    debug_trace_runtime=NS(snapshot=lambda:__import__('copy').deepcopy(trace)))
                service._execution=NS(busy=True,_input_ready=True)
                owner=RealRunLock(name);owner.acquire();checked=[]
                def finishing(*args,**kwargs):
                    owner.close()
                    try:
                        with RealRunLock(name):raise AssertionError('original task lock was not retained')
                    except RuntimeError:checked.append(True)
                    return observed(source,*args,**kwargs)
                try:
                    with patch('autozzz.run_lock.RUN_MUTEX_NAME',name),patch.object(source,'get_screenshot',side_effect=finishing):
                        result=runtime.capture(runtime.status(str(game))['token'],str(game),include_overlay=True);assert checked and result['mode']=='borrowed'
                        assert result['overlay_path'] and Path(result['overlay_path']).is_file()
                        assert Path(result['overlay_path']).read_bytes()!=Path(result['path']).read_bytes()
                        np.testing.assert_array_equal(np.asarray(Image.open(result['path']).convert('RGB')),frames[-1])
                    with RealRunLock(name):pass
                finally:owner.close();source.cleanup();window.clear_binding()
                # The legacy route uses the real Runner's exported lease and an actual SDK descendant.
                from autozzz.app import Runner
                from autozzz.config import Settings
                runner=Runner(Settings(game_path=str(game)));legacy_lock=RealRunLock(name);legacy_lock.acquire()
                runner._capture_mutex=legacy_lock.handle;runner._window=NS(hwnd=handle)
                legacy=runner.capture_lease()
                try:
                    child=subprocess.Popen([sys.executable,'-X','utf8',__file__,'--reader-lease',legacy.export(),
                        '--lease-mutex',name,'--hwnd',str(handle),'--data',str(data)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
                    deadline=time.monotonic()+12
                    while child.poll() is None:
                        root.update()
                        if time.monotonic()>deadline:raise AssertionError('legacy SDK capture timed out')
                        time.sleep(.01)
                    stdout,stderr=child.communicate();assert child.returncode==0,(stdout,stderr)
                    child_result=json.loads(stdout.splitlines()[-1]);assert child_result['mode']=='legacy'
                    assert Image.open(child_result['path']).size==raw.size
                    runner.toggle_pause();assert legacy.closed
                finally:
                    runner._capture_mutex=None;runner._invalidate_capture_leases();legacy.close();legacy_lock.close()
                runtime.close()
        assert not root_errors,root_errors
    finally:
        for token in root.tk.splitlist(root.tk.call('after','info')):root.tk.call('after','cancel',token)
        root.destroy()
    assert foreground==native.user32.GetForegroundWindow(),'Changed user foreground'
    result=dict(actual_sdk_capture=True,owned_native_window=True,standalone_without_executor=True,
        raw_png_pixel_exact=True,actual_sdk_CF_DIB_copy_replay=True,copy_toggle=True,
        task_mutex_collision=True,borrowed_mutex_retained_until_capture_exits=True,F10_refuses=True,
        environment_fields=True,optional_overlay_is_separate=True,actual_legacy_runner_lease_and_sdk_descendant=True,
        clipboard_not_modified=True,game_accessed=False,input_sent=False,foreground_unchanged=True)
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
