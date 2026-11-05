import json
from pathlib import Path
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from autozzz.battle_budget import BattleBudget
from autozzz.budget_records import BudgetRecords


class BudgetRecordTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.folder=Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.lock=patch('autozzz.budget_records.RunLock',return_value=nullcontext()).start()
        self.addCleanup(patch.stopall)
        self.budget=BattleBudget(self.folder,'stopped-test','cn',40,2)
        self.budget.reserve(20,'fixture')
        self.records=BudgetRecords(self.folder)

    def test_reconciliation_keeps_cost_and_unknown_result_and_releases_pending_barrier(self):
        item=self.records.pending('cn')[0]
        self.assertEqual(item['cost'],20)
        self.records.account_reserved_upper_bound(item['id'],item['revision'])
        budget=BattleBudget(self.folder,'stopped-test','cn',40,2)
        self.assertEqual((budget.reserved,budget.remaining,budget.rounds_left),(20,20,1))
        self.assertFalse(budget.pending)
        self.assertEqual(budget.data['entries'][0]['battle_result'],'unknown')
        self.assertEqual(budget.data['entries'][0]['resolution'],'reserved_upper_bound')
        self.assertIsNone(budget.data['entries'][0]['energy_after'])
        BattleBudget(self.folder,'another-test','cn',20,1)

    def test_changed_record_cannot_be_reconciled_using_old_view(self):
        item=self.records.pending('cn')[0]
        self.budget.data['note']='changed'
        self.budget.save()
        original=self.budget.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.records.account_reserved_upper_bound(item['id'],item['revision'])
        self.assertEqual(self.budget.path.read_bytes(),original)

    def test_running_game_task_prevents_reconciliation(self):
        item=self.records.pending('cn')[0]
        original=self.budget.path.read_bytes()
        self.lock.side_effect=RuntimeError('正在运行')
        with self.assertRaisesRegex(RuntimeError,'正在运行'):
            self.records.account_reserved_upper_bound(item['id'],item['revision'])
        self.assertEqual(self.budget.path.read_bytes(),original)

    def test_failed_settlement_keeps_original_pending_entry_on_disk_and_in_reports(self):
        entry=self.budget.pending[0].copy()
        original=self.budget.path.read_bytes()
        with patch.object(Path,'replace',side_effect=OSError('fixture write failed')):
            with self.assertRaisesRegex(OSError,'write failed'):
                self.budget.settle(entry['id'],20)
        self.assertEqual(self.budget.pending,[entry])
        self.assertEqual(self.budget.path.read_bytes(),original)
        self.assertFalse(list(self.folder.glob('*.tmp')))
        reopened=BattleBudget(self.folder,'stopped-test','cn',40,2)
        self.assertEqual(reopened.pending,[entry])

    def test_failed_reservation_does_not_leave_a_phantom_entry(self):
        budget=BattleBudget(self.folder,'reservation','global',40,2)
        original=budget.path.read_bytes()
        with patch('autozzz.battle_budget.os.fsync',side_effect=OSError('fixture flush failed')):
            with self.assertRaisesRegex(OSError,'flush failed'):
                budget.reserve(20,'fixture')
        self.assertEqual((budget.pending,budget.remaining,budget.rounds_left),([],40,2))
        self.assertEqual(budget.path.read_bytes(),original)

    def test_other_pending_record_blocks_before_creating_an_empty_budget(self):
        with self.assertRaisesRegex(RuntimeError,'未结算'):
            BattleBudget(self.folder,'blocked','cn',40,2)
        self.assertFalse((self.folder/'blocked.json').exists())

    def test_profile_filter_path_and_corrupted_records(self):
        self.assertEqual(self.records.pending('global'),[])
        with self.assertRaises(ValueError):self.records.account_reserved_upper_bound('../outside','revision')
        self.budget.data['entries'][0]['cost']=41
        self.budget.save()
        with self.assertRaises(ValueError):self.records.pending('cn')


if __name__=='__main__':unittest.main()
