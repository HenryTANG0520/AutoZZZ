"""Automatic game updates wait without input, timeouts, or loss of run controls."""
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from autozzz.app import Context, Runner
from autozzz.config import Settings
from autozzz.models import AutomationError, InputInterrupted, Scene, ScreenshotNotReady, Stopped, Token
from autozzz.workflows import DailyWorkflow
from tests.test_core import game_start_page, scene, token, world_with_npc


def recorded_update(name="entry_resource_download"):
    data = json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))
    return Scene([Token(t["text"], t["score"], tuple(t["box"])) for t in data["tokens"]])


class EntryUpdateTests(unittest.TestCase):
    def context(self, frames, *, auto_launch=True):
        runner = Runner(Settings(auto_launch=auto_launch, transition_timeout=1))
        context = Context(runner, Mock(), Mock())
        context.snapshot = Mock(side_effect=frames)
        context.template = Mock(return_value=None)
        context.evidence = Mock()
        context.log = Mock()
        context.click = Mock()
        context.key = Mock()
        context.sleep = Mock(side_effect=lambda _: runner.checkpoint())
        return context, runner

    def assert_no_input(self, context):
        context.click.assert_not_called()
        context.key.assert_not_called()
        for name in ("click", "key", "drag", "move", "scroll", "focus", "close"):
            getattr(context.window, name).assert_not_called()

    def test_recorded_update_states_wait_longer_than_transition_budget_then_continue(self):
        check, download = recorded_update("entry_version_check"), recorded_update()
        world = world_with_npc()
        for auto_launch in (False, True):
            with self.subTest(auto_launch=auto_launch):
                context, _ = self.context([check]*100 + [download]*100 + [world], auto_launch=auto_launch)
                self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
                self.assertEqual(context.snapshot.call_count, 201)
                self.assertEqual(context.evidence.call_count, 2)
                self.assertEqual(context.log.call_count, 3)
                self.assert_no_input(context)

    def test_update_after_entry_click_does_not_click_entry_again(self):
        title, world = game_start_page(), world_with_npc()
        context, _ = self.context([title] + [recorded_update()]*100 + [world, world])
        workflow = DailyWorkflow(context)
        self.assertEqual(workflow.ensure_main().status, "ok")
        context.click.assert_called_once_with(title, title.find("点击进入游戏"))
        self.assertFalse(workflow._entry_transition_pending)
        context.key.assert_not_called()

    def test_pause_during_download_resumes_without_repeating_entry_click(self):
        title, world = game_start_page(), world_with_npc()
        context, _ = self.context([title, recorded_update(), InputInterrupted("F9")])
        workflow = DailyWorkflow(context)
        with self.assertRaises(InputInterrupted):
            workflow.ensure_main()
        self.assertTrue(workflow._entry_transition_pending)
        context.snapshot.side_effect = [recorded_update()]*100 + [world]
        self.assertEqual(workflow.ensure_main().status, "ok")
        context.click.assert_called_once()

    def test_stop_during_download_remains_immediate_and_sends_no_input(self):
        context, runner = self.context([recorded_update()]*100)
        def stop(_):
            runner.stop()
            runner.checkpoint()
        context.sleep.side_effect = stop
        with self.assertRaises(Stopped):
            DailyWorkflow(context).ensure_main()
        self.assertEqual(context.snapshot.call_count, 2)
        self.assert_no_input(context)

    def test_authentication_and_failed_update_are_not_hidden_by_download_status(self):
        for label in ("密码", "验证码", "扫码", "请同意用户协议", "资源下载失败", "重试", "确认更新"):
            with self.subTest(label=label):
                blocked = recorded_update()
                blocked.tokens.append(token(label))
                context, _ = self.context([recorded_update(), blocked, world_with_npc()])
                with self.assertRaises(AutomationError):
                    DailyWorkflow(context).ensure_main()
                self.assertEqual(context.snapshot.call_count, 2)
                self.assert_no_input(context)

    def test_only_unique_confident_automatic_status_in_bottom_panel_can_wait(self):
        for labels in ([token("资源下载中", y=.3)], [token("立即下载", y=.82)],
                       [token("资源下载中", y=.82)]*2,
                       [Token("资源下载中", .2, (.45, .8, .55, .84))]):
            with self.subTest(labels=labels):
                frame = Scene(labels)
                context, _ = self.context([frame])
                self.assertEqual(DailyWorkflow(context)._entry_update_label(frame), "")

    def test_update_status_does_not_expose_background_as_ready(self):
        frame = recorded_update()
        frame.tokens.extend(world_with_npc().tokens)
        context, _ = self.context([frame])
        workflow = DailyWorkflow(context)
        self.assertFalse(workflow._entry_ready(frame))
        self.assertFalse(workflow._startup_ready(frame))

    def test_unknown_frames_after_update_still_time_out(self):
        context, _ = self.context([recorded_update()]*100 + [scene()]*10)
        with self.assertRaisesRegex(AutomationError, "超时"):
            DailyWorkflow(context).ensure_main()
        self.assertLess(context.snapshot.call_count, 110)
        self.assert_no_input(context)

    def test_short_black_frame_between_update_states_can_recover(self):
        context, _ = self.context([recorded_update(), ScreenshotNotReady("black"),
                                   recorded_update(), world_with_npc()])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assert_no_input(context)

    def test_window_closed_during_update_is_not_swallowed(self):
        context, _ = self.context([recorded_update(), AutomationError("游戏窗口已关闭")])
        with self.assertRaisesRegex(AutomationError, "游戏窗口已关闭"):
            DailyWorkflow(context).ensure_main()
        self.assert_no_input(context)

    def test_wait_grants_full_transition_budget_after_long_update(self):
        update, unknown, world = recorded_update(), scene(), world_with_npc()
        context, _ = self.context([update]*100 + [unknown, world])
        now = [0.0]
        context.sleep.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
        with patch("autozzz.app.time.monotonic", side_effect=lambda: now[0]):
            result = context.wait(lambda s: s is world, timeout=1,
                                  keep_waiting=lambda s: s is update)
        self.assertIs(result, world)
        self.assertGreater(now[0], 70)
        self.assert_no_input(context)

    def test_ordinary_wait_does_not_automatically_ignore_update_timeout(self):
        context, _ = self.context([recorded_update()]*10)
        with self.assertRaisesRegex(AutomationError, "超时"):
            context.wait(lambda s: False, timeout=1)
        self.assertEqual(context.snapshot.call_count, 2)
        self.assert_no_input(context)


if __name__ == "__main__":
    unittest.main()
