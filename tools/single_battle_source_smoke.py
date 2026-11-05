"""Replay the fixed SDK's current-fight operation with simulated frames and input."""
from __future__ import annotations

from contextlib import ExitStack
from functools import partial
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'vendor' / 'onedragon'
sys.path[:0] = [str(ROOT), str(SOURCE / 'src')]

import cv2
import numpy as np

from autozzz.onedragon_battle import BattleInputControl
from autozzz.onedragon_control import ExecutionControl, UpstreamControlAdapter, WindowState
from autozzz.onedragon_single_battle import SingleBattleApplication, single_battle_operation
from one_dragon.base.debug.debug_trace_bus import DebugTraceBus
from one_dragon.base.matcher.template_matcher import TemplateMatcher
from one_dragon.base.operation.application.application_run_context import ApplicationRunContext
from one_dragon.base.operation.context_event_bus import ContextEventBus
from one_dragon.base.screen.screen_loader import ScreenContext
from one_dragon.base.screen.template_loader import TemplateLoader
from one_dragon.utils import os_utils
from zzz_od.auto_battle.auto_battle_agent_context import TeamInfo
from zzz_od.auto_battle.auto_battle_context import AutoBattleContext
from zzz_od.backend.backend_context import RunSlot


POLICY = '''author: AutoZZZ fixture
check_chain_interval: 0
auto_lock_interval: -1
auto_turn_interval: -1
scenes:
  - priority: 1
    interval: 0.02
    handlers:
      - states: "[按键可用-普通攻击,0,1]"
        operations:
          - op_name: 按键-普通攻击
            way: 按下
            press: 1
  - triggers: [按键可用-连携技]
    priority: 100
    interval: 1.5
    handlers:
      - states: "[按键可用-连携技,0,1]"
        operations:
          - op_name: 按键-连携技-左
            way: 按下
            press: 1
'''


def wait_for(predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('固定 SDK 接管回放超时')


def forbidden(*_args, **_kwargs):
    raise AssertionError('接管回放访问了真实设备、通知或运行记录')


def prepare_fixture(directory):
    for filename in ('battle.yml', 'battle_menu.yml', 'battle_result_fail.yml'):
        relative = Path('assets/game_data/screen_info') / filename
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / relative, target)
    for relative in ('assets/template/battle/btn_normal_attack', 'assets/template/menu/back',
                     'assets/template/battle/avatar_chain_anby', 'assets/template/battle/avatar_chain_anton',
                     'assets/template/battle/avatar_chain_ben'):
        shutil.copytree(SOURCE / relative, directory / relative)
    pipeline = Path('assets/image_analysis_pipelines/战斗-连携条.yml')
    (directory / pipeline).parent.mkdir(parents=True)
    shutil.copyfile(SOURCE / pipeline, directory / pipeline)
    policy = directory / 'config' / 'auto_battle' / '单场回放.yml'
    policy.parent.mkdir(parents=True)
    policy.write_text(POLICY, encoding='utf-8')
    os_utils.set_work_dir(directory)


def rectangle_key(rect):
    return rect.x1, rect.y1, rect.x2, rect.y2


