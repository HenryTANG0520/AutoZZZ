from types import SimpleNamespace as NS
import json
import unittest

from autozzz.onedragon_battle_state import battle_snapshot


def fixture(records=None):
    records=records or {'自定义-能攻击':NS(last_record_time=98.,last_value=3)}
    operator=NS(is_running=True,get_template_name=lambda:'实际策略',running_executor=NS(trigger_time=97.),
                current_execution_info=NS(trigger_display='主循环',expr_display='[自定义-能攻击]',priority_display='99'),usage_states=set(records))
    battle=NS(auto_op=operator,state_record_service=NS(get_state_recorder=records.get))
    return NS(auto_battle_context=battle)


class BattleStateTests(unittest.TestCase):
    def test_no_context_does_not_initialize_one(self):
        self.assertFalse(battle_snapshot(NS(),100.)['available'])
        self.assertFalse(battle_snapshot(NS(auto_battle_context=NS(auto_op=None)),100.)['available'])

    def test_actual_state_and_execution_fields(self):
        result=battle_snapshot(fixture(),100.)
        self.assertEqual(result['task'],dict(trigger='主循环',conditions='[自定义-能攻击]',priority='99',elapsed=3.))
        self.assertEqual(result['states'],[dict(name='自定义-能攻击',age=2.,value=3)])
        self.assertEqual(result['policy'],'实际策略');self.assertTrue(result['running'])

    def test_stopped_operator_has_no_old_task(self):
        ctx=fixture();ctx.auto_battle_context.auto_op.is_running=False
        result=battle_snapshot(ctx,100.);self.assertTrue(result['available'])
        self.assertFalse(result['running']);self.assertEqual(result['states'],[]);self.assertIsNone(result['task'])

    def test_invalid_and_cleared_records_cannot_break_protocol(self):
        ctx=fixture({'前台-角色':NS(last_record_time=0,last_value=1),'未触发':NS(last_record_time=-1,last_value=1),
                     '无效时间':NS(last_record_time=float('nan'),last_value=2),'无效数值':NS(last_record_time=2,last_value=float('inf')),
                     '自定义-清空':NS(last_record_time=0,last_value=None)})
        result=battle_snapshot(ctx,100.)
        self.assertEqual([item['name'] for item in result['states']],['无效数值','自定义-清空'])
        self.assertIsNone(result['states'][0]['value']);json.dumps(result,allow_nan=False)

    def test_switch_during_sampling_drops_old_records(self):
        ctx=fixture();old=ctx.auto_battle_context.state_record_service.get_state_recorder
        def replace(name):ctx.auto_battle_context.auto_op=None;return old(name)
        ctx.auto_battle_context.state_record_service.get_state_recorder=replace
        result=battle_snapshot(ctx,100.);self.assertFalse(result['running']);self.assertEqual(result['states'],[])

    def test_display_failure_does_not_escape(self):
        ctx=fixture();ctx.auto_battle_context.auto_op.usage_states=None
        self.assertFalse(battle_snapshot(ctx,100.)['available'])

    def test_response_is_bounded(self):
        ctx=fixture({str(index):NS(last_record_time=99,last_value=index) for index in range(1100)})
        result=battle_snapshot(ctx,100.);self.assertTrue(result['truncated']);self.assertEqual(len(result['states']),1024)


if __name__=='__main__':unittest.main()
