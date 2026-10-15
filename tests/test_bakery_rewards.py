"""Bakery closure is bound to the card footer, independent of the prize."""
import unittest
from unittest.mock import patch

from autozzz.models import AutomationError, Scene, Token
from autozzz.workflows import DailyWorkflow
from tests.test_core import WorkflowContext, captured_daily_cards, scene, token, world_with_npc


def polychrome_reward():
    # 2026-10-15 failed capture. Only reward OCR is retained, without the UID.
    return Scene([
        Token("获得", .997, (.48281, .37315, .51875, .41111)),
        Token("20", .999, (.49271, .54352, .50833, .56574)),
        Token("菲林", .999, (.48854, .56667, .51198, .59444)),
        Token("确认", .999, (.49531, .66111, .52500, .69259)),
    ])


def bakery_card():
    return Scene([token("Roarin", .5, .65), token("确定", .5, .81), token("分享", .94, .88)])


class BakeryPolychromeRewardTests(unittest.TestCase):
    def test_observed_reward_is_known_but_cannot_substitute_for_coffee_energy(self):
        reward = polychrome_reward()
        workflow = DailyWorkflow(WorkflowContext([reward]))
        self.assertTrue(workflow._result_screen(reward))
        self.assertTrue(workflow._known(reward))
        self.assertFalse(workflow._coffee_reward(reward))

    def test_reward_and_card_confirmations_still_require_daily_task_evidence(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                daily = captured_daily_cards()
                if completed:
                    daily.tokens = [Token("进度：1/1", t.score, t.box)
                                    if t.text == "进度：0/1" and abs(t.center[0] - .63672) < .02 else t
                                    for t in daily.tokens]
                context = WorkflowContext([polychrome_reward(), bakery_card(), world_with_npc()])
                workflow = DailyWorkflow(context)
                with patch.object(workflow, "_open_daily", return_value=daily):
                    result = workflow.scratch()
                self.assertEqual(result.status, "ok" if completed else "partial")
                self.assertEqual(context.actions, [("click", "确认"), ("click", "确定")])
                self.assertIn("bakery_reward", context.report.evidence)
                self.assertIn("bakery_card", context.report.evidence)
                self.assertFalse(context.report.daily_verified)

    def test_restarting_on_failed_reward_can_return_to_main_without_reopening_box(self):
        context = WorkflowContext([polychrome_reward(), bakery_card(), world_with_npc()])
        workflow = DailyWorkflow(context)
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "确认"), ("click", "确定")])

    def test_missing_or_ambiguous_popup_controls_cannot_authorize_confirm(self):
        for invalid in ("no_title", "no_confirm", "duplicate_confirm", "wrong_button_position", "payment"):
            with self.subTest(invalid=invalid):
                reward = polychrome_reward()
                if invalid == "no_title":
                    reward.tokens = [t for t in reward.tokens if t.text != "获得"]
                elif invalid == "no_confirm":
                    reward.tokens = [t for t in reward.tokens if t.text != "确认"]
                elif invalid == "duplicate_confirm":
                    reward.tokens.append(token("确认", .52, .66))
                elif invalid == "wrong_button_position":
                    reward.tokens = [token(t.text, .1, .1) if t.text == "确认" else t for t in reward.tokens]
                else:
                    reward.tokens = [t for t in reward.tokens if t.text != "获得"]
                    reward.tokens.append(token("是否消耗菲林", .5, .45))
                context = WorkflowContext([reward])
                context.settings.auto_launch = False
                with self.assertRaises(AutomationError):
                    DailyWorkflow(context).ensure_main()
                self.assertEqual(context.actions, [])

    def test_any_or_unreadable_prize_still_closes_popup_then_card_footer(self):
        for prize in ("丁尼", "菲林", "邦布券", "其他奖品", None):
            with self.subTest(prize=prize):
                reward = polychrome_reward()
                reward.tokens = [Token(prize, t.score, t.box) if t.text == "菲林" else t
                                 for t in reward.tokens if t.text != "菲林" or prize is not None]
                context = WorkflowContext([reward, bakery_card(), world_with_npc()])
                workflow = DailyWorkflow(context)
                with patch.object(workflow, "_verify_task_card", return_value="verified") as verify:
                    self.assertEqual(workflow.scratch(), "verified")
                verify.assert_called_once()
                self.assertEqual(context.actions, [("click", "确认"), ("click", "确定")])

    def test_starting_on_card_only_clicks_its_footer_without_prize_evidence(self):
        for action in ("scratch", "ensure_main"):
            with self.subTest(action=action):
                context = WorkflowContext([bakery_card(), world_with_npc()])
                workflow = DailyWorkflow(context)
                with patch.object(workflow, "_verify_task_card", return_value="verified"):
                    result = getattr(workflow, action)()
                self.assertEqual(result if action == "scratch" else result.status,
                                 "verified" if action == "scratch" else "ok")
                self.assertEqual(context.actions, [("click", "确定")])

    def test_card_footer_must_be_unique_and_bound_to_the_card(self):
        for invalid in ("duplicate", "wrong_position", "no_card"):
            with self.subTest(invalid=invalid):
                card = bakery_card()
                if invalid == "duplicate":
                    card.tokens.append(token("确定", .52, .82))
                elif invalid == "wrong_position":
                    card.tokens = [token(t.text, .1, .1) if t.text == "确定" else t for t in card.tokens]
                else:
                    card.tokens = [t for t in card.tokens if t.text == "确定"]
                context = WorkflowContext([card])
                workflow = DailyWorkflow(context)
                self.assertFalse(workflow._bakery_card(card))
                self.assertEqual(workflow._bakery_daily(card).status, "partial")
                self.assertEqual(context.actions, [])

    def test_reward_popup_is_handled_before_the_card_behind_it(self):
        reward = polychrome_reward()
        reward.tokens += bakery_card().tokens
        context = WorkflowContext([reward, bakery_card(), world_with_npc()])
        workflow = DailyWorkflow(context)
        with patch.object(workflow, "_verify_task_card", return_value="verified"):
            self.assertEqual(workflow.scratch(), "verified")
        self.assertEqual(context.actions, [("click", "确认"), ("click", "确定")])


if __name__ == "__main__":
    unittest.main()
