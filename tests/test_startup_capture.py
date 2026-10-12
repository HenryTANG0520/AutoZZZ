"""Cold-launch capture waits are read only, bounded, and initial-frame only."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autozzz import app, windows
from autozzz.app import Context, Runner
from autozzz.config import Settings
from autozzz.models import AutomationError, InputInterrupted, Scene, ScreenshotNotReady, Stopped


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class StartupCaptureTests(unittest.TestCase):
    def make_context(self, *, auto_launch=True, timeout=1.0):
        clock = Clock()
        runner = Runner(Settings(auto_launch=auto_launch, transition_timeout=timeout))
        window = Mock()
        vision = Mock()
        context = Context(runner, window, vision)
        context.snapshot = Mock()
        def sleep(seconds):
            runner.checkpoint()
            clock.now += seconds
            runner.checkpoint()
        context.sleep = Mock(side_effect=sleep)
        return runner, context, clock, window, vision

    def assert_no_input(self, window):
        for method in ("click", "key", "drag", "move", "scroll", "send", "focus"):
            getattr(window, method).assert_not_called()

    def test_cold_launch_retries_uniform_frames_then_returns_first_real_scene(self):
        runner, context, clock, window, _ = self.make_context(timeout=3)
        first = Scene([])
        context.snapshot.side_effect = [ScreenshotNotReady("black"), ScreenshotNotReady("black"), first]
        with patch.object(app.time, "monotonic", clock.monotonic):
            self.assertIs(runner._initial_snapshot(context), first)
        self.assertEqual(context.snapshot.call_count, 3)
        self.assertEqual([c.args[0] for c in context.sleep.call_args_list], [.5, .5])
        self.assert_no_input(window)

    def test_attached_manual_run_still_fails_on_first_black_frame(self):
        runner, context, _, window, _ = self.make_context(auto_launch=False)
        context.snapshot.side_effect = ScreenshotNotReady("black")
        with self.assertRaises(ScreenshotNotReady):
            runner._initial_snapshot(context)
        context.snapshot.assert_called_once()
        context.sleep.assert_not_called()
        self.assert_no_input(window)

    def test_startup_timeout_is_bounded_and_retries_do_not_reset_budget(self):
        runner, context, clock, window, _ = self.make_context(timeout=1.2)
        context.snapshot.side_effect = ScreenshotNotReady("black")
        with patch.object(app.time, "monotonic", clock.monotonic), \
                self.assertRaisesRegex(ScreenshotNotReady, "首次画面超时"):
            runner._initial_snapshot(context)
        self.assertAlmostEqual(clock.now, 1.2)
        self.assertEqual(context.snapshot.call_count, 3)
        self.assertAlmostEqual(context.sleep.call_args.args[0], .2)
        self.assert_no_input(window)

    def test_non_capture_errors_are_never_treated_as_loading(self):
        for message in ("游戏窗口已关闭", "客户区大小变化", "充值界面", "账号登录", "截图为空或黑屏"):
            with self.subTest(message=message):
                runner, context, _, window, _ = self.make_context()
                original = AutomationError(message)
                context.snapshot.side_effect = original
                with self.assertRaises(AutomationError) as raised:
                    runner._initial_snapshot(context)
                self.assertIs(raised.exception, original)
                context.snapshot.assert_called_once()
                context.sleep.assert_not_called()
                self.assert_no_input(window)

    def test_focus_or_manual_pause_duration_does_not_consume_loading_timeout(self):
        for paused_phase in ("snapshot", "sleep"):
            with self.subTest(paused_phase=paused_phase):
                runner, context, clock, window, _ = self.make_context()
                first = Scene([])
                def capture():
                    if context.snapshot.call_count == 1:
                        if paused_phase == "snapshot":
                            clock.now += 50
                            runner.inactive_duration += 50
                        raise ScreenshotNotReady("black")
                    return first
                context.snapshot.side_effect = capture
                regular_sleep = context.sleep.side_effect
                def sleep(seconds):
                    if paused_phase == "sleep":
                        clock.now += 50
                        runner.inactive_duration += 50
                    regular_sleep(seconds)
                context.sleep.side_effect = sleep
                with patch.object(app.time, "monotonic", clock.monotonic):
                    self.assertIs(runner._initial_snapshot(context), first)
                self.assertAlmostEqual(clock.now - runner.inactive_duration, .5)
                self.assert_no_input(window)

    def test_manual_pause_interruptions_keep_original_budget(self):
        runner, context, clock, window, _ = self.make_context()
        context.snapshot.side_effect = InputInterrupted("old frame invalidated")
        with patch.object(app.time, "monotonic", clock.monotonic), \
                self.assertRaisesRegex(ScreenshotNotReady, "首次画面超时"):
            runner._initial_snapshot(context)
        self.assertEqual(clock.now, 1)
        self.assertEqual(context.snapshot.call_count, 2)
        self.assert_no_input(window)

    def test_stop_cancels_initial_capture_wait_before_next_attempt(self):
        runner, context, clock, window, _ = self.make_context()
        def capture():
            runner.stop()
            raise ScreenshotNotReady("black")
        context.snapshot.side_effect = capture
        with patch.object(app.time, "monotonic", clock.monotonic), self.assertRaises(Stopped):
            runner._initial_snapshot(context)
        context.snapshot.assert_called_once()
        self.assert_no_input(window)

    def test_emergency_monitor_stop_prevents_initial_capture(self):
        runner, context, _, window, _ = self.make_context()
        runner._window = window
        window.emergency_pressed.return_value = True
        api = Mock()
        api.GetAsyncKeyState.return_value = 0
        with patch.object(runner._monitor_end, "wait", side_effect=[False, True]), \
                patch.object(windows, "user32", api):
            runner._hotkeys()
        with self.assertRaises(Stopped):
            runner._initial_snapshot(context)
        context.snapshot.assert_not_called()
        self.assert_no_input(window)

    def test_later_context_capture_errors_still_fail_immediately(self):
        runner, context, _, window, vision = self.make_context()
        initial = Scene([])
        context.snapshot.return_value = initial
        self.assertIs(runner._initial_snapshot(context), initial)
        context.snapshot = lambda: Context.snapshot(context)
        window.capture.side_effect = ScreenshotNotReady("black after startup")
        with self.assertRaisesRegex(ScreenshotNotReady, "after startup"):
            context.snapshot()
        window.capture.assert_called_once()
        vision.read.assert_not_called()
        context.sleep.assert_not_called()
        self.assert_no_input(window)


class ScreenshotClassificationTests(unittest.TestCase):
    def test_black_frame_has_typed_error_and_valid_frame_remains_usable(self):
        import mss
        import numpy as np
        window = windows.GameWindow.__new__(windows.GameWindow)
        geometry = windows.Geometry(0, 0, 1920, 1080)
        window.guard = Mock(return_value=geometry)
        capture = Mock()
        with patch.object(mss, "mss") as factory:
            factory.return_value.__enter__.return_value = capture
            capture.grab.return_value = np.zeros((4, 4, 4), dtype=np.uint8)
            with self.assertRaises(ScreenshotNotReady):
                window.capture()
            frame = np.arange(64, dtype=np.uint8).reshape(4, 4, 4)
            capture.grab.return_value = frame
            image, actual_geometry = window.capture()
        self.assertIs(actual_geometry, geometry)
        self.assertTrue(np.array_equal(image, frame[:, :, :3]))

    def test_invalid_geometry_propagates_before_capture_instead_of_black_error(self):
        import mss
        window = windows.GameWindow.__new__(windows.GameWindow)
        original = AutomationError("invalid geometry")
        window.guard = Mock(side_effect=original)
        with patch.object(mss, "mss") as capture, self.assertRaises(AutomationError) as raised:
            window.capture()
        self.assertIs(raised.exception, original)
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
