"""Use OneDragon's conditional strategy for the current ordinary fight only."""
from __future__ import annotations

import threading
import time
from unicodedata import normalize

from one_dragon.base.operation.application_base import Application
from one_dragon.base.operation.operation import Operation
from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.screen import screen_utils
from one_dragon.utils.i18_utils import gt
from zzz_od.application.battle_assistant.auto_battle.auto_battle_const import APP_ID
from zzz_od.application.battle_assistant.battle_assistant_input_mode import apply_battle_assistant_input_mode

from .onedragon_gamepad import release_controller_inputs


class SingleBattleApplication(Application):
    START_HUD_WAIT_SECONDS = 20
    LOST_HUD_WAIT_SECONDS = 8
    WAIT_INTERVAL = 0.1
    VICTORY = '普通战斗胜利'
    WITHDRAWN = '战斗已撤退'
    NO_BATTLE = '未识别到可接管的普通战斗画面'
    LOST_BATTLE = '战斗画面丢失，已停止接管'

    def __init__(self, ctx, *, battle_input, on_state):
        super().__init__(ctx, APP_ID, op_name='接管当前战斗',
                         need_check_game_win=False, use_factory_run_record=False)
        self.control = ctx.execution_control
        self.battle_input = battle_input
        self.battle = ctx.auto_battle_context
        self.on_state = on_state
        self._engine_lock = threading.RLock()
        self.handle_init()

    def handle_init(self):
        # Source application hooks would read notification settings and update
        # application records. The custom run slot already owns the lifecycle.
        self._state = None
        self._entered_battle = False
        self._engine_epoch = None
        self._wait_epoch = None
        self._wait_started = None
        self._finished = False

    def _publish(self, state):
        if self._state != state:
            self._state = state
            self.on_state(state)

    @operation_node(name='加载当前战斗策略', is_start_node=True, screenshot_before_round=False)
    def prepare_policy(self):
        self._publish('waiting')
        success, status = apply_battle_assistant_input_mode(self.ctx)
        if not success:
            return self.round_fail(status)
        config = self.ctx.battle_assistant_config
        self.battle.init_auto_op(sub_dir='auto_battle', op_name=config.auto_battle_config)
        return self.round_success()

    def _find_text(self, screen_name, area_name):
        area = self.ctx.screen_loader.get_area(screen_name, area_name)
        if area is None or not area.is_text_area:
            return False
        expected = ''.join(normalize('NFKC', gt(area.text, 'game')).casefold().split())
        results = self.ctx.ocr_service.get_ocr_result_list(image=self.last_screenshot, rect=area.rect,
                                                           color_range=area.color_range)
        return any(''.join(normalize('NFKC', result.data).casefold().split()) == expected for result in results)

    def _screen_state(self, hud):
        if hud:
            # Use the source's fast HUD path during battle. OCR is needed only
            # for a conflicting menu template or after the attack icon vanishes.
            menu_back = screen_utils.find_area(self.ctx, self.last_screenshot, '战斗-菜单', '菜单-返回')
            if menu_back is screen_utils.FindAreaResultEnum.TRUE:
                if self._find_text('战斗-菜单', '按钮-战况') and self._find_text('战斗-菜单', '按钮-设置'):
                    return 'waiting'
            return 'fighting'

        # The fixed source's loose LCS lets 出战 match 退出. Keep its screen
        # regions and OCR, and require whole labels plus supporting evidence.
        finish = self._find_text('战斗画面', '战斗结果-完成')
        retreat = self._find_text('战斗画面', '战斗结果-撤退')
        if finish and retreat:
            return 'waiting'
        title = self._find_text('战斗-挑战结果-失败', '标题-挑战结果')
        if retreat and (title or self._find_text('战斗画面', '战斗结果-倒带')):
            return 'withdrawn'
        if finish and (title or self._find_text('战斗画面', '战斗结果-再来一次')):
            return 'victory'
        if title and self._find_text('战斗-挑战结果-失败', '按钮-退出'):
            return 'withdrawn'
        return 'waiting'

    def _suspend(self):
        with self._engine_lock:
            # Revoke the previous frame's permit before stopping the source
            # executor. A pending atomic can run concurrently with recognition.
            self.battle_input.revoke_frame('single_battle_waiting')
            was_running = self._engine_epoch is not None
            self._engine_epoch = None
            if was_running:
                try:
                    self.battle.stop_auto_battle()
                finally:
                    release_controller_inputs(self.ctx.controller)
            return was_running

    def _start_from_frame(self):
        with self._engine_lock:
            self.control.checkpoint(require_frame=True)
            epoch = self.control.task_epoch
            if self._engine_epoch == epoch:
                return
            self._suspend()
            self._engine_epoch = epoch
            if self._entered_battle:
                self.battle.resume_auto_battle()
            else:
                self.battle.start_auto_battle()
                self.battle.auto_ultimate_enabled = self.ctx.battle_assistant_config.auto_ultimate_enabled
                self._entered_battle = True

    def _unverified(self):
        self._publish('unverified')
        return self.round_fail(self.LOST_BATTLE if self._entered_battle else self.NO_BATTLE)

    @node_from(from_name='加载当前战斗策略')
    @operation_node(name='识别当前战斗', mute=True)
    def watch_battle(self):
        self.control.checkpoint(require_frame=True)
        hud = self.battle.is_normal_attack_btn_available(self.last_screenshot)
        chain = None
        if not hud and self._entered_battle:
            chain = self.battle_input.observe_chain_frame(self.battle, self.last_screenshot, self.last_screenshot_time)
        # The chain probe also rules out overlapping menus and result labels.
        interrupted = self._suspend() if not hud and chain is None else False
        state = 'fighting' if chain is not None else self._screen_state(hud)
        self.control.checkpoint(require_frame=True)

        if state == 'fighting':
            self._start_from_frame()
            self.battle.check_battle_state(self.last_screenshot, self.last_screenshot_time,
                                           check_battle_end_normal_result=chain is None, chain_prompt=chain)
            self.control.checkpoint(require_frame=True)
            self._wait_started = None
            self._publish('fighting')
            return self.round_wait(wait_round_time=self.ctx.battle_assistant_config.screenshot_interval)

        if hud:
            interrupted = self._suspend()
        self.control.checkpoint(require_frame=True)
        if state in ('victory', 'withdrawn'):
            if not self._entered_battle:
                return self._unverified()
            self._publish(state)
            if state == 'victory':
                return self.round_success(self.VICTORY)
            return self.round_fail(self.WITHDRAWN)

        self._publish('waiting')
        if interrupted:
            # Fence source async work from the previously verified fight frame
            # before a later HUD can grant permission in a new epoch.
            self.control.discard_frame()
        now = time.monotonic()
        if self._wait_started is None or self._wait_epoch != self.control.epoch:
            self._wait_started = now
            self._wait_epoch = self.control.epoch
        timeout = self.LOST_HUD_WAIT_SECONDS if self._entered_battle else self.START_HUD_WAIT_SECONDS
        if now - self._wait_started >= timeout:
            return self._unverified()
        return self.round_wait(wait_round_time=self.WAIT_INTERVAL)

    def handle_pause(self):
        with self._engine_lock:
            # Ignore a delayed pause callback after a newer round has already
            # restarted from a fresh HUD in the current observation epoch.
            if not self._finished and (self.ctx.run_context.is_context_pause or self._engine_epoch != self.control.epoch):
                self._suspend()

    def handle_resume(self):
        # The next verified HUD round starts the strategy again.
        pass

    def after_operation_done(self, result):
        try:
            with self._engine_lock:
                self._finished = True
                self._suspend()
        finally:
            self.ctx.run_context.event_bus.unlisten_all_event(self)
            Operation.after_operation_done(self, result)


def single_battle_operation(ctx, *, battle_input, on_state):
    return SingleBattleApplication(ctx, battle_input=battle_input, on_state=on_state)
