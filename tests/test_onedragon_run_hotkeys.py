"""Configured runtime shortcuts through the original pause and stop controls."""

from contextlib import nullcontext
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, patch

from autozzz.onedragon_control import ExecutionControl, WindowState
from autozzz.onedragon_execution import OneDragonExecution
from autozzz.onedragon_runtime import RemoteError

if __package__:
    from . import test_onedragon_rotation_session as rotation_fixture
else:
    import test_onedragon_rotation_session as rotation_fixture


class KeyEvents:
    def __init__(self):
        self.edges = queue.SimpleQueue()
        self.closed = False
        self.joined = False

    def press(self, action, *, down=True, injected=False, source='hook'):
        self.edges.put(SimpleNamespace(action=action, down=down, injected=injected, source=source))

    def drain(self):
        events = []
        while True:
            try:
                events.append(self.edges.get_nowait())
            except queue.Empty:
                return events

    def status(self):
        return {'mode': 'hook'}

    def wait_ready(self, _timeout):
        return True

    def close(self):
        self.closed = True

    def join(self):
        self.joined = self.closed


class NativeShortcutTests(unittest.TestCase):
    def setUp(self):
        self.engine = object.__new__(OneDragonExecution)
        engine = self.engine
        engine.lock = threading.RLock()
        engine.manual_pause = threading.Event()
        engine.cancelled = threading.Event()
        engine._input_ready = True
        engine._f9_down = False
        engine.run_lease = None
        self.focused = True
        self.releases = []
        engine.control = ExecutionControl(
            lambda: WindowState(True, self.focused, (0, 0, 1920, 1080)),
            lambda: self.releases.append(True), lambda: True, lambda: None, lambda: None,
        )
        self.keys = {}
        key_reader = patch('autozzz.windows.user32.GetAsyncKeyState', side_effect=lambda key: self.keys.get(key, 0))
        key_reader.start()
        self.addCleanup(key_reader.stop)
        engine._run_key_events = self.source = KeyEvents()

    def test_physical_alias_pause_is_retained_across_focus_loss_and_stop_wins(self):
        engine = self.engine
        self.source.press('start', injected=True)
        self.source.press('stop', source='polling')
        engine._poll_task_keys()
        self.assertFalse(engine.manual_pause.is_set())
        self.assertFalse(engine.cancelled.is_set())

        self.focused = False
        engine.control.poll()
        self.source.press('start')
        self.source.press('start', down=False)
        engine._poll_task_keys()
        self.focused = True
        engine.control.poll()
        self.assertTrue(engine.control.status()['manual_paused'])
        self.source.press('start')
        engine._poll_task_keys()
        self.assertEqual(engine.control.status()['state'], 'running')

        self.source.press('start')
        self.source.press('stop')
        engine._poll_task_keys()
        self.assertFalse(engine.manual_pause.is_set())
        self.assertTrue(engine.cancelled.is_set())
        self.assertEqual(engine.control.status()['state'], 'stopped')
        self.assertTrue(self.releases)

    def test_native_job_discards_previous_held_f9_and_closes_its_listener(self):
        engine = self.engine
        engine.ctx = SimpleNamespace(download_cancel=threading.Event(), controller=object())
        engine.closed = False
        engine.worker = None
        engine.rotation_guard = None
        engine.handoff = None
        engine._run_aliases = {'start': (17, 123)}
        engine._run_key_events = None
        engine.status = lambda: {'state': engine.state}
        self.keys[0x78] = 0x8000
        pauses = []

        def operation():
            with engine._run_scope():
                engine._poll_task_keys()
                pauses.append(engine.manual_pause.is_set())
                self.keys[0x78] = 0
                engine._poll_task_keys()
                self.keys[0x78] = 0x8000
                engine._poll_task_keys()
                pauses.append(engine.manual_pause.is_set())
                self.keys[0x79] = 0x8000
                self.assertTrue(engine.cancelled.wait(1))

        with tempfile.TemporaryDirectory() as folder:
            engine.data = Path(folder)
            with (patch('autozzz.run_hotkeys.RunKeyEvents', return_value=self.source) as listener,
                  patch('autozzz.onedragon_execution.RunLock', return_value=nullcontext()),
                  patch('autozzz.onedragon_plugins.verify_context_plugins')):
                engine._submit('running', operation)
                engine.worker.join(3)
            self.assertFalse(engine.worker.is_alive())
            self.assertEqual(engine.error, '')
            listener.assert_called_once_with({'f10': (121,), 'start': (17, 123)}, fixed_stop_event=ANY)
        self.assertEqual(pauses, [False, True])
        self.assertEqual(engine.state, 'stopped')
        self.assertIsNone(engine._run_key_events)
        self.assertTrue(self.source.joined)

    def test_unacquired_native_lease_never_arms_f10(self):
        self.engine.rotation_guard = None
        with (patch('autozzz.onedragon_execution.RunLock', side_effect=RuntimeError('fixture lease busy')),
              patch('autozzz.run_hotkeys.RunKeyEvents') as listener):
            with self.assertRaisesRegex(RuntimeError, 'lease busy'):
                with self.engine._run_scope():
                    self.fail('competing run acquired the fixture lease')
        listener.assert_not_called()


