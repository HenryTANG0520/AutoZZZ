"""Headless completion display checks; no Tk root or desktop window is created."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from autozzz.gui import AutoZZZWindow, DAILY_TASK_NAMES, FINISH_TASK_NAMES, TASK_NAMES
from autozzz.models import RunReport, TaskResult


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class ResultTable:
    def __init__(self):
        self.rows = {name: {"values": (label, "等待", ""), "tags": ("pending",)}
                     for name, label in TASK_NAMES.items()}

    def item(self, name, option=None, **values):
        self.rows[name].update(values)
        return self.rows[name].get(option) if option else self.rows[name]


def window_state(**checked):
    window = AutoZZZWindow.__new__(AutoZZZWindow)
    window.running = window.busy = True
    window.paused = window.focus_waiting = True
    window.tasks = {name: Value(checked.get(name, True)) for name in TASK_NAMES}
    window.table = ResultTable()
    window.status = Value("正在执行")
    window.summary = Value("")
    window.engagement = Value("")
    window.report_text = object()
    window.last_output_dir = window.last_report_file = None
    window._freeze_clock = Mock()
    window._replace_text = Mock()
    window._log = Mock()
    window._restore = Mock()
    return window


def terminal_report(*bonus):
    return RunReport(status="success", daily_verified=True, engagement=400,
                     results=[TaskResult(name, "partial", "原步骤未核实") for name in DAILY_TASK_NAMES]
                             + list(bonus))


class RewardCompletionGuiTests(unittest.TestCase):
    def test_daily_proof_marks_only_core_tasks_complete_and_keeps_bonus_partial(self):
        window = window_state()
        report = terminal_report(TaskResult("weekly", "partial", "周纪页面尚未核实"),
                                 TaskResult("city_fund", "skipped", "前一页面未核实，未执行城募"))
        window._finished(report)
        for name in DAILY_TASK_NAMES:
            with self.subTest(task=name):
                self.assertEqual(window.table.item(name, "values")[1], "完成")
        self.assertEqual(window.table.item("weekly", "values")[1:], ("部分完成", "周纪页面尚未核实"))
        self.assertEqual(window.table.item("city_fund", "values")[1:], ("跳过", "前一页面未核实，未执行城募"))
        self.assertTrue(window._daily_goal_complete)
        self.assertIn("每日任务已完成", window.summary.get())
        self.assertIn("收尾奖励有未完成项", window.summary.get())
        self.assertNotIn("全部完成", window.summary.get())

    def test_f10_bonus_stop_and_following_skip_remain_visible_after_daily_completion(self):
        window = window_state()
        window._finished(terminal_report(TaskResult("weekly", "stopped", "F10 已停止"),
                                         TaskResult("city_fund", "skipped", "用户已停止，未执行后续收尾奖励")))
        self.assertEqual(window.table.item("weekly", "values")[1:], ("已停止", "F10 已停止"))
        self.assertEqual(window.table.item("city_fund", "values")[1:], ("跳过", "用户已停止，未执行后续收尾奖励"))
        self.assertIn("每日已验证", window.engagement.get())
        self.assertIn("收尾奖励有未完成项", window.status.get())
        self.assertFalse(window.running or window.paused or window.focus_waiting)
        window._restore.assert_called_once()

    def test_unselected_core_task_is_not_upgraded_by_terminal_daily_proof(self):
        window = window_state(scratch=False, weekly=False, city_fund=False)
        report = terminal_report()
        window._finished(report)
        self.assertEqual(window.table.item("scratch", "values")[1], "部分完成")
        self.assertEqual(window.table.item("coffee", "values")[1], "完成")
        self.assertTrue(window._daily_goal_complete)
        self.assertNotIn("收尾奖励有未完成项", window.summary.get())

    def test_disabled_bonus_does_not_report_a_selected_cleanup_failure(self):
        window = window_state(weekly=False, city_fund=False)
        window._finished(terminal_report(TaskResult("weekly", "partial", "旧报告周纪未完成"),
                                         TaskResult("city_fund", "partial", "旧报告城募未完成")))
        self.assertEqual(window.table.item("weekly", "values")[1], "部分完成")
        self.assertEqual(window.table.item("city_fund", "values")[1], "部分完成")
        self.assertEqual(window.status.get(), "每日任务已完成")

    def test_completed_bonus_does_not_make_unverified_daily_complete(self):
        window = window_state()
        report = RunReport(status="partial", daily_verified=False, engagement=400,
                           results=[TaskResult("daily", "partial", "四档奖励未核实")]
                                   + [TaskResult(name, "ok", "收尾已完成") for name in FINISH_TASK_NAMES])
        window._finished(report)
        self.assertFalse(window._daily_goal_complete)
        self.assertEqual(window.table.item("daily", "values")[1:], ("部分完成", "四档奖励未核实"))
        self.assertNotIn("每日任务已完成", window.summary.get())
        self.assertIn("每日未验证", window.engagement.get())

    def test_ambiguous_or_incomplete_daily_numeric_proof_is_not_upgraded(self):
        for engagement, verified in (("400", True), (True, True), (399, True), (400, False)):
            with self.subTest(engagement=engagement, verified=verified):
                window = window_state()
                report = RunReport(status="partial", daily_verified=verified, engagement=engagement,
                                   results=[TaskResult("daily", "partial", "缺少终点证据")])
                window._finished(report)
                self.assertFalse(window._daily_goal_complete)
                self.assertEqual(window.table.item("daily", "values")[1], "部分完成")
                self.assertNotIn("每日任务已完成", window.summary.get())

    def test_all_cleanup_succeeded_summary_names_daily_goal_without_overstating_scope(self):
        window = window_state()
        window._finished(terminal_report(*(TaskResult(name, "ok", "奖励已核实") for name in FINISH_TASK_NAMES)))
        self.assertEqual(window.status.get(), "每日任务已完成")
        self.assertNotIn("全部完成", window.summary.get())
        for name in FINISH_TASK_NAMES:
            self.assertEqual(window.table.item(name, "values")[1], "完成")

    def test_waiting_unexecuted_bonus_gets_skip_feedback_without_fabricated_success(self):
        window = window_state()
        window._finished(terminal_report())
        for name in FINISH_TASK_NAMES:
            with self.subTest(task=name):
                self.assertEqual(window.table.item(name, "values")[1], "跳过")
                self.assertIn("未确认完成", window.table.item(name, "values")[2])
        self.assertIn("收尾奖励有未完成项", window.summary.get())

    def test_enabled_bonus_skip_is_distinct_from_no_available_rewards_in_summary(self):
        window = window_state(weekly=False)
        window._finished(terminal_report(TaskResult("city_fund", "skipped", "页面未核实，未执行")))
        self.assertEqual(window.table.item("city_fund", "values")[1:], ("跳过", "页面未核实，未执行"))
        self.assertIn("每日任务已完成", window.summary.get())
        self.assertIn("收尾奖励有未完成项", window.summary.get())
        self.assertNotIn("全部完成", window.summary.get())


if __name__ == "__main__":
    unittest.main()
