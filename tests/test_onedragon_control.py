"""验证失焦等待、手动暂停、旧截图失效和长按释放的独立语义。"""
from __future__ import annotations

import threading
import time
import unittest

from autozzz.onedragon_control import ExecutionControl, ExecutionStopped, ObservationExpired, WindowState


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.window = WindowState(True, True, (0, 0, 1920, 1080))
        self.events = []
        self.control = ExecutionControl(lambda: self.window, lambda: self.events.append("release"),
                                        lambda: self.events.append("pause"), lambda: self.events.append("resume"),
                                        lambda: self.events.append("stop"))
        self.control.poll()
        self.frame()
        self.events.clear()

    def frame(self):
        with self.control.scope():
            return self.control.observe(lambda: "fresh image")

    def test_focus_loss_pauses_and_focus_return_requires_new_picture(self):
        old = self.control.epoch
        self.window = WindowState(True, False, self.window.geometry)
        self.assertEqual(self.control.poll()["state"], "focus_wait")
        self.assertEqual(self.events, ["release", "pause"])
        self.window = WindowState(True, True, self.window.geometry)
        self.assertEqual(self.control.poll()["state"], "running")
        self.assertFalse(self.control.status()["fresh_frame"])
        with self.assertRaises(ObservationExpired), self.control.scope(old):
            self.control.input(lambda: self.fail("old input executed"))
        self.frame()
        with self.control.scope():
            self.control.input(lambda: self.events.append("click"))
        self.assertEqual(self.events[-2:], ["resume", "click"])

    def test_focus_return_does_not_cancel_manual_pause(self):
        self.window = WindowState(True, False, self.window.geometry)
        self.control.poll()
        self.control.set_manual_pause(True)
        self.window = WindowState(True, True, self.window.geometry)
        self.assertEqual(self.control.poll()["state"], "paused")
        self.assertNotIn("resume", self.events)
        self.control.set_manual_pause(False)
        self.assertEqual(self.events.count("resume"), 1)

    def test_manual_resume_cannot_resume_unfocused_window(self):
        self.control.set_manual_pause(True)
        self.window = WindowState(True, False, self.window.geometry)
        self.control.poll()
        self.control.set_manual_pause(False)
        self.assertEqual(self.control.status()["state"], "focus_wait")
        self.assertNotIn("resume", self.events)

    def test_pause_and_stop_are_idempotent(self):
        self.control.set_manual_pause(True)
        self.control.set_manual_pause(True)
        self.assertEqual(self.events.count("pause"), 1)
        self.control.stop()
        self.control.stop()
        self.assertEqual(self.events.count("stop"), 1)
        self.control.set_manual_pause(False)
        self.assertEqual(self.control.status()["state"], "stopped")

    def test_closed_or_replaced_window_is_terminal(self):
        self.window = WindowState(False, False, reason="客户端进程已改变")
        self.control.poll()
        with self.assertRaisesRegex(ExecutionStopped, "进程已改变"):
            self.control.checkpoint()
        self.assertEqual(self.events, ["release", "stop"])

    def test_geometry_change_cancels_old_tasks_and_forces_new_picture(self):
        old = self.control.epoch
        self.window = WindowState(True, True, (10, 10, 1280, 720))
        self.control.poll()
        self.assertGreater(self.control.epoch, old)
        self.assertEqual(self.events, ["release", "pause", "resume"])
        with self.assertRaises(ObservationExpired):
            self.control.input(lambda: self.fail("input without fresh frame"))

    def test_focus_change_during_capture_discards_image(self):
        def capture():
            self.window = WindowState(True, False, self.window.geometry)
            return "obsolete image"
        with self.assertRaises(ObservationExpired), self.control.scope():
            self.control.observe(capture)
        self.assertFalse(self.control.status()["fresh_frame"])

    def test_timed_hold_returns_quickly_after_pause(self):
        completed = threading.Event()
        result = []
        def hold():
            with self.control.scope():
                result.append(self.control.hold(30))
            completed.set()
        worker = threading.Thread(target=hold)
        worker.start()
        self.control.set_manual_pause(True)
        self.assertTrue(completed.wait(.5))
        worker.join()
        self.assertEqual(result, [False])
        self.assertEqual(self.events[:2], ["release", "pause"])

    def test_old_background_task_remains_invalid_after_resume_and_new_frame(self):
        old = self.control.epoch
        self.control.set_manual_pause(True)
        self.control.set_manual_pause(False)
        self.frame()
        with self.control.scope(old), self.assertRaises(ObservationExpired):
            self.control.input(lambda: self.fail("stale task resumed"))

    def test_new_run_does_not_revalidate_old_run_epoch(self):
        old = self.control.epoch
        self.control.stop()
        self.control.reset()
        self.frame()
        with self.control.scope(old), self.assertRaises(ObservationExpired):
            self.control.input(lambda: self.fail("old run input executed"))

    def test_probe_failure_releases_keys_and_stops_backend(self):
        def fail():
            raise OSError("无法查询窗口")
        self.control.probe = fail
        self.control.poll()
        self.assertEqual(self.events, ["release", "stop"])
        self.assertIn("无法查询", self.control.status()["error"])

    def test_failure_to_release_keys_cannot_reopen_input(self):
        def fail():
            raise OSError("松键失败")
        self.control.release = fail
        with self.assertRaises(OSError):
            self.control.set_manual_pause(True)
        self.assertTrue(self.control.stopped)
        self.assertIn("stop", self.events)


if __name__ == "__main__":
    unittest.main()
