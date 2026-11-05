from collections import deque
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from autozzz.trace_preferences import DEFAULTS,TracePreferences,validate
from autozzz.overlay import GameLogOverlay,LogLine
from autozzz.onedragon_trace_session import TraceSubscription
from autozzz.trace_auto_capture import AutomaticOverlayCapture


class OverlayCompletionTests(unittest.TestCase):
    def test_old_v2_migrates_only_in_memory(self):
        added={'state_poll_interval_ms','log_panel_enabled','log_max_lines','log_fade_seconds','patched_capture_enabled'}
        old={k:v for k,v in deepcopy(DEFAULTS).items() if k not in added}
        old['panels']['state']['x']=123
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'overlay.json';path.write_text(json.dumps(old),encoding='utf-8');before=path.read_bytes()
            loaded=TracePreferences(path).load()
            self.assertEqual(loaded['panels']['state']['x'],123)
            self.assertEqual(loaded['state_poll_interval_ms'],200)
            self.assertFalse(loaded['patched_capture_enabled'])
            self.assertEqual(path.read_bytes(),before)

    def test_custom_metric_is_validated_saved_and_filters_real_rows(self):
        from autozzz.onedragon_trace_ui import overlay_groups
        p=deepcopy(DEFAULTS);p['metrics']['model_custom_ms']=False
        validate(p)
        data=dict(items=dict(perf=[dict(metric='model_custom_ms',value=9,unit='ms',created=time.time())]))
        self.assertEqual(overlay_groups(data,{},p['layers'],p)['perf'],[])
        p['metrics']['model_custom_ms']=True
        self.assertIn('model_custom_ms',str(overlay_groups(data,{},p['layers'],p)))
        for metric in ('', 'bad\nmetric', 'a'*129):
            p['metrics']={**DEFAULTS['metrics'],metric:True}
            with self.assertRaises(ValueError):validate(p)

    def test_runtime_preferences_reject_unsafe_types_and_ranges(self):
        for key,value in [('state_poll_interval_ms',True),('state_poll_interval_ms',10),('log_max_lines',0),
            ('log_fade_seconds',float('nan')),('patched_capture_enabled','true'),('log_panel_enabled',1)]:
            p=deepcopy(DEFAULTS);p[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate(p)

    def test_sampling_interval_controls_next_actual_rpc(self):
        subscription=TraceSubscription(lambda _:None);subscription.configure(True)
        calls=[]
        def call(method,**kwargs):calls.append(method);return {}
        p=deepcopy(DEFAULTS);p['state_poll_interval_ms']=750
        with patch('autozzz.trace_preferences.TracePreferences.load',return_value=p),patch('autozzz.onedragon_trace_session.time.monotonic',return_value=10):
            subscription.pump('client',call)
            self.assertEqual(subscription.next_poll,10.75)
            subscription.pump('client',call)
        self.assertEqual(calls.count('trace_snapshot'),1)

    def test_log_preferences_shrink_buffer_expire_lines_and_hide(self):
        log=object.__new__(GameLogOverlay)
        log.closed=False;log.preference_next=0;log.lines=deque([LogLine('old',created=1),LogLine('new',created=9)],maxlen=40)
        log._dirty=False;log.enabled=True;log.handle=1;log.log_panel_enabled=True;log.log_fade_seconds=0
        log.active=True;log.linger_until=0;log.game_handle=lambda:1;hidden=[]
        log._hide=lambda:hidden.append(True)
        log.presentation=NS(game_bounds=lambda _: (0,0,1920,1080))
        log.root=NS(after=lambda *_:None)
        p=deepcopy(DEFAULTS);p.update(log_panel_enabled=False,log_max_lines=1,log_fade_seconds=2)
        with patch('autozzz.trace_preferences.TracePreferences.load',return_value=p),patch('autozzz.overlay.time.monotonic',return_value=10):
            log._tick()
        self.assertEqual([row.text for row in log.lines],['new']);self.assertEqual(log.lines.maxlen,1);self.assertTrue(hidden)
        with patch('autozzz.trace_preferences.TracePreferences.load',return_value=p),patch('autozzz.overlay.time.monotonic',return_value=12):log._tick()
        self.assertFalse(log.lines)

    def auto(self):
        control=NS(epoch=3,task_epoch=3,status=lambda:dict(state='running',fresh_frame=True))
        snapshot=dict(epoch=3,generation=1,window_identity=dict(hwnd=1,pid=2),standard_size=[16,8],items={key:[] for key in ('vision','perf','timeline','decision')})
        runtime=NS(ctx=object(),enabled=True,closed=False,generation=1,snapshot=lambda:deepcopy(snapshot),_control=lambda:control)
        return AutomaticOverlayCapture(runtime),control,runtime

    def test_automatic_copy_preserves_source_and_uses_same_observation(self):
        hook,control,runtime=self.auto();image=np.zeros((8,16,3),dtype=np.uint8);before=image.copy()
        hook.remember(NS(ctx=runtime.ctx),image)
        p=deepcopy(DEFAULTS);p['patched_capture_enabled']=True
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'original.png';Image.fromarray(image).save(source);original=source.read_bytes()
            with patch('autozzz.trace_capture.compose_overlay',return_value=Image.new('RGB',(16,8),'red')) as compose:
                target=hook.append(image,source,p)
                self.assertEqual(target.name,'original_overlay.png');self.assertTrue(target.is_file())
                self.assertEqual(compose.call_args.args[1]['epoch'],3)
            self.assertEqual(source.read_bytes(),original);np.testing.assert_array_equal(image,before)
            self.assertIsNone(hook.append(image,source,p)) # Existing copy is never overwritten.

    def test_automatic_hook_rejects_paused_stale_unregistered_and_cropped_frames(self):
        hook,control,runtime=self.auto();image=np.zeros((8,16,3),dtype=np.uint8)
        hook.remember(NS(ctx=runtime.ctx),image);p=deepcopy(DEFAULTS);p['patched_capture_enabled']=True
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'raw.png'
            self.assertIsNone(hook.append(image.copy(),path,p))
            control.epoch=4;self.assertIsNone(hook.append(image,path,p));control.epoch=3
            control.status=lambda:dict(state='paused',fresh_frame=True)
            self.assertIsNone(hook.append(image,path,p))
            control.status=lambda:dict(state='running',fresh_frame=True)
            crop=np.zeros((4,4,3),dtype=np.uint8);hook.remember(NS(ctx=runtime.ctx),crop)
            self.assertIsNone(hook.append(crop,path,p));self.assertEqual(list(Path(folder).iterdir()),[])
