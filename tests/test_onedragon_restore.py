import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.battle_budget import BattleBudget
from autozzz.onedragon_charge import ChargeBudgetControl
from autozzz.onedragon_control import ExecutionStopped
from autozzz.onedragon_restore import restore_action,RestoreChargeControl,NONE,BACKUP,ETHER,BOTH


class Control:
    def __init__(self):self.stopped=False;self.input_validator=None
    def stop(self,*_):self.stopped=True
    def checkpoint(self,**_):pass


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup);self.root=Path(self.directory.name);self.control=Control()
    def adapter(self,mode=BACKUP,accepted=True):
        ledger=BattleBudget(self.root/'budgets','restore','cn',20,1)
        return ChargeBudgetControl(NS(),self.control,ledger,20,mode,accepted)
    def test_saved_source_mode_does_not_silently_authorize_spending(self):
        adapter=self.adapter(accepted=False);adapter.restore_cost=20
        with self.assertRaises(ExecutionStopped):adapter.check_restore_budget()
        self.assertTrue(self.control.stopped);self.assertEqual(adapter.budget.reserved,0)
    def test_recovery_requires_a_verified_affordable_next_cost(self):
        adapter=self.adapter()
        for cost in (0,40):
            with self.subTest(cost=cost):
                adapter.restore_cost=cost
                with self.assertRaises(ExecutionStopped):adapter.check_restore_budget()
        self.assertEqual(adapter.budget.data['entries'],[])
    def test_recovery_can_finish_after_repeat_has_reserved_the_same_battle(self):
        adapter=self.adapter();adapter._reserve(NS(plan=NS(uid='mission')),20)
        self.assertEqual(adapter.budget.remaining,0);adapter.check_restore_budget()
        self.assertEqual(len(adapter.budget.pending),1)
    def test_spent_budget_does_not_authorize_recovery_for_another_battle(self):
        adapter=self.adapter();key=adapter.budget.reserve(20,'mission');adapter.budget.settle(key,None);adapter.restore_cost=20
        with self.assertRaises(ExecutionStopped):adapter.check_restore_budget()
        self.assertTrue(adapter.limited)
    def test_all_native_modes_are_explicit_and_unknown_sources_are_rejected(self):
        self.assertIsNone(restore_action(NONE))
        for mode in (BACKUP,ETHER,BOTH):
            action=restore_action(mode);self.assertTrue(action['requires_confirmation']);self.assertEqual(action['kind'],'restore_charge')
            self.assertIn(mode,action['text']);self.assertIn('可能高于',action['text'])
        for mode in ('菲林','unknown',None):
            with self.subTest(mode=mode),self.assertRaises(ValueError):restore_action(mode)
    def setup_restore(self):
        path=self.root/'charge.json';path.write_text(json.dumps(dict(restore_charge=BACKUP,run_times=0)),encoding='utf-8')
        config=NS(file_path=str(path),restore_charge=BACKUP);checks=[];budget=NS(check_restore_budget=lambda:checks.append(True))
        ctx=NS(run_context=NS(get_config=lambda *_:config),current_instance_idx=1)
        return RestoreChargeControl(ctx,self.control,BACKUP,budget),NS(config=config),path,checks
    def yaml(self):return patch.dict(sys.modules,{'one_dragon.utils.yaml_utils':NS(safe_load=json.loads)})
    def test_external_mode_change_is_rejected_before_resource_input(self):
        restore,operation,path,checks=self.setup_restore()
        with self.yaml():
            restore.authorize(operation);path.write_text(json.dumps(dict(restore_charge=ETHER)),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'变化'):restore.authorize(operation)
        self.assertEqual(checks,[True])
    def test_legitimate_plan_progress_does_not_change_restore_authorization(self):
        restore,operation,path,checks=self.setup_restore();path.write_text(json.dumps(dict(restore_charge=BACKUP,run_times=1)),encoding='utf-8')
        with self.yaml():restore.authorize(operation)
        self.assertEqual(checks,[True])
    def test_deleted_configuration_cannot_reuse_cached_restore_permission(self):
        restore,operation,path,checks=self.setup_restore();path.unlink()
        with self.yaml(),self.assertRaisesRegex(ValueError,'缺失'):restore.authorize(operation)
        self.assertFalse(checks)


if __name__=='__main__':unittest.main()
