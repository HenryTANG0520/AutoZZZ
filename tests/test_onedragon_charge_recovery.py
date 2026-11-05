from contextlib import nullcontext
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from autozzz.battle_budget import BattleBudget
from autozzz.onedragon_charge_recovery import NativeChargeRecovery, RECEIPT_DIRECTORY, SOURCE_VICTORY


class NativeChargeRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.budget = BattleBudget(self.directory, 'existing-budget', 'cn', 80, 4)
        self.budget.settle(self.budget.reserve(20, 'earlier'), 150)
        self.previous = dict(self.budget.data['entries'][0])
        self.plan = '训练_区域巡防_机师与叛甲_'
        self.reservation = self.budget.reserve(60, self.plan)
        self.recovery = NativeChargeRecovery(self.directory)
        self.receipt_path = self.directory / RECEIPT_DIRECTORY / self.budget.path.name
        lock = patch('autozzz.onedragon_charge_recovery.RunLock', return_value=nullcontext())
        self.lock = lock.start()
        self.addCleanup(lock.stop)

    def record(self, **overrides):
        values = dict(reservation_id=self.reservation, source_result=SOURCE_VICTORY, plan_uid=self.plan)
        return self.recovery.record_victory(self.budget, **(values | overrides))

    def snapshot(self):
        return self.recovery.records._read(self.budget.path)

    def test_crash_after_victory_receipt_recovers_only_that_pending_charge(self):
        original = self.budget.path.read_bytes()
        proof = self.record()
        self.assertEqual(self.budget.path.read_bytes(), original)
        with patch.object(self.budget, '_write', side_effect=OSError('ledger write interrupted')):
            with self.assertRaises(OSError):
                self.budget.settle(self.reservation, None)
        self.assertEqual(self.budget.path.read_bytes(), original)

        reopened = NativeChargeRecovery(self.directory)
        recovered = reopened.recover_verified(self.budget.data['id'], proof['ledger_revision'], self.reservation)
        self.assertEqual(recovered['battle_result'], 'victory')
        self.assertEqual(recovered['cost'], 60)
        after = BattleBudget(self.directory, self.budget.data['id'], 'cn', 80, 4)
        earlier, settled = after.data['entries']
        self.assertEqual(earlier, self.previous)
        self.assertEqual((after.reserved, after.remaining, after.rounds_left, after.pending), (80, 0, 2, []))
        self.assertEqual((settled['id'], settled['source'], settled['verification']), (self.reservation, self.plan, proof))
        self.assertEqual(settled['resolution'], 'verified_source_receipt')
        self.assertIsNone(settled['energy_after'])
        settled_bytes = after.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '已变化'):
            reopened.recover_verified(after.data['id'], proof['ledger_revision'], self.reservation)
        self.assertEqual(after.path.read_bytes(), settled_bytes)

    def test_legacy_pending_entry_without_receipt_stays_pending(self):
        data, revision = self.snapshot()
        before = self.budget.path.read_bytes()
        self.assertIsNone(self.recovery.verified_receipt(data, revision))
        with self.assertRaisesRegex(ValueError, '没有与原待核对出战一致'):
            self.recovery.recover_verified(data['id'], revision, self.reservation)
        self.assertEqual(self.budget.path.read_bytes(), before)

    def test_receipt_creation_requires_matching_source_plan_id_and_victory(self):
        for override in (dict(reservation_id='f' * 32), dict(plan_uid='different'), dict(source_result='撤退')):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.record(**override)
        self.assertFalse(self.receipt_path.exists())

    def test_tampered_witness_does_not_settle_pending_row(self):
        proof = self.record()
        data, revision = self.snapshot()
        before = self.budget.path.read_bytes()
        for key, value in (('cost', 20), ('server', 'global'), ('plan_uid', 'different'), ('version', True),
                           ('reservation_id', 'f' * 32), ('ledger_revision', 'a' * 64), ('budget_id', 'different'),
                           ('source_result', '普通战斗-撤退')):
            with self.subTest(key=key):
                self.receipt_path.write_text(json.dumps(proof | {key: value}), encoding='utf-8')
                self.assertIsNone(self.recovery.verified_receipt(data, revision))
                with self.assertRaisesRegex(ValueError, '没有与原待核对出战一致'):
                    self.recovery.recover_verified(data['id'], revision, self.reservation)
        self.assertEqual(self.budget.path.read_bytes(), before)

    def test_new_ledger_revision_invalidates_an_otherwise_matching_receipt(self):
        proof = self.record()
        self.budget.data['note'] = 'external revision change'
        self.budget.save()
        data, revision = self.snapshot()
        self.assertNotEqual(revision, proof['ledger_revision'])
        before = self.budget.path.read_bytes()
        self.assertIsNone(self.recovery.verified_receipt(data, revision))
        with self.assertRaises(ValueError):
            self.recovery.recover_verified(data['id'], revision, self.reservation)
        self.assertEqual(self.budget.path.read_bytes(), before)

    def test_original_task_lock_and_atomic_failure_preserve_pending(self):
        proof = self.record()
        before = self.budget.path.read_bytes()
        arguments = self.budget.data['id'], proof['ledger_revision'], self.reservation
        self.lock.side_effect = RuntimeError('task is still running')
        with self.assertRaisesRegex(RuntimeError, 'still running'):
            self.recovery.recover_verified(*arguments)
        self.lock.side_effect = None
        with patch('autozzz.sdk_upgrade_files.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaisesRegex(OSError, 'replace failed'):
                self.recovery.recover_verified(*arguments)
        self.assertEqual(self.budget.path.read_bytes(), before)
        self.assertEqual(self.recovery.verified_receipt(*self.snapshot()), proof)
        self.assertFalse(list(self.directory.glob('*.tmp')))
        self.assertEqual(self.recovery.recover_verified(*arguments)['pending'], 0)

    def test_missing_ledger_is_never_recreated(self):
        proof = self.record()
        self.budget.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.recovery.recover_verified(proof['budget_id'], proof['ledger_revision'], self.reservation)
        self.assertFalse(self.budget.path.exists())


if __name__ == '__main__':
    unittest.main()
