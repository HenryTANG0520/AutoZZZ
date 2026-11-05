"""Replay the pinned SDK's repeat and parent charge nodes with fake screen evidence."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'vendor/onedragon/src')]

from autozzz.battle_budget import BattleBudget
from autozzz.onedragon_charge import ChargeBudgetControl
from autozzz.onedragon_control import ExecutionControl, UpstreamControlAdapter, WindowState
from autozzz.onedragon_restore import BACKUP, NONE
from one_dragon.base.operation.operation_base import OperationResult
from one_dragon.base.operation.operation_round_result import OperationRoundResultEnum
from zzz_od.application.charge_plan.charge_plan_app import ChargePlanApp
from zzz_od.operation.challenge_mission.check_next_after_battle import ChooseNextOrFinishAfterBattle
from zzz_od.operation.restore_charge import RestoreCharge

REPEAT = ChargeBudgetControl.REPEAT_BUTTON
FINISH = ChargeBudgetControl.FINISH_BUTTON
RECOVERY = '标题-恢复电量'


def replay(directory, restore_enabled):
    config_path = directory / 'charge-plan.yml'
    mode = BACKUP if restore_enabled else NONE
    config_path.write_text(f'restore_charge: {mode}\n', encoding='utf-8')
    config = SimpleNamespace(file_path=str(config_path), restore_charge=mode, is_restore_charge_enabled=restore_enabled)
    ctx = SimpleNamespace(current_instance_idx=1, run_context=SimpleNamespace(
        get_config=lambda *args, **kwargs: config, get_run_record=lambda **_: None,
    ))
    control = ExecutionControl(lambda: WindowState(True, True, (0, 0, 1920, 1080)),
                               lambda: None, lambda: None, lambda: None, lambda: None)
    budget = BattleBudget(directory, 'fixture', 'cn', 40, 2)
    charge = ChargeBudgetControl(ctx, control, budget, 20, mode, restore_enabled)
    app = ChargePlanApp(ctx)
    repeat = ChooseNextOrFinishAfterBattle(ctx, True)
    plan = SimpleNamespace(uid='fixture-plan')
    clicks = []
    scope = UpstreamControlAdapter(control)
    scope.install()
    charge.install()
    try:
        def prepare(op):
            op._init_network()
            op._after_round_wait = lambda **_: None
            op.screenshot = lambda: control.observe(lambda: None)

        prepare(repeat)
        prepare(app)

        def find(frame, _screen, area):
            if area in frame:
                return repeat.round_success(area)
            return repeat.round_retry(area)

        def click(frame, _screen, area, **_):
            assert area in frame, (area, frame)
            control.input(lambda: clicks.append(area))
            return repeat.round_success(area)

        repeat.round_by_find_area = find
        repeat.round_by_find_and_click_area = click

        def step(op, name, *frame):
            op._current_node = op._node_map[name]
            op.round_start_time = time.time()
            op.last_screenshot = frame
            return op._execute_one_round()

        control.observe(lambda: None)
        charge._reserve(SimpleNamespace(plan=plan), 20)
        budget.settle(charge.reservation, None)
        charge.reservation = None

        sent = step(repeat, '判断再来一次', REPEAT, FINISH)
        assert sent.is_success and sent.status == REPEAT and clicks == [REPEAT]
        assert repeat._get_next_node(sent).cn == '恢复电量'
        entry = budget.pending[0].copy()
        stale = step(repeat, '判断再来一次', REPEAT, FINISH)
        assert stale.result is OperationRoundResultEnum.RETRY and clicks == [REPEAT]

        if restore_enabled:
            nested = RestoreCharge(ctx)
            nested.is_after_battle_retry = True
            nested.round_by_find_area = find
            nested.round_by_find_and_click_area = click
            prepare(nested)
            hidden_popup = step(nested, '打开恢复界面', REPEAT)
            assert hidden_popup.result is OperationRoundResultEnum.RETRY and clicks == [REPEAT]
            # Source still selects the real graph edge. Replenishment itself is an isolated result fixture.
            with patch.object(RestoreCharge, 'execute', return_value=OperationResult(
                    success=True, status=RestoreCharge.STATUS_RESTORE_SUCCESS)):
                recovered = step(repeat, '恢复电量', RECOVERY)
                assert recovered.is_success and recovered.status == FINISH and repeat.try_next
                assert repeat._get_next_node(recovered).cn == '判断再来一次'
                retried = step(repeat, '判断再来一次', REPEAT, FINISH)
                assert retried.is_success and retried.status == REPEAT and clicks == [REPEAT, REPEAT]
                blocked = step(repeat, '恢复电量', RECOVERY)
                assert blocked.is_fail and control.stopped and '电量不足' in control.error
        else:
            cancelled = step(repeat, '恢复电量', RECOVERY, '取消')
            assert cancelled.is_success and cancelled.status == FINISH and not repeat.try_next
            assert repeat._get_next_node(cancelled).cn == '判断再来一次'
            finished = step(repeat, '判断再来一次', REPEAT, FINISH)
            assert finished.is_success and finished.status == FINISH
            assert clicks == [REPEAT, '取消', FINISH]

            app.current_plan = plan
            app._current_node = app._previous_node = app._node_map['区域巡防']
            app._previous_round_result = app.round_success(finished.status)
            assert app._get_next_node(app._previous_round_result).cn == '挑战完成'
            completion = step(app, '挑战完成')
            assert completion.is_fail and control.error == charge.pending_result_error()

        reopened = BattleBudget(directory, 'fixture', 'cn', 40, 2)
        assert reopened.pending == budget.pending == [entry]
        assert budget.reserved == 40 and budget.rounds_left == 0
        return clicks
    finally:
        charge.close()
        scope.close()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='autozzz-native-charge-') as folder:
        directory = Path(folder)
        cancelled = directory / 'cancelled'
        restored = directory / 'restored'
        cancelled.mkdir()
        restored.mkdir()
        result = dict(cancelled_clicks=replay(cancelled, False), restored_clicks=replay(restored, True),
                      pending_kept=True, game_input_sent=False)
        print(json.dumps(result, ensure_ascii=False))
