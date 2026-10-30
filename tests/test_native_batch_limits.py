import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace as NS
import unittest

from autozzz.battle_budget import BattleBudget,validate_runtime_budget
from autozzz.onedragon_charge import ChargeBudgetControl
from autozzz.onedragon_control import validate_runtime_timeout,ExecutionStopped
from autozzz.models import AutomationError


class BatchLimitTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def test_large_explicit_totals_survive_roundtrip_and_still_stop_at_actual_limits(self):
        validate_runtime_budget(dict(id='long',allowance=2400,battle_limit=120,max_per_battle=100))
        ledger=BattleBudget(self.root,'long','cn',2400,120)
        for _ in range(16):
            key=ledger.reserve(20,'fixture');ledger.settle(key,None)
        again=BattleBudget(self.root,'long','cn',2400,120)
        self.assertEqual((again.reserved,again.remaining,again.rounds_left),(320,2080,104))
        original=again.path.read_bytes()
        with self.assertRaises(AutomationError):BattleBudget(self.root,'long','cn',4800,240)
        self.assertEqual(again.path.read_bytes(),original)

    def test_pending_large_batch_cannot_be_bypassed_by_another_budget(self):
        ledger=BattleBudget(self.root,'long','global',2400,120);ledger.reserve(20,'fixture')
        with self.assertRaises(AutomationError):BattleBudget(self.root,'another','global',4800,240)
        self.assertEqual(len(ledger.pending),1)

    def test_legacy_script_batch_preserves_defaults_but_accepts_explicit_long_plan(self):
        from autozzz.battle import BattlePlan
        original=BattlePlan();self.assertEqual((original.energy_budget,original.battles),(20,1))
        plan=BattlePlan(energy_budget=2400,battles=120);path=self.root/'battle.json';plan.save(path)
        self.assertEqual((BattlePlan.load(path).energy_budget,BattlePlan.load(path).battles),(2400,120))
        for values in (dict(battles=True),dict(battles=0),dict(energy_budget=19),dict(energy_budget=20.)):
            with self.subTest(values=values),self.assertRaises(ValueError):BattlePlan(**values).validate()

    def test_invalid_types_totals_and_server_never_create_ledger_files(self):
        for allowance,limit in ((0,1),(1,0),(-1,1),(1,-1),(True,1),(20,False),(20.,1),(20,'1')):
            with self.subTest(allowance=allowance,limit=limit),self.assertRaises(ValueError):
                BattleBudget(self.root,'invalid','cn',allowance,limit)
        for server in ('unknown',None):
            with self.assertRaises(ValueError):BattleBudget(self.root,'invalid',server,20,1)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_single_ceiling_does_not_allow_overspending_smaller_total(self):
        ledger=BattleBudget(self.root,'small','cn',10,1)
        stopped=[];control=NS(checkpoint=lambda **_:None,stop=lambda *args:stopped.append(args))
        adapter=ChargeBudgetControl(NS(),control,ledger,100)
        with self.assertRaises(ExecutionStopped):adapter._reserve(NS(plan=NS(uid='fixture')),20)
        self.assertTrue(adapter.limited);self.assertFalse(ledger.data['entries']);self.assertTrue(stopped)

    def test_runtime_types_and_intrinsic_single_cost_ceiling_are_still_validated(self):
        budget=dict(id='valid',allowance=2400,battle_limit=120,max_per_battle=100)
        for change in (dict(allowance=True),dict(battle_limit=0),dict(max_per_battle=101),dict(id='')):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_runtime_budget(dict(budget,**change))
        with self.assertRaises(ValueError):validate_runtime_budget(dict(budget,extra=True))

    def test_runtime_timeout_has_no_development_ceiling_but_requires_explicit_integer(self):
        for seconds in (30,7201,86400,2**63):validate_runtime_timeout(seconds)
        for seconds in (None,0,29,True,30.,'86400'):
            with self.subTest(seconds=seconds),self.assertRaises(ValueError):validate_runtime_timeout(seconds)

    def test_large_available_energy_is_parsed_without_relaxing_verified_single_cost(self):
        from unittest.mock import patch
        adapter=ChargeBudgetControl(NS(screen_loader=NS(get_area=lambda *_:NS(rect=NS(x1=500,y1=20,y2=60))),
            ocr=NS(run_ocr=lambda _: {'2400/20':NS(max=NS(confidence=.99))})),NS(),NS(pending=[]),100)
        operation=NS(last_screenshot=object(),plan=NS(category_name='实战模拟室',card_num='1'))
        modules={'one_dragon.utils':NS(cv2_utils=NS(crop_image_only=lambda *_:object())),
                 'one_dragon.base.geometry.rectangle':NS(Rect=lambda *args:args)}
        with patch.dict(sys.modules,modules):
            self.assertEqual(adapter._read_cost(operation),(2400,20))
            adapter.ctx.ocr.run_ocr=lambda _: {'2400/100':NS(max=NS(confidence=.99))}
            with self.assertRaises(ValueError):adapter._read_cost(operation)

    def test_pending_records_remain_readable_when_a_valid_long_ledger_exceeds_two_megabytes(self):
        from unittest.mock import patch
        from contextlib import nullcontext
        from autozzz.budget_records import BudgetRecords
        entries=[dict(id=f'{i:032x}',cost=20,source='native-long-plan',status='settled',energy_after=None) for i in range(16000)]
        entries[-1]['status']='pending'
        value=dict(version=1,id='long',server='cn',allowance=320000,battle_limit=16000,entries=entries)
        path=self.root/'long.json';path.write_text(json.dumps(value,indent=2),encoding='utf-8')
        self.assertGreater(path.stat().st_size,2*1024*1024)
        with patch('autozzz.budget_records.RunLock',return_value=nullcontext()):
            records=BudgetRecords(self.root);item,=records.pending('cn')
            self.assertEqual((item['pending'],item['cost']),(1,20))
            records.account_reserved_upper_bound(item['id'],item['revision'])
        result=json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(sum(e['cost'] for e in result['entries']),320000)
        self.assertEqual(result['entries'][-1]['battle_result'],'unknown')


if __name__=='__main__':unittest.main()
