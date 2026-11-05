import ctypes
from ctypes import wintypes as W
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from autozzz.run_hotkeys import F9, F10, MOUSE_CODES, RunKeyEvents, RunKeyQueue, display_run_key, parse_run_key, run_bindings


class RunBindingTests(unittest.TestCase):
    def test_fixed_keys_and_optional_aliases(self):
        fixed = {'f9': (F9,), 'f10': (F10,)}
        self.assertEqual(run_bindings(), fixed)
        self.assertEqual(run_bindings('Vk_120', ' '), fixed)
        self.assertEqual(run_bindings('numpad_2', 'Shift+Ctrl+P'), fixed | {
            'start': (98,), 'stop': (16, 17, ord('P')),
        })

    def test_rejects_conflicts_and_bad_spellings(self):
        for value, action in [('f10', 'start'), ('vk_120', 'stop'), ('Ctrl+F9', 'start'), ('Ctrl+', 'stop')]:
            with self.subTest(value=value, action=action), self.assertRaises(ValueError):
                parse_run_key(value, action)
        with self.assertRaisesRegex(ValueError, '不能相同'):
            run_bindings('Shift+Ctrl+P', 'ctrl+shift+p')
        with self.assertRaisesRegex(ValueError, '不能相同'):
            run_bindings('ctrl', 'ctrl_r')

    def test_mouse_source_names_and_standalone_modifiers(self):
        names = ['mouse_left', 'mouse_right', 'mouse_middle', 'mouse_x1', 'mouse_x2']
        self.assertEqual({parse_run_key(name, 'start')[0] for name in names}, MOUSE_CODES)
        self.assertEqual(parse_run_key('Alt+mouse_x2', 'stop'), (18, 6))
        self.assertEqual(parse_run_key('shift', 'start'), (16,))
        self.assertEqual(parse_run_key('alt_r', 'stop'), (165,))

    def test_punctuation_uses_layout_and_display_preserves_plus(self):
        scan = Mock(side_effect=lambda value: {'+': 0x01BB, ';': 0xBA}[value])
        with patch('autozzz.run_hotkeys.ctypes.WinDLL', return_value=SimpleNamespace(VkKeyScanW=scan)):
            self.assertEqual(parse_run_key('+', 'start'), (16, 187))
            self.assertEqual(parse_run_key('ctrl + +', 'stop'), (16, 17, 187))
            self.assertEqual(parse_run_key(';', 'start'), (186,))
        self.assertEqual(display_run_key('+'), '+')
        self.assertEqual(display_run_key('ctrl + +'), 'Ctrl++')
        self.assertEqual(display_run_key('Ctrl+'), 'Ctrl+')


