"""Daily completion is terminal; all capture, OCR, and Win32 input are mocked."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autozzz.app import Context, Runner
from autozzz.config import Settings
from autozzz.models import AutomationError, FocusLost, Scene, Stopped, TaskResult


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "runs" / "offline-tests"


class CompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def verify_daily(runner):
        # This represents the workflow's compound proof: 400 AND all claims.
        runner.report.evidence.append("verified_daily_four_claims.png")
        runner.report.engagement = 400
        runner.report.daily_verified = True

    def run_mock(self, configure=None, settings=None, emit=None, *, report_write_error=None,
                 close_error=None, close_result=1, expected_error=None):
        runner = Runner(settings or Settings(), emit)
        workflow = Mock()
        workflow.daily_already_complete.return_value = False
        for task in ("scratch", "coffee", "video", "daily"):
            getattr(workflow, task).return_value = TaskResult(task, "ok", "步骤已完成。")
        context = Mock()
        context.report = runner.report
        context.last_scene = None
        context.snapshot.return_value = Scene([])
        window = Mock()
        if configure:
            configure(runner, workflow, context, window)
        write_text = Path.write_text

        def write_report(path, *args, **kwargs):
            if path.name == "report.json" and report_write_error is not None:
                raise report_write_error
            return write_text(path, *args, **kwargs)

        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory, \
                patch("autozzz.app.ROOT", Path(directory)), \
                patch("autozzz.app.find_game", return_value=window), \
                patch("autozzz.app.is_elevated", return_value=False), \
                patch("autozzz.app.Vision"), \
                patch("autozzz.app.Context", return_value=context), \
                patch("autozzz.workflows.DailyWorkflow", return_value=workflow), \
                patch("autozzz.app.threading.Thread"), \
                patch("autozzz.app.C.get_last_error", return_value=0), \
                patch("autozzz.app.Path.write_text", autospec=True, side_effect=write_report), \
                patch("autozzz.windows.kernel32") as kernel:
            kernel.CreateMutexW.return_value = 123
            kernel.CloseHandle.return_value = close_result
            kernel.CloseHandle.side_effect = close_error
            if expected_error is None:
                report = runner.run()
            else:
                with self.assertRaises(type(expected_error)) as raised:
                    runner.run()
                self.assertIs(raised.exception, expected_error)
                report = runner.report
            report_path = Path(report.run_dir) / "report.json"
            stored = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
            logs = (Path(report.run_dir) / "run.log").read_text(encoding="utf-8")
        return runner, report, stored, logs, workflow, context, window

    def assert_daily_success(self, report, stored, selected=("scratch", "coffee", "video", "daily")):
        self.assertEqual(report.status, "success")
        self.assertTrue(report.daily_verified)
        self.assertEqual(report.engagement, 400)
        self.assertEqual(report.stamina_spent, 0)
        self.assertEqual([result.task for result in report.results], list(selected))
        self.assertTrue(all(result.status == "ok" for result in report.results))
        self.assertEqual(stored["status"], "success")
        self.assertTrue(stored["daily_verified"])

    def test_verified_precheck_stops_before_every_selected_action(self):
        events = []

        def configure(runner, workflow, context, window):
            workflow.daily_already_complete.side_effect = lambda: self.verify_daily(runner) or True
            for task in ("scratch", "coffee", "video", "daily"):
                getattr(workflow, task).side_effect = AssertionError("No task may run after proof.")

        _, report, stored, _, workflow, context, window = self.run_mock(configure, emit=events.append)
        self.assert_daily_success(report, stored)
        workflow.daily_already_complete.assert_called_once_with()
        context.snapshot.assert_called_once_with()
        context.evidence.assert_called_once()
        window.focus.assert_called_once_with()
        self.assertFalse(any(event.get("status") == "running" for event in events))
        self.assertEqual(len([event for event in events if event["type"] == "task"]), 4)

    def test_precheck_false_still_executes_unfinished_steps(self):
        _, report, _, _, workflow, _, _ = self.run_mock()
        workflow.daily_already_complete.assert_called_once_with()
        for task in ("scratch", "coffee", "video", "daily"):
            getattr(workflow, task).assert_called_once_with()
        self.assertEqual(report.status, "partial")
        self.assertFalse(report.daily_verified)

    def test_numeric_400_without_claim_verification_does_not_stop(self):
        def configure(runner, workflow, context, window):
            runner.report.engagement = 400

        _, report, _, _, workflow, _, _ = self.run_mock(configure)
        workflow.video.assert_called_once_with()
        workflow.daily.assert_called_once_with()
        self.assertEqual(report.status, "partial")
        self.assertFalse(report.daily_verified)

    def test_claim_flag_without_400_does_not_stop(self):
        def configure(runner, workflow, context, window):
            runner.report.daily_verified = True
            runner.report.engagement = 300

        _, report, _, _, workflow, _, _ = self.run_mock(configure)
        workflow.video.assert_called_once_with()
        self.assertNotEqual(report.engagement, 400)
        self.assertEqual(report.status, "partial")

    def test_precheck_boolean_alone_cannot_publish_success(self):
        def configure(runner, workflow, context, window):
            workflow.daily_already_complete.return_value = True

        _, report, _, _, workflow, _, _ = self.run_mock(configure)
        workflow.video.assert_called_once_with()
        self.assertFalse(report.daily_verified)

    def test_screenshot_regression_full_daily_inside_scratch_never_enters_video(self):
        def configure(runner, workflow, context, window):
            def scratch():
                self.verify_daily(runner)
                return TaskResult("scratch", "ok", "每日已满 400 活跃度且全部奖励已领取。")
            workflow.scratch.side_effect = scratch
            workflow.video.side_effect = FocusLost("游戏窗口失去焦点。")

        _, report, stored, _, workflow, _, _ = self.run_mock(configure)
        self.assert_daily_success(report, stored)
        workflow.coffee.assert_not_called()
        workflow.video.assert_not_called()
        workflow.daily.assert_not_called()

    def test_unchecked_reward_task_does_not_disable_terminal_daily_goal(self):
        settings = Settings(scratch=False, coffee=False, video=True, daily=False)

        def configure(runner, workflow, context, window):
            workflow.daily_already_complete.side_effect = lambda: self.verify_daily(runner) or True

        _, report, stored, _, workflow, _, _ = self.run_mock(configure, settings)
        self.assert_daily_success(report, stored, selected=("video",))
        workflow.video.assert_not_called()

    def test_unchecked_daily_still_stops_after_mid_task_verified_goal(self):
        settings = Settings(daily=False)

        def configure(runner, workflow, context, window):
            def coffee():
                self.verify_daily(runner)
                return TaskResult("coffee", "ok", "每日已满 400。")
            workflow.coffee.side_effect = coffee

        _, report, stored, _, workflow, _, _ = self.run_mock(configure, settings)
        self.assert_daily_success(report, stored, selected=("scratch", "coffee", "video"))
        workflow.scratch.assert_called_once_with()
        workflow.coffee.assert_called_once_with()
        workflow.video.assert_not_called()

    def test_verified_goal_overrides_prior_partial_but_keeps_reason_in_log(self):
        def configure(runner, workflow, context, window):
            workflow.scratch.return_value = TaskResult("scratch", "partial", "未识别到可领取盲盒。")
            workflow.coffee.side_effect = lambda: self.verify_daily(runner) or TaskResult("coffee", "ok", "饮品完成。")

        _, report, stored, logs, _, _, _ = self.run_mock(configure)
        self.assert_daily_success(report, stored)
        self.assertIn("未识别到可领取盲盒", logs)
        self.assertIn("无需继续执行", report.results[0].detail)

    def test_stop_after_verified_proof_preserves_success(self):
        def configure(runner, workflow, context, window):
            def scratch():
                self.verify_daily(runner)
                runner.stop()
                raise Stopped("任务已停止。")
            workflow.scratch.side_effect = scratch

        _, report, stored, _, workflow, _, _ = self.run_mock(configure)
        self.assert_daily_success(report, stored)
        workflow.coffee.assert_not_called()

    def test_focus_loss_and_other_errors_after_proof_preserve_success(self):
        for error in (FocusLost("失去焦点"), AutomationError("识别画面过期"), RuntimeError("通知异常")):
            with self.subTest(error=type(error).__name__):
                def configure(runner, workflow, context, window):
                    def scratch():
                        self.verify_daily(runner)
                        raise error
                    workflow.scratch.side_effect = scratch
                _, report, stored, logs, workflow, context, _ = self.run_mock(configure)
                self.assert_daily_success(report, stored)
                workflow.coffee.assert_not_called()
                self.assertIn(str(error), logs)
                context.evidence.assert_called_once()

    def test_ui_callback_failures_after_proof_cannot_reverse_completion(self):
        events = []

        def emit(event):
            events.append(event)
            if event["type"] in ("task", "finished"):
                raise RuntimeError("GUI callback is unavailable")

        def configure(runner, workflow, context, window):
            workflow.daily_already_complete.side_effect = lambda: self.verify_daily(runner) or True

        _, report, stored, logs, _, _, _ = self.run_mock(configure, emit=emit)
        self.assert_daily_success(report, stored)
        self.assertIn("界面通知失败", logs)
        self.assertTrue(any(event["type"] == "finished" for event in events))

    def test_stop_before_proof_remains_stopped(self):
        def configure(runner, workflow, context, window):
            workflow.daily_already_complete.side_effect = Stopped("任务已停止。")

        _, report, stored, _, workflow, _, _ = self.run_mock(configure)
        self.assertEqual(report.status, "stopped")
        self.assertEqual(stored["status"], "stopped")
        self.assertFalse(report.daily_verified)
        workflow.scratch.assert_not_called()

    def test_focus_loss_before_proof_remains_failed(self):
        def configure(runner, workflow, context, window):
            runner.report.engagement = 400
            workflow.scratch.side_effect = FocusLost("游戏窗口失去焦点。")

        _, report, stored, _, workflow, _, _ = self.run_mock(configure)
        self.assertEqual(report.status, "failed")
        self.assertEqual(stored["status"], "failed")
        self.assertFalse(report.daily_verified)
        workflow.coffee.assert_not_called()

    def test_ui_callback_failure_before_proof_remains_failed(self):
        def emit(event):
            if event["type"] == "task" and event.get("status") == "running":
                raise RuntimeError("GUI callback is unavailable")

        _, report, stored, _, workflow, _, _ = self.run_mock(emit=emit)
        self.assertEqual(report.status, "failed")
        self.assertEqual(stored["status"], "failed")
        self.assertFalse(report.daily_verified)
        workflow.scratch.assert_not_called()

    def test_report_write_failure_after_proof_still_returns_and_emits_success(self):
        events = []

        def configure(runner, workflow, context, window):
            workflow.daily_already_complete.side_effect = lambda: self.verify_daily(runner) or True

        _, report, stored, logs, _, _, _ = self.run_mock(
            configure, emit=events.append, report_write_error=PermissionError("report directory is read only"))
        self.assertIsNone(stored)
        finished = [event["report"] for event in events if event["type"] == "finished"]
        self.assertEqual(len(finished), 1)
        self.assert_daily_success(report, finished[0])
        self.assertIn("运行报告保存失败不影响完成结果", logs)
        self.assertIn("WARNING", logs)

    def test_mutex_close_exception_after_proof_still_saves_and_emits_success(self):
        events = []

        def configure(runner, workflow, context, window):
            workflow.daily_already_complete.side_effect = lambda: self.verify_daily(runner) or True

        _, report, stored, logs, _, _, _ = self.run_mock(
            configure, emit=events.append, close_error=RuntimeError("CloseHandle failed"))
        self.assert_daily_success(report, stored)
        self.assertEqual([event["report"]["status"] for event in events if event["type"] == "finished"], ["success"])
        self.assertIn("运行锁释放失败不影响完成结果", logs)

    def test_mutex_close_false_after_proof_is_reported_without_reversing_success(self):
        def configure(runner, workflow, context, window):
            workflow.daily_already_complete.side_effect = lambda: self.verify_daily(runner) or True

        _, report, stored, logs, _, _, _ = self.run_mock(configure, close_result=0)
        self.assert_daily_success(report, stored)
        self.assertIn("Windows 运行锁释放失败", logs)

    def test_report_write_failure_without_proof_is_failed_and_raised(self):
        events = []
        error = PermissionError("report directory is read only")
        _, report, stored, _, _, _, _ = self.run_mock(
            emit=events.append, report_write_error=error, expected_error=error)
        self.assertIsNone(stored)
        self.assertEqual(report.status, "failed")
        self.assertFalse(report.daily_verified)
        self.assertEqual([event["report"]["status"] for event in events if event["type"] == "finished"], ["failed"])

    def test_mutex_close_exception_without_proof_is_failed_and_raised(self):
        events = []
        error = RuntimeError("CloseHandle failed")
        _, report, stored, _, _, _, _ = self.run_mock(
            emit=events.append, close_error=error, expected_error=error)
        self.assertEqual(report.status, "failed")
        self.assertEqual(stored["status"], "failed")
        self.assertFalse(report.daily_verified)
        self.assertEqual([event["report"]["status"] for event in events if event["type"] == "finished"], ["failed"])


class ScrollBoundsTests(unittest.TestCase):
    def test_invalid_scroll_is_rejected_before_any_win32_input(self):
        runner = Runner(Settings())
        window = Mock()
        context = Context(runner, window, Mock())
        for amount in (True, False, 0, 1, 119, 480, -480, 120.0, "120", None):
            with self.subTest(amount=amount), self.assertRaises(AutomationError):
                context.scroll(amount)
        window.move.assert_not_called()
        window.scroll.assert_not_called()

    def test_stale_scroll_anchor_blocks_valid_amount_before_input(self):
        runner = Runner(Settings())
        window = Mock()
        context = Context(runner, window, Mock())
        frame = Scene([], geometry=object(), captured_at=time.monotonic() - 9)
        context.last_scene = frame
        context._scroll_anchor = ((0.8, 0.6), frame.geometry, 0)
        with self.assertRaises(AutomationError):
            context.scroll(-360)
        window.move.assert_not_called()
        window.scroll.assert_not_called()

    def test_valid_bounded_scroll_forwards_three_native_ticks(self):
        runner = Runner(Settings())
        window = Mock()
        context = Context(runner, window, Mock())
        frame = Scene([], geometry=object(), captured_at=time.monotonic())
        context.last_scene = frame
        context._scroll_anchor = ((0.8, 0.6), frame.geometry, 0)
        context.sleep = Mock()
        context.scroll(-360)
        window.move.assert_called_once_with(0.8, 0.6, frame.geometry)
        window.scroll.assert_called_once_with(-360)
        self.assertIsNone(context.last_scene)


if __name__ == "__main__":
    unittest.main()
