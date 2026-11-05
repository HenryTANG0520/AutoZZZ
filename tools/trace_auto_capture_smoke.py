"""Real fixed SDK screenshot/save hooks with synthetic pixels; no game or input."""
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/onedragon/src')]
import numpy as np
from one_dragon.base.operation.operation import Operation
from one_dragon.base.debug.debug_trace_bus import DebugTraceBus,VisionTraceItem
from one_dragon.utils import debug_utils
from autozzz.onedragon_control import ExecutionControl,WindowState
from autozzz.onedragon_trace import TraceRuntime
from autozzz.trace_preferences import DEFAULTS
from copy import deepcopy

before_screenshot=Operation.screenshot;before_save=debug_utils.save_debug_image
with tempfile.TemporaryDirectory(prefix='autozzz-auto-overlay-') as folder:
    directory=Path(folder)
    control=ExecutionControl(lambda:WindowState(True,True,(0,0,1920,1080)),lambda:None,lambda:True,lambda:None,lambda:None)
    control.observe(lambda:None)
    pixels=np.full((1080,1920,3),70,dtype=np.uint8)
    window=NS(bound=NS(hwnd=1),process_id=2,identity_valid=lambda:True)
    controller=NS(screenshot=lambda:(time.time(),pixels),standard_width=1920,standard_height=1080,game_win=window)
    ctx=NS(debug_trace_bus=DebugTraceBus(),execution_control=control,controller=controller)
    runtime=TraceRuntime(ctx)
    preferences=deepcopy(DEFAULTS);preferences['patched_capture_enabled']=True
    operation=NS(ctx=ctx,last_screenshot=None,last_screenshot_time=0)
    try:
        runtime.configure(True)
        with patch('one_dragon.utils.debug_utils.get_debug_image_path',side_effect=lambda name: str(directory/(name+'.png'))),\
             patch('autozzz.trace_preferences.TracePreferences.load',return_value=preferences):
            runtime.bus.add_vision(VisionTraceItem('ocr','same-frame',10,10,160,80))
            Operation.screenshot(operation)
            name=debug_utils.save_debug_image(operation.last_screenshot,file_name='frame')
            assert name=='frame' and (directory/'frame.png').is_file()
            assert (directory/'frame_overlay.png').is_file()
            original=(directory/'frame.png').read_bytes()
            assert original!=(directory/'frame_overlay.png').read_bytes()
            assert np.all(pixels==70)
            control.set_manual_pause(True)
            debug_utils.save_debug_image(operation.last_screenshot,file_name='paused')
            assert (directory/'paused.png').exists() and not (directory/'paused_overlay.png').exists()
            assert (directory/'frame.png').read_bytes()==original
            assert runtime.snapshot()['automatic_capture']['saved']==1
    finally:runtime.close()
assert Operation.screenshot is before_screenshot and debug_utils.save_debug_image is before_save
print(json.dumps(dict(real_sdk_screenshot_and_save_hooks=True,same_frame_overlay=True,source_unchanged=True,
    pause_rejected=True,original_hooks_restored=True,game_input=False)))
