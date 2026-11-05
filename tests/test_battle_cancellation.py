"""Pure cancellation evidence checks; real executor races live in SDK smoke."""
from types import SimpleNamespace as NS
import unittest
from autozzz.onedragon_battle import BattleInputControl,_battle_input,_BattleInputExpired


class BattleCancellationTests(unittest.TestCase):
    def setUp(self):
        self.control=NS(epoch=4,task_epoch=4,observed_epoch=4,manual_paused=False,focus_waiting=False,stopped=False)
        self.adapter=BattleInputControl(NS(),self.control)
        self.adapter.allowed=True;self.adapter.allowed_epoch=4;self.adapter.permit_reason='battle_visible'

    def test_valid_state_does_not_classify_arbitrary_error_as_cancellation(self):
        self.assertIsNone(self.adapter._cancellation_evidence('atomic','actual error'))

    def test_rejection_evidence_survives_a_new_hud_frame(self):
        self.adapter.allowed=False;self.adapter.permit_reason='battle_not_verified'
        token=_battle_input.set(True)
        try:
            with self.assertRaises(_BattleInputExpired)as caught:self.adapter.check_input()
        finally:_battle_input.reset(token)
        self.adapter.allowed=True;self.adapter.permit_reason='battle_visible'
        self.assertIsNone(self.adapter._cancellation_evidence('atomic'))
        self.assertEqual(caught.exception.evidence['reason'],'battle_not_verified')
        self.assertFalse(caught.exception.evidence['allowed'])

    def test_epoch_and_pause_reasons_are_explicit(self):
        self.control.task_epoch=3
        self.assertEqual(self.adapter._cancellation_evidence('atomic')['reason'],'observation_epoch_changed')
        self.control.manual_paused=True
        self.assertEqual(self.adapter._cancellation_evidence('atomic')['reason'],'manual_pause')

    def test_evidence_has_bounded_message_and_explicit_epoch_fields(self):
        self.adapter.allowed=False
        evidence=self.adapter._cancellation_evidence('entry','x'*1000)
        self.assertEqual(len(evidence['message']),160)
        self.assertEqual((evidence['allowed_epoch'],evidence['current_epoch'],evidence['task_epoch']),(4,4,4))
        self.assertEqual(evidence['stage'],'entry')


if __name__=='__main__':unittest.main()