class Frames:
    def __init__(self, ctx):
        self.ctx = ctx
        self.frames = {}
        self.labels = {}
        self.captured = []
        self.on_ocr = None
        self.canvas = np.random.default_rng(5).integers(4, 40, (1080, 1920, 3), dtype=np.uint8)
        hud = ('战斗画面', '按键-普通攻击', 'battle', 'btn_normal_attack')
        menu = ('战斗-菜单', '菜单-返回', 'menu', 'back')
        finish = ('战斗画面', '战斗结果-完成', '完成')
        repeat = ('战斗画面', '战斗结果-再来一次', '再来一次')
        title = ('战斗-挑战结果-失败', '标题-挑战结果', '挑战结果')
        retreat = ('战斗画面', '战斗结果-撤退', '撤退')
        rewind = ('战斗画面', '战斗结果-倒带', '倒带')
        exit_button = ('战斗-挑战结果-失败', '按钮-退出', '退出')
        self.add('hud', icons=(hud,))
        chain = (('战斗画面', '连携技-1', 'battle', 'avatar_chain_anby'),
                 ('战斗画面', '连携技-2', 'battle', 'avatar_chain_anton'))
        self.add('chain', icons=chain, chain_bar=True)
        self.add('transition', labels=(title,))
        self.add('win', labels=(finish, repeat))
        self.add('withdraw', labels=(retreat, rewind))
        self.add('failure_exit', labels=(title, exit_button))
        self.add('team', labels=(title, (*exit_button[:2], '出战')))
        self.add('partial', labels=(finish,))
        self.add('menu', icons=(hud, menu), labels=(('战斗-菜单', '按钮-战况', '战况'),
                                                    ('战斗-菜单', '按钮-设置', '设置')))
        self.current = 'team'

    def add(self, name, *, icons=(), labels=(), chain_bar=False):
        frame = self.canvas.copy()
        tag = len(self.frames) + 1
        frame[0, 0, 0] = tag
        for screen, area, directory, template_id in icons:
            rect = self.ctx.screen_loader.get_area(screen, area).rect
            template = self.ctx.template_loader.get_template(directory, template_id).raw
            height, width = template.shape[:2]
            x = rect.center.x - width // 2 if chain_bar else rect.x1
            y = rect.center.y - height // 2 if chain_bar else rect.y1
            frame[y:y + height, x:x + width] = template
        if chain_bar:
            rect = self.ctx.screen_loader.get_area('战斗画面', '连携条').rect
            rgb = cv2.cvtColor(np.array([[[15, 183, 237]]], dtype=np.uint8), cv2.COLOR_HSV2RGB)[0, 0]
            frame[rect.y1:rect.y2, rect.x1:rect.x2] = rgb
        self.labels[tag] = {rectangle_key(self.ctx.screen_loader.get_area(screen, area).rect): value
                            for screen, area, value in labels}
        self.frames[name] = frame

    def get_ocr_result_list(self, *, image, rect, **_kwargs):
        if self.on_ocr is not None:
            self.on_ocr(image)
        text = self.labels[int(image[0, 0, 0])].get(rectangle_key(rect))
        return [SimpleNamespace(data=text)] if text else []

    def capture(self):
        name = self.current
        self.captured.append(name)
        return time.time(), self.frames[name]


class Device:
    def __init__(self, control):
        self.control = control
        self.held = False
        self.downs = 0
        self.ups = 0
        self.actions = []

    def attack(self, press=False, press_time=None, release=False, *, name='normal_attack'):
        if not press:
            assert release
            self.reset()
            return

        def down():
            self.held = True
            self.downs += 1
            self.actions.append(name)

        self.control.input(down)
        try:
            self.control.hold(press_time)
        finally:
            self.reset()

    def reset(self):
        with self.control.lock:
            if self.held:
                self.held = False
                self.ups += 1


class Controller:
    def __init__(self, control, frames):
        self.control = control
        self.frames = frames
        self.keyboard_controller = Device(control)

    def init_before_context_run(self):
        return True

    def screenshot(self):
        return self.control.observe(self.frames.capture)

    def enable_foreground_mode(self):
        pass

    def active_window(self):
        pass

    def normal_attack(self, **kwargs):
        self.keyboard_controller.attack(**kwargs)

    def chain_left(self, **kwargs):
        self.keyboard_controller.attack(name='chain_left', **kwargs)

    def __getattr__(self, name):
        release_only = ('dodge', 'special_attack', 'move_w', 'move_s', 'move_a', 'move_d',
                        'switch_next', 'switch_prev', 'switch_backup', 'lock', 'ultimate',
                        'chain_cancel', 'chain_right')
        if name in release_only:
            return self.release_only
        if name in ('click', 'mouse_move', 'press_key', 'turn_by_distance'):
            return forbidden
        raise AttributeError(name)

    @staticmethod
    def release_only(*, press=False, press_time=None, release=False):
        assert release and not press and press_time is None


