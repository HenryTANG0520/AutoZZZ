"""Install/close a passive listener, or observe chosen F-key edges without input."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes as W
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.global_capture import parse_capture_hotkey
from autozzz.global_key_events import ReadOnlyKeyboardEvents


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--key',default='F11')
    parser.add_argument('--observe-seconds',type=float,default=.25)
    args=parser.parse_args()
    if not 0<=args.observe_seconds<=120:raise ValueError('Observation must be at most 120 seconds.')
    combo=parse_capture_hotkey(args.key)
    user32=ctypes.WinDLL('user32',use_last_error=True);user32.GetForegroundWindow.restype=W.HWND
    before=user32.GetForegroundWindow();source=ReadOnlyKeyboardEvents();events=[];result={}
    try:
        if not source.ready.wait(3):raise AssertionError('Listener startup did not complete')
        result['startup']=source.status()
        assert source.status()['state']=='listening',source.status()
        source.configure(combo,1)
        print('Passive listener ready; this tool sends no keyboard or mouse input.',flush=True)
        deadline=time.monotonic()+args.observe_seconds
        while time.monotonic()<deadline:
            events.extend(asdict(event) for event in source.drain());time.sleep(.01)
        events.extend(asdict(event) for event in source.drain())
        result['before_close']=source.status()
    finally:
        source.close();source.join(3)
        result.update(after_close=source.status(),worker_exited=not source.worker.is_alive(),events=events,
            configured_key=args.key,initial_foreground_hwnd=before,final_foreground_hwnd=user32.GetForegroundWindow(),
            input_injected=False,window_created=False,game_controller_created=False,clipboard_modified=False)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    assert result['worker_exited'],result
    if args.observe_seconds<=1:
        assert result['initial_foreground_hwnd']==result['final_foreground_hwnd'],'Foreground changed during idle install/close'
    print(json.dumps(result))


if __name__=='__main__':main()
