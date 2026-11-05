from contextlib import contextmanager,nullcontext
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS,ModuleType
import unittest
from unittest.mock import patch,Mock
import numpy as np
from PIL import Image

from autozzz.global_capture import GlobalCaptureRuntime,parse_capture_hotkey,validate_capture_hotkey
from autozzz.global_capture_hotkeys import GlobalCaptureHotkeys
from autozzz.trace_preferences import DEFAULTS


class GlobalCaptureTests(unittest.TestCase):
    def test_key_names_reserved_keys_and_tool_conflicts(self):
        for key in ('f11','F24','print_screen','Ctrl+Shift+P','Win+F13',''):
            parse_capture_hotkey(key)
        for key in ('F9','Ctrl+F10','Shift','Ctrl+Ctrl+P','invalid key'):
            with self.subTest(key=key),self.assertRaises(ValueError):parse_capture_hotkey(key)
        with self.assertRaises(ValueError):validate_capture_hotkey('Ctrl+Shift+F8',trace=DEFAULTS['hotkeys'],template={})
        with self.assertRaises(ValueError):validate_capture_hotkey('Ctrl+1',trace={},template={'custom':'Ctrl+1'})
        self.assertTrue(validate_capture_hotkey('F11',trace={},template={}))

    def fixture(self,root):
        image=np.zeros((8,16,3),dtype=np.uint8);image[:]=[10,30,220]
        control=NS(epoch=3,status=lambda:dict(state='running',fresh_frame=True))
        window=NS(bound=NS(hwnd=7,path='E:/Game/ZenlessZoneZero.exe'),process_id=8,
            win_rect=NS(x1=0,y1=0,x2=16,y2=8),identity_valid=lambda:True)
        source=NS(get_screenshot=Mock(return_value=image),cleanup=Mock())
        controller=NS(game_win=window,screenshot_controller=source)
        service=NS(data=root,_ctx=NS(controller=controller,execution_control=control),_execution=None)
        runtime=GlobalCaptureRuntime(service)
        return runtime,source,window,control,image

    def patches(self,root):
        debug=NS(get_debug_image_path=lambda name:str(root/(name+'.png')),
            save_debug_image=lambda image,file_name,copy_screenshot:Image.fromarray(image).save(root/(file_name+'.png')),
            copy_image_to_clipboard=Mock(return_value=True))
        module=ModuleType('one_dragon.utils');module.debug_utils=debug
        return debug,[patch('autozzz.global_capture.settings',return_value=dict(key_screenshot='f11',copy_screenshot=True)),
            patch('autozzz.global_capture.process_created',return_value=99),
            patch('autozzz.windows.user32.GetForegroundWindow',return_value=7),
            patch('autozzz.windows.user32.IsIconic',return_value=False),
            patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0),
            patch('autozzz.run_lock.RunLock',return_value=nullcontext()),
            patch('autozzz.trace_preferences.TracePreferences.load',return_value=deepcopy(DEFAULTS)),
            patch.dict('sys.modules',{'one_dragon':ModuleType('one_dragon'),'one_dragon.utils':module})]

    def test_raw_capture_is_read_only_pixel_exact_and_copies_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);runtime,source,window,control,image=self.fixture(root)
            debug,patches=self.patches(root)
            from contextlib import ExitStack
            with ExitStack() as stack:
                for item in patches:stack.enter_context(item)
                before=image.copy();state=runtime.status();result=runtime.capture(state['token'])
                self.assertEqual(result['mode'],'borrowed');self.assertTrue(result['copied'])
                np.testing.assert_array_equal(np.asarray(Image.open(result['path'])),before)
                np.testing.assert_array_equal(debug.copy_image_to_clipboard.call_args.args[0],before)
                source.get_screenshot.assert_called_once_with(independent=True,resize=False);source.cleanup.assert_not_called()
                self.assertEqual(control.epoch,3);np.testing.assert_array_equal(image,before)

    def test_epoch_focus_geometry_f10_and_foreign_token_never_publish(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);runtime,source,window,control,image=self.fixture(root);debug,patches=self.patches(root)
            with ExitStack() as stack:
                for item in patches:stack.enter_context(item)
                token=runtime.status()['token']
                with self.assertRaises(ValueError):runtime.capture('a'*64)
                window.win_rect.x2=17
                with self.assertRaises(ValueError):runtime.capture(token)
                window.win_rect.x2=16
                control.epoch=4
                with self.assertRaises(ValueError):runtime.capture(token)
                control.epoch=3
                for target,value in (('GetForegroundWindow',9),('GetAsyncKeyState',0x8000)):
                    with patch('autozzz.windows.user32.'+target,return_value=value),self.assertRaises(ValueError):runtime.capture(token)
                source.get_screenshot.side_effect=lambda **_:setattr(control,'epoch',4) or image
                with self.assertRaises(ValueError):runtime.capture(token)
                self.assertEqual(list(root.iterdir()),[]);debug.copy_image_to_clipboard.assert_not_called()

    def test_idle_capture_has_no_executor_and_releases_owned_source(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);runtime,source,window,control,image=self.fixture(root);runtime.service._ctx=None
            debug,patches=self.patches(root)
            @contextmanager
            def binding(_):yield window
            with ExitStack() as stack:
                for item in patches:stack.enter_context(item)
                stack.enter_context(patch('autozzz.global_capture.standalone_window',binding))
                host=ModuleType('autozzz.onedragon_host');host.WindowCapture=Mock(return_value=source)
                stack.enter_context(patch.dict('sys.modules',{'autozzz.onedragon_host':host}))
                status=runtime.status('selected');result=runtime.capture(status['token'],'selected')
                self.assertEqual(result['mode'],'standalone');source.cleanup.assert_called_once()
                self.assertIsNone(runtime.service._ctx);self.assertIsNone(runtime.service._execution)

    def test_task_lock_failure_does_not_take_frame_or_copy(self):
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);runtime,source,window,control,image=self.fixture(root);debug,patches=self.patches(root)
            with ExitStack() as stack:
                for item in patches:stack.enter_context(item)
                token=runtime.status()['token']
                stack.enter_context(patch('autozzz.run_lock.RunLock',side_effect=RuntimeError('another task')))
                with self.assertRaisesRegex(RuntimeError,'another task'):runtime.capture(token)
                source.get_screenshot.assert_not_called();debug.copy_image_to_clipboard.assert_not_called()

    def test_key_controller_works_without_trace_window_and_does_not_repeat_held_key(self):
        keys=set();requests=[];messages=[]
        root=NS(after=lambda *args:1,after_cancel=lambda *_:None)
        def request(method,params,done):requests.append((method,params,done))
        controller=GlobalCaptureHotkeys(root,lambda code:0x8000 if code in keys else 0,lambda:7,request,messages.append)
        self.assertEqual(requests[-1][0],'global_capture_status')
        requests[-1][2](dict(settings=dict(key_screenshot='F11'),ready=True,foreground=True,window_identity=dict(hwnd=7),token='a'*64))
        keys.add(0x7A);controller.tick();self.assertEqual(requests[-1][0],'global_capture_capture')
        count=len(requests);controller.tick();self.assertEqual(len(requests),count)
        requests[-1][2](dict(path='frame.png',copied=True,copy_requested=True));self.assertTrue(messages)
        controller.close();controller.tick();self.assertEqual(len(requests),count)

    def test_key_edge_during_status_query_waits_for_fresh_identity_once(self):
        keys=set();requests=[]
        root=NS(after=lambda *_:1,after_cancel=lambda *_:None)
        controller=GlobalCaptureHotkeys(root,lambda code:0x8000 if code in keys else 0,lambda:7,
            lambda *args:requests.append(args),lambda *_:None)
        state=dict(settings=dict(key_screenshot='F11'),ready=True,foreground=True,window_identity=dict(hwnd=7),token='a'*64)
        requests[-1][2](state)
        controller.next_status=0;controller.tick();keys.add(0x7A);controller.tick()
        self.assertTrue(controller.queued_capture);self.assertEqual(requests[-1][0],'global_capture_status')
        requests[-1][2](dict(state,token='b'*64))
        self.assertEqual(requests[-1][0],'global_capture_capture');self.assertEqual(requests[-1][1]['token'],'b'*64)
        controller.close()


if __name__=='__main__':unittest.main()