class RunKeyQueueTests(unittest.TestCase):
    def test_short_press_records_both_edges_and_suppresses_repeats(self):
        queue = RunKeyQueue(clock=lambda: 1.)
        generation = queue.configure(run_bindings('Ctrl+P'))
        queue.feed(17, True, foreground=40, flags=1)  # Right Ctrl.
        queue.feed(ord('P'), True, foreground=40, created=1.01, os_time=73)
        queue.feed(ord('P'), True, foreground=40, created=1.015, os_time=74)
        queue.feed(17, False, foreground=41, flags=1)
        queue.feed(ord('P'), False, foreground=41, created=1.02, os_time=75)
        down, up = queue.drain()
        self.assertEqual((down.action, up.action), ('start', 'start'))
        self.assertTrue(down.down)
        self.assertFalse(up.down)
        self.assertEqual((down.generation, up.generation), (generation, generation))
        self.assertEqual(down.press_id, up.press_id)
        self.assertEqual((down.created, up.created), (1.01, 1.02))
        self.assertEqual((down.foreground, up.foreground), (40, 41))
        self.assertEqual((down.os_time, up.os_time), (73, 75))

    def test_exact_modifiers_for_aliases_and_fixed_key_identity(self):
        queue = RunKeyQueue()
        queue.configure(run_bindings('Ctrl+P', 'Alt+P'))
        queue.feed(162, True, foreground=40)
        queue.feed(164, True, foreground=40)
        queue.feed(ord('P'), True, foreground=40)
        queue.feed(ord('P'), False, foreground=40)
        self.assertEqual(queue.drain(), [])
        queue.feed(162, False, foreground=40)
        queue.feed(ord('P'), True, foreground=40)
        queue.feed(F10, True, foreground=41)
        self.assertEqual([(edge.action, edge.code) for edge in queue.drain()], [('stop', ord('P')), ('f10', F10)])

    def test_held_and_preconfiguration_edges_do_not_start_new_binding(self):
        now = [2.]
        queue = RunKeyQueue(clock=lambda: now[0])
        queue.configure({'start': (ord('P'),)}, lambda code: 0x8000 if code == ord('P') else 0)
        queue.feed(ord('P'), True, foreground=40)
        queue.feed(ord('P'), False, foreground=40)
        self.assertEqual(queue.drain(), [])
        generation = queue.configure({'start': (ord('P'),)})
        queue.feed(ord('P'), True, foreground=40, created=1.9)
        queue.feed(ord('P'), True, foreground=40, created=2.01)
        queue.feed(ord('P'), False, foreground=40, created=2.02)
        self.assertEqual(queue.drain(), [])
        queue.feed(ord('P'), True, foreground=40, created=2.03)
        edge, = queue.drain()
        self.assertEqual(edge.generation, generation)

    def test_injected_input_cannot_qualify_or_clear_a_physical_press(self):
        queue = RunKeyQueue()
        queue.configure({'start': (ord('P'),)})
        queue.feed(162, True, foreground=40, flags=0x10)
        queue.feed(ord('P'), True, foreground=40, flags=0x12)
        self.assertEqual(queue.drain(), [])
        queue.feed(ord('P'), True, foreground=40)
        queue.feed(ord('P'), False, foreground=40, flags=0x10)
        queue.feed(ord('P'), True, foreground=40)
        queue.feed(ord('P'), False, foreground=40)
        down, up = queue.drain()
        self.assertEqual(down.press_id, up.press_id)
        self.assertTrue(down.down)
        self.assertFalse(up.down)

    def test_fixed_stop_signal_is_job_scoped_and_suppresses_repeat_and_injection(self):
        signal = Mock()
        queue = RunKeyQueue(fixed_stop_event=signal)
        queue.configure({'f10': (F10,)}, lambda code: 0x8000 if code == F10 else 0)
        queue.feed(F10, True, foreground=40)  # held across job startup
        queue.feed(F10, False, foreground=40)
        queue.feed(F10, True, foreground=40, flags=0x10)
        signal.set.assert_not_called()
        queue.feed(F10, True, foreground=40)
        queue.feed(F10, True, foreground=40)  # auto-repeat
        queue.feed(F10, False, foreground=40)
        queue.close()
        queue.feed(F10, True, foreground=40)  # late callback from the old job
        signal.set.assert_called_once_with()
        self.assertEqual(queue.drain(), [])

    def test_queue_overflow_retains_pending_stop(self):
        queue = RunKeyQueue(limit=2)
        queue.configure(run_bindings('P'))
        queue.feed(F10, True, foreground=40)
        for _ in range(4):
            queue.feed(ord('P'), True, foreground=40)
            queue.feed(ord('P'), False, foreground=40)
        self.assertEqual(queue.status()['queue_size'], 2)
        self.assertEqual(queue.status()['queue_dropped'], 7)
        self.assertEqual(queue.drain()[0].action, 'f10')

    def test_generic_modifier_uses_one_pair_across_sides(self):
        queue = RunKeyQueue()
        queue.configure(run_bindings('ctrl', 'shift_r'))
        for key, down in ((162, True), (163, True), (162, False), (163, False)):
            queue.feed(key, down, foreground=40)
        down, up = queue.drain()
        self.assertEqual((down.action, up.action), ('start', 'start'))
        self.assertEqual((down.code, up.code), (162, 162))
        self.assertEqual(down.press_id, up.press_id)
        queue.feed(161, True, foreground=40)
        stop, = queue.drain()
        self.assertEqual((stop.action, stop.code), ('stop', 161))


