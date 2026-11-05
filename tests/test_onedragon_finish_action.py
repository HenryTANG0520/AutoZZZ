import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from autozzz.onedragon_finish_action import FinishAction, _dispatch_windows, _suspend_windows


class FinishActionTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.report = Path(folder) / 'report.json'
        self.receipt = self.report.with_name('after-done.json')
        self.now = 100
        self.dispatched = []
        self.lock_held = False

    def write_report(self, **overrides):
        data = dict(operation='onedragon', app_id='one_dragon', status='success', finished_at='done',
                    request={'finish_action': 'shutdown'},
                    execution={'job_id': 'original', 'state': 'success', 'busy': False})
        data.update(overrides)
        self.report.write_text(json.dumps(data), encoding='utf-8')

    def action(self, mode='shutdown', **kwargs):
        outer = self

        class LocalLock:
            def acquire(self):
                if outer.lock_held:
                    raise RuntimeError('active job')
                outer.lock_held = True

            def close(self):
                outer.lock_held = False

        options = dict(clock=lambda: self.now, dispatch=self.dispatched.append, lock_factory=LocalLock)
        options.update(kwargs)
        return FinishAction(mode, **options)

    def arm(self, action, *, app_id='one_dragon', succeeded=True):
        return action.arm(self.report, 'original', app_id, succeeded=succeeded)

    def recorded(self):
        return json.loads(self.receipt.read_text(encoding='utf-8'))

    def wait_for_final(self, action):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = action.poll()
            if status.state != 'dispatching':
                return status
            time.sleep(0.005)
        self.fail('The fake dispatcher did not finish.')

    def test_power_action_requires_local_explicit_mode_and_success(self):
        self.write_report()
        self.assertEqual(self.arm(self.action(None)).state, 'disabled')
        self.assertEqual(self.arm(self.action(), succeeded=False).state, 'refused')
        self.assertEqual(self.arm(self.action('sleep')).state, 'refused')
        for override in (
            {'request': {'finish_action': 'shutdown', 'source': 'mcp'}},
            {'execution': {'job_id': 'another', 'state': 'success', 'busy': False}},
            {'execution': {'job_id': 'original', 'state': 'stopped', 'busy': False}},
            {'execution': {'job_id': 'original', 'state': 'success', 'busy': True}},
            {'status': 'failed'},
        ):
            with self.subTest(override=override):
                self.write_report(**override)
                self.assertEqual(self.arm(self.action()).state, 'refused')
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.dispatched, [])

    def test_cancel_is_one_shot_and_late_report_cannot_rearm(self):
        self.write_report()
        idle = self.action()
        self.assertTrue(idle.cancel())
        self.assertEqual(self.arm(idle).state, 'cancelled')
        action = self.action()
        self.assertEqual(self.arm(action).remaining_seconds, 60)
        self.now = 159.5
        self.assertEqual(action.poll().remaining_seconds, 1)
        self.now = 160
        self.assertTrue(action.cancel())
        self.assertEqual(action.poll().state, 'cancelled')
        self.assertEqual(self.recorded()['state'], 'cancelled')
        self.assertEqual(self.arm(self.action()).state, 'refused')
        self.assertEqual(self.dispatched, [])

    def test_rotation_dispatch_claim_is_durable_and_holds_task_lock(self):
        self.write_report(operation='account_rotation', job_id='original', finish_action='hibernate')
        entered = threading.Event()
        release = threading.Event()

        def dispatch(mode):
            self.dispatched.append((mode, self.recorded()['state'], self.lock_held))
            entered.set()
            release.wait(2)

        action = self.action('hibernate', dispatch=dispatch)
        self.assertEqual(self.arm(action, app_id='_rotation').state, 'pending')
        report_before = self.report.read_bytes()
        try:
            self.now = 160
            self.assertEqual(action.poll().state, 'dispatching')
            self.assertTrue(entered.wait(2))
            self.assertFalse(action.cancel())
            self.assertEqual(action.poll().state, 'dispatching')
            self.assertEqual(self.dispatched, [('hibernate', 'dispatching', True)])
        finally:
            release.set()
        self.assertEqual(self.wait_for_final(action).state, 'dispatched')
        self.assertFalse(self.lock_held)
        self.assertEqual(self.recorded()['job_id'], 'original')
        self.assertEqual(self.report.read_bytes(), report_before)

    def test_report_replacement_invalidates_waiting_action(self):
        self.write_report()
        action = self.action()
        self.assertEqual(self.arm(action).state, 'pending')
        self.write_report(finished_at='different run result')
        self.now = 160
        self.assertEqual(action.poll().state, 'failed')
        self.assertEqual(self.recorded()['state'], 'failed')
        self.assertFalse(self.lock_held)
        self.assertEqual(self.dispatched, [])

    def test_busy_task_prevents_dispatch_and_is_never_retried(self):
        self.write_report()
        action = self.action()
        self.assertEqual(self.arm(action).state, 'pending')
        self.lock_held = True
        self.now = 160
        self.assertEqual(action.poll().state, 'failed')
        self.assertTrue(self.lock_held)
        self.lock_held = False
        self.assertEqual(action.poll().state, 'failed')
        self.assertEqual(self.dispatched, [])

    def test_missing_durable_claim_prevents_dispatch(self):
        self.write_report()
        action = self.action()
        self.assertEqual(self.arm(action).state, 'pending')
        self.now = 160
        with patch('autozzz.onedragon_finish_action.os.replace', side_effect=OSError('disk unavailable')):
            self.assertEqual(action.poll().state, 'failed')
        self.assertFalse(self.lock_held)
        self.assertEqual(self.dispatched, [])

    def test_guard_failure_at_dispatch_stops_even_after_claim(self):
        self.write_report()
        call_count = 0

        def guard():
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise RuntimeError('generation changed')

        action = self.action(guard=guard)
        self.assertEqual(self.arm(action).state, 'pending')
        self.now = 160
        action.poll()
        self.assertEqual(self.wait_for_final(action).state, 'failed')
        self.assertEqual(self.recorded()['detail'], 'generation changed')
        self.assertEqual(self.dispatched, [])

    def test_windows_dispatch_uses_local_commands_without_force_or_abort(self):
        def system_directory(buffer, size):
            buffer.value = r'C:\Windows\System32'
            return len(buffer.value)

        with patch('autozzz.onedragon_finish_action.sys.platform', 'win32'), \
             patch('autozzz.onedragon_finish_action.ctypes.WinDLL', create=True) as library, \
             patch('autozzz.onedragon_finish_action.subprocess.CREATE_NO_WINDOW', 0, create=True), \
             patch('autozzz.onedragon_finish_action.subprocess.run') as run, \
             patch('autozzz.onedragon_finish_action._suspend_windows') as suspend:
            library.return_value.GetSystemDirectoryW.side_effect = system_directory
            _dispatch_windows('shutdown')
            command = run.call_args.args[0]
            self.assertTrue(command[0].endswith('shutdown.exe'))
            self.assertEqual(command[1:], ['/s', '/t', '0'])
            _dispatch_windows('sleep')
            suspend.assert_called_with(hibernate=False)
            _dispatch_windows('hibernate')
            suspend.assert_called_with(hibernate=True)

    def test_suspend_restores_original_privilege_and_refuses_missing_privilege(self):
        with patch('autozzz.onedragon_finish_action.ctypes.WinDLL') as library, \
             patch('autozzz.onedragon_finish_action.ctypes.get_last_error', return_value=0) as last_error:
            kernel, advapi, power = library.return_value, library.return_value, library.return_value
            kernel.GetCurrentProcess.return_value = 2
            advapi.OpenProcessToken.return_value = True
            advapi.LookupPrivilegeValueW.return_value = True

            def adjust(_token, _disabled, changed, _size, previous, _returned):
                if previous is not None:
                    self.assertEqual(changed._obj.attributes, 2)
                    previous._obj.count = 1
                    previous._obj.attributes = 0
                else:
                    self.assertEqual(changed._obj.attributes, 0)
                return True

            advapi.AdjustTokenPrivileges.side_effect = adjust
            power.SetSuspendState.return_value = True
            _suspend_windows(hibernate=False)
            power.SetSuspendState.assert_called_once_with(False, False, False)
            self.assertEqual(advapi.AdjustTokenPrivileges.call_count, 2)
            kernel.CloseHandle.assert_called_once()
            power.SetSuspendState.reset_mock()
            last_error.return_value = 1300
            with self.assertRaises(OSError):
                _suspend_windows(hibernate=True)
            power.SetSuspendState.assert_not_called()


if __name__ == '__main__':
    unittest.main()
