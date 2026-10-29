import threading
import time
from types import SimpleNamespace as NS
import unittest

from autozzz.onedragon_control import ExecutionControl,ObservationExpired,WindowState
from autozzz.onedragon_gamepad import ControlledGamepad,release_controller_inputs


class Raw:
    def __init__(self):
        self.events=[];self.key_press_time=.005;self.combo_press_time=.5
        self.pad=NS(update=lambda:self.events.append(('update',)),right_joystick_float=lambda x,y:self.events.append(('camera',x,y)))
        self._key_bindings={key:(lambda key=key:self.events.append(('down',key)),lambda key=key:self.events.append(('up',key))) for key in ('a','b')}
    def set_key_press_time(self,value):self.key_press_time=value
    def reset(self):self.events.append(('reset',))


class GamepadTests(unittest.TestCase):
    def setUp(self):
        self.window=WindowState(True,True,(0,0,1920,1080));self.raw=Raw()
        self.control=ExecutionControl(lambda:self.window,lambda:self.pad.reset(),lambda:True,lambda:None,lambda:None)
        self.pad=ControlledGamepad(self.raw,self.control);self.control.observe(lambda:None)
        self.raw.events.clear()

    def test_tap_and_explicit_press_release(self):
        self.pad.tap('a');self.pad.press('b');self.assertIn('b',self.pad.pressed);self.pad.release('b')
        self.assertEqual([event for event in self.raw.events if event[0]!='update'],[('down','a'),('up','a'),('down','b'),('up','b')])
        self.assertFalse(self.pad.pressed)

    def test_focus_interrupts_hold_and_releases(self):
        def run():
            with self.control.scope():self.pad.press('a',3)
        thread=threading.Thread(target=run);thread.start()
        deadline=time.monotonic()+1
        while 'a' not in self.pad.pressed and time.monotonic()<deadline:time.sleep(.001)
        self.window=WindowState(True,False,self.window.geometry);self.control.poll();thread.join(.3)
        self.assertFalse(thread.is_alive());self.assertFalse(self.pad.pressed);self.assertIn(('reset',),self.raw.events)
        with self.assertRaises(ObservationExpired):self.pad.press('a')

    def test_old_release_cannot_release_new_observation(self):
        old=self.control.epoch;self.pad.press('a');owner=self.pad.pressed['a']
        self.control.set_manual_pause(True);self.control.set_manual_pause(False);self.control.observe(lambda:None)
        self.pad.press('a');before=list(self.raw.events)
        with self.control.scope(old):self.pad.release('a')
        self.pad.release('a',owner=owner)
        self.assertEqual(self.raw.events,before);self.assertIn('a',self.pad.pressed)
        self.pad.release('a');self.assertFalse(self.pad.pressed)

    def test_earlier_timed_press_cannot_release_new_same_epoch(self):
        self.pad.press('a');owner=self.pad.pressed['a'];self.pad.press('a');before=list(self.raw.events)
        self.pad.release('a',owner=owner);self.assertEqual(self.raw.events,before)

    def test_combo_invalid_key_has_no_partial_input(self):
        with self.assertRaises(ValueError):self.pad.tap_combo(['a','bad'])
        self.assertFalse(self.raw.events)

    def test_combo_pause_before_next_key(self):
        original=self.control.hold
        def pause(seconds):self.control.set_manual_pause(True);return original(seconds)
        self.control.hold=pause;self.pad.tap_combo(['a','b'])
        self.assertNotIn(('down','b'),self.raw.events);self.assertFalse(self.pad.pressed)

    def test_camera_formula_and_stop_neutral(self):
        self.pad.turn(2,-1,1000)
        self.assertIn(('camera',1.,.5),self.raw.events);self.assertIn(('camera',0,0),self.raw.events)
        self.assertIsNone(self.pad.camera)

    def test_invalid_duration_never_sends(self):
        for value in (float('inf'),float('nan'),-1,True,'1'):
            with self.assertRaises(ValueError):self.pad.press('a',value)
            with self.assertRaises(ValueError):self.pad.set_key_press_time(value)
        self.assertFalse(self.raw.events)

    def test_missing_device_is_reported(self):
        with self.assertRaises(RuntimeError):ControlledGamepad(NS(pad=None),self.control)

    def test_all_devices_are_cleaned_even_if_one_fails(self):
        events=[]
        def fail():events.append('keyboard');raise OSError('fixture failure')
        other=NS(reset=lambda:events.append('pad'))
        with self.assertRaises(RuntimeError):release_controller_inputs(NS(keyboard_controller=NS(reset=fail),xbox_controller=other,ds4_controller=other))
        self.assertEqual(events,['keyboard','pad'])

    def test_combo_release_failure_still_releases_other_key(self):
        self.raw.combo_press_time=0
        def fail():raise OSError('fixture release failure')
        self.raw._key_bindings['b']=(self.raw._key_bindings['b'][0],fail)
        with self.assertRaises(RuntimeError):self.pad.tap_combo(['a','b'])
        self.assertIn(('up','a'),self.raw.events)

    def test_failed_reset_keeps_ownership_for_retry(self):
        self.pad.press('a')
        original=self.raw.reset
        def fail():raise OSError('fixture reset failure')
        self.raw.reset=fail
        with self.assertRaises(OSError):self.pad.reset()
        self.assertIn('a',self.pad.pressed)
        self.raw.reset=original;self.pad.reset();self.assertFalse(self.pad.pressed)


if __name__=='__main__':unittest.main()
