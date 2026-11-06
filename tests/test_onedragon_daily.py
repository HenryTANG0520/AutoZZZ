import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.onedragon_daily import DailyInterfaceControl


def result(kind, status=None, **kwargs):
    return NS(kind=kind, status=status, is_success=kind=='success', is_fail=kind=='fail')


class DailyInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.clicks=[]
        self.ctx=NS(controller=NS(click=self.clicks.append), ocr=NS(run_ocr=lambda _:{}),
            team_config=NS(get_team_by_idx=lambda _:NS(name='坏女人')))
        self.adapter=DailyInterfaceControl(self.ctx)

    def operation(self, **values):
        return NS(ctx=self.ctx, last_screenshot=object(),
            round_success=lambda status=None,**kw:result('success',status),
            round_fail=lambda status=None,**kw:result('fail',status),
            round_retry=lambda status=None,**kw:result('retry',status),
            round_wait=lambda status=None,**kw:result('wait',status), **values)

    def cards(self):
        self.panel=True
        def find(_screen,_page,area):
            return result('success' if (self.panel if area=='保存方案' else not self.panel) else 'fail')
        def click(_page,area):self.clicks.append(area);return result('success')
        def save(*_):self.clicks.append('保存方案');self.panel=False;return result('success')
        return self.operation(plan=NS(card_num='5'),round_by_find_area=find,
            round_by_click_area=click,round_by_find_and_click_area=save)

    def test_card_clicks_are_split_across_fresh_rounds_and_verified_before_success(self):
        op=self.cards()
        for index in range(11):
            before=len(self.clicks)
            self.assertEqual(self.adapter.choose_cards(op,None).kind,'wait')
            self.assertLessEqual(len(self.clicks)-before,1)
        self.assertEqual(self.clicks,['内层-已选择卡片1']*5+['内层-卡片1']*5+['保存方案'])
        with patch('autozzz.onedragon_charge.ChargeBudgetControl._read_cost',return_value=(208,100)):
            self.assertEqual(self.adapter.choose_cards(op,None).kind,'success')

    def test_card_mismatch_reopens_once_then_fails_without_deploying_or_reserving(self):
        op=self.cards()
        op._autozzz_cards=dict(phase='verify',step=0,attempts=1,rounds=0)
        self.panel=False
        with patch('autozzz.onedragon_charge.ChargeBudgetControl._read_cost',side_effect=ValueError('画面 208/20，计划 100')):
            first=self.adapter.choose_cards(op,None)
            self.assertEqual(first.kind,'wait');self.assertIn('重新配置',first.status)
            self.adapter.choose_cards(op,None)
            self.assertEqual(self.clicks,['外层-卡片1'])
            op._autozzz_cards['phase']='verify'
            self.assertEqual(self.adapter.choose_cards(op,None).kind,'fail')
        self.assertNotIn('下一步',self.clicks)

    def test_missing_card_panel_has_a_bound(self):
        op=self.cards();self.panel=False
        for _ in range(50):self.assertEqual(self.adapter.choose_cards(op,None).kind,'wait')
        self.assertEqual(self.adapter.choose_cards(op,None).kind,'fail')
        self.assertFalse(self.clicks)

    def test_default_card_count_preserves_upstream(self):
        op=self.operation(plan=NS(card_num='默认数量'))
        self.assertEqual(self.adapter.choose_cards(op,lambda _:42),42)

    def test_team_opening_waits_for_visible_readiness_without_repeating_the_click(self):
        ready_area = object()
        self.ctx.screen_loader = NS(get_area=lambda *_: ready_area)

        def read(frame, text, *, area, lcs_percent):
            self.assertEqual((text, area, lcs_percent), ('预备出战', ready_area, 1.))
            return result('success' if frame == 'list' else 'retry')

        def click(frame, screen, name):
            self.assertEqual((frame, screen, name), ('entry', '实战模拟室', '预备编队'))
            self.clicks.append(name)
            op.node_clicked = True
            return result('success', name)

        op = self.operation(start_at_team_list=False, node_clicked=False, STATUS_TEAM_LIST_READY='ready',
                            round_by_ocr=read, round_by_find_and_click_area=click)
        observed = []
        for frame in ('entry', 'blank', 'entry', 'list'):
            op.last_screenshot = frame
            observed.append(self.adapter.wait_for_team_list(op, None))
        self.assertEqual([item.kind for item in observed], ['wait', 'retry', 'retry', 'success'])
        self.assertEqual(observed[-1].status, 'ready')
        self.assertEqual(self.clicks, ['预备编队'])
        op.start_at_team_list = True
        op.node_clicked = False
        op.last_screenshot = 'entry'
        self.assertEqual(self.adapter.wait_for_team_list(op, None).kind, 'retry')
        self.assertEqual(self.clicks, ['预备编队'])

    def test_team_confirmation_requires_the_enabled_list_button_before_any_input(self):
        area = object()
        self.ctx.screen_loader = NS(get_area=lambda *_: area)
        self.ctx.controller.mouse_move = self.clicks.append
        screen_module = NS(ScreenNormalWorldEnum=NS(UID=NS(value=NS(center='uid'))))

        def read_and_click(frame, text, **options):
            self.assertEqual((text, options['area'], options['lcs_percent']), ('预备出战', area, 1.))
            self.assertEqual(options['color_range'], [[180, 180, 180], [255, 255, 255]])
            self.assertEqual(options['pre_delay'], 0)
            if frame != 'selected list':
                return result('retry')
            self.clicks.append(text)
            return result('success', text)

        op = self.operation(round_by_ocr_and_click=read_and_click)
        with patch.dict(sys.modules, {'zzz_od.screen_area.screen_normal_world': screen_module}):
            for frame in ('plain deploy', 'fading list', 'selected list'):
                op.last_screenshot = frame
                self.assertEqual(self.adapter.confirm_team(op, None).is_success, frame == 'selected list')
        self.assertEqual(self.clicks, ['预备出战', 'uid'])

    def team(self,names):
        self.names=names;self.selected=False;self.scrolls=[]
        self.ctx.screen_loader = NS(get_area=lambda *_: object())
        self.markers = self.enterContext(patch.object(self.adapter, '_has_active_team_markers', return_value=True))
        return self.operation(is_shiyu_defense=False,current_target_idx=0,target_team_idx_list=[2],
            round_by_ocr=lambda *_, **__:result('success'),
            TEAM_SLOT_COUNT=6,_get_team_slot_rect_by_idx=lambda i:NS(index=i,center=NS(y=i*150)),
            _find_team_name=lambda _,rect:(self.names[rect.index] if rect.index<len(self.names) else None,None),
            _find_select_button=lambda _,rect:NS(center=NS(y=rect.index*150)),
            _find_selected_button=lambda *_:None,
            _is_team_selected=lambda _:self.selected,
            _scroll_team_list=self.scrolls.append)

    def test_team_list_without_active_markers_never_selects_or_scrolls(self):
        op = self.team(['坏女人'])
        self.markers.return_value = False
        self.assertEqual(self.adapter.choose_team(op, None).kind, 'retry')
        self.names.clear()
        self.assertEqual(self.adapter.choose_team(op, None).kind, 'retry')
        self.assertFalse(self.clicks or self.scrolls)

    def test_name_wins_over_stale_index_and_requires_selected_feedback(self):
        op=self.team(['其他',' 坏 女 人 ','错误序号2'])
        self.assertEqual(self.adapter.choose_team(op,None).kind,'wait')
        self.assertEqual(self.clicks[0].y,150)
        self.assertEqual(op.current_target_idx,0)
        self.adapter.choose_team(op,None)
        self.assertEqual(len(self.clicks),1)
        self.selected=True;self.adapter.choose_team(op,None)
        self.assertEqual(op.current_target_idx,1)

    def test_duplicate_names_never_choose_arbitrarily(self):
        op=self.team(['坏女人','坏女人'])
        self.assertEqual(self.adapter.choose_team(op,None).kind,'fail')
        self.assertFalse(self.clicks)

    def test_absent_team_searches_up_then_down_and_stops(self):
        op=self.team(['其他'])
        terminal=None
        for _ in range(20):
            terminal=self.adapter.choose_team(op,None)
            if terminal.kind=='fail':break
        self.assertEqual(terminal.kind,'fail')
        self.assertIn(1,self.scrolls);self.assertIn(-1,self.scrolls)
        self.assertFalse(self.clicks)

    def test_shiyu_team_scoring_uses_original(self):
        op=self.team([]);op.is_shiyu_defense=True
        self.assertEqual(self.adapter.choose_team(op,lambda _:42),42)

    def test_already_selected_card_does_not_toggle_off(self):
        op=self.team(['坏女人'])
        op._find_select_button=lambda *_:None
        op._find_selected_button=lambda _,rect:NS(center=NS(y=rect.center.y))
        self.adapter.choose_team(op,None)
        self.assertEqual(op.current_target_idx,1);self.assertFalse(self.clicks)

    def test_other_card_selected_mark_is_not_target_selection(self):
        op=self.team(['坏女人'])
        op._find_select_button=lambda *_:None
        op._find_selected_button=lambda *_:NS(center=NS(y=600))
        self.assertEqual(self.adapter.choose_team(op,None).kind,'retry')
        self.assertEqual(op.current_target_idx,0)

    def test_renamed_or_missing_saved_name_never_falls_back(self):
        op=self.team(['坏女人']);self.ctx.team_config.get_team_by_idx=lambda _:None
        self.assertEqual(self.adapter.choose_team(op,None).kind,'fail');self.assertFalse(self.clicks)
