"""Focus waits resume the current state; all desktop APIs and input are mocked."""
from __future__ import annotations

import ctypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autozzz import windows
from autozzz.app import Context, Runner
from autozzz.config import Settings
from autozzz.models import AutomationError, FocusLost, InputInterrupted, Scene, Stopped, TaskResult, Token
from autozzz.workflows import DailyWorkflow


GEOMETRY = windows.Geometry(0, 0, 1920, 1080)


def frame(text="确认", geometry=GEOMETRY, x=.5):
    return Scene([Token(text, .99, (x-.02, .49, x+.02, .51))], geometry=geometry,
                 captured_at=time.monotonic())


class VisibleWindow:
    def __init__(self):
        self.hwnd = 1
        self.ready = threading.Event()
        self.ready.set()
        self.geometry = self.baseline = GEOMETRY
        self.closed = self.minimized = self.emergency = False
        self.focus = Mock()
        self.click = Mock()
        self.key = Mock()
        self.drag = Mock()
        self.move = Mock()
        self.scroll = Mock()
        self.refreshes = []
        self.capture = Mock(side_effect=lambda: (object(), self.guard()))

    def emergency_pressed(self):
        return self.emergency

    def guard(self, expected=None, *, refresh=False):
        if self.closed:
            raise AutomationError("游戏窗口已关闭")
        if self.minimized or not self.ready.is_set():
            raise FocusLost("等待游戏回到前台")
        if not refresh and self.geometry != self.baseline:
            raise AutomationError("坐标变化")
        if expected and expected != self.geometry:
            raise AutomationError("旧坐标")
        if refresh:
            self.refreshes.append(self.geometry)
        return self.geometry


