"""Run the pinned chain pipeline and portrait matcher with isolated fixtures."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from functools import partial
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'vendor' / 'onedragon'
sys.path[:0] = [str(ROOT), str(SOURCE / 'src')]

import cv2
import numpy as np

from autozzz.onedragon_battle import BattleInputControl
from autozzz.onedragon_chain import observe_chain_prompt
from autozzz.onedragon_control import ExecutionControl, ObservationExpired, UpstreamControlAdapter, WindowState
from autozzz.onedragon_host import ControlledDevice
from one_dragon.base.conditional_operation.state_recorder import StateRecord
from one_dragon.base.debug.debug_trace_bus import DebugTraceBus
from one_dragon.base.matcher.template_matcher import TemplateMatcher
from one_dragon.base.screen.screen_loader import ScreenContext
from one_dragon.base.screen.template_loader import TemplateLoader
from one_dragon.utils import os_utils
from zzz_od.auto_battle.auto_battle_agent_context import TeamInfo
from zzz_od.auto_battle.auto_battle_context import AutoBattleContext
from zzz_od.auto_battle.auto_battle_state import BattleStateEnum
from zzz_od.game_data.agent import AgentEnum


def forbidden(*_args, **_kwargs):
    raise AssertionError('The chain probe attempted to send input or publish a battle state.')


CHAIN_POLICY = '''author: AutoZZZ fixture
check_chain_interval: 0
check_end_interval: 0
auto_lock_interval: -1
auto_turn_interval: -1
scenes:
  - triggers: [按键可用-连携技]
    priority: 100
    interval: 0
    handlers:
      - states: "[按键可用-连携技,0,1]"
        operations:
          - op_name: 按键-连携技-左
            way: 按下
            press: 1
          - op_name: 按键-连携技-右
'''


def wait_for(predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('The fixed-SDK chain replay did not reach the expected state.')


class RecordedDevice:
    """Run the same input guard as the real device and record only Python calls."""

    INPUTS = {'chain_left', 'chain_right', 'switch_next', 'switch_prev'}
    RELEASE_ONLY = {'dodge', 'normal_attack', 'special_attack', 'move_w', 'move_a',
                    'move_s', 'move_d', 'switch_backup', 'lock', 'ultimate', 'chain_cancel'}

    def __init__(self, control):
        self.control = control
        self.events = []
        self.keyboard_controller = ControlledDevice(self, control)

    def press(self, key):
        self.events.append(('down', key, self.control.task_epoch))

    def release(self, key):
        self.events.append(('up', key, self.control.task_epoch))

    def action(self, name, *, press=False, press_time=None, release=False):
        if release and not press:
            self.keyboard_controller.release(name)
            return
        if name not in self.INPUTS:
            raise AssertionError(f'Unexpected action during a chain prompt: {name}')
        if not press:
            self.keyboard_controller.tap(name)
            return
        self.keyboard_controller.press(name)
        if press_time is not None:
            try:
                self.control.hold(press_time)
            finally:
                self.keyboard_controller.release(name)

    def __getattr__(self, name):
        if name in self.INPUTS or name in self.RELEASE_ONLY:
            return partial(self.action, name)
        raise AttributeError(name)

    def downs(self):
        return [name for event, name, _epoch in self.events if event == 'down']


def fixture_context(directory):
    for relative in (
        'assets/game_data/screen_info/battle.yml',
        'assets/game_data/screen_info/battle_menu.yml',
        'assets/game_data/screen_info/battle_result_fail.yml',
        'assets/game_data/screen_info/common_deploy.yml',
        'assets/game_data/screen_info/restore_charge.yml',
        'assets/image_analysis_pipelines/战斗-连携条.yml',
    ):
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / relative, target)
    for name in ('anby', 'anton', 'ben'):
        relative = f'assets/template/battle/avatar_chain_{name}'
        shutil.copytree(SOURCE / relative, directory / relative)
    shutil.copytree(SOURCE / 'assets/template/battle/btn_normal_attack',
                    directory / 'assets/template/battle/btn_normal_attack')
    policy_path = directory / 'config/auto_battle/连携回放.yml'
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(CHAIN_POLICY, encoding='utf-8')
    os_utils.set_work_dir(directory)

    from one_dragon.base.cv_process.cv_service import CvService

    loader = ScreenContext()
    loader.reload(from_separated_files=True)
    templates = TemplateLoader()
    ctx = SimpleNamespace(screen_loader=loader, template_loader=templates,
                          tm=TemplateMatcher(templates), debug_trace_bus=DebugTraceBus(), ocr=None)
    ctx.cv_service = CvService(ctx)
    return ctx


def replay_managed(ctx, render, bar, labels, rectangle_key):
    """Exercise source recognizers, event service, strategy, and correction in order."""
    ctx.battle_assistant_config = SimpleNamespace(use_merged_file=False)
    ctx.auto_battle_context = battle = AutoBattleContext(ctx)
    battle.init_screen_area()
    window = [WindowState(True, True, (0, 0, 1920, 1080))]
    control = ExecutionControl(lambda: window[0], lambda: ctx.controller.keyboard_controller.reset(),
                               lambda: True, lambda: None, lambda: None)
    ctx.controller = device = RecordedDevice(control)
    adapter = UpstreamControlAdapter(control)
    gate = BattleInputControl(ctx, control)
    ready = BattleStateEnum.STATUS_CHAIN_READY.value
    candidates = ((1, 'anby'), (2, 'anton'))
    chain_frame = render(candidates, bar.width)
    checks = []

    def observe(screen, *, explicit=True):
        with control.scope():
            stamp, frame = control.observe(lambda: (time.time(), screen))
            options = dict(check_battle_end_normal_result=True, sync=True)
            if explicit:
                options['chain_prompt'] = gate.observe_chain_frame(battle, frame, stamp)
            return battle.check_battle_state(frame, stamp, **options)

    def select_team(front, next_agent, previous):
        battle.agent_context.team_info = TeamInfo([agent.agent_name for agent in (front, next_agent, previous)])
        battle.state_record_service.update_state(StateRecord(f'前台-{front.agent_name}', time.time()))
        battle._last_chain_front_correction_time = 0

    def team_ids():
        return [member.agent.agent_id for member in battle.agent_context.team_info.agent_list]

    def wait_for_release():
        wait_for(lambda: not device.keyboard_controller.pressed)

    def caption(screen_name, area_name):
        area = ctx.screen_loader.get_area(screen_name, area_name)
        labels[rectangle_key(area.rect)] = area.text

    adapter.install()
    gate.install()
    control.poll()
    try:
        with ExitStack() as fixtures:
            for name in ('init_auto_op', 'init_battle_dodge_context', 'start_context_async', 'stop_context'):
                fixtures.enter_context(patch.object(battle.dodge_context, name, return_value=None))
            battle.init_auto_op(sub_dir='auto_battle', op_name='连携回放')
            battle.start_auto_battle()
            fixtures.callback(battle.stop_auto_battle)
            select_team(AgentEnum.BEN.value, AgentEnum.ANBY.value, AgentEnum.ANTON.value)

            # Either source signal alone is insufficient and cannot create a
            # source ready state or permit a conditional task.
            for screen in (render(candidates), render((), bar.width)):
                observe(screen)
                assert not gate.allowed and device.downs() == []
                assert battle.state_record_service.get_state_recorder(ready).last_record_time < 0

            observe(chain_frame)
            wait_for(lambda: device.downs() == ['chain_left'])
            assert gate.allowed and gate.permit_reason == 'chain_visible'
            assert battle.state_record_service.get_state_recorder(ready).last_record_time > 0
            assert battle.state_record_service.get_state_recorder('连携技-1-安比').last_record_time > 0
            assert battle.state_record_service.get_state_recorder('连携技-2-安东').last_record_time > 0
            checks.append('source_chain_ready_drives_conditional_left_action')

            observe(render(()))
            wait_for_release()
            wait_for(lambda: battle.auto_op.running_executor_cnt.get() == 0)
            assert not gate.allowed and device.downs() == ['chain_left']
            assert battle.state_record_service.get_state_recorder(ready).last_record_time <= 0
            checks.append('unknown_frame_releases_input_and_drops_queued_right_action')

            # The same native path with automatic probe invocation should
            # work too. A result overlay retaining both chain cues wins.
            observe(chain_frame, explicit=False)
            wait_for(lambda: device.downs() == ['chain_left', 'chain_left'])
            checks.append('same_source_operator_can_run_a_second_equal_priority_chain')
            caption('战斗画面', '战斗结果-完成')
            observe(chain_frame, explicit=False)
            labels.clear()
            wait_for_release()
            assert battle.last_check_end_result == '普通战斗-完成' and not battle.auto_op.is_running
            assert not gate.allowed and device.downs() == ['chain_left', 'chain_left']
            checks.append('terminal_result_blocks_chain_even_if_the_prompt_remains_visible')

            # A separate case for the SDK's own front-agent correction. It
            # may switch to Ben when current Anby is one of the chain options.
            battle.last_check_end_result = None
            select_team(AgentEnum.ANBY.value, AgentEnum.BEN.value, AgentEnum.ANTON.value)
            observe(chain_frame)
            assert team_ids() == ['ben', 'anton', 'anby']
            assert device.downs() == ['chain_left', 'chain_left', 'switch_next']
            checks.append('fresh_prompt_allows_source_direct_team_correction')

            # Invalidate the frame between the source's asynchronous portrait
            # work and its direct correction, then let the old worker finish.
            select_team(AgentEnum.ANBY.value, AgentEnum.BEN.value, AgentEnum.ANTON.value)
            before_team, before_actions = team_ids(), device.downs()
            entered, continue_match = threading.Event(), threading.Event()
            original = battle._match_chain_agent_in

            def held_match(image, possible_agents):
                entered.set()
                assert continue_match.wait(4)
                return original(image, possible_agents)

            with patch.object(battle, '_match_chain_agent_in', side_effect=held_match):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(observe, chain_frame)
                    try:
                        assert entered.wait(4)
                        window[0] = WindowState(True, False)
                        control.poll()
                        window[0] = WindowState(True, True, (0, 0, 1920, 1080))
                        control.poll()
                        assert not control.status()['fresh_frame']
                    finally:
                        continue_match.set()
                    try:
                        pending.result(timeout=5)
                    except ObservationExpired:
                        pass
            assert device.downs() == before_actions and team_ids() == before_team
            checks.append('focus_change_during_recognition_drops_stale_input_and_team_correction')

            wait_for(lambda: time.time() - battle._last_chain_front_correction_time >= 1)
            observe(chain_frame)
            assert device.downs() == [*before_actions, 'switch_next'] and team_ids()[0] == 'ben'
            checks.append('new_observation_after_focus_recovery_allows_current_prompt')
    finally:
        control.stop()
        gate.close()
        adapter.close()
    assert not device.keyboard_controller.pressed
    return checks


def replay(directory):
    ctx = fixture_context(directory)
    battle = AutoBattleContext(ctx)
    battle.init_screen_area()
    candidates = [(AgentEnum.ANBY.value, None), (AgentEnum.ANTON.value, 'anton')]
    battle.agent_context.get_possible_agent_list = lambda: candidates
    battle._check_chain_bar = battle._check_chain_attack_in_parallel = battle.switch_by_name = forbidden
    battle.state_record_service.update_state = battle.state_record_service.batch_update_states = forbidden

    bar = ctx.screen_loader.get_area('战斗画面', '连携条').rect
    strip_color = cv2.cvtColor(np.array([[[15, 183, 237]]], dtype=np.uint8), cv2.COLOR_HSV2RGB)[0, 0]
    labels = {}

    def rectangle_key(rect):
        return rect.x1, rect.y1, rect.x2, rect.y2

    def ocr(image, rect, **_kwargs):
        label = labels.get(rectangle_key(rect))
        return [SimpleNamespace(data=label)] if label else []

    ctx.ocr_service = SimpleNamespace(get_ocr_result_list=ocr)

    def render(avatars=(), width=0, *, color=strip_color, y_offset=0):
        screen = np.full((1080, 1920, 3), 29, dtype=np.uint8)
        for side, name in avatars:
            area = ctx.screen_loader.get_area('战斗画面', f'连携技-{side}').rect
            template = ctx.template_loader.get_template('battle', f'avatar_chain_{name}').raw
            height, width_px = template.shape[:2]
            x, y = area.center.x - width_px // 2, area.center.y - height // 2
            screen[y:y + height, x:x + width_px] = template
        screen[bar.y1 + y_offset:bar.y2 + y_offset, bar.x1:bar.x1 + width] = color
        return screen

    def probe(frame, stamp=None):
        return observe_chain_prompt(battle, frame, time.time() if stamp is None else stamp)

    one = ((1, 'anby'),)
    both = (*one, (2, 'anton'))
    assert (positive := probe(render(one, bar.width))) is not None and positive.full_bar
    assert positive.bar_pixels == bar.width
    assert [(row.side, row.agent.agent_id) for row in positive.portraits] == [(1, 'anby')]
    assert [(row.side, row.agent.agent_id) for row in probe(render(((2, 'anton'),), bar.width)).portraits] == [(2, 'anton')]

    short = bar.width // 2
    assert (positive := probe(render(both, short))) is not None and not positive.full_bar
    assert [(row.side, row.agent.agent_id) for row in positive.portraits] == [(1, 'anby'), (2, 'anton')]
    assert all(row.confidence > .99 for row in positive.portraits)

    for frame in (render((), bar.width), render(both), render(one, short),
                  render(((1, 'anby'), (2, 'anby')), short),
                  render(both, bar.width, y_offset=4), render(both, bar.width, color=(70, 180, 240)),
                  render(both, bar.width)[:800]):
        assert probe(frame) is None
    assert probe(render(both, bar.width), stamp=time.time() - 2) is None

    overlaid = render(both, bar.width)
    for markers in (
        (('战斗-菜单', '按钮-战况'), ('战斗-菜单', '按钮-设置')),
        (('战斗-挑战结果-失败', '标题-挑战结果'),),
        (('战斗画面', '战斗结果-撤退'),),
        (('通用-出战', '按钮-出战'),),
        (('恢复电量', '标题-恢复电量'),),
    ):
        labels.clear()
        for screen_name, area_name in markers:
            area = ctx.screen_loader.get_area(screen_name, area_name)
            labels[rectangle_key(area.rect)] = area.text
        assert probe(overlaid) is None

    labels.clear()
    snapshot = overlaid.copy()
    assert probe(overlaid) is not None and np.array_equal(overlaid, snapshot)
    managed_checks = replay_managed(ctx, render, bar, labels, rectangle_key)
    return dict(full_bar_single_side=True, shortened_bar_two_distinct_portraits=True,
                insufficient_evidence_rejected=True, pause_results_and_noncombat_rejected=True,
                source_image_preserved=True, managed_checks=managed_checks, game_input_sent=False)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='autozzz-chain-prompt-') as directory:
        print(json.dumps(replay(Path(directory)), ensure_ascii=False))
