"""A sent title click can leave a later frame in configuration loading."""
import unittest
from unittest.mock import Mock

from autozzz.models import AutomationError, InputInterrupted
from autozzz.workflows import DailyWorkflow
from tests.test_core import WorkflowContext, game_start_page, scene, world_with_npc


class EntryTransitionTests(unittest.TestCase):
    def test_loading_after_a_still_visible_title_waits_without_a_second_click(self):
        title, loading, world = game_start_page(), scene('加载配置数据中','5.90%'), world_with_npc()
        context = WorkflowContext([title,title,loading,loading,world])
        context.wait = Mock(wraps=context.wait)
        workflow = DailyWorkflow(context)
        self.assertEqual(workflow.ensure_main().status,'ok')
        self.assertEqual(context.actions,[('click','点击进入游戏')])
        self.assertFalse(workflow._entry_transition_pending)
        self.assertEqual(context.wait.call_count,2)
        self.assertTrue(all(c.kwargs['timeout']==context.settings.transition_timeout for c in context.wait.call_args_list))

    def test_f9_during_loading_keeps_sent_entry_state_and_does_not_repeat_click(self):
        title, loading, world = game_start_page(), scene('加载配置数据中'), world_with_npc()
        context = WorkflowContext([title,title,loading])
        wait = context.wait
        def interrupt_second(predicate, **kwargs):
            if context.wait.call_count == 2:
                raise InputInterrupted('在加载等待时暂停')
            return wait(predicate, **kwargs)
        context.wait = Mock(side_effect=interrupt_second)
        workflow = DailyWorkflow(context)
        with self.assertRaises(InputInterrupted):
            workflow.ensure_main()
        self.assertTrue(workflow._entry_transition_pending)
        context.frames = [loading,loading,world]
        context.wait = wait
        self.assertEqual(workflow.ensure_main().status,'ok')
        self.assertEqual(context.actions,[('click','点击进入游戏')])

    def test_unknown_manual_initial_frame_does_not_gain_entry_transition_allowance(self):
        context = WorkflowContext([scene('未知内容')])
        context.settings.auto_launch = False
        context.wait = Mock(wraps=context.wait)
        workflow = DailyWorkflow(context)
        with self.assertRaises(AutomationError):
            workflow.ensure_main()
        context.wait.assert_not_called()
        self.assertEqual(context.actions,[])
