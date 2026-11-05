import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from autozzz.onedragon_run_ui import ExecutionWindow


class SingleBattleUiTests(unittest.TestCase):
    def start_fixture(self, app_id, *, selected):
        return SimpleNamespace(app_id=app_id, prepared=True, busy=False, closing=False, finish_display=None,
                               resource_required=False, timeout=SimpleNamespace(get=lambda: '600'), uses_charge=False,
                               finish_after_battle=SimpleNamespace(get=lambda: selected), finish_selector=None,
                               auto_launch=SimpleNamespace(get=lambda: True), session=Mock(), status=Mock(),
                               _update_finish_display=Mock(), update_buttons=Mock())

    def test_single_battle_requests_one_shot_without_launching_the_game(self):
        view = self.start_fixture('auto_battle', selected=True)
        ExecutionWindow.start(view)
        view.session.send.assert_called_once_with('start', app_id='auto_battle', budget=None, timeout_seconds=600,
                                                  auto_launch=False, resource_confirmation=None,
                                                  finish_after_battle=True)

    def test_continuous_and_other_apps_keep_their_start_arguments(self):
        for app_id, selected in (('auto_battle', False), ('dodge_assistant', True), ('one_dragon', True)):
            with self.subTest(app_id=app_id):
                view = self.start_fixture(app_id, selected=selected)
                ExecutionWindow.start(view)
                view.session.send.assert_called_once_with('start', app_id=app_id, budget=None, timeout_seconds=600,
                                                          auto_launch=True, resource_confirmation=None)

    def test_state_reports_verified_victory_and_respects_pause_and_failure(self):
        cases = (
            ('running', 'waiting', {}, '等待识别战斗', ''),
            ('running', 'fighting', {}, '正在接管战斗', ''),
            ('running', 'fighting', {'manual_paused': True}, '已暂停', ''),
            ('success', 'victory', {}, '战斗胜利', ''),
            ('failed', 'victory', {}, '运行失败', '故障原因'),
            ('failed', 'unverified', {}, '战斗结算未核实', '故障原因'),
            ('stopped', 'stopped', {}, '已停止', ''),
        )
        for state, battle, control, title, detail in cases:
            with self.subTest(state=state, battle=battle, control=control):
                view = SimpleNamespace(app_id='auto_battle', closed=False, closing=False, events=queue.Queue(),
                                       ui=SimpleNamespace(closing=False, overlay=Mock(), root=Mock()), window=Mock(),
                                       trace_window=None, battle_monitor=None, rotation_table=None, login_mode=None,
                                       budget_note=Mock(), mode_selector=Mock(), resource_check=Mock(),
                                       resource_accepted=Mock(), resource_confirmation_id='', resource_text=Mock(),
                                       poll=Mock(), status=Mock(), update_buttons=Mock())
                view.events.put(dict(type='state', state=dict(state=state, busy=state == 'running', control=control,
                                                              single_battle={'state': battle}, error=detail)))
                ExecutionWindow.poll(view)
                view.status.set_status.assert_called_once_with(title, detail)


if __name__ == '__main__':
    unittest.main()