class FocusResumeTests(unittest.TestCase):
    def make_context(self):
        events = []
        waiting = threading.Event()
        def emit(event):
            events.append(event)
            if event.get("type") == "focus_wait" and event["waiting"]:
                waiting.set()
        runner = Runner(Settings(action_delay=.3), emit)
        window = VisibleWindow()
        runner._window = window
        vision = Mock()
        vision.read.side_effect = lambda image, geometry: frame(geometry=geometry)
        context = Context(runner, window, vision)
        return context, runner, window, events, waiting

    def worker(self, call):
        errors, results = [], []
        def run():
            try:
                results.append(call())
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, errors, results

    def finish(self, thread, runner, errors):
        thread.join(2)
        if thread.is_alive():
            runner.stop()
            thread.join(2)
        self.assertFalse(thread.is_alive(), "Waiting worker must remain cancellable")
        self.assertEqual(errors, [])

    def test_temporary_focus_loss_waits_and_resumes_without_stealing_focus(self):
        _, runner, window, events, waiting = self.make_context()
        window.ready.clear()
        thread, errors, _ = self.worker(runner.checkpoint)
        self.assertTrue(waiting.wait(1))
        self.assertTrue(thread.is_alive())
        window.click.assert_not_called()
        window.focus.assert_not_called()
        window.ready.set()
        self.finish(thread, runner, errors)
        self.assertEqual([e["waiting"] for e in events if e["type"] == "focus_wait"], [True, False])
        self.assertFalse(runner._pause.is_set())
        self.assertEqual(runner.input_epoch, 2)
        self.assertGreater(runner.inactive_duration, 0)

    def test_minimization_waits_until_restored_then_refreshes_geometry(self):
        _, runner, window, _, waiting = self.make_context()
        window.minimized = True
        thread, errors, _ = self.worker(runner.checkpoint)
        self.assertTrue(waiting.wait(1))
        window.geometry = windows.Geometry(100, 50, 1280, 720)
        window.minimized = False
        self.finish(thread, runner, errors)
        self.assertEqual(window.baseline, window.geometry)
        self.assertTrue(window.refreshes)
        window.focus.assert_not_called()

    def test_initial_focus_request_failure_waits_instead_of_failing_or_stealing_focus(self):
        _, runner, window, _, waiting = self.make_context()
        window.focus.side_effect = FocusLost("lost during initial focus request")
        window.ready.clear()
        thread, errors, _ = self.worker(runner._prepare_window)
        self.assertTrue(waiting.wait(1))
        window.geometry = windows.Geometry(20, 30, 1280, 720)
        window.ready.set()
        self.finish(thread, runner, errors)
        self.assertEqual(window.baseline, window.geometry)
        window.focus.assert_called_once()

    def test_stop_cancels_focus_wait(self):
        _, runner, window, events, waiting = self.make_context()
        window.ready.clear()
        thread, errors, _ = self.worker(runner.checkpoint)
        self.assertTrue(waiting.wait(1))
        runner.stop()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Stopped)
        self.assertEqual(events[-1], {"type": "focus_wait", "waiting": False})
        window.click.assert_not_called()

    def test_f10_cancels_wait_even_before_hotkey_thread_starts(self):
        _, runner, window, _, _ = self.make_context()
        window.ready.clear()
        window.emergency = True
        with self.assertRaises(Stopped):
            runner.checkpoint()
        self.assertTrue(runner._stop.is_set())
        self.assertFalse(runner._focus_waiting)

    def test_closed_window_is_an_error_instead_of_an_infinite_focus_wait(self):
        _, runner, window, events, _ = self.make_context()
        window.closed = True
        with self.assertRaisesRegex(AutomationError, "关闭") as error:
            runner.checkpoint()
        self.assertNotIsInstance(error.exception, FocusLost)
        self.assertFalse(any(e["type"] == "focus_wait" for e in events))

    def test_closing_game_while_waiting_ends_wait(self):
        _, runner, window, _, waiting = self.make_context()
        window.ready.clear()
        thread, errors, _ = self.worker(runner.checkpoint)
        self.assertTrue(waiting.wait(1))
        window.closed = True
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], AutomationError)
        self.assertNotIsInstance(errors[0], FocusLost)

    def test_focus_restore_does_not_cancel_manual_pause(self):
        _, runner, window, _, waiting = self.make_context()
        window.ready.clear()
        thread, errors, _ = self.worker(runner.checkpoint)
        self.assertTrue(waiting.wait(1))
        runner.toggle_pause()
        window.ready.set()
        thread.join(.15)
        self.assertTrue(thread.is_alive())
        self.assertTrue(runner._pause.is_set())
        runner.toggle_pause()
        self.finish(thread, runner, errors)
        window.focus.assert_not_called()

    def test_manual_resume_outside_game_stays_in_focus_wait(self):
        _, runner, window, events, waiting = self.make_context()
        runner.toggle_pause()
        window.ready.clear()
        thread, errors, _ = self.worker(runner.checkpoint)
        runner.toggle_pause()
        self.assertTrue(waiting.wait(1))
        self.assertTrue(thread.is_alive())
        self.assertEqual([e["paused"] for e in events if e["type"] == "paused"], [True, False])
        window.ready.set()
        self.finish(thread, runner, errors)
        window.focus.assert_not_called()

    def test_focus_loss_during_capture_discards_image_and_retries(self):
        context, runner, window, _, waiting = self.make_context()
        window.capture.side_effect = [FocusLost("capture blur"), (object(), GEOMETRY)]
        window.ready.clear()
        thread, errors, results = self.worker(context.snapshot)
        self.assertTrue(waiting.wait(1))
        window.ready.set()
        self.finish(thread, runner, errors)
        self.assertEqual(window.capture.call_count, 2)
        self.assertEqual(context.vision.read.call_count, 1)
        self.assertIs(results[0], context.last_scene)

    def test_focus_loss_during_ocr_discards_old_pixels_and_old_geometry(self):
        context, runner, window, _, waiting = self.make_context()
        old, fresh = frame("旧目标"), frame("新目标", windows.Geometry(50, 20, 1280, 720), .7)
        def read(*_):
            if context.vision.read.call_count == 1:
                window.ready.clear()
                return old
            return fresh
        context.vision.read.side_effect = read
        thread, errors, results = self.worker(context.snapshot)
        self.assertTrue(waiting.wait(1))
        window.geometry = fresh.geometry
        window.ready.set()
        self.finish(thread, runner, errors)
        self.assertEqual(context.vision.read.call_count, 2)
        self.assertIs(results[0], fresh)
        self.assertIsNot(context.last_scene, old)
        self.assertEqual(context.scene_epoch, runner.input_epoch)

    def test_pending_click_uses_new_scene_and_new_coordinates_after_resume(self):
        context, runner, window, _, waiting = self.make_context()
        old = context.snapshot()
        fresh = frame("确认", windows.Geometry(10, 20, 1280, 720), .7)
        context.vision.read.return_value = fresh
        context.vision.read.side_effect = None
        attempts = []
        def step():
            attempts.append(1)
            scene = old if len(attempts) == 1 else context.snapshot()
            context.click(scene, scene.tokens[0])
        window.ready.clear()
        thread, errors, _ = self.worker(lambda: runner._resume_step(step))
        self.assertTrue(waiting.wait(1))
        window.geometry = fresh.geometry
        window.ready.set()
        self.finish(thread, runner, errors)
        self.assertEqual(len(attempts), 2)
        window.click.assert_called_once_with(*fresh.tokens[0].center, fresh.geometry)

    def test_click_already_sent_is_not_replayed_after_focus_loss(self):
        context, runner, window, _, waiting = self.make_context()
        scene = context.snapshot()
        window.click.side_effect = lambda *_: window.ready.clear()
        thread, errors, _ = self.worker(lambda: runner._resume_step(lambda: context.click(scene, scene.tokens[0])))
        self.assertTrue(waiting.wait(1))
        window.ready.set()
        self.finish(thread, runner, errors)
        window.click.assert_called_once()
        self.assertIsNone(context.last_scene)

    def test_focus_race_before_click_reidentifies_without_sending_old_target(self):
        context, runner, window, _, _ = self.make_context()
        scene = context.snapshot()
        window.click.side_effect = FocusLost("lost immediately before SendInput")
        with self.assertRaises(InputInterrupted):
            context.click(scene, scene.tokens[0])
        self.assertIsNone(context.last_scene)
        window.click.assert_called_once()
        window.focus.assert_not_called()

    def test_wait_deadline_excludes_focus_wait_inside_snapshot(self):
        context, runner, _, _, _ = self.make_context()
        clock = [0.]
        scenes = [frame("加载"), frame("完成")]
        def capture():
            scene = scenes.pop(0)
            if scene.has("加载"):
                clock[0] += 100
                runner.inactive_duration += 100
            return scene
        context.snapshot = Mock(side_effect=capture)
        context.sleep = Mock()
        with patch("autozzz.app.time.monotonic", side_effect=lambda: clock[0]):
            result = context.wait(lambda s: s.has("完成"), timeout=1)
        self.assertTrue(result.has("完成"))
        self.assertEqual(context.snapshot.call_count, 2)

    def test_crossing_daily_reset_while_waiting_stops_before_input(self):
        _, runner, window, _, _ = self.make_context()
        runner.report.reset_day = "2026-10-10"
        window.ready.clear()
        with patch("autozzz.app.datetime") as clock:
            clock.now.side_effect = [datetime(2026, 10, 11, 3, 59, tzinfo=timezone(timedelta(hours=8))),
                                     datetime(2026, 10, 11, 4, 1, tzinfo=timezone(timedelta(hours=8)))]
            with self.assertRaises(Stopped):
                runner.checkpoint()
        window.click.assert_not_called()

    def test_only_current_task_retries_and_successful_task_is_retained(self):
        from tests.test_completion import CompletionTests
        calls = []
        def configure(runner, workflow, context, window):
            def coffee():
                calls.append("coffee")
                if len(calls) == 1:
                    raise InputInterrupted("old pending target discarded")
                return TaskResult("coffee", "ok", "恢复后完成")
            workflow.coffee.side_effect = coffee
        _, report, _, _, workflow, _, _ = CompletionTests().run_mock(configure,
            Settings(scratch=True, coffee=True, video=False, daily=False))
        self.assertEqual(report.status, "success")
        self.assertEqual([(r.task, r.status) for r in report.results], [("scratch", "ok"), ("coffee", "ok")])
        workflow.scratch.assert_called_once()
        self.assertEqual(workflow.coffee.call_count, 2)

    def assert_held_input_releases_before_wait(self, drag):
        context, runner, window, _, waiting = self.make_context()
        scene = frame("每日可刮取一次" if drag else "六分街")
        context.last_scene = scene
        api = Mock()
        api.MapVirtualKeyW.return_value = 17
        released = threading.Event()
        releases = []
        def release(count, ptr, size):
            releases.append(ctypes.cast(ptr, ctypes.POINTER(windows.INPUT))[0])
            released.set()
            return 1
        api.SendInput.side_effect = release
        window.send = Mock(side_effect=lambda *_: window.ready.clear())
        if drag:
            window.move.side_effect = lambda x, y, expected: window.guard(expected)
            window.drag = types.MethodType(windows.GameWindow.drag, window)
            call = lambda: context.drag(scene, (.4, .4), (.6, .6))
        else:
            window.key = types.MethodType(windows.GameWindow.key, window)
            call = lambda: context.key("w", scene, duration=.4)
        with patch.object(windows, "user32", api):
            thread, errors, _ = self.worker(call)
            self.assertTrue(released.wait(1))
            self.assertTrue(waiting.wait(1))
            self.assertTrue(thread.is_alive())
            window.ready.set()
            self.finish(thread, runner, errors)
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0].type, 0 if drag else 1)
        self.assertTrue((releases[0].mi.dwFlags & 4) if drag else (releases[0].ki.dwFlags & 2))
        window.send.assert_called_once()

    def test_held_movement_key_releases_before_automatic_wait_without_replay(self):
        self.assert_held_input_releases_before_wait(False)

    def test_scratch_drag_releases_before_automatic_wait_without_replay(self):
        self.assert_held_input_releases_before_wait(True)

    def test_coffee_animation_resume_does_not_order_a_second_drink(self):
        from tests.test_core import WorkflowContext, calibrated_coffee_dialogue, calibrated_coffee_reward, world_with_npc
        context = WorkflowContext([calibrated_coffee_dialogue(), calibrated_coffee_reward(), world_with_npc()])
        workflow = DailyWorkflow(context)
        finish = workflow._finish_coffee
        workflow._finish_coffee = Mock(side_effect=InputInterrupted("animation target invalidated"))
        with self.assertRaises(InputInterrupted):
            workflow.coffee()
        self.assertTrue(workflow._coffee_ordered)
        workflow._finish_coffee = finish
        result = workflow.coffee()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [("click", "来杯汀曼特调！"), ("click", "确认")])

    def test_coffee_reward_claim_resume_keeps_proof_without_returning_to_order(self):
        from tests.test_core import WorkflowContext, calibrated_coffee_reward, world_with_npc
        context = WorkflowContext([calibrated_coffee_reward(), world_with_npc()])
        workflow = DailyWorkflow(context)
        capture = context.snapshot
        calls = []
        def interrupted_capture():
            calls.append(1)
            if len(calls) == 2:
                raise InputInterrupted("paused after reward confirmation")
            return capture()
        context.snapshot = interrupted_capture
        with self.assertRaises(InputInterrupted):
            workflow.coffee()
        self.assertTrue(workflow._coffee_ordered)
        self.assertTrue(workflow._coffee_reward_claimed)
        result = workflow.coffee()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [("click", "确认")])

    def test_first_startup_pause_preserves_read_only_loading_wait_on_retry(self):
        from tests.test_core import WorkflowContext, scene, world_with_npc
        for phase in ("capture", "loading_wait"):
            with self.subTest(phase=phase):
                context = WorkflowContext([scene(), scene(), world_with_npc()])
                context.settings.auto_launch = True
                capture, wait = context.snapshot, context.wait
                if phase == "capture":
                    count = [0]
                    def snapshot():
                        count[0] += 1
                        if count[0] == 1:
                            raise InputInterrupted("manual pause interrupted startup capture")
                        return capture()
                    context.snapshot = snapshot
                    context.wait = Mock(wraps=wait)
                else:
                    count = [0]
                    def loading_wait(*args, **kwargs):
                        count[0] += 1
                        if count[0] == 1:
                            raise InputInterrupted("manual pause interrupted startup loading")
                        return wait(*args, **kwargs)
                    context.wait = Mock(side_effect=loading_wait)
                workflow = DailyWorkflow(context)
                with self.assertRaises(InputInterrupted):
                    workflow.ensure_main()
                self.assertFalse(workflow._startup_checked)
                result = workflow.ensure_main()
                self.assertEqual(result.status, "ok")
                self.assertTrue(workflow._startup_checked)
                self.assertEqual(context.actions, [])
                self.assertEqual(context.wait.call_count, 1 if phase == "capture" else 2)

    def test_confirmed_video_success_survives_interrupted_wait_without_reopening_store(self):
        from tests.test_core import WorkflowContext, calibrated_video_opened, world_with_npc
        opened, world = calibrated_video_opened(), world_with_npc()
        context = WorkflowContext([opened, world])
        context.wait = Mock(side_effect=InputInterrupted("manual pause after opening confirmation"))
        workflow = DailyWorkflow(context)
        with self.assertRaises(InputInterrupted):
            workflow.video()
        self.assertTrue(workflow._video_opened)
        workflow._daily_jump = Mock(side_effect=AssertionError("completed store must not navigate or reopen"))
        result = workflow.video()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [("click", "确认")])
        self.assertEqual(context.evidence_frames[0], (opened, "video_opened"))
        workflow._daily_jump.assert_not_called()


