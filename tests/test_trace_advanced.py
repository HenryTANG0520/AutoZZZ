from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from autozzz.trace_hotkeys import HotkeyPoller,parse_hotkey
from autozzz.trace_layout_ui import move_panel
from autozzz.trace_preferences import DEFAULTS,validate
from autozzz.trace_capture import capture_source,read_capture,compose_overlay
from autozzz.onedragon_trace_session import TraceSubscription


class TraceAdvancedTests(unittest.TestCase):
    def test_hotkey_hold_focus_and_close(self):
        down=set();poll=HotkeyPoller(lambda code:0x8000 if code in down else 0)
        poll.configure(DEFAULTS['hotkeys']);down.update(parse_hotkey(DEFAULTS['hotkeys']['toggle']))
        self.assertEqual(poll.poll(True),['toggle']);self.assertEqual(poll.poll(True),[])
        down.clear();poll.poll(False);down.update(parse_hotkey(DEFAULTS['hotkeys']['capture']))
        self.assertEqual(poll.poll(False),[]);self.assertEqual(poll.poll(True),[])
        down.clear();poll.poll(True);down.update(parse_hotkey(DEFAULTS['hotkeys']['capture']))
        self.assertEqual(poll.poll(True),['capture']);poll.close();self.assertEqual(poll.poll(True),[])

    def test_hotkeys_held_when_configured_and_reserved_keys(self):
        poll=HotkeyPoller(lambda _:0x8000);poll.configure(DEFAULTS['hotkeys']);self.assertEqual(poll.poll(True),[])
        for value in ('F7','Ctrl+F9','Alt+F10','Ctrl+F09','Ctrl+Ctrl+F7','Ctrl+F99'):
            with self.assertRaises(ValueError):parse_hotkey(value)
        with self.assertRaises(ValueError):poll.configure(dict(toggle='Ctrl+Shift+F7',capture='Shift+Ctrl+F7'))

    def test_old_preferences_get_safe_hotkey_defaults(self):
        old=deepcopy(DEFAULTS);old.pop('hotkeys');self.assertEqual(validate(old),DEFAULTS)

    def test_drag_snap_and_resize(self):
        panel=deepcopy(DEFAULTS['panels']['state'])
        self.assertEqual(move_panel(panel,10,10)['x'],0)
        moved=move_panel(panel,9999,-100);self.assertEqual((moved['x'],moved['y']),(1520,0))
        resized=move_panel(panel,1600,50,resize=True);self.assertEqual((resized['w'],resized['h']),(120,60))

    def fixture(self,folder,change=None):
        import numpy as np
        control=NS(epoch=4,status=lambda:dict(state='running',fresh_frame=True))
        window=NS(identity_valid=lambda:True,is_win_active=True,win_rect=NS(x1=0,y1=0,x2=960,y2=540))
        def get_screenshot(**kwargs):
            self.assertEqual(kwargs,dict(independent=True,resize=False))
            if change:change(control,window)
            image=np.zeros((540,960,3),dtype=np.uint8);image[:]=[150,80,20];return image
        snapshot=dict(epoch=4,generation=2,control=dict(state='running'),window_identity=dict(hwnd=1,pid=2),standard_size=[1920,1080],items={key:[] for key in ('vision','decision','timeline','perf')})
        runtime=NS(enabled=True,closed=False,epoch=4,generation=2,_control=lambda:control,snapshot=lambda:deepcopy(snapshot),
                   ctx=NS(controller=NS(game_win=window,screenshot_controller=NS(get_screenshot=get_screenshot))))
        return runtime

    def test_actual_source_png_roundtrip_and_composite_is_independent(self):
        from PIL import ImageChops
        with tempfile.TemporaryDirectory() as folder:
            runtime=self.fixture(folder);result=capture_source(runtime,folder,4,2);self.assertFalse(result['waiting'])
            self.assertLess(len(json.dumps(result)),4096) # Pixel bytes never enter 2 MiB RPC.
            image,path=read_capture(folder,result['capture']);original=image.copy();self.assertEqual(image.getpixel((500,300)),(150,80,20))
            snapshot=result['snapshot'];snapshot['items']['vision']=[dict(source='ocr',label='400',created=__import__('time').time(),x1=10,y1=10,x2=120,y2=120)]
            layers=dict(vision=True,state=True,decision=True,timeline=True,perf=True)
            output=compose_overlay(image,snapshot,{},layers,{},DEFAULTS)
            self.assertIsNotNone(ImageChops.difference(image,output).getbbox());self.assertIsNone(ImageChops.difference(image,original).getbbox())
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):read_capture(folder,result['capture'])

    def test_epoch_resize_and_close_during_capture_discard(self):
        changes=(lambda c,w:setattr(c,'epoch',5),lambda c,w:setattr(w.win_rect,'x2',800))
        for change in changes:
            with tempfile.TemporaryDirectory() as folder:
                result=capture_source(self.fixture(folder,change),folder,4,2);self.assertTrue(result['waiting']);self.assertEqual(list(Path(folder).rglob('*.png')),[])
        with tempfile.TemporaryDirectory() as folder:
            runtime=self.fixture(folder);runtime.ctx.controller=None
            self.assertTrue(capture_source(runtime,folder,4,2)['waiting'])
            runtime=self.fixture(folder);runtime._control=lambda:None
            self.assertTrue(capture_source(runtime,folder,4,2)['waiting'])
            runtime=self.fixture(folder);runtime.closed=True
            self.assertTrue(capture_source(runtime,folder,4,2)['waiting'])

    def test_subscription_retains_one_capture_until_consumed_and_cancels_late_result(self):
        subscription=TraceSubscription(lambda _:None);version=subscription.configure(True,capture=True)
        source=dict(epoch=4,generation=2,control=dict(fresh_frame=True,state='running'),items=dict(decision=['captured']))
        calls=[]
        def call(method,**params):
            calls.append(method)
            if method=='trace_snapshot':return deepcopy(source)
            if method=='trace_capture':return dict(waiting=False,snapshot=deepcopy(source),capture=dict(path='fixture'))
        subscription.pump('client',call);source['items']['decision'].append('future');subscription.next_poll=0;subscription.pump('client',call)
        self.assertEqual(calls.count('trace_capture'),1);delivered=subscription.take()['snapshot']
        self.assertEqual(delivered['capture_snapshot']['items']['decision'],['captured'])
        self.assertEqual(delivered['items']['decision'],['captured','future'])
        subscription.configure(True,capture=True);subscription.next_poll=0
        def late(method,**params):
            result=call(method,**params)
            if method=='trace_capture':subscription.configure(False)
            return result
        subscription.pump('client',late);self.assertIsNone(subscription.take())


if __name__=='__main__':unittest.main()
