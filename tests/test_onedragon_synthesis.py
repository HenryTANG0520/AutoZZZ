import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.onedragon_synthesis import SynthesisControl,confirmation_quantity,synthesis_action
from autozzz.onedragon_resources import resource_confirmation_label,resource_result_warning


class SynthesisTests(unittest.TestCase):
    def test_confirmation_must_identify_one_battery_not_another_quantity_or_item(self):
        for text in ('确认使用下列素材合成1个[以太电池]吗？','合成 １ 个【以太电池】'):
            self.assertEqual(confirmation_quantity(text),1)
        for text in ('确认使用','合成2个[以太电池]','合成10个[以太电池]','合成1个[其他道具]',
                     '合成1个以太电池 合成2个以太电池'):
            with self.subTest(text=text),self.assertRaises(ValueError):confirmation_quantity(text)

    def test_disabled_synthesis_needs_no_permission_but_enabled_plan_lists_all_costs(self):
        plan=NS(category_name='合成电池',plan_times=0)
        self.assertIsNone(synthesis_action([plan],False));plan.plan_times=2
        action=synthesis_action([plan],True)
        self.assertTrue(action['requires_confirmation'])
        for value in ('60 电量','100 储值电卡','丁尼','循环计划'):self.assertIn(value,action['text'])
        self.assertIn('合成以太电池',resource_confirmation_label([action]))

    def fixture(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        path=Path(temporary.name)/'charge.json'
        row=dict(category_name='合成电池',plan_id='fixed',plan_times=2,run_times=0)
        plan=NS(category_name='合成电池',to_dict=lambda:row.copy())
        config=NS(file_path=str(path),plan_list=[plan],loop=False)
        value=dict(loop=False,plan_list=[row.copy()]);path.write_text(json.dumps(value))
        ctx=NS(run_context=NS(get_config=lambda *_:config),current_instance_idx=1)
        return SynthesisControl(ctx,NS(),NS()),NS(config=config,plan=plan),path,row,value

    def yaml(self):return patch.dict(sys.modules,{'one_dragon.utils.yaml_utils':NS(safe_load=json.loads)})

    def test_source_progress_writes_preserve_same_confirmation(self):
        adapter,op,path,row,value=self.fixture();row['run_times']=1;value['plan_list'][0]['run_times']=1
        path.write_text(json.dumps(value))
        with self.yaml():adapter.authorize(op)

    def test_external_plan_quantity_change_revokes_confirmation(self):
        adapter,op,path,row,value=self.fixture();value['plan_list'][0]['plan_times']=10;path.write_text(json.dumps(value))
        with self.yaml(),self.assertRaisesRegex(ValueError,'变化'):adapter.authorize(op)

    def test_loop_change_cannot_reuse_original_confirmation(self):
        adapter,op,path,row,value=self.fixture();op.config.loop=True
        with self.yaml(),self.assertRaisesRegex(ValueError,'变化'):adapter.authorize(op)

    def test_detached_operation_cannot_synthesize_an_unconfirmed_plan(self):
        adapter,op,path,row,value=self.fixture();op.plan=NS(category_name='合成电池',to_dict=lambda:dict(row,plan_id='other'))
        with self.yaml(),self.assertRaisesRegex(ValueError,'清单'):adapter.authorize(op)

    def test_missing_configuration_cannot_reuse_cached_permission(self):
        adapter,op,path,row,value=self.fixture();path.unlink()
        with self.yaml(),self.assertRaisesRegex(ValueError,'缺失'):adapter.authorize(op)

    def test_unknown_resource_results_are_named_together_and_verified_results_are_omitted(self):
        warning=resource_result_warning([dict(action='synthesize_battery',reward_verified=False),
            dict(action='restore_charge',reward_verified=False),dict(app_id='drive_disc_dismantle',reward_verified=True)])
        self.assertEqual(warning,'电池合成、电量恢复结果未核实')
        self.assertEqual(resource_result_warning([dict(action='synthesize_battery',reward_verified=True)]),'')


if __name__=='__main__':unittest.main()
