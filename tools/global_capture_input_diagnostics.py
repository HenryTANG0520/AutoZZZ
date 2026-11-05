"""Deterministic input-timing characterization; no listener, window or OS input."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.global_capture_hotkeys import GlobalCaptureHotkeys


class Fixture:
    def __init__(self,foreground=True):
        self.time=0.;self.keys=set();self.requests=[];self.actual_foreground=7
        self.clock=patch('autozzz.global_capture_hotkeys.time.monotonic',lambda:self.time);self.clock.start()
        self.root=SimpleNamespace(after=lambda *_:1,after_cancel=lambda *_:None)
        self.listener=GlobalCaptureHotkeys(self.root,lambda key:0x8000 if key in self.keys else 0,
            lambda:self.actual_foreground,lambda *args:self.requests.append(args),lambda *_:None)
        self.state=dict(settings=dict(key_screenshot='f11'),ready=True,foreground=foreground,
            window_identity=dict(hwnd=7),token='a'*64)
        self.requests[-1][2](self.state)

    def tick(self,milliseconds):
        self.time=milliseconds/1000;self.listener.tick()

    def captures(self):return len([item for item in self.requests if item[0]=='global_capture_capture'])

    def close(self):self.listener.close();self.clock.stop()


def diagnose():
    cases=[]
    fixture=Fixture()
    try:
        # A real high-bit poll sees neither transition if both fall between calls.
        fixture.time=.005;fixture.keys.add(0x7A)
        fixture.time=.015;fixture.keys.clear()
        fixture.tick(40)
        assert fixture.captures()==0
        cases.append(dict(case='10ms_down_up_between_40ms_ticks',captures=0,known_limitation=True))
    finally:fixture.close()
    fixture=Fixture()
    try:
        fixture.keys.add(0x7A);fixture.tick(40)
        assert fixture.requests[-1][0]=='global_capture_status'
        fixture.requests[-1][2](fixture.state)
        assert fixture.captures()==1
        cases.append(dict(case='key_down_at_sample_baseline',captures=1))
    finally:fixture.close()
    fixture=Fixture(foreground=False)
    try:
        # Actual foreground is already correct, but previous status cached False.
        fixture.keys.add(0x7A);fixture.tick(40)
        assert fixture.captures()==0 and fixture.requests[-1][0]=='global_capture_status'
        fixture.time=.1;fixture.requests[-1][2](dict(fixture.state,foreground=True,token='b'*64))
        fixture.tick(120)
        assert fixture.captures()==1
        cases.append(dict(case='first_press_after_focus_change_uses_current_os_foreground',first_press_captures=1,
            cached_focus_issue_fixed=True))
    finally:fixture.close()
    fixture=Fixture()
    try:
        fixture.time=.1;fixture.keys.add(0x7A)
        fixture.time=.2;fixture.keys.clear()
        fixture.tick(300)
        assert fixture.captures()==0
        cases.append(dict(case='100ms_press_inside_300ms_tk_stall',captures=0,known_limitation=True))
    finally:fixture.close()
    fixture=Fixture()
    try:
        fixture.keys.update((0x7A,0x11));fixture.tick(40)
        assert fixture.captures()==0
        cases.append(dict(case='plain_F11_with_extra_Ctrl_is_intentionally_rejected',captures=0))
    finally:fixture.close()
    return dict(cases=cases,uses_current_product_hotkey_code=True,os_keyboard_read=False,
        keyboard_injected=False,window_created=False,foreground_touched=False,game_accessed=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    result=diagnose()
    if args.output:args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