class WindowFocusClassificationTests(unittest.TestCase):
    def make_api(self):
        game = windows.GameWindow.__new__(windows.GameWindow)
        game.hwnd, game.baseline = 1, GEOMETRY
        api = Mock()
        api.IsWindow.return_value = True
        api.IsIconic.return_value = False
        api.GetForegroundWindow.return_value = 1
        api.GetSystemMetrics.side_effect = lambda n: {76: 0, 77: 0, 78: 3840, 79: 2160}[n]
        return game, api

    def test_real_window_guard_distinguishes_closed_minimized_and_blurred(self):
        game, api = self.make_api()
        with patch.object(windows, "user32", api), patch.object(windows, "geometry", return_value=GEOMETRY):
            api.IsIconic.return_value = True
            with self.assertRaises(FocusLost):
                game.guard()
            api.IsIconic.return_value = False
            api.GetForegroundWindow.return_value = 2
            with self.assertRaises(FocusLost):
                game.guard()
            api.IsWindow.return_value = False
            with self.assertRaisesRegex(AutomationError, "关闭") as error:
                game.guard()
            self.assertNotIsInstance(error.exception, FocusLost)

    def test_refresh_geometry_still_requires_visible_valid_16_by_9_client(self):
        game, api = self.make_api()
        with patch.object(windows, "user32", api), patch.object(windows, "geometry") as read:
            fresh = windows.Geometry(20, 30, 1280, 720)
            read.return_value = fresh
            with self.assertRaises(AutomationError):
                game.guard()
            self.assertEqual(game.guard(refresh=True), fresh)
            for invalid in (windows.Geometry(0, 0, 800, 450), windows.Geometry(0, 0, 1280, 800),
                            windows.Geometry(3800, 0, 1280, 720)):
                read.return_value = invalid
                with self.subTest(geometry=invalid), self.assertRaises(AutomationError):
                    game.guard(refresh=True)

    def test_zero_client_during_restore_is_temporarily_unavailable_before_baseline_checks(self):
        game, api = self.make_api()
        with patch.object(windows, "user32", api), patch.object(windows, "geometry") as read:
            for transient in (windows.Geometry(0, 0, 0, 0), windows.Geometry(0, 0, 1920, 0),
                              windows.Geometry(0, 0, 0, 1080)):
                read.return_value = transient
                for refresh in (False, True):
                    with self.subTest(geometry=transient, refresh=refresh), self.assertRaises(FocusLost):
                        game.guard(refresh=refresh)
                with self.assertRaises(FocusLost):
                    game.guard(GEOMETRY)
            api.IsWindow.return_value = False
            with self.assertRaisesRegex(AutomationError, "关闭") as error:
                game.guard(refresh=True)
            self.assertNotIsInstance(error.exception, FocusLost)

    def test_runner_waits_through_zero_client_then_resumes_with_valid_geometry(self):
        game, api = self.make_api()
        api.GetAsyncKeyState.return_value = 0
        events = []
        runner = Runner(Settings(), events.append)
        runner._window = game
        fresh = windows.Geometry(50, 30, 1280, 720)
        transient = windows.Geometry(0, 0, 0, 0)
        with patch.object(windows, "user32", api), \
                patch.object(windows, "geometry", side_effect=[transient, transient, fresh, fresh]) as read:
            runner.checkpoint()
        self.assertEqual(read.call_count, 4)
        self.assertEqual(game.baseline, fresh)
        self.assertEqual(runner.input_epoch, 2)
        self.assertEqual([e["waiting"] for e in events if e["type"] == "focus_wait"], [True, False])
        api.SendInput.assert_not_called()
        api.SetForegroundWindow.assert_not_called()


if __name__ == "__main__":
    unittest.main()
