"""Real fixed-source screenshot controller and debug bus, synthetic device boundary only."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor/onedragon/src'))
from autozzz.onedragon_control import ExecutionControl,WindowState
from autozzz.onedragon_trace import TraceRuntime
from autozzz.trace_capture import capture_source,read_capture
from one_dragon.base.controller.pc_screenshot.pc_screenshot_controller import PcScreenshotController
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.base.debug.debug_trace_bus import DebugTraceBus,VisionTraceItem


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output=args.output.resolve();args.output.mkdir(parents=True,exist_ok=True)
    import numpy as np
    with tempfile.TemporaryDirectory(prefix='autozzz-source-trace-capture-') as folder:
        os.chdir(folder)
        control=ExecutionControl(lambda:WindowState(True,True,(0,0,3840,2160)),lambda:None,lambda:True,lambda:None,lambda:None)
        control.poll();control.observed_epoch=control.epoch
        window=NS(bound=NS(hwnd=123),process_id=456,identity_valid=lambda:True,is_win_active=True,win_rect=Rect(0,0,3840,2160))
        capture=PcScreenshotController(window,1920,1080);calls=[]
        image=np.empty((2160,3840,3),dtype=np.uint8);image[:]=[200,70,15]
        def read(rect,independent):
            calls.append((rect.width,rect.height,independent));return image
        # Preserve actual source controller priority/rect/resize routing; replace only OS pixel readers.
        for strategy in capture.strategies.values():strategy.capture=read
        controller=NS(game_win=window,screenshot_controller=capture,standard_width=1920,standard_height=1080)
        ctx=NS(debug_trace_bus=DebugTraceBus(),execution_control=control,controller=controller)
        runtime=TraceRuntime(ctx);runtime.configure(True);ctx.debug_trace_bus.add_vision(VisionTraceItem('ocr','400',10,20,100,50))
        snapshot=runtime.snapshot();result=capture_source(runtime,folder,snapshot['epoch'],snapshot['generation'])
        assert not result['waiting'] and result['snapshot']['items']['vision']
        restored,path=read_capture(folder,result['capture']);assert restored.size==(3840,2160) and restored.getpixel((100,100))==(200,70,15)
        assert calls==[(3840,2160,True)] and capture.active_strategy_name is None
        assert len(json.dumps(result))<20_000 and control.observed_epoch==control.epoch
        control.set_manual_pause(True);snapshot=runtime.snapshot()
        ctx.debug_trace_bus.add_vision(VisionTraceItem('ocr','old paused state',10,20,100,50))
        paused=capture_source(runtime,folder,snapshot['epoch'],snapshot['generation'])
        assert not paused['waiting'] and paused['capture']['paused'] and not any(paused['snapshot']['items'].values())
        window.is_win_active=False;before=len(calls)
        assert capture_source(runtime,folder,snapshot['epoch'],snapshot['generation'])['waiting'] and len(calls)==before
        runtime.close()
        assert capture_source(runtime,folder,snapshot['epoch'],snapshot['generation'])['waiting']
        result=dict(actual_source_controller=True,actual_source_bus=True,actual_execution_control=True,
                    synthetic_pixel_reader=True,raw_resolution=[3840,2160],rgb_roundtrip=True,
                    independent_does_not_initialize_capture=True,paused_clears_old_state=True,
                    unfocused_waits_without_capture=True,closed_waits=True,game_accessed=False,game_input_sent=False)
        (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result));os.chdir(ROOT)


if __name__=='__main__':main()
