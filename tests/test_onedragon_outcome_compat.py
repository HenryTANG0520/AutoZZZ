from contextlib import contextmanager
from dataclasses import dataclass
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from autozzz import onedragon_lost_void as lost, onedragon_shiyu as shiyu


@dataclass
class Edge:
    node_from_name: str
    success: bool = True
    status: str | None = None


def sdk_type(*names, **attrs):
    cls = type("SdkFixture", (), attrs)
    for name in names:
        def node(app):
            return app.upstream_result

        node.operation_node_annotation = NS(op_method=node)
        node.operation_edge_annotation = []
        setattr(cls, name, node)
    return cls


def outcome(kind, status=None, data=None, **_):
    return NS(kind=kind, is_success=kind == "success", status=status, data=data)


def application(cls, ctx):
    app = cls()
    app.ctx = ctx
    app.last_screenshot = object()
    app.upstream_result = outcome("success", "SDK original")
    app.round_success = lambda *args, **kwargs: outcome("success", *args, **kwargs)
    app.round_retry = lambda *args, **kwargs: outcome("retry", *args, **kwargs)
    app.round_wait = lambda *args, **kwargs: outcome("wait", *args, **kwargs)
    app.round_fail = lambda *args, **kwargs: outcome("fail", *args, **kwargs)
    return app


@contextmanager
def lost_app():
    ctx = NS()
    cls = sdk_type("init_for_lost_void", "tp_to_lost_void", "check_bounty_commission_before",
                   "claim_all", "back_at_last", STATUS_AGAIN="again", STATUS_AGAIN_MATRIX="matrix")
    level = sdk_type("handle_fail_exit", "handle_challenge_result_finish")
    modules = {
        "one_dragon.base.operation.operation_edge": NS(OperationEdgeDesc=Edge),
        "one_dragon.utils.log_utils": NS(log=Mock()),
        "zzz_od.application.hollow_zero.lost_void.lost_void_app": NS(LostVoidApp=cls),
        "zzz_od.application.hollow_zero.lost_void.operation.lost_void_run_level": NS(LostVoidRunLevel=level),
    }
    app = application(cls, ctx)
    app.config = NS(is_bounty_commission_mode=True, daily_plan_times=1)
    app.run_record = NS(bounty_commission_complete=False, daily_run_times=1, is_finished_by_day=True,
                        update=Mock())
    app.check_and_update_current_screen = Mock()
    with patch.dict(sys.modules, modules), lost.preserve_failed_exit(ctx):
        yield app


@contextmanager
def shiyu_app():
    ctx = NS(screen_loader=NS(get_area=Mock(return_value=NS(rect=object()))),
             ocr=NS(crop_and_run_ocr=Mock()))
    cls = sdk_type("choose_node_idx", "to_next_node", "all_node_finished", "claim_reward",
                   "close_reward", "back_after_all", STATUS_ALL_FINISHED="full")
    modules = {
        "one_dragon.base.operation.operation_edge": NS(OperationEdgeDesc=Edge),
        "one_dragon.base.geometry.point": NS(Point=object),
        "zzz_od.application.shiyu_defense.shiyu_defense_app": NS(ShiyuDefenseApp=cls, MULTI_ROOM_NODES=[5]),
    }
    app = application(cls, ctx)
    app.config = NS(critical_max_node_idx=2)
    data = {}
    app.run_record = NS(data=data, get=data.get, update=data.__setitem__, save=Mock(),
                        get_current_dt=Mock(return_value="20261106"), next_node_idx=Mock(return_value=1),
                        critical_history=[])
    app.round_by_find_and_click_area = Mock(return_value=outcome("success", "select"))
    app.round_by_find_area = Mock(return_value=outcome("success"))
    app.round_by_click_area = Mock(return_value=outcome("retry", "click rejected"))
    with patch.dict(sys.modules, modules), shiyu.preserve_battle_failure(ctx):
        yield app


