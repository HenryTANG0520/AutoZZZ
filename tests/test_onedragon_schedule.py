import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.onedragon_schedule import normalize_intent,intent_revision,review


class ScheduleIntentTests(unittest.TestCase):
    def test_only_native_progress_is_ignored_and_original_value_is_unchanged(self):
        value={'plan_list':[{'plan_id':'stable','run_times':2,'plan_times':3,'auto_battle_config':'original'}],
               'restore_charge':'不使用','last_daily_reset_dt':'20261030','skip_plan':True,
               'combat_simulation_double_reward_config':{'run_times':1,'plan_times':2}}
        before=copy.deepcopy(value);normalized=normalize_intent('config/01/one_dragon/charge_plan.yml',value)
        self.assertEqual(value,before);self.assertNotIn('last_daily_reset_dt',normalized)
        self.assertNotIn('run_times',normalized['plan_list'][0]);self.assertEqual(normalized['plan_list'][0]['plan_times'],3)
        self.assertEqual(normalized['restore_charge'],'不使用')
        for name in ('config/auto_battle/charge_plan.yml','plugins/demo/charge_plan.yml','config/01/custom/charge_plan.yml'):
            self.assertEqual(normalize_intent(name,value),value)

    def test_active_marker_and_default_language_normalize_but_identity_and_login_options_remain(self):
        self.assertEqual(normalize_intent('config/one_dragon.yml',{'instance_list':[{'idx':1,'active':True,'active_in_od':False}]}),
                         {'instance_list':[{'idx':1,'active_in_od':False}]})
        self.assertEqual(normalize_intent('config/01/game_account.yml',{'game_language':'cn','password':'private'}),{'password':'private'})
        self.assertEqual(normalize_intent('config/01/game_account.yml',{'game_language':'en'}),{'game_language':'en'})

    def test_snapshot_progress_changes_do_not_hide_strategy_budget_or_plugin_changes(self):
        import yaml
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=root/'config/01/one_dragon/charge_plan.yml';config.parent.mkdir(parents=True)
            write=lambda value:config.write_text(json.dumps(value),encoding='utf-8')
            value={'plan_list':[{'run_times':0,'plan_times':2,'auto_battle_config':'original'}],'restore_charge':'不使用'};write(value)
            import hashlib
            def entries(folder):return {p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
            with patch('autozzz.onedragon_schedule.snapshot',side_effect=entries),patch.dict('sys.modules',{'one_dragon.utils.yaml_utils':NS(safe_load=yaml.safe_load)}):
                initial=intent_revision(root);value['plan_list'][0]['run_times']=2;write(value)
                record=root/'config/01/app_run_record/charge_plan.yml';record.parent.mkdir();record.write_text('run_status: 1')
                self.assertEqual(intent_revision(root),initial)
                for key,new in (('plan_times',3),('auto_battle_config','different')):
                    changed=copy.deepcopy(value);changed['plan_list'][0][key]=new;write(changed)
                    self.assertNotEqual(intent_revision(root),initial)
                changed=copy.deepcopy(value);changed['restore_charge']='使用以太电池';write(changed)
                self.assertNotEqual(intent_revision(root),initial)
                write(value);self.assertEqual(intent_revision(root),initial)
                other=root/'config/02/notify.yml';other.parent.mkdir();other.write_text('enable_notify: false')
                self.assertEqual(intent_revision(root,[1]),initial)
                self.assertNotEqual(intent_revision(root),initial)
                plugin=root/'plugins/demo/demo_factory.py';plugin.parent.mkdir(parents=True);plugin.write_text('first plugin version')
                self.assertNotEqual(intent_revision(root,[1]),initial)
                version=intent_revision(root,[1]);plugin.write_text('second plugin version');self.assertNotEqual(intent_revision(root,[1]),version)

    def test_review_is_bound_to_the_preflight_snapshot_and_never_returns_credentials(self):
        plan=dict(plan_id='same',revision='raw',original_current=1,configuration_valid=True,issues=[],uses_charge=False,
                  instances=[dict(idx=2,name='chosen',game_path='game',region='cn',language='cn',applications=[],uses_charge=False,resources=[],requirements={},login_configured=True)])
        calls=[];plans=NS(preview=lambda *args:copy.deepcopy(plan),validate=lambda key:calls.append(key))
        service=NS(data=Path('fixture'),_rotation_plans=lambda:plans)
        registry=NS(current_active_instance=NS(idx=1))
        with patch('autozzz.onedragon_schedule.profile_lock') as lock,patch('autozzz.onedragon_rotation.rotation_revision',return_value='raw'),patch('autozzz.onedragon_accounts.AccountStore') as account,patch('autozzz.onedragon_schedule.intent_revision',return_value='stable'):
            account.return_value._registry.return_value=registry
            result=review(service,'cn',2,'email')
            self.assertEqual(result['selection'],dict(instance_idx=2,app_id='email'));self.assertEqual(result['intent'],'stable')
            self.assertNotIn('login_configured',result['instances'][0]);self.assertEqual(calls,['same']);lock.assert_called_once()
            registry.current_active_instance.idx=3
            with self.assertRaisesRegex(ValueError,'变化'):review(service,'cn',2,'email')


if __name__=='__main__':unittest.main()
