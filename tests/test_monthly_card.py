"""Monthly-card entry regressions; all screenshots and desktop input are scripted."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autozzz.models import AutomationError, InputInterrupted, Scene, Token
from autozzz.workflows import DailyWorkflow
from tests.test_core import (WorkflowContext, cached_global_account, game_start_page,
                            scene, token, world_with_npc)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "monthly_card.json"


def monthly_frame(*, world_background=False):
    values = json.loads(FIXTURE.read_text(encoding="utf-8"))["tokens"]
    frame = scene()
    frame.tokens = [Token(value["text"], value["score"], tuple(value["box"])) for value in values]
    if world_background:
        frame.tokens.extend(world_with_npc().tokens)
    return frame


def context_for(frames, *, auto_launch=False, server="global"):
    context = WorkflowContext(frames)
    context.settings.select_server(server)
    context.settings.auto_launch = auto_launch
    context.log = Mock()
    return context


class MonthlyRecognitionTests(unittest.TestCase):
    def workflow(self, frame):
        return DailyWorkflow(context_for([frame]))

    def test_real_sanitized_ocr_anchors_identify_the_daily_ticket(self):
        frame = monthly_frame()
        workflow = self.workflow(frame)
        self.assertIs(workflow._monthly_card(frame), frame.tokens[1])
        self.assertTrue(workflow._entry_ready(frame))
        self.assertTrue(workflow._startup_ready(frame))
        self.assertNotIn("UID", frame.text)
        self.assertNotIn("@", frame.text)

    def test_monthly_ticket_with_background_hud_cannot_be_main(self):
        frame = monthly_frame(world_background=True)
        workflow = self.workflow(frame)
        self.assertTrue(workflow._world(frame))
        self.assertFalse(workflow._main(frame))
        self.assertTrue(workflow._entry_ready(frame))

    def test_each_missing_anchor_blocks_entry_even_with_visible_world(self):
        for missing in range(3):
            with self.subTest(missing=missing):
                frame = monthly_frame(world_background=True)
                del frame.tokens[missing]
                workflow = self.workflow(frame)
                self.assertIsNone(workflow._monthly_card(frame))
                self.assertFalse(workflow._main(frame))
                self.assertFalse(workflow._entry_ready(frame))

    def test_ambiguous_anchor_or_amount_does_not_authorize_click(self):
        for duplicate in range(3):
            with self.subTest(duplicate=duplicate):
                frame = monthly_frame()
                frame.tokens.append(replace(frame.tokens[duplicate]))
                self.assertIsNone(self.workflow(frame)._monthly_card(frame))

    def test_anchor_outside_its_observed_ticket_region_is_rejected(self):
        for moved in range(3):
            with self.subTest(moved=moved):
                frame = monthly_frame()
                frame.tokens[moved] = replace(frame.tokens[moved], box=(.02, .03, .15, .07))
                self.assertIsNone(self.workflow(frame)._monthly_card(frame))

    def test_low_confidence_anchor_and_wrong_amount_are_rejected(self):
        for changed in range(3):
            with self.subTest(changed=changed):
                frame = monthly_frame()
                frame.tokens[changed] = replace(frame.tokens[changed], score=.59)
                self.assertIsNone(self.workflow(frame)._monthly_card(frame))
        for text in ("×900", "×91", "×9", "90", "90菲林", "购买菲林×90"):
            with self.subTest(text=text):
                frame = monthly_frame()
                frame.tokens[2] = replace(frame.tokens[2], text=text)
                self.assertIsNone(self.workflow(frame)._monthly_card(frame))

    def test_normalized_monthly_amount_variants_are_supported(self):
        for text in ("×90", "x90", "X90", "*90", "✕90", "菲林×90", "× ９０"):
            with self.subTest(text=text):
                frame = monthly_frame()
                frame.tokens[2] = replace(frame.tokens[2], text=text)
                self.assertIs(self.workflow(frame)._monthly_card(frame), frame.tokens[1])

    def test_split_currency_and_amount_need_aligned_nearby_currency(self):
        frame = monthly_frame()
        frame.tokens[2] = replace(frame.tokens[2], text="90")
        frame.tokens.append(token("菲林", .37, frame.tokens[2].center[1]))
        self.assertIs(self.workflow(frame)._monthly_card(frame), frame.tokens[1])
        frame.tokens[-1] = token("菲林", .60, .66)
        self.assertIsNone(self.workflow(frame)._monthly_card(frame))

    def test_anchors_must_have_real_row_order_and_compact_alignment(self):
        frame = monthly_frame()
        frame.tokens[2] = replace(frame.tokens[2], box=(.40, .536, .445, .554))
        self.assertIsNone(self.workflow(frame)._monthly_card(frame))
        frame = monthly_frame()
        frame.tokens[0] = replace(frame.tokens[0], box=(.59, .393, .64, .421))
        self.assertIsNone(self.workflow(frame)._monthly_card(frame))

    def test_purchase_renewal_topup_or_login_blockers_never_authorize_ticket(self):
        for text in ("购买月卡", "续费", "续购月卡", "充值", "确认支付", "售价30元",
                     "兑换", "purchase", "renew", "top up", "password", "验证码", "同意协议", "下载更新"):
            with self.subTest(text=text):
                frame = monthly_frame()
                frame.tokens.append(token(text, .75, .80))
                self.assertIsNone(self.workflow(frame)._monthly_card(frame))


class MonthlyFlowTests(unittest.TestCase):
    def test_ticket_visible_when_manual_run_starts_is_dismissed_before_main(self):
        for server in ("cn", "global"):
            with self.subTest(server=server):
                ticket = monthly_frame(world_background=True)
                context = context_for([ticket, world_with_npc()], server=server)
                workflow = DailyWorkflow(context)
                self.assertEqual(workflow.ensure_main().status, "ok")
                self.assertEqual(context.actions, [("click", "月卡剩余21天")])
                self.assertIs(context.targets[0], ticket.tokens[1])
                self.assertEqual(workflow._monthly_claim_clicks, 1)
                self.assertFalse(workflow._monthly_claim_pending)
                self.assertFalse(context.report.daily_verified)
                self.assertIsNone(context.report.engagement)
                self.assertEqual(context.report.stamina_spent, 0)
                self.assertEqual([name for _, name in context.evidence_frames],
                                 ["monthly_card", "monthly_card_dismissed"])

    def test_auto_launch_loading_can_transition_to_monthly_then_world(self):
        context = context_for([scene(), scene(), monthly_frame(), world_with_npc()], auto_launch=True)
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "月卡剩余21天")])

    def test_title_entry_wait_accepts_monthly_instead_of_timing_out(self):
        title, ticket = game_start_page(), monthly_frame()
        context = context_for([title, ticket, ticket, world_with_npc()])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "点击进入游戏"), ("click", "月卡剩余21天")])

    def test_saved_account_and_title_entry_reach_monthly_then_world(self):
        login, title, ticket = cached_global_account(), game_start_page(), monthly_frame()
        context = context_for([login, title, title, ticket, ticket, world_with_npc()])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "进入游戏"), ("click", "点击进入游戏"),
                                           ("click", "月卡剩余21天")])

    def test_monthly_after_fifth_title_click_has_its_own_finite_claim_budget(self):
        title, ticket = game_start_page(), monthly_frame()
        context = context_for([title] * 9 + [ticket, ticket, world_with_npc()])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "点击进入游戏")] * 5 + [("click", "月卡剩余21天")])

    def test_repeat_monthly_clicks_use_fresh_targets_then_verify_world(self):
        first, second = monthly_frame(), monthly_frame()
        second.tokens[1] = replace(second.tokens[1], box=(.40, .55, .48, .578))
        context = context_for([first, second, world_with_npc()])
        workflow = DailyWorkflow(context)
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(workflow._monthly_claim_clicks, 2)
        self.assertIs(context.targets[0], first.tokens[1])
        self.assertIs(context.targets[1], second.tokens[1])

    def test_persistent_monthly_popup_stops_after_three_claim_clicks(self):
        ticket = monthly_frame()
        context = context_for([ticket])
        workflow = DailyWorkflow(context)
        with self.assertRaisesRegex(AutomationError, "有限点击"):
            workflow.ensure_main()
        self.assertEqual(context.actions, [("click", "月卡剩余21天")] * 3)
        self.assertEqual(workflow._monthly_claim_clicks, 3)
        with self.assertRaises(AutomationError):
            workflow.ensure_main()
        self.assertEqual(len(context.actions), 3, "Retrying the workflow must not refill its click allowance")

    def test_partial_ticket_waits_without_clicking_dimmed_world_until_full_proof(self):
        partial = monthly_frame(world_background=True)
        partial.tokens.pop(2)
        full = monthly_frame()
        context = context_for([partial, partial, full, world_with_npc()])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "月卡剩余21天")])
        self.assertIs(context.targets[0], full.tokens[1])

    def test_persistent_partial_ticket_never_sends_click_or_escape(self):
        ticket = monthly_frame(world_background=True)
        ticket.tokens.pop(2)
        context = context_for([ticket])
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        self.assertEqual(context.actions, [])

    def test_monthly_purchase_page_never_gets_a_click_or_escape(self):
        for text in ("购买月卡", "续费", "续购月卡", "充值"):
            with self.subTest(text=text):
                ticket = monthly_frame(world_background=True)
                ticket.tokens.append(token(text, .72, .70))
                context = context_for([ticket])
                with self.assertRaises(AutomationError):
                    DailyWorkflow(context).ensure_main()
                self.assertEqual(context.actions, [])

    def test_credentials_after_monthly_click_never_get_additional_input(self):
        context = context_for([monthly_frame(), scene("密码", "进入游戏")])
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        self.assertEqual(context.actions, [("click", "月卡剩余21天")])

    def test_post_claim_reward_confirmation_precedes_background_world(self):
        reward = world_with_npc()
        reward.tokens.extend([token("获得奖励", .50, .45), token("菲林", .5, .5), token("确认", .5, .60)])
        context = context_for([monthly_frame(), reward, world_with_npc()])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "月卡剩余21天"), ("click", "确认")])

    def test_fifth_title_entry_followed_by_monthly_reward_confirms_before_main(self):
        title, ticket = game_start_page(), monthly_frame()
        reward = world_with_npc()
        reward.tokens.extend([token("获得奖励", .50, .45), token("菲林", .5, .5), token("确认", .5, .60)])
        context = context_for([title] * 9 + [ticket, ticket, reward, world_with_npc()])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "点击进入游戏")] * 5
                         + [("click", "月卡剩余21天"), ("click", "确认")])

    def test_final_reward_transition_waits_for_overlay_to_close_before_main(self):
        title, ticket, world = game_start_page(), monthly_frame(), world_with_npc()
        reward = world_with_npc()
        reward.tokens.extend([token("获得奖励", .50, .45), token("菲林", .5, .5), token("确认", .5, .60)])
        context = context_for([title] * 9 + [ticket, ticket, reward, reward, world])
        snapshot, observed = context.snapshot, []

        def capture():
            value = snapshot()
            observed.append(value)
            return value

        context.snapshot = capture
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertIs(observed[-1], world, "Visible reward overlays must not count as the ready world")
        self.assertEqual(context.actions, [("click", "点击进入游戏")] * 5
                         + [("click", "月卡剩余21天"), ("click", "确认")])

    def test_pause_before_monthly_input_discards_old_target_without_spending_click(self):
        initial, fresh = monthly_frame(), monthly_frame()
        context = context_for([initial])
        original_click = context.click
        attempts = [0]

        def interrupted_click(frame, target):
            attempts[0] += 1
            if attempts[0] == 1:
                raise InputInterrupted("pending target invalidated before SendInput")
            original_click(frame, target)

        context.click = interrupted_click
        workflow = DailyWorkflow(context)
        with self.assertRaises(InputInterrupted):
            workflow.ensure_main()
        self.assertEqual(workflow._monthly_claim_clicks, 0)
        self.assertEqual(context.actions, [])
        context.frames = [scene(), fresh, world_with_npc()]
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(workflow._monthly_claim_clicks, 1)
        self.assertEqual(context.actions, [("click", "月卡剩余21天")])
        self.assertIs(context.targets[0], fresh.tokens[1])

    def test_pause_after_sent_claim_resumes_blank_transition_without_replaying_click(self):
        context = context_for([monthly_frame()])
        wait = context.wait
        context.wait = Mock(side_effect=InputInterrupted("manual pause during post-claim read"))
        workflow = DailyWorkflow(context)
        with self.assertRaises(InputInterrupted):
            workflow.ensure_main()
        self.assertEqual(workflow._monthly_claim_clicks, 1)
        self.assertTrue(workflow._monthly_claim_pending)
        context.wait = Mock(wraps=wait)
        context.frames = [scene(), scene(), world_with_npc()]
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "月卡剩余21天")])
        self.assertEqual(workflow._monthly_claim_clicks, 1)
        self.assertFalse(workflow._monthly_claim_pending)
        context.wait.assert_called_once()

    def test_pending_claim_with_only_remaining_anchor_waits_past_dimmed_world(self):
        context = context_for([monthly_frame()])
        wait = context.wait
        context.wait = Mock(side_effect=InputInterrupted("manual pause during post-claim read"))
        workflow = DailyWorkflow(context)
        with self.assertRaises(InputInterrupted):
            workflow.ensure_main()
        partial = monthly_frame(world_background=True)
        partial.tokens = [partial.tokens[1], *partial.tokens[3:]]
        self.assertFalse(workflow._entry_ready(partial))
        context.wait = Mock(wraps=wait)
        context.frames = [partial, partial, world_with_npc()]
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "月卡剩余21天")])
        self.assertEqual(workflow._monthly_claim_clicks, 1)
        self.assertFalse(workflow._monthly_claim_pending)
        context.wait.assert_called_once()

    def test_repeated_pre_send_interruptions_do_not_consume_actual_click_budget(self):
        context = context_for([monthly_frame()])
        click = context.click
        context.click = Mock(side_effect=InputInterrupted("invalidated before SendInput"))
        workflow = DailyWorkflow(context)
        for _ in range(5):
            with self.assertRaises(InputInterrupted):
                workflow.ensure_main()
            self.assertEqual(workflow._monthly_claim_clicks, 0)
            self.assertEqual(context.actions, [])
        context.click = click
        context.frames = [monthly_frame(), world_with_npc()]
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(workflow._monthly_claim_clicks, 1)
        self.assertEqual(context.actions, [("click", "月卡剩余21天")])


if __name__ == "__main__":
    unittest.main()