class LostVoidCompatibilityTests(unittest.TestCase):
    def test_capped_bounty_routes_to_the_entry_or_transport(self):
        with lost_app() as app:
            for screen, method in (("迷失之地-入口-周期", "check_bounty_commission_before"),
                                   (None, "tp_to_lost_void")):
                with self.subTest(screen=screen):
                    app.check_and_update_current_screen.return_value = screen
                    result = app.init_for_lost_void()
                    target = getattr(app, method)
                    self.assertIn(Edge("初始化加载", status=result.status), target.operation_edge_annotation)
            for bounty, limit in ((False, 1), (True, 0), (True, 2)):
                with self.subTest(bounty=bounty, limit=limit):
                    app.config.is_bounty_commission_mode = bounty
                    app.config.daily_plan_times = limit
                    app.check_and_update_current_screen.reset_mock()
                    self.assertIs(app.init_for_lost_void(), app.upstream_result)
                    app.check_and_update_current_screen.assert_not_called()

    def test_partial_bounty_at_cap_routes_to_cleanup(self):
        with lost_app() as app:
            for status in (app.STATUS_AGAIN, app.STATUS_AGAIN_MATRIX):
                with self.subTest(status=status):
                    app.upstream_result = outcome("success", status)
                    result = app.check_bounty_commission_before()
                    self.assertIn(Edge("识别悬赏委托完成进度", status=result.status),
                                  app.back_at_last.operation_edge_annotation)
                    self.assertNotEqual(result.status, status)

    def test_stable_nonempty_slots_require_a_recognized_reward(self):
        level = NS(last_screenshot=object(), last_screenshot_time=1.0,
                   round_by_find_area=lambda *args: outcome("success" if args[-1] in
                       ("标题-挑战结果", "按钮-完成") else "retry"))
        slots = ("270", "4500") + ("EMPTY",) * 6
        with patch.object(lost, "_reward_area", return_value=object()), patch.object(lost, "_reward_slots", return_value=slots):
            results = []
            for capture in range(3):
                level.last_screenshot_time = 1.0 + capture * .5
                results.append(lost._observe_settlement(level))
        self.assertEqual(results, [None, None, lost._INCOMPLETE_REWARDS])


class ShiyuCompatibilityTests(unittest.TestCase):
    def test_first_node_requires_progress_until_a_selection_is_accepted(self):
        with shiyu_app() as app:
            read_progress = app.ctx.ocr.crop_and_run_ocr
            read_progress.return_value = {}
            self.assertEqual(app.choose_node_idx().kind, "retry")
            app.round_by_find_and_click_area.assert_not_called()

            read_progress.return_value = {"0/5": object()}
            self.assertEqual(app.choose_node_idx().kind, "wait")
            self.assertEqual(app.current_node_idx, 1)

            app.node_clicked = True
            read_progress.return_value = {}
            self.assertTrue(app.choose_node_idx().is_success)
            app.round_by_find_and_click_area.assert_called_once()

    def test_inverted_progress_uses_saved_nodes_and_explicit_ten_is_accepted(self):
        with shiyu_app() as app:
            app.run_record.critical_history = [1]
            app.run_record.next_node_idx.return_value = 2
            app.ctx.ocr.crop_and_run_ocr.return_value = {"2/1": object()}
            self.assertEqual(app.choose_node_idx().kind, "wait")
            self.assertEqual(app.current_node_idx, 2)
            self.assertEqual(app.config.critical_max_node_idx, 2)
            app.ctx.ocr.crop_and_run_ocr.return_value = {"10/10": object()}
            self.assertEqual(app.choose_node_idx().status, app.STATUS_ALL_FINISHED)
            self.assertEqual(app.config.critical_max_node_idx, 10)

    def test_rejected_claim_survives_reset_and_requires_verified_progress(self):
        with shiyu_app() as app:
            record = app.run_record
            self.assertEqual(app.claim_reward().kind, "fail")
            self.assertIn("20261106", record.data.values())
            for day, reads in (("20261106", {"0/1": object()}), ("20261107", {})):
                with self.subTest(day=day):
                    record.get_current_dt.return_value = day
                    app.ctx.ocr.crop_and_run_ocr.return_value = reads
                    self.assertEqual(app.choose_node_idx().kind, "retry")
                    app.round_by_find_and_click_area.assert_not_called()
            app.ctx.ocr.crop_and_run_ocr.return_value = {"1/1": object()}
            self.assertEqual(app.choose_node_idx().status, app.STATUS_ALL_FINISHED)
            app.round_by_click_area.return_value = outcome("success", "全部领取")
            self.assertTrue(app.claim_reward().is_success)
            self.assertEqual(record.data, {})
