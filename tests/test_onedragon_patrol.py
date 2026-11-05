from types import SimpleNamespace
import unittest

from autozzz.localization import language_scope
from autozzz.onedragon_patrol import PatrolOrderControl
from autozzz.onedragon_patrol_results import format_patrol_report, report_patrols


class PatrolReportTests(unittest.TestCase):
    def test_finished_sdk_round_reports_a_failed_route_separately(self):
        control = PatrolOrderControl(SimpleNamespace())
        route = SimpleNamespace(full_id='area_teleport_07', tp_area=SimpleNamespace(full_name='粗加工中心'),
                                tp_name='一楼广场', idx=7)
        progress = SimpleNamespace(dt='20261106', completed_rounds=0, current_round=1,
                                   total_rounds=1, finished=[])
        operation = SimpleNamespace(run_record=progress, route_idx=0, route_list=[],
                                    config=SimpleNamespace(route_list=''))
        sdk_result = SimpleNamespace(success=True, status='全部完成')

        def initialize(op):
            op.route_list.append(route)

        def advance_after_failure(op):
            op.route_idx += 1
            return SimpleNamespace(is_fail=False, status='路线执行失败')

        def execute(op):
            control._initialize(op, initialize)
            control._run_route(op, advance_after_failure)
            progress.completed_rounds = 1
            return sdk_result

        self.assertIs(control._execute(operation, execute), sdk_result)
        report = control.status()
        self.assertEqual(report['outcome'], 'incomplete')
        self.assertTrue(report['sdk_success'])
        self.assertEqual(report['route_counts'], {'failed': 1})
        self.assertEqual(report['routes'], [{'round': 1, 'route_id': route.full_id,
                                             'outcome': 'failed', 'status': '路线执行失败'}])
        self.assertEqual(report['progress_before']['completed_rounds'], 0)
        self.assertEqual(report['progress_after']['completed_rounds'], 1)
        self.assertEqual(report['progress_after']['finished'], [])
        sections = report_patrols({'app_id': 'world_patrol', 'execution': {'world_patrol': report}})
        with language_scope('zh'):
            detail = format_patrol_report(sections, '{}')
        self.assertIn('巡逻未完成', detail)
        self.assertIn('粗加工中心 · 一楼广场 #7 [area_teleport_07]', detail)
        control.reset()
        self.assertIsNone(control.status())

    def test_older_success_report_has_no_verified_route_outcome(self):
        old_report = {'app_id': 'world_patrol', 'status': 'success'}
        self.assertEqual(report_patrols(old_report), [('', {'outcome': 'unrecorded'})])
        debug_report = dict(old_report, connection={'route_debug': {'key': 'area_teleport_07'}})
        self.assertEqual(report_patrols(debug_report), [])