def replay(directory):
    prepare_fixture(directory)
    from one_dragon.base.cv_process.cv_service import CvService

    ctx = ContextEventBus()
    ctx.current_instance_idx = 0
    ctx.debug_trace_bus = DebugTraceBus()
    ctx.battle_assistant_config = SimpleNamespace(background_mode=False, auto_battle_config='单场回放',
                                                  auto_ultimate_enabled=False, use_merged_file=False,
                                                  screenshot_interval=.02)
    ctx.model_config = SimpleNamespace(flash_classifier_gpu=False)
    ctx.run_context = ApplicationRunContext(ctx)
    ctx.run_context.get_run_record = forbidden
    ctx.run_context.is_app_need_notify = forbidden
    ctx.notify_config = SimpleNamespace(enable_notify=True)
    ctx.push_service = SimpleNamespace(push_config=SimpleNamespace(send_image=True), push_async=forbidden)
    ctx.screen_loader = ScreenContext()
    ctx.screen_loader.reload(from_separated_files=True)
    ctx.template_loader = TemplateLoader()
    ctx.tm = TemplateMatcher(ctx.template_loader)
    frames = Frames(ctx)
    ctx.ocr_service = frames
    ctx.ocr = None
    ctx.cv_service = CvService(ctx)

    window = [WindowState(True, True, (0, 0, 1920, 1080))]

    def pause_backend():
        if ctx.run_context.is_context_running:
            ctx.run_context.switch_context_pause_and_run()
        return ctx.run_context.is_context_pause

    control = ExecutionControl(lambda: window[0], lambda: ctx.controller.keyboard_controller.reset(),
                               pause_backend, ctx.run_context.switch_context_pause_and_run,
                               ctx.run_context.stop_running)
    ctx.execution_control = control
    ctx.controller = Controller(control, frames)
    ctx.auto_battle_context = battle = AutoBattleContext(ctx)
    battle.init_screen_area()
    gate = BattleInputControl(ctx, control)
    adapter = UpstreamControlAdapter(control)
    slot = RunSlot(ctx)
    device = ctx.controller.keyboard_controller
    state_events = []
    checks = []

    def start(frame):
        control.reset()
        control.poll()
        state_events.clear()
        frames.current = frame
        ok, future = slot._start('fixture', op_factory=partial(single_battle_operation,
                                                                 battle_input=gate,
                                                                 on_state=state_events.append))
        assert ok and future is not None
        return future

    def assert_ended(future, success, status):
        result = future.result(timeout=5)
        assert (result.success, result.status) == (success, status), (result.status, state_events)
        assert not battle.auto_op.is_running and not gate.allowed and not device.held
        assert not any(ctx.run_context.event_bus.callbacks.values())

    def stop_replay():
        control.stop()
        if slot.future is not None:
            slot.future.result(timeout=5)

    adapter.install()
    gate.install()
    try:
        # Keep the actual operator, atomic combat actions, HUD matcher and run
        # slot. Replace peripheral model and audio detectors and OCR tokens.
        with ExitStack() as fixtures:
            for obj, names in ((battle.dodge_context, ('init_auto_op', 'init_battle_dodge_context',
                                                        'start_context_async', 'stop_context',
                                                        'check_dodge_audio', 'check_dodge_flash')),
                               (battle.agent_context, ('check_agent_related',)),
                               (battle.target_context, ('run_all_checks',)),
                               (battle, ('check_quick_assist', 'check_switch_backup'))):
                for name in names:
                    fixtures.enter_context(patch.object(obj, name, return_value=None))
            fixtures.enter_context(patch.object(SingleBattleApplication, 'START_HUD_WAIT_SECONDS', .8))
            fixtures.enter_context(patch.object(SingleBattleApplication, 'LOST_HUD_WAIT_SECONDS', .8))
            fixtures.callback(stop_replay)

            future = start('chain')
            wait_for(lambda: 'chain' in frames.captured)
            assert state_events == ['waiting'] and device.downs == 0
            frames.current = 'hud'
            wait_for(lambda: state_events[-1:] == ['fighting'] and device.held)
            assert battle.auto_op.__class__.__name__ == 'AutoBattleOperator'
            assert battle.auto_ultimate_enabled is False

            window[0] = WindowState(True, False)
            control.poll()
            wait_for(lambda: not device.held and not battle.auto_op.is_running)
            count = device.downs
            frames.current = 'menu'
            window[0] = WindowState(True, True, (0, 0, 1920, 1080))
            control.poll()
            wait_for(lambda: state_events[-1:] == ['waiting'] and 'menu' in frames.captured)
            assert device.downs == count and not battle.auto_op.is_running
            frames.current = 'hud'
            wait_for(lambda: state_events[-1:] == ['fighting'] and device.downs > count)
            checks.append('actual_combat_policy_waits_for_fresh_hud_after_focus_loss')

            battle.agent_context.team_info = TeamInfo(['本', '安比', '安东'])
            frames.current = 'chain'
            wait_for(lambda: device.actions[-1:] == ['chain_left'] and device.held)
            assert state_events[-1:] == ['fighting'] and not future.done()
            checks.append('single_battle_requires_hud_initially_then_accepts_real_chain_evidence')

            frames.current = 'partial'
            wait_for(lambda: state_events[-1:] == ['waiting'] and not device.held)
            assert not future.done() and not gate.allowed
            frames.current = 'transition'
            wait_for(lambda: 'transition' in frames.captured)
            assert not future.done() and not battle.auto_op.is_running
            frames.current = 'win'
            assert_ended(future, True, SingleBattleApplication.VICTORY)
            assert state_events[-1] == 'victory' and frames.current == 'win'
            checks.append('partial_result_releases_input_and_confirmed_victory_stops')

            count = device.downs
            assert_ended(start('win'), False, SingleBattleApplication.NO_BATTLE)
            assert device.downs == count and state_events == ['waiting', 'unverified']
            checks.append('preexisting_result_cannot_report_victory')

            for frame in ('withdraw', 'failure_exit'):
                future = start('hud')
                wait_for(lambda: state_events[-1:] == ['fighting'] and device.held)
                frames.current = frame
                assert_ended(future, False, SingleBattleApplication.WITHDRAWN)
                assert state_events[-1] == 'withdrawn' and frames.current == frame
            checks.append('both_verified_failure_screens_stop_without_result_input')

            future = start('hud')
            wait_for(lambda: state_events[-1:] == ['fighting'] and device.held)
            frames.current = 'team'
            assert_ended(future, False, SingleBattleApplication.LOST_BATTLE)
            assert state_events[-1] == 'unverified'
            checks.append('team_screen_exit_label_does_not_match_withdrawal')

            def interrupt_result(image):
                if image is frames.frames['win']:
                    frames.on_ocr = None
                    window[0] = WindowState(True, False)
                    control.poll()
                    frames.current = 'team'
                    window[0] = WindowState(True, True, (0, 0, 1920, 1080))
                    control.poll()

            future = start('hud')
            wait_for(lambda: state_events[-1:] == ['fighting'] and device.held)
            frames.on_ocr = interrupt_result
            frames.current = 'win'
            assert_ended(future, False, SingleBattleApplication.LOST_BATTLE)
            assert 'victory' not in state_events
            checks.append('focus_change_during_result_ocr_rejects_the_old_result')

            count = device.downs
            captured = len(frames.captured)
            future = start('team')
            wait_for(lambda: len(frames.captured) > captured)
            control.stop()
            assert not future.result(timeout=5).success and not device.held and device.downs == count
            assert 'fighting' not in state_events
            checks.append('stop_while_waiting_never_starts_combat')
    finally:
        gate.close()
        adapter.close()
    assert device.downs == device.ups
    return dict(checks=checks, simulated_presses=device.downs, simulated_releases=device.ups,
                game_input_sent=False)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='autozzz-single-battle-') as directory:
        print(json.dumps(replay(Path(directory)), ensure_ascii=False))
