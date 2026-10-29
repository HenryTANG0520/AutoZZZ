"""复用源虚拟手柄的映射，按 AutoZZZ 观察代次控制等待与释放。"""
from __future__ import annotations

import math
import threading


def _duration(value,*,positive=False):
    if type(value) not in (int,float) or not math.isfinite(value) or value<(0.001 if positive else 0):
        raise ValueError('手柄时长须为有限非负数。')
    return float(value)


def release_controller_inputs(controller):
    seen=set();failures=[]
    for name in ('keyboard_controller','xbox_controller','ds4_controller','background_mouse'):
        device=getattr(controller,name,None)
        if device is None or id(device) in seen:continue
        seen.add(id(device))
        try:device.reset()
        except Exception as exc:failures.append(exc)
    if failures:raise RuntimeError('输入设备清理失败：'+str(failures[0])) from failures[0]


class ControlledGamepad:
    def __init__(self,raw,control):
        if raw.pad is None:raise RuntimeError('虚拟手柄未创建，请检查 vgamepad 与 ViGEmBus。')
        self.raw,self.control=raw,control
        self.lock=threading.RLock();self.pressed={};self.camera=None
        self.before_input=None

    @property
    def key_press_time(self):return self.raw.key_press_time

    def set_key_press_time(self,value):self.raw.set_key_press_time(_duration(value,positive=True))

    def _press(self,key):
        activate,_=self.raw._key_bindings[key]
        owner=(self.control.task_epoch,object())
        def send():
            with self.lock:
                if self.before_input is not None:self.before_input(key)
                self.pressed[key]=owner;activate();self.raw.pad.update()
        try:self.control.input(send)
        except Exception:
            self.release(key,owner=owner)
            raise
        return owner

    def press(self,key,press_time=None):
        if key is None:return
        duration=None if press_time is None else max(_duration(self.key_press_time),_duration(press_time))
        owner=self._press(key)
        if duration is not None:
            try:self.control.hold(duration)
            finally:self.release(key,owner=owner)

    def tap(self,key):
        if key is None:return
        duration=_duration(self.key_press_time)
        owner=self._press(key)
        try:self.control.hold(duration)
        finally:self.release(key,owner=owner)

    def release(self,key,*,owner=None):
        if key is None:return
        with self.lock:
            actual=self.pressed.get(key)
            if actual is None:return
            if owner is not None:
                if actual!=owner:return
            elif actual[0]!=self.control.task_epoch:return
            _,deactivate=self.raw._key_bindings[key]
            deactivate();self.raw.pad.update();self.pressed.pop(key,None)

    def tap_combo(self,keys):
        if not keys:return
        # 先完整校验，不能按下修饰键后才发现后续键无效。
        if not isinstance(keys,list) or any(key is not None and (type(key)is not str or key not in self.raw._key_bindings) for key in keys):
            raise ValueError('组合键包含未注册的手柄按键。')
        delay=_duration(self.raw.combo_press_time);duration=_duration(self.key_press_time);pressed=[]
        try:
            for index,key in enumerate(keys):
                if key is None:continue
                owner=self._press(key);pressed.append((key,owner))
                if index<len(keys)-1 and not self.control.hold(delay):return
            self.control.hold(duration)
        finally:
            failures=[]
            for key,owner in reversed(pressed):
                try:self.release(key,owner=owner)
                except Exception as exc:failures.append(exc)
            if failures:raise RuntimeError('手柄组合键释放失败。') from failures[0]

    def turn(self,dx,dy,speed):
        if not all(type(value) in (int,float) and math.isfinite(value) for value in (dx,dy,speed)):
            raise ValueError('手柄镜头参数必须为有限数。')
        if speed<=0 or dx==dy==0:return
        distance=max(abs(dx),abs(dy));owner=(self.control.task_epoch,object())
        def send():
            with self.lock:
                if self.before_input is not None:self.before_input(None)
                self.camera=owner;self.raw.pad.right_joystick_float(dx/distance,-dy/distance);self.raw.pad.update()
        try:
            self.control.input(send);self.control.hold(distance/speed)
        finally:
            with self.lock:
                if self.camera==owner:
                    self.raw.pad.right_joystick_float(0,0);self.raw.pad.update();self.camera=None

    def reset(self):
        # pad 为本进程创建的独立虚拟设备；停止清理只能发送中立态。
        with self.lock:
            self.raw.reset();self.pressed.clear();self.camera=None
