"""Entry is successful only after main-screen proof; never performs dailies."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from autozzz.app import Runner
from autozzz.config import Settings
from autozzz.models import AutomationError, RunReport, Scene, Stopped, TaskResult
from autozzz.gui import AutoZZZWindow
from tests.test_reward_gui import window_state


class LaunchOperationTests(unittest.TestCase):
    def run_entry(self, result=None, error=None, stop=False):
        settings = Settings(auto_launch=False)
        events = []
        runner = Runner(settings, events.append)
        runner._prepare_window = Mock()
        workflow = Mock()
        workflow.ensure_main.return_value = result or TaskResult("main", "ok", "已识别主界面")
        workflow.ensure_main.side_effect = error
        context = Mock(last_scene=Scene([]))
        context.snapshot.return_value = Scene([])
        if stop:
            runner.stop()
        with tempfile.TemporaryDirectory() as directory, \
                patch("autozzz.app.ROOT", Path(directory)), \
                patch("autozzz.app.launch_game", return_value="启动请求已发送") as launch, \
                patch("autozzz.app.find_game"), patch("autozzz.app.is_elevated", return_value=False), \
                patch("autozzz.app.Vision"), patch("autozzz.app.Context", return_value=context), \
                patch("autozzz.app.threading.Thread"), \
                patch("autozzz.workflows.DailyWorkflow", return_value=workflow), \
                patch("autozzz.app.C.get_last_error", return_value=0), \
                patch("autozzz.windows.kernel32"):
            report = runner.enter_game()
            stored = json.loads((Path(report.run_dir) / "report.json").read_text(encoding="utf-8"))
        for task in ("scratch", "coffee", "video", "daily", "daily_already_complete"):
            getattr(workflow, task).assert_not_called()
        self.assertFalse(settings.auto_launch, "Entry must not change the saved daily launch preference.")
        self.assertFalse(report.daily_verified)
        self.assertIsNone(report.engagement)
        self.assertFalse(any(e.get("type") == "task" for e in events))
        return report, stored, launch, context

    def test_entry_launches_and_verifies_main_without_daily_or_cleanup(self):
        with patch.object(Runner, "_run_finish_rewards") as finish:
            report, stored, launch, context = self.run_entry()
        finish.assert_not_called()
        launch.assert_called_once()
        self.assertEqual(report.status, "success")
        self.assertEqual(stored["operation"], "enter_game")
        self.assertEqual([(r.task, r.status) for r in report.results], [("launch", "ok")])
        self.assertEqual(context.evidence.call_args.args[1], "entered_game")

    def test_process_launch_without_main_proof_is_failure(self):
        for result in (TaskResult("main", "partial", "等待登录"), TaskResult("launch", "ok", "仅启动")):
            with self.subTest(result=result):
                report, stored, launch, _ = self.run_entry(result)
                launch.assert_called_once()
                self.assertEqual(report.status, "failed")
                self.assertEqual(stored["status"], "failed")
                self.assertFalse(any(r.task == "launch" and r.status == "ok" for r in report.results))

    def test_login_error_and_stop_are_not_entry_success(self):
        for error, expected in ((AutomationError("请手动完成验证码"), "failed"), (Stopped("F10 已停止"), "stopped")):
            with self.subTest(expected=expected):
                report, stored, _, _ = self.run_entry(error=error)
                self.assertEqual(report.status, expected)
                self.assertEqual(stored["status"], expected)

    def test_stop_before_entry_does_not_launch(self):
        report, _, launch, _ = self.run_entry(stop=True)
        self.assertEqual(report.status, "stopped")
        launch.assert_not_called()

    def test_entry_gui_preserves_daily_rows_and_leaves_game_in_front(self):
        window = window_state()
        window.operation = "enter_game"
        original = dict(window.table.rows)
        window._finished(RunReport(operation="enter_game", status="success",
                                  results=[TaskResult("launch", "ok", "已到主界面")]))
        self.assertEqual(window.status.get(), "已进入游戏")
        self.assertEqual(window.table.rows, original)
        self.assertFalse(getattr(window, "_daily_goal_complete", False))
        window._restore.assert_not_called()

    def test_entry_gui_rejects_success_without_launch_result(self):
        window = window_state()
        window.operation = "enter_game"
        window._finished(RunReport(operation="enter_game", status="success"))
        self.assertEqual(window.status.get(), "进入游戏未完成")
        window._restore.assert_called_once()

    def test_launch_button_uses_entry_operation(self):
        window = AutoZZZWindow.__new__(AutoZZZWindow)
        window._start = Mock()
        window._launch()
        window._start.assert_called_once_with("enter_game")

    def test_cli_launch_uses_entry_instead_of_daily(self):
        from autozzz.__main__ import main
        with patch("autozzz.app.Runner") as runner, patch("autozzz.config.Settings.load", return_value=Settings()), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            runner.return_value.enter_game.return_value = RunReport(operation="enter_game", status="success")
            self.assertEqual(main(["--launch"]), 0)
            runner.return_value.enter_game.assert_called_once()
            runner.return_value.run.assert_not_called()