class FailedHook:
    def __init__(self, queue):
        self.queue = queue
        self.joined = False

    def status(self):
        return self.queue.status() | {'state': 'failed', 'error': 'fixture', 'installed': False, 'thread_alive': False}

    def close(self):
        self.queue.close()

    def join(self, timeout):
        self.joined = True


class RunKeyEventTests(unittest.TestCase):
    def test_fallback_samples_levels_and_marks_the_source(self):
        pressed = set()
        foreground = [40]
        sources = []

        def make_source(queue):
            source = FailedHook(queue)
            sources.append(source)
            return source

        events = RunKeyEvents({'stop': (17, ord('P'))}, read_key=lambda code: 0x8000 if code in pressed else 1,
                             foreground=lambda: foreground[0], source_factory=make_source)
        try:
            self.assertEqual(events.status()['mode'], 'polling')
            self.assertEqual(events.drain(), [])  # GetAsyncKeyState's unrelated low bit is ignored.
            pressed.update((163, ord('P')))
            down, = events.drain()
            self.assertEqual((down.action, down.down, down.source, down.foreground), ('stop', True, 'polling', 40))
            self.assertEqual(events.drain(), [])
            pressed.clear()
            foreground[0] = 41
            up, = events.drain()
            self.assertEqual((up.action, up.down, up.press_id, up.foreground), ('stop', False, down.press_id, 41))
        finally:
            events.close()
            events.join(1)
        self.assertEqual(events.status()['mode'], 'closed')
        self.assertEqual(events.drain(), [])
        self.assertTrue(sources[0].joined)

    def test_shared_native_hook_observes_mouse_buttons_without_motion_or_wheel(self):
        from tests.test_global_key_events import HookLifecycleTests
        user, kernel = HookLifecycleTests().api()
        user.SetWindowsHookExW.side_effect = lambda hook_id, *_: {13: 41, 14: 42}[hook_id]

        def wait_for(condition):
            deadline = time.monotonic() + 2
            while not condition() and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(condition())

        class MouseData(ctypes.Structure):
            _fields_ = [('point', W.POINT), ('button_data', W.DWORD), ('flags', W.DWORD),
                        ('time', W.DWORD), ('extra', ctypes.c_size_t)]

        with patch('autozzz.global_key_events.ctypes.WinDLL', side_effect=lambda name, **_: user if name == 'user32' else kernel):
            events = RunKeyEvents({'stop': (6,)}, read_key=lambda _: 0, foreground=lambda: 7)
            try:
                wait_for(lambda: events.status()['mouse_mode'] == 'hook')
                mouse_callback = user.SetWindowsHookExW.call_args_list[-1].args[1]
                self.assertEqual(mouse_callback(0, 0x0200, 0), 88)  # move
                self.assertEqual(mouse_callback(0, 0x020A, 0), 88)  # wheel
                data = MouseData(W.POINT(11, 12), 2 << 16, 1, 1, 0)
                address = ctypes.addressof(data)
                self.assertEqual(mouse_callback(0, 0x020B, address), 88)  # injected X2
                self.assertEqual(events.drain(), [])
                data.flags = 0
                self.assertEqual(mouse_callback(0, 0x020B, address), 88)
                self.assertEqual(mouse_callback(0, 0x020C, address), 88)
                down, up = events.drain()
                self.assertEqual((down.action, down.code, down.foreground), ('stop', 6, 7))
                self.assertEqual(down.press_id, up.press_id)
                self.assertEqual(user.CallNextHookEx.call_count, 5)
                events.configure({})
                wait_for(lambda: events.status()['mouse_state'] == 'disabled')
                user.UnhookWindowsHookEx.assert_called_once_with(42)
            finally:
                events.close()
                events.join(2)
        self.assertEqual(events.status()['mode'], 'closed')
        self.assertEqual([call.args[0] for call in user.UnhookWindowsHookEx.call_args_list], [42, 41])
