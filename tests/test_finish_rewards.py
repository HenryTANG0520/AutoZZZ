"""Reward cleanup is separate from the authoritative daily completion proof."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from autozzz.app import Runner
from autozzz.config import Settings
from autozzz.models import AutomationError, InputInterrupted, Stopped, TaskResult
from tests import test_completion


class FinishRewardTests(unittest.TestCase):
    def make_runner(self, verified=True, **flags):
        runner = Runner(Settings(**flags))
        runner.checkpoint = Mock()
        if verified:
            runner.report.daily_verified = True
            runner.report.engagement = 400
            runner._complete_daily()
        context = Mock()
        context.last_scene = None
        workflows = {}
        for name in ('weekly', 'city_fund'):
            work = SimpleNamespace(**{name: Mock(return_value=TaskResult(name, 'ok', '已核实收尾。'))})
            workflows[name] = work
        runner._reward_workflow = Mock(side_effect=lambda name, _: workflows[name])
        return runner, context, workflows

    def test_default_and_legacy_config_enable_both_free_cleanup_tasks(self):
        self.assertTrue(Settings().weekly)
        self.assertTrue(Settings().city_fund)
        directory = Path(__file__).resolve().parents[1] / 'runs' / 'offline-tests'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temp:
            path = Path(temp) / 'config.json'
            path.write_text(json.dumps({'promoter':'薇薇安', 'auto_launch':True}), encoding='utf-8')
            loaded = Settings.load(path)
            self.assertTrue(loaded.weekly and loaded.city_fund)
            self.assertEqual(loaded.promoter, '薇薇安')
            self.assertTrue(loaded.auto_launch)

    def test_verified_daily_still_runs_enabled_cleanup_in_order(self):
        runner, context, workflows = self.make_runner()
        runner._run_finish_rewards(context)
        self.assertEqual([r.task for r in runner.report.results], ['scratch','coffee','video','daily','weekly','city_fund'])
        self.assertEqual([c.args[0] for c in runner._reward_workflow.call_args_list], ['weekly','city_fund'])
        workflows['weekly'].weekly.assert_called_once()
        workflows['city_fund'].city_fund.assert_called_once()
        self.assertTrue(runner._daily_completed())
        self.assertEqual(runner.report.status, 'success')

    def test_flags_independently_control_cleanup(self):
        for weekly, fund in ((False,False),(True,False),(False,True)):
            with self.subTest(weekly=weekly,fund=fund):
                runner, context, _ = self.make_runner(weekly=weekly, city_fund=fund)
                runner._run_finish_rewards(context)
                self.assertEqual([c.args[0] for c in runner._reward_workflow.call_args_list],
                                 (['weekly'] if weekly else []) + (['city_fund'] if fund else []))

    def test_partial_cleanup_preserves_daily_success_and_stops_following_inputs(self):
        runner, context, workflows = self.make_runner()
        workflows['weekly'].weekly.return_value = TaskResult('weekly','partial','页身份未核实。')
        runner._run_finish_rewards(context)
        self.assertEqual([(r.task,r.status) for r in runner.report.results[-2:]], [('weekly','partial'),('city_fund','skipped')])
        workflows['city_fund'].city_fund.assert_not_called()
        self.assertTrue(runner._daily_completed())
        self.assertEqual(runner.report.status,'success')

    def test_cleanup_exception_has_separate_result_and_does_not_invent_claim_success(self):
        runner, context, workflows = self.make_runner()
        workflows['weekly'].weekly.side_effect = AutomationError('未知页面')
        runner._run_finish_rewards(context)
        self.assertEqual(runner.report.results[-2].status,'partial')
        self.assertIn('未知页面',runner.report.results[-2].detail)
        self.assertEqual(runner.report.status,'success')
        workflows['city_fund'].city_fund.assert_not_called()

    def test_repeat_terminal_publishing_keeps_real_cleanup_results(self):
        runner, context, workflows = self.make_runner()
        workflows['city_fund'].city_fund.return_value = TaskResult('city_fund','partial','需要用户自选。')
        runner._run_finish_rewards(context)
        runner._complete_daily()
        runner._complete_daily()
        self.assertEqual([r.task for r in runner.report.results].count('city_fund'),1)
        self.assertEqual(runner.report.results[-1].status,'partial')
        self.assertTrue(all(r.status=='ok' for r in runner.report.results[:4]))

    def test_f10_during_cleanup_is_honored_and_does_not_run_next_task(self):
        runner, context, workflows = self.make_runner()
        workflows['weekly'].weekly.side_effect = Stopped('F10停止')
        with self.assertRaises(Stopped):
            runner._run_finish_rewards(context)
        runner._complete_daily()
        self.assertTrue(runner._daily_completed())
        self.assertEqual(runner.report.results[-2].status,'stopped')
        self.assertEqual(runner.report.results[-1].status,'skipped')
        workflows['city_fund'].city_fund.assert_not_called()

    def test_stop_before_first_cleanup_also_records_unexecuted_tasks(self):
        runner, context, workflows = self.make_runner()
        runner.checkpoint.side_effect = Stopped('已停止')
        with self.assertRaises(Stopped):
            runner._run_finish_rewards(context)
        self.assertEqual(runner.report.results[-2].status,'stopped')
        workflows['weekly'].weekly.assert_not_called()
        workflows['city_fund'].city_fund.assert_not_called()

    def test_f9_reuses_same_workflow_state_without_replaying_previous_task(self):
        runner, context, workflows = self.make_runner()
        workflows['city_fund'].city_fund.side_effect = [InputInterrupted('暂停'), TaskResult('city_fund','ok','恢复完成')]
        runner._run_finish_rewards(context)
        self.assertEqual(workflows['city_fund'].city_fund.call_count,2)
        workflows['weekly'].weekly.assert_called_once()
        self.assertEqual(runner._reward_workflow.call_count,2)

    def test_unverified_daily_never_becomes_verified_due_to_bonus_success(self):
        runner, context, _ = self.make_runner(verified=False)
        runner.report.status = 'partial'
        runner._run_finish_rewards(context)
        self.assertFalse(runner._daily_completed())
        self.assertEqual(runner.report.status,'partial')

    def test_failure_evidence_error_does_not_erase_cleanup_result(self):
        runner, context, workflows = self.make_runner()
        context.last_scene = object()
        context.evidence.side_effect = OSError('磁盘错误')
        workflows['weekly'].weekly.side_effect = AutomationError('页面错误')
        runner._run_finish_rewards(context)
        self.assertEqual(runner.report.results[-2].status,'partial')
        self.assertEqual(runner.report.status,'success')

    def test_complete_runner_checks_cleanup_even_when_daily_precheck_is_terminal(self):
        # Reuse the established Win32/OCR-mocked run fixture, but inject the
        # new cleanup at its real location in Runner.run.
        harness = test_completion.CompletionTests()
        harness.setUpClass()
        calls = []
        original = Runner._run_finish_rewards
        def cleanup(runner, context):
            self.assertTrue(runner._daily_completed())
            calls.append('cleanup')
            runner.report.results.append(TaskResult('weekly','ok','无可领取奖励'))
        from unittest.mock import patch
        def configure(runner, workflow, context, window):
            workflow.daily_already_complete.side_effect = lambda: harness.verify_daily(runner) or True
        with patch.object(Runner,'_run_finish_rewards',cleanup):
            _, report, stored, _, workflow, _, _ = harness.run_mock(configure)
        self.assertEqual(calls,['cleanup'])
        self.assertEqual(report.results[-1].task,'weekly')
        self.assertEqual(stored['results'][-1]['task'],'weekly')
        workflow.scratch.assert_not_called()
        self.assertEqual(Runner._run_finish_rewards, original)