class RotationShortcutTests(unittest.TestCase):
    fixture = rotation_fixture.RotationSessionTests
    setUp = fixture.setUp
    close = fixture.close
    preview = fixture.preview
    start = fixture.start
    wait = fixture.wait
    terminal = fixture.terminal

    def test_rotation_forwards_custom_pause_once_and_stops_before_the_next_account(self):
        (self.root / 'config' / 'env.yml').write_text(
            'key_start_running: ctrl+f12\nkey_stop_running: ctrl+f8\n', encoding='utf-8',
        )
        self.broker.hold.clear()
        source = KeyEvents()
        with (patch('autozzz.run_hotkeys.RunKeyEvents', return_value=source) as listener,
              patch('autozzz.trace_preferences.TracePreferences.load', return_value={'hotkeys': {}})):
            self.preview()
            self.assertEqual(self.session.run_hotkeys, {'start': 'Ctrl+F12', 'stop': 'Ctrl+F8', 'warning': ''})
            self.start()
            self.wait(lambda event: event['type'] == 'state' and event['state']['busy'] and event['state'].get('budget'))
            source.press('start')
            source.press('start', down=False)
            self.wait(lambda event: event['type'] == 'state' and event['state']['control']['manual_paused'])
            source.press('stop')
            self.assertEqual(self.terminal()['state'], 'stopped')
            listener.assert_called_once_with({'f10': (121,), 'start': (17, 123), 'stop': (17, 119)}, read_key=self.session.read_key)
        pause_calls = [call for call in self.broker.calls if call[1] == 'pause_execution']
        self.assertEqual([params for _, _, _, params in pause_calls], [{'paused': True}])
        self.assertEqual(self.broker.app_starts, [1])
        self.assertTrue(source.joined)

    def test_unacquired_rotation_lease_never_arms_f10(self):
        def unavailable():
            raise RuntimeError('fixture lease busy')

        self.session.lease_factory = unavailable
        with patch('autozzz.run_hotkeys.RunKeyEvents') as listener:
            self.preview()
            self.start()
            result = self.terminal()
        self.assertEqual(result['state'], 'failed')
        self.assertIn('lease busy', result['error'])
        listener.assert_not_called()

    def test_short_f10_stops_only_the_current_startup_and_waits_for_child(self):
        source = KeyEvents()
        original_submit = rotation_fixture.Client.submit
        entered, stop_ack, release_native = threading.Event(), threading.Event(), threading.Event()
        self.broker.hold.clear()

        def submit(client, method, **params):
            if method == 'execution_status' and stop_ack.is_set() and release_native.is_set():
                client.state.update(state='stopped', busy=False)
            request = original_submit(client, method, **params)
            if method == 'start_login':
                client.state.update(state='running', busy=True)
                client.answers[request] = (method, dict(client.state))
                entered.set()
            elif method == 'stop_execution':
                client.state.update(state='stopping', busy=True)
                client.answers[request] = (method, dict(client.state))
                stop_ack.set()
            return request

        with (patch.object(rotation_fixture.Client, 'submit', submit),
              patch('autozzz.run_hotkeys.RunKeyEvents', return_value=source) as listener):
            try:
                self.preview()
                self.start()
                self.assertTrue(entered.wait(2))
                source.press('f10', injected=True)
                self.assertFalse(stop_ack.wait(.15))
                source.press('f10')
                source.press('f10', down=False)
                source.press('f10')
                self.assertTrue(stop_ack.wait(2))
                self.assertTrue(self.session.busy)
                self.assertEqual(self.broker.app_starts, [])
            finally:
                release_native.set()
            self.assertEqual(self.terminal()['state'], 'stopped')
        listener.assert_called_once_with({'f10': (121,)}, read_key=self.session.read_key)
        starts = [call[0] for call in self.broker.calls if call[1] == 'start_login']
        stops = [call[0] for call in self.broker.calls if call[1] == 'stop_execution']
        self.assertEqual(len(starts), 1)
        self.assertEqual(stops, starts)
        self.assertTrue(source.joined)

    def test_rotation_preserves_alias_pause_when_child_finishes_before_accepting_it(self):
        (self.root / 'config' / 'env.yml').write_text('key_start_running: ctrl+f12\n', encoding='utf-8')
        self.broker.hold.clear()
        source = KeyEvents()
        original_submit = rotation_fixture.Client.submit
        rejected = []

        def submit(client, method, **params):
            if method == 'pause_execution' and params['paused']:
                # The child already handled a fixed-key edge and finished before
                # reading the parent alias request. Carry the held F9 state across.
                client.state.update(state='success', busy=False, handoff_control={'paused': False, 'f9_down': True})
                self.broker.keys[0x78] = 0x8000
                rejected.append(client.state['job_id'])
                raise RemoteError('fixture stage already finished')
            return original_submit(client, method, **params)

        with (patch.object(rotation_fixture.Client, 'submit', submit),
              patch('autozzz.run_hotkeys.RunKeyEvents', return_value=source),
              patch('autozzz.trace_preferences.TracePreferences.load', return_value={'hotkeys': {}})):
            self.preview()
            self.start()
            self.wait(lambda event: event['type'] == 'state' and event['state']['busy'] and event['state'].get('budget'))
            source.press('start')
            self.wait(lambda event: event['type'] == 'state' and event['state']['control']['manual_paused'])
            self.assertTrue(self.session.f9_down)
            self.session.stop()
            self.assertEqual(self.terminal()['state'], 'stopped')
        self.assertEqual(len(rejected), 1)
        self.assertEqual(self.broker.app_starts, [1])
