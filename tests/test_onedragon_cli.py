from types import SimpleNamespace
import signal
import contextlib
import io
import unittest
from unittest.mock import Mock

from autozzz.onedragon_cli import combat_budget, run_application


class NativeCliTests(unittest.TestCase):
    def test_native_limits_cannot_be_silently_ignored_by_legacy_mode(self):
        from autozzz.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()) as output, self.assertRaises(SystemExit):
            main(['--battle', '--energy-limit', '20'])
        self.assertIn('--onedragon-run', output.getvalue())

    def test_budget_requires_all_three_explicit_limits(self):
        self.assertIsNone(combat_budget(None, None, None))
        for values in ((20, None, 20), (20, 1, None), (None, 1, 20),
                       (20, True, 20), (10, 1, 20), (301, 1, 20)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                combat_budget(*values)
        self.assertEqual(combat_budget(20, 1, 20)['allowance'], 20)
        self.assertEqual(combat_budget(20, 1, 20, 'existing')['id'], 'existing')
        for args in ((None, None, None, 'existing'), (20, 1, 20, '../other')):
            with self.assertRaises(ValueError): combat_budget(*args)

    def session(self, state, stop_during_prepare=False):
        session = SimpleNamespace(worker=SimpleNamespace(join=Mock()), report_dir=None,
                                  close=Mock(), stop=Mock())
        def factory(paths, emit):
            self.assertEqual(paths.data.name, 'global')
            def send(command, **params):
                self.commands.append((command, params))
                if command == 'prepare':
                    if stop_during_prepare: signal.raise_signal(signal.SIGINT)
                    emit(dict(type='state', state=state))
                else:
                    emit(dict(type='state', state=dict(state='success', busy=False)))
            session.send = send
            emit(dict(type='connected'))
            return session
        self.commands = []
        self.created = session
        return factory

    def test_preparation_cannot_silently_add_energy_or_resource_permission(self):
        for state in (dict(state='prepared', uses_charge=True),
                      dict(state='prepared', resources=dict(confirmation_required=True))):
            with self.subTest(state=state), self.assertRaises(ValueError):
                run_application('charge_plan', server='global', game_path='game', region='asia',
                                session_factory=self.session(state))
            self.assertEqual([c[0] for c in self.commands], ['prepare'])
            self.created.close.assert_called_once()
            self.created.worker.join.assert_called_once()

    def test_same_session_runs_once_and_waits_for_shutdown(self):
        result = run_application('charge_plan', server='global', game_path='game', region='asia',
            allowance=20, battles=1, maximum=20,
            session_factory=self.session(dict(state='prepared', uses_charge=True)))
        self.assertEqual(result['state']['state'], 'success')
        self.assertEqual([c[0] for c in self.commands], ['prepare', 'start'])
        self.assertEqual(self.commands[-1][1]['budget']['max_per_battle'], 20)
        self.created.close.assert_called_once()
        self.created.worker.join.assert_called_once()

    def test_interrupt_while_preparing_never_restarts_after_prepared_event(self):
        result = run_application('charge_plan', server='global', game_path='game', region='asia',
            allowance=20, battles=1, maximum=20,
            session_factory=self.session(dict(state='prepared', uses_charge=True), stop_during_prepare=True))
        self.assertEqual(result['state']['state'], 'stopped')
        self.assertEqual([c[0] for c in self.commands], ['prepare'])
        self.created.stop.assert_called_once()
