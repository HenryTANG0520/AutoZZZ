import unittest

from autozzz.recovery_battle_clock import BattleClockWitness
from autozzz.recovery_observation import CapturedFrame, RecoveryObservation, WindowIdentity


class BattleClockWitnessTests(unittest.TestCase):
    def test_contiguous_clock_progresses_and_rejects_an_elapsed_reset(self):
        witness = BattleClockWitness(.5)
        self.assertIsNone(witness.observe(599, 0., .02))
        self.assertIsNone(witness.observe(609, .1, .12))
        self.assertTrue(witness.progressed)
        self.assertEqual(witness.observe(204, .2, .22), 'battle_clock_discontinuity')
        self.assertEqual(witness.observe(619, .3, .32), 'battle_clock_discontinuity')

    def test_similar_clock_across_a_capture_gap_does_not_restore_continuity(self):
        witness = BattleClockWitness(.5)
        self.assertIsNone(witness.observe(1275, 0., .02))
        self.assertEqual(witness.observe(1275, 10., 10.02), 'battle_clock_gap')

    def test_readiness_requires_new_clock_progress_and_does_not_attest_victory(self):
        identity = WindowIdentity(1, 2, 3, 4, (0, 0, 1920, 1080))
        frame = lambda stamp, elapsed: CapturedFrame(identity, elapsed, stamp, stamp + .01)
        frozen = iter((frame(.1, 300), frame(.2, 300), frame(.3, 309)))
        now = .01
        observation = RecoveryObservation(
            frame(0., 300), lambda: next(frozen), None, lambda: True, clock=lambda: now,
            elapsed_clock=lambda sample: sample.image,
        )
        now = .22
        self.assertEqual(observation.sample(), 'pending')
        self.assertEqual(observation.sample(), 'pending')
        self.assertFalse(observation.verified())
        now = .32
        self.assertEqual(observation.sample(), 'ready')
        self.assertTrue(observation.reserve_first_frame())
        now = .4
        self.assertTrue(observation.finish(frame(.36, 315)))
        self.assertFalse(observation.continue_frame(frame(.39, 318), victory=True))
        self.assertEqual(observation.failure, 'battle_terminal_unproven')

    def test_too_fast_and_frozen_clock_are_rejected(self):
        fast = BattleClockWitness(.5)
        self.assertIsNone(fast.observe(400, 0., .02))
        self.assertEqual(fast.observe(499, .1, .12), 'battle_clock_discontinuity')
        stale = BattleClockWitness(.5)
        self.assertIsNone(stale.observe(400, 0., .02))
        self.assertIsNone(stale.observe(400, .3, .32))
        self.assertEqual(stale.observe(400, .6, .62), 'battle_clock_stale')
