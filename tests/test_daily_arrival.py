"""A persistent F shortcut is not proof that a daily NPC dialog opened."""
import unittest
from unittest.mock import Mock

from autozzz.models import AutomationError, InputInterrupted, Scene
from autozzz.workflows import DailyWorkflow
from tests.test_core import WorkflowContext, scene, token, world_with_npc


def arrived(name="吼吼先生", x=.5):
    frame = world_with_npc()
    frame.tokens.append(token(name, x, .32))
    return frame


def setup(frames):
    context = WorkflowContext(frames)
    # This exact key icon was also matched on the real failed world screenshot.
    context.template.side_effect = lambda s, name, area, threshold: (
        (.757, .958, .98) if name == "interact_f" else None)
    return context, DailyWorkflow(context)


class DailyArrivalTests(unittest.TestCase):
    def test_bakery_still_in_world_after_f_approaches_then_checks_open_page(self):
        world, opened = arrived(), scene("零食盲盒", "每日可领取一次", "集卡兑换")
        context, workflow = setup([world, world, opened])
        result = workflow._open_daily_target("scratch", world, ("吼吼先生",))
        self.assertIs(result, opened)
        self.assertEqual(context.actions, [("key", "f"), ("key", "w"), ("key", "f")])
        self.assertFalse(context.report.daily_verified)
        self.assertIn("scratch_arrival", context.report.evidence)

    def test_video_arrow_wrapped_name_is_bound_to_central_npc(self):
        world = arrived(">阿玖《", x=.46)
        opened = scene()
        opened.tokens = [token("阿玖", .49, .74), token("查看经营状况", .88, .55)]
        context, workflow = setup([world, world, opened])
        self.assertEqual(workflow._daily_target_npc("video", world).text, ">阿玖《")
        self.assertIs(workflow._open_daily_target("video", world, ("阿玖",)), opened)
        self.assertEqual(context.actions.count(("key", "w")), 1)

    def test_working_arrival_needs_one_f_and_no_approach(self):
        world, opened = arrived(), scene("查看今天")
        context, workflow = setup([opened])
        self.assertIs(workflow._open_daily_target("scratch", world, ("吼吼先生",)), opened)
        self.assertEqual(context.actions, [("key", "f")])

    def test_persistent_hud_has_finite_retry_budget_and_stays_partial(self):
        world = arrived()
        context, workflow = setup([world])
        result = workflow._open_daily_target("scratch", world, ("吼吼先生",))
        self.assertEqual(result.status, "partial")
        self.assertIn("仍未打开", result.detail)
        self.assertEqual(context.actions.count(("key", "f")), 4)
        self.assertEqual(context.actions.count(("key", "w")), 3)
        self.assertFalse(context.report.daily_verified)

    def test_background_name_duplicate_name_and_wrong_npc_never_move(self):
        wrong = arrived("阿玖")
        duplicate = arrived()
        duplicate.tokens.append(token("吼吼先生", .55, .32))
        for world in (arrived(x=.9), wrong, duplicate):
            with self.subTest(tokens=world.text):
                context, workflow = setup([world])
                result = workflow._open_daily_target("scratch", world, ("吼吼先生", "阿玖"))
                self.assertEqual(result.status, "partial")
                self.assertEqual(context.actions, [])

    def test_shop_sign_and_f_without_bound_npc_do_not_authorize_movement(self):
        world = arrived("影像档案架", x=.12)
        context, workflow = setup([world])
        result = workflow._open_daily_target("video", world, ("影像档案架",))
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.actions, [])

    def test_target_disappearing_after_f_stops_without_approach(self):
        world, missing = arrived(), world_with_npc()
        context, workflow = setup([missing])
        result = workflow._open_daily_target("scratch", world, ("吼吼先生",))
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.actions, [("key", "f")])

    def test_target_disappearing_after_approach_stops_before_next_f(self):
        world, missing = arrived(), world_with_npc()
        context, workflow = setup([world, missing])
        result = workflow._open_daily_target("scratch", world, ("吼吼先生",))
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.actions, [("key", "f"), ("key", "w")])

    def test_unknown_page_after_f_does_not_receive_movement_or_extra_f(self):
        world, unknown = arrived(), scene("未知页面")
        context, workflow = setup([unknown])
        self.assertIs(workflow._open_daily_target("scratch", world, ("吼吼先生",)), unknown)
        self.assertEqual(context.actions, [("key", "f")])

    def test_payment_and_pause_interruptions_are_not_retried_as_arrival(self):
        for error in (AutomationError("付费弹窗"), InputInterrupted("F9 暂停")):
            world = arrived()
            context, workflow = setup([world])
            context.key = Mock(side_effect=error)
            with self.assertRaises(type(error)):
                workflow._open_daily_target("scratch", world, ("吼吼先生",))
            context.key.assert_called_once()
