"""Auto exit is opt-in, compound-proof gated, normal-close only; no live game."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from autozzz.app import Runner
from autozzz.closing import close_verified_game, exit_confirmation
from autozzz.config import Settings, GAME_PATHS
from autozzz.models import AutomationError, Stopped, TaskResult
from autozzz.windows import GameExitWatch, GameWindow
from tests import test_completion
from tests.test_core import scene, token


class AutoCloseConfigurationTests(unittest.TestCase):
    def test_default_legacy_and_round_trip_preserve_choices(self):
        self.assertFalse(Settings().close_game_on_success)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = {"server": "global", "game_path": GAME_PATHS["global"], "promoter": "薇薇安", "auto_launch": True}
            path.write_text(json.dumps(original), encoding="utf-8")
            settings = Settings.load(path)
            self.assertFalse(settings.close_game_on_success)
            for enabled in (True, False):
                changed = replace(settings, close_game_on_success=enabled)
                changed.save(path)
                self.assertEqual(Settings.load(path), changed)
                self.assertEqual(changed.promoter, "薇薇安")
                self.assertTrue(changed.auto_launch)

    def test_flag_requires_boolean(self):
        for value in (1, 0, "false", "true", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings(close_game_on_success=value).validate()


class AutoCloseRunnerTests(unittest.TestCase):
    def run_case(self, configure=None, enabled=True, cleanup=None):
        harness = test_completion.CompletionTests()
        harness.setUpClass()
        def prepare(runner, workflow, context, window):
            workflow.daily_already_complete.side_effect = lambda: harness.verify_daily(runner) or True
            if configure:
                configure(runner, workflow, context, window)
        with patch("autozzz.closing.close_verified_game", return_value=TaskResult("close_game", "ok", "正常退出")) as close:
            if cleanup is None:
                result = harness.run_mock(prepare, Settings(close_game_on_success=enabled))
            else:
                with patch.object(Runner, "_run_finish_rewards", cleanup):
                    result = harness.run_mock(prepare, Settings(close_game_on_success=enabled))
            calls = close.call_count
        return result, calls

    def test_opt_in_success_closes_once_and_saves_independent_result(self):
        (runner, report, stored, _, workflow, _, _), calls = self.run_case()
        self.assertEqual(calls, 1)
        self.assertEqual(stored["results"][-1]["task"], "close_game")
        self.assertEqual(report.status, "success")
        workflow.scratch.assert_not_called()

    def test_default_off_does_not_close(self):
        _, calls = self.run_case(enabled=False)
        self.assertEqual(calls, 0)

    def test_400_without_claim_proof_never_closes(self):
        def configure(runner, workflow, *_):
            workflow.daily_already_complete.side_effect = lambda: setattr(runner.report, "engagement", 400) or False
        (runner, report, *_), calls = self.run_case(configure)
        self.assertEqual(calls, 0)
        self.assertEqual(report.status, "partial")

    def test_failure_stop_and_error_after_proof_never_close(self):
        for after_proof in (False, True):
            for error in (AutomationError("failure"), Stopped("F10")):
                def configure(runner, workflow, *_):
                    def precheck():
                        if after_proof:
                            test_completion.CompletionTests.verify_daily(runner)
                        raise error
                    workflow.daily_already_complete.side_effect = precheck
                with self.subTest(proof=after_proof, error=type(error).__name__):
                    _, calls = self.run_case(configure)
                    self.assertEqual(calls, 0)

    def test_stop_flag_after_proof_prevents_close(self):
        def configure(runner, workflow, *_):
            workflow.daily_already_complete.side_effect = lambda: test_completion.CompletionTests.verify_daily(runner) or runner.stop() or True
        _, calls = self.run_case(configure)
        self.assertEqual(calls, 0)

    def test_earlier_partial_is_not_erased_by_later_compound_proof_for_auto_close(self):
        def configure(runner, workflow, *_):
            workflow.daily_already_complete.side_effect = None
            workflow.daily_already_complete.return_value = False
            workflow.scratch.return_value = TaskResult("scratch", "partial", "unknown")
            workflow.coffee.side_effect = lambda: test_completion.CompletionTests.verify_daily(runner) or TaskResult("coffee", "ok", "done")
        (_, report, *_), calls = self.run_case(configure)
        self.assertTrue(report.daily_verified)
        self.assertEqual(calls, 0)

    def test_selected_cleanup_must_have_actual_ok_result(self):
        for status in (None, "partial", "failed", "stopped", "skipped", "ok"):
            def configure(runner, *_):
                runner.settings.weekly = True
            def cleanup(runner, _):
                if status:
                    runner.report.results.append(TaskResult("weekly", status, "cleanup"))
            with self.subTest(status=status):
                _, calls = self.run_case(configure, cleanup=cleanup)
                self.assertEqual(calls, int(status == "ok"))

    def test_close_failure_keeps_daily_success_but_reports_unconfirmed_exit(self):
        harness = test_completion.CompletionTests()
        harness.setUpClass()
        def configure(runner, workflow, *_):
            workflow.daily_already_complete.side_effect = lambda: harness.verify_daily(runner) or True
        with patch("autozzz.closing.close_verified_game", side_effect=AutomationError("close denied")):
            _, report, stored, *_ = harness.run_mock(configure, Settings(close_game_on_success=True))
        self.assertEqual(report.status, "success")
        self.assertEqual(stored["results"][-1]["status"], "partial")

    def test_entry_operation_never_closes_even_with_enabled_setting_and_proof(self):
        runner = Runner(Settings(close_game_on_success=True))
        test_completion.CompletionTests.verify_daily(runner)
        runner._complete_daily()
        runner.report.operation = "enter_game"
        with patch("autozzz.closing.close_verified_game") as close:
            runner._close_game_after_success(Mock())
        close.assert_not_called()


class NormalExitTests(unittest.TestCase):
    def exit_scene(self):
        frame = scene()
        frame.tokens = [token("是否退出游戏？", .50, .40), token("取消", .40, .60), token("确认", .60, .60)]
        return frame

    def test_only_unique_exit_prompt_can_authorize_confirmation(self):
        frame = self.exit_scene()
        self.assertIs(exit_confirmation(frame), frame.tokens[2])
        for tokens in ([token("确认")], frame.tokens + [token("确认", .55, .62)],
                       [token("是否返回登录界面？"), token("取消"), token("确认")]):
            self.assertIsNone(exit_confirmation(scene()))
            frame = scene()
            frame.tokens = tokens
            self.assertIsNone(exit_confirmation(frame))

    def test_payment_blocker_is_not_dismissed_as_exit(self):
        frame = self.exit_scene()
        frame.tokens.append(token("确认支付"))
        with self.assertRaises(AutomationError):
            exit_confirmation(frame)

    def test_direct_normal_exit_requires_process_confirmation_and_releases_handle(self):
        context = Mock()
        watch = context.window.request_close.return_value
        watch.exited.return_value = True
        result = close_verified_game(context)
        self.assertEqual(result.status, "ok")
        context.window.request_close.assert_called_once()
        context.click.assert_not_called()
        watch.close.assert_called_once()

    def test_ordinary_exit_confirmation_clicked_once_from_fresh_frame(self):
        context = Mock()
        frame = self.exit_scene()
        context.snapshot.return_value = frame
        watch = context.window.request_close.return_value
        watch.exited.side_effect = [False, False, True]
        with patch("autozzz.closing.time.sleep"):
            result = close_verified_game(context)
        self.assertEqual(result.status, "ok")
        context.click.assert_called_once_with(frame, frame.tokens[2])
        watch.close.assert_called_once()

    def test_timeout_never_means_terminated_or_success(self):
        context = Mock()
        context.window.request_close.return_value.exited.return_value = False
        result = close_verified_game(context, timeout=0)
        self.assertEqual(result.status, "partial")
        context.click.assert_not_called()
        context.window.request_close.return_value.close.assert_called_once()

    def test_stop_before_close_sends_no_window_message(self):
        context = Mock()
        context.runner._control_checkpoint.side_effect = Stopped("F10")
        with self.assertRaises(Stopped):
            close_verified_game(context)
        context.window.request_close.assert_not_called()

    def test_process_watch_uses_exact_retained_handle_and_is_idempotent(self):
        with patch("autozzz.windows.kernel32") as kernel:
            kernel.WaitForSingleObject.side_effect = [258, 0, 0xFFFFFFFF]
            watch = GameExitWatch(123)
            self.assertFalse(watch.exited())
            self.assertTrue(watch.exited())
            with self.assertRaises(AutomationError):
                watch.exited()
            watch.close()
            watch.close()
            kernel.CloseHandle.assert_called_once_with(123)

    def test_mismatched_window_never_receives_close(self):
        window = object.__new__(GameWindow)
        window.path = r"E:\Game\ZenlessZoneZero.exe"
        window.hwnd = 321
        window.guard = Mock()
        with patch("autozzz.windows.process_path", return_value=r"C:\Other.exe"), \
                patch("autozzz.windows.user32") as user:
            with self.assertRaises(AutomationError):
                window.request_close(Mock())
            user.PostMessageW.assert_not_called()

    def test_normal_close_is_bound_to_verified_pid_and_only_posts_wm_close(self):
        window = object.__new__(GameWindow)
        window.path = r"E:\Game\ZenlessZoneZero.exe"
        window.hwnd = 321
        window.guard = Mock()
        checkpoint = Mock()
        with patch("autozzz.windows.process_path", return_value=window.path), \
                patch("autozzz.windows.user32") as user, patch("autozzz.windows.kernel32") as kernel:
            user.GetWindowThreadProcessId.side_effect = lambda hwnd, pointer: setattr(pointer._obj, "value", 55)
            kernel.OpenProcess.return_value = 123
            def query(handle, flags, buffer, length):
                buffer.value = window.path
                return True
            kernel.QueryFullProcessImageNameW.side_effect = query
            user.PostMessageW.return_value = 1
            watch = window.request_close(checkpoint)
            kernel.OpenProcess.assert_called_once_with(0x101000, False, 55)
            user.PostMessageW.assert_called_once_with(321, 0x0010, 0, 0)
            checkpoint.assert_called_once()
            self.assertEqual(watch.handle, 123)
            watch.close()
            kernel.CloseHandle.assert_called_once_with(123)

    def test_changed_retained_process_identity_releases_handle_without_close(self):
        window = object.__new__(GameWindow)
        window.path = r"E:\Game\ZenlessZoneZero.exe"
        window.hwnd = 321
        window.guard = Mock()
        with patch("autozzz.windows.process_path", return_value=window.path), \
                patch("autozzz.windows.user32") as user, patch("autozzz.windows.kernel32") as kernel:
            kernel.OpenProcess.return_value = 123
            def query(handle, flags, buffer, length):
                buffer.value = r"C:\Other.exe"
                return True
            kernel.QueryFullProcessImageNameW.side_effect = query
            with self.assertRaises(AutomationError):
                window.request_close(Mock())
            user.PostMessageW.assert_not_called()
            kernel.CloseHandle.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
