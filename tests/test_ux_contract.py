import unittest
from autozzz.config import Settings
from autozzz.ux import report_context, recovery_text, exit_text


class ContractTests(unittest.TestCase):
    def test_independent_operations_do_not_require_daily_tasks_or_promoter(self):
        settings = Settings(scratch=False, coffee=False, video=False, daily=False, promoter='invalid draft')
        for operation in ('check', 'launch', 'enter_game'):
            settings.validate(operation)
        with self.assertRaises(ValueError):
            settings.validate('daily')

    def test_unknown_report_never_claims_today_or_success(self):
        self.assertIn('游戏日未核实', report_context({}))
        self.assertEqual(exit_text({}), '游戏保留（未请求自动退出）')

    def test_skipped_exit_reason_is_visible_and_failure_has_next_step(self):
        data = dict(results=[dict(task='video', status='partial', detail='营业结果未核实'),
            dict(task='close_game', status='skipped', detail='已选收尾未核实')])
        self.assertIn('已选收尾', exit_text(data))
        self.assertIn('下一步', recovery_text(data))

    def test_skipped_exit_is_not_presented_as_attempted_or_unconfirmed(self):
        from tests.test_reward_gui import window_state
        ui = window_state()
        ui._finished(dict(status='failed', results=[dict(task='close_game', status='skipped', detail='未发送退出请求')]))
        self.assertIn('游戏保留', ui.summary.get())
        self.assertNotIn('退出未确认', ui.summary.get())
