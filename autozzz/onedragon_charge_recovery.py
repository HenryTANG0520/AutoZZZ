"""Recover only a victory observed by the original OneDragon battle worker.

A later screenshot, process identity or matching plan cannot identify an
encounter. The receipt binds the original terminal node to the exact pending
ledger bytes, and replay changes only that existing reservation.
"""
from __future__ import annotations

from pathlib import Path
import re

from .budget_records import BudgetRecords
from .run_lock import RunLock
from .runtime_selection import plain, read_json
from .sdk_upgrade_files import atomic_json


SOURCE_VICTORY = '普通战斗-完成'
RECEIPT_DIRECTORY = '.native-recovery'
_BUDGET_ID = re.compile(r'[a-zA-Z0-9_-]{1,80}')
_RESERVATION_ID = re.compile(r'[a-f0-9]{32}')
_REVISION = re.compile(r'[a-f0-9]{64}')


class NativeChargeRecovery:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.records = BudgetRecords(self.directory)

    def _path(self, identifier: str, *, receipt=False) -> Path:
        if not isinstance(identifier, str) or _BUDGET_ID.fullmatch(identifier) is None:
            raise ValueError('战斗预算编号无效。')
        parent = self.directory / RECEIPT_DIRECTORY if receipt else self.directory
        return plain(parent / (identifier + '.json'))

    @staticmethod
    def _binding(data: dict, revision: str) -> dict:
        if not isinstance(revision, str) or _REVISION.fullmatch(revision) is None:
            raise ValueError('战斗预算版本无效。')
        pending = [entry for entry in data['entries'] if entry['status'] == 'pending']
        if len(pending) != 1:
            raise ValueError('需要唯一的待核对出战。')
        entry = pending[0]
        identifier, source = entry['id'], entry.get('source')
        if (_RESERVATION_ID.fullmatch(identifier) is None or not isinstance(source, str)
                or not source or len(source) > 4096):
            raise ValueError('待核对出战缺少有效的原生计划记录。')
        return dict(version=1, budget_id=data['id'], server=data['server'], ledger_revision=revision,
                    reservation_id=identifier, cost=entry['cost'], plan_uid=source, source_result=SOURCE_VICTORY)

    def record_victory(self, budget, reservation_id: str, *, source_result: str, plan_uid: str) -> dict:
        """Called under the original run lock after the source victory node succeeds."""
        if source_result != SOURCE_VICTORY:
            raise ValueError('原生战斗未核实胜利。')
        path = self._path(budget.data['id'])
        if Path(budget.path).resolve() != path:
            raise ValueError('胜利记录不属于当前战斗预算。')
        data, revision = self.records._read(path)
        binding = self._binding(data, revision)
        if data != budget.data or binding['reservation_id'] != reservation_id or binding['plan_uid'] != plan_uid:
            raise ValueError('胜利记录与原待核对出战不一致。')
        atomic_json(self._path(data['id'], receipt=True), binding)
        return binding

    def verified_receipt(self, data: dict, revision: str) -> dict | None:
        """Read a receipt against a locked ledger snapshot. Invalid evidence leaves it pending."""
        try:
            expected = self._binding(data, revision)
            saved = read_json(self._path(data['id'], receipt=True), limit=16 * 1024)
        except (KeyError, OSError, RecursionError, TypeError, ValueError):
            return None
        if (isinstance(saved, dict) and type(saved.get('version')) is int and type(saved.get('cost')) is int
                and saved == expected):
            return expected
        return None

    def recover_verified(self, identifier: str, revision: str, reservation_id: str) -> dict:
        """Replay one existing settlement without a game window or an SDK run."""
        with RunLock():
            path = self._path(identifier)
            data, current = self.records._read(path)
            if current != revision:
                raise ValueError('预算记录已变化，请重新读取后核对。')
            proof = self.verified_receipt(data, current)
            if proof is None or proof['reservation_id'] != reservation_id:
                raise ValueError('没有与原待核对出战一致的胜利记录。')
            entries = [dict(entry, status='settled', energy_after=None, battle_result='victory',
                            resolution='verified_source_receipt', verification=proof)
                       if entry['id'] == reservation_id else entry for entry in data['entries']]
            atomic_json(path, dict(data, entries=entries))
            return dict(id=identifier, reservation_id=reservation_id, cost=proof['cost'],
                        pending=0, battle_result='victory')
