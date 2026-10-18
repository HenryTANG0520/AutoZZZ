"""Retry only the observed entry error, with a shared finite click budget."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from autozzz.models import AutomationError, InputInterrupted, Scene, Token
from autozzz.workflows import DailyWorkflow
from tests.test_core import WorkflowContext, game_start_page, scene, token, world_with_npc


def network_error():
    data = json.loads((Path(__file__).parent / "fixtures/entry_network_error.json").read_text(encoding="utf-8"))
    return Scene([Token(t["text"], t["score"], tuple(t["box"])) for t in data["tokens"]])


class EntryNetworkTests(unittest.TestCase):
    def test_observed_error_retries_then_confirms_world(self):
        error, world = network_error(), world_with_npc()
        context = WorkflowContext([error, world, world])
        workflow = DailyWorkflow(context)
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "重试")])
        self.assertEqual(workflow._entry_network_retries, 1)
        self.assertFalse(context.report.daily_verified)

    def test_error_can_follow_a_sent_title_click_without_timing_out(self):
        error, title, world = network_error(), game_start_page(), world_with_npc()
        context = WorkflowContext([title, error, error, world, world])
        workflow = DailyWorkflow(context)
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "点击进入游戏"), ("click", "重试")])

    def test_background_world_does_not_hide_network_error(self):
        error = network_error()
        error.tokens.extend(world_with_npc().tokens)
        workflow = DailyWorkflow(WorkflowContext([error]))
        self.assertTrue(workflow._world(error))
        self.assertFalse(workflow._main(error))
        self.assertTrue(workflow._entry_ready(error))

    def test_persistent_error_stops_after_three_clicks(self):
        error = network_error()
        context = WorkflowContext([error])
        workflow = DailyWorkflow(context)
        with self.assertRaisesRegex(AutomationError, "已有限重试 3 次"):
            workflow.ensure_main()
        self.assertEqual(context.actions, [("click", "重试")]*3)
        self.assertEqual(workflow._entry_network_retries, 3)
        # Restarting the same workflow or resuming it cannot reset the budget.
        with self.assertRaisesRegex(AutomationError, "已有限重试 3 次"):
            workflow.ensure_main()
        self.assertEqual(len(context.actions), 3)

    def test_missing_low_confidence_duplicate_and_misplaced_retry_are_rejected(self):
        for variant in ("missing", "low", "duplicate", "misplaced"):
            error = network_error()
            index = next(i for i, t in enumerate(error.tokens) if t.text == "重试")
            if variant == "missing":
                del error.tokens[index]
            elif variant == "duplicate":
                error.tokens.append(replace(error.tokens[index]))
            else:
                error.tokens[index] = replace(error.tokens[index], **(
                    {"score": .59} if variant == "low" else {"box": (.05, .05, .1, .1)}))
            context = WorkflowContext([error])
            with self.subTest(variant=variant), self.assertRaises(AutomationError):
                DailyWorkflow(context).ensure_main()
            self.assertEqual(context.actions, [])

    def test_generic_retry_or_different_error_does_not_authorize_click(self):
        for frame in (scene("重试"), scene("未知错误", "重试")):
            context = WorkflowContext([frame])
            with self.assertRaises(AutomationError):
                DailyWorkflow(context).ensure_main()
            self.assertEqual(context.actions, [])

    def test_login_update_and_purchase_blocks_do_not_authorize_retry(self):
        for label in ("验证码", "密码", "下载更新", "续费", "购买", "充值"):
            error = network_error()
            error.tokens.append(token(label))
            context = WorkflowContext([error])
            with self.subTest(label=label), self.assertRaises(AutomationError):
                DailyWorkflow(context).ensure_main()
            self.assertEqual(context.actions, [])

    def test_pause_before_click_does_not_spend_retry_budget(self):
        error, world = network_error(), world_with_npc()
        context = WorkflowContext([error])
        workflow = DailyWorkflow(context)
        real_click = context.click
        context.click = Mock(side_effect=InputInterrupted("F9"))
        with self.assertRaises(InputInterrupted):
            workflow.ensure_main()
        self.assertEqual(workflow._entry_network_retries, 0)
        context.click = real_click
        context.frames = [error, world, world]
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "重试")])

    def test_resume_after_sent_retry_does_not_reclick_on_world(self):
        error, world = network_error(), world_with_npc()
        context = WorkflowContext([error])
        workflow = DailyWorkflow(context)
        real_wait = context.wait
        context.wait = Mock(side_effect=InputInterrupted("F9 after retry"))
        with self.assertRaises(InputInterrupted):
            workflow.ensure_main()
        self.assertEqual(workflow._entry_network_retries, 1)
        context.wait = real_wait
        context.frames = [world]
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "重试")])
