"""固定 SDK 的日常界面适配；保持出战预算与实际领取终态的区别。"""
from __future__ import annotations

import copy
from functools import wraps


def normalized_name(value):
    return ''.join(str(value or '').split()).casefold()


class TransportConfigView:
    """仅本次应用使用的兼容传送点，不写回玩家配置。"""
    def __init__(self, original, point):
        self.original, self.transport_point = original, point

    def __getattr__(self, name):
        return getattr(self.original, name)


class DailyInterfaceControl:
    def __init__(self, ctx):
        self.ctx = ctx
        self.originals = []
        self.fallbacks = []
        self.engagement = {}

    def patch(self, cls, name, handler):
        original = getattr(cls, name)
        @wraps(original)
        def replacement(operation, *args, **kwargs):
            if operation.ctx is not self.ctx:
                return original(operation, *args, **kwargs)
            return handler(operation, original, *args, **kwargs)
        annotation = copy.copy(original.operation_node_annotation)
        annotation.op_method = replacement
        replacement.operation_node_annotation = annotation
        self.originals.append((cls, name, original))
        setattr(cls, name, replacement)

    def choose_cards(self, op, original):
        """每次点击后让节点重新截图；保存后核实电量，失败最多重配一次。"""
        if op.plan.card_num == '默认数量':
            return original(op)
        state = getattr(op, '_autozzz_cards', None)
        if state is None:
            state = op._autozzz_cards = dict(phase='clear', step=0, attempts=1, rounds=0)
        state['rounds'] += 1
        if state['rounds'] > 50:
            return op.round_fail('怪物卡配置未在限定轮次内完成')
        panel = op.round_by_find_area(op.last_screenshot, '实战模拟室', '保存方案').is_success
        if state['phase'] == 'verify':
            if panel:
                return op.round_wait('等待怪物卡方案关闭', wait=.5)
            if not op.round_by_find_area(op.last_screenshot, '实战模拟室', '下一步').is_success:
                return op.round_wait('等待怪物卡配置结果', wait=.5)
            try:
                from .onedragon_charge import ChargeBudgetControl
                probe = getattr(self.ctx,'autozzz_charge_control',None)
                if probe is None:
                    probe = object.__new__(ChargeBudgetControl)
                    probe.ctx = self.ctx
                available, cost = probe._read_cost(op)
            except ValueError as exc:
                if state['attempts'] >= 2:
                    return op.round_fail(str(exc))
                state.update(phase='reopen', step=0, attempts=state['attempts']+1)
                return op.round_wait(str(exc)+' 重新配置怪物卡', wait=.5)
            return op.round_success(f'已核实 {op.plan.card_num} 张怪物卡，电量 {available}/{cost}')
        if state['phase'] == 'reopen':
            if panel:
                state.update(phase='clear', step=0)
                return op.round_wait('重新选择怪物卡', wait=.5)
            op.round_by_click_area('实战模拟室', '外层-卡片1')
            return op.round_wait('打开怪物卡配置', wait=.8)
        if not panel:
            return op.round_wait('等待怪物卡配置页面', wait=.5)
        if state['phase'] == 'clear':
            if state['step'] < 5:
                op.round_by_click_area('实战模拟室', '内层-已选择卡片1')
                state['step'] += 1
                return op.round_wait('清空已选怪物卡', wait=.7)
            state.update(phase='add', step=0)
        if state['phase'] == 'add':
            if state['step'] < int(op.plan.card_num):
                op.round_by_click_area('实战模拟室', '内层-卡片1')
                state['step'] += 1
                return op.round_wait(f'选择怪物卡 {state["step"]}/{op.plan.card_num}', wait=.7)
            result = op.round_by_find_and_click_area(op.last_screenshot, '实战模拟室', '保存方案')
            if result.is_success:
                state['phase'] = 'verify'
            return op.round_wait('保存并核实怪物卡方案', wait=1)

    def choose_team(self, op, original):
        # 防卫战仍使用上游的多队评分；普通刷本严格按用户保存的真实名称。
        if op.is_shiyu_defense or op.current_target_idx >= len(op.target_team_idx_list):
            return original(op)
        state = getattr(op, '_autozzz_team', None)
        if state is None or state['target'] != op.current_target_idx:
            team = self.ctx.team_config.get_team_by_idx(op.target_team_idx_list[op.current_target_idx])
            if team is None or not normalized_name(team.name):
                return op.round_fail('请先保存实际预编队伍名称')
            state = op._autozzz_team = dict(target=op.current_target_idx, name=team.name,
                rounds=0, direction=1, last=None, same=0, down=False, pending=None)
        state['rounds'] += 1
        if state['rounds'] > 24:
            return op.round_fail('未能按名称核实预编队伍：'+state['name'])
        ocr = self.ctx.ocr.run_ocr(op.last_screenshot)
        slots = []
        matches = []
        for index in range(op.TEAM_SLOT_COUNT):
            rect = op._get_team_slot_rect_by_idx(index)
            name, _ = op._find_team_name(ocr, rect)
            slots.append(normalized_name(name))
            if name and normalized_name(name) == normalized_name(state['name']):
                matches.append(rect)
        if len(matches) > 1:
            return op.round_fail('预编队伍名称重复，无法唯一选择：'+state['name'])
        if len(matches) == 1:
            rect = matches[0]
            if state['pending'] is not None:
                op.pending_select_button_center = state['pending']
                if op._is_team_selected(ocr):
                    op.pending_select_button_center = None
                    op.current_target_idx += 1
                    return op.round_wait('已按名称核实预编队伍：'+state['name'], wait=.5)
                return op.round_wait('等待预编队伍选中反馈：'+state['name'], wait=.5)
            button = op._find_select_button(ocr, rect)
            selected = op._find_selected_button(ocr, rect)
            # 已选标记必须在此卡片附近，不能把同列其他队伍当作目标。
            if button is None and selected is not None and abs(selected.center.y-rect.center.y) <= 120:
                op.current_target_idx += 1
                return op.round_wait('目标预编队伍已选中：'+state['name'], wait=.5)
            if button is None or abs(button.center.y-rect.center.y) > 120:
                return op.round_retry('目标队伍卡片未找到 SELECT：'+state['name'], wait=.5)
            self.ctx.controller.click(button.center)
            state['pending'] = button.center
            return op.round_wait('选择预编队伍：'+state['name'], wait=.7)
        if state['pending'] is not None:
            return op.round_retry('选择后目标队名不可见，未确认队伍', wait=.5)
        fingerprint = tuple(slots)
        state['same'] = state['same']+1 if fingerprint == state['last'] else 0
        state['last'] = fingerprint
        if state['same'] >= 2:
            if state['down']:
                return op.round_fail('未找到预编队伍：'+state['name'])
            state.update(direction=-1, down=True, same=0, last=None)
        op._scroll_team_list(state['direction'])
        return op.round_wait('按名称查找预编队伍：'+state['name'], wait=1)

    def install(self):
        from one_dragon.base.geometry.rectangle import Rect
        from zzz_od.operation.compendium.combat_simulation import CombatSimulation
        from zzz_od.operation.choose_predefined_team import ChoosePredefinedTeam
        from zzz_od.application.engagement_reward.engagement_reward_app import EngagementRewardApp
        from zzz_od.operation.map_transport import MapTransport
        from zzz_od.application.coffee.coffee_app import CoffeeApp
        from zzz_od.application.coffee.coffee_config import CoffeeTransportPoint
        from zzz_od.application.random_play.random_play_app import RandomPlayApp
        from zzz_od.application.random_play.random_play_config import RandomPlayTransportPoint
        from zzz_od.application.daily_signin.daily_signin_app import DailySignInApp

        unavailable = {'未找到目标地图区域', '未找到目标传送点'}

        def compatible_transport(op, original, point):
            result = original(op)
            if result.is_success or result.status not in unavailable or op.config.transport_point == point:
                return result
            previous = op.config.transport_point
            op.config = TransportConfigView(op.config, point)
            self.fallbacks.append(dict(app_id=op.app_id, original=previous,
                fallback=point, reason=result.status))
            return original(op)

        def compatible_signin(op, original):
            result = original(op)
            if result.is_success or result.status not in unavailable or op.config.selected_sign == 'scratch_card':
                return result
            # 尚未开始签到交互；仅地图不存在才回退同一每日额度的六分街刮卡。
            app = self.ctx.run_context.get_application(app_id='scratch_card',
                instance_idx=op.instance_idx, group_id=op.group_id)
            self.fallbacks.append(dict(app_id='daily_signin', original=op.config.selected_sign,
                fallback='scratch_card', reason=result.status))
            return op.round_by_op_result(app.execute())

        def reward_result(op, original):
            result = original(op)
            if result.is_success and result.status == EngagementRewardApp.STATUS_CLAIM_SUCCESS:
                op._autozzz_reward_claimed = True
            return result

        def begin_reward(op, original):
            op._autozzz_reward_claimed = False
            return original(op)

        def enter_cards(op, original):
            op.__dict__.pop('_autozzz_cards', None)
            return original(op)

        def check_reward_tiers(op, _original):
            area = self.ctx.screen_loader.get_area('快捷手册', '活跃度奖励-4')
            if area is None or not area.is_template_area:
                return op.round_fail('未配置日常奖励已领取标记')
            # 固定 SDK 的 1080p 奖励栏每档间隔 274 像素，四档共用同一已领取模板。
            rect = area.rect
            tiers = []
            for tier in range(1, 5):
                offset = (4 - tier) * 274
                region = Rect(rect.x1 - offset, rect.y1, rect.x2 - offset, rect.y2)
                match = self.ctx.tm.crop_and_match_template(
                    op.last_screenshot, region, area.template_sub_dir, area.template_id,
                    threshold=area.template_match_threshold,
                ).max
                if match is not None:
                    tiers.append(tier)
            if len(tiers) == 4:
                return op.round_success('活跃度已满，四档日常奖励均已领取', data=tiers)
            return op.round_fail('未核实四档日常奖励全部领取', data=tiers)

        def reward_done(op, original):
            verification = op.previous_node
            tiers = verification.data or []
            result = original(op)
            completed = verification.is_success and result.is_success
            claimed = getattr(op, '_autozzz_reward_claimed', False)
            self.engagement = dict(activity_full=4 in tiers, claimed_current_rewards=claimed,
                daily_complete=completed, verified_reward_tiers=tiers)
            if verification.is_success:
                return op.round_success(verification.status) if completed else result
            detail = verification.status or '未核实每日完成'
            if claimed:
                detail = f'本次已确认奖励领取弹窗。{detail}'
            return op.round_fail(detail)

        def map_area(op, original):
            # 记录每次观察到的区域与滑动方向，便于实机失败时查明地图/账号差异。
            from one_dragon.utils.log_utils import log
            result = original(op)
            log.info('AutoZZZ 地图区域查找: target=%s visible=%s direction=%s reversed=%s status=%s',
                op.area_name, sorted(op._last_area_idx_set or []), op._area_search_drag_x,
                op._area_search_reversed, result.status)
            return result

        self.patch(CombatSimulation, 'choose_card_num', self.choose_cards)
        self.patch(CombatSimulation, 'click_card', enter_cards)
        self.patch(ChoosePredefinedTeam, 'choose_team', self.choose_team)
        self.patch(EngagementRewardApp, 'check_reward', reward_result)
        self.patch(EngagementRewardApp, 'back_at_first', begin_reward)
        self.patch(EngagementRewardApp, 'check_engagement', check_reward_tiers)
        self.patch(EngagementRewardApp, 'back_afterwards', reward_done)
        self.patch(MapTransport, 'choose_area', map_area)
        self.patch(CoffeeApp, 'transport', lambda op, original:
            compatible_transport(op, original, CoffeeTransportPoint.POINT_1.value.value))
        self.patch(RandomPlayApp, 'transport', lambda op, original:
            compatible_transport(op, original, RandomPlayTransportPoint.POINT_1.value.value))
        self.patch(DailySignInApp, 'run_sub_app', compatible_signin)

    def reset(self):
        self.fallbacks.clear()
        self.engagement.clear()

    def status(self):
        return dict(transport_fallbacks=[dict(item) for item in self.fallbacks],engagement=dict(self.engagement))

    def close(self):
        for cls, name, original in reversed(self.originals):
            setattr(cls, name, original)
        self.originals.clear()
