from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from autozzz.__main__ import main
from autozzz.program_sdk_commit import ProgramSdkCommit
from autozzz.program_update import ProgramUpdate, write_json
from autozzz.program_update_handoff import RECOVERY_COMMAND
from autozzz.program_update_recovery import discover_transactions, inspect_transaction
from autozzz.program_update_recovery_ui import ProgramUpdateRecoveryWindow
from autozzz.runtime_selection import tree_hash
from tests.test_program_update import package


class ProgramUpdateRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.local = self.root/'appdata'
        environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)})
        environment.start()
        self.addCleanup(environment.stop)
        self.target = package(self.root/'AutoZZZ', 'old')
        self.new = package(self.root/'new', 'new')
        self.tx = ProgramUpdate.prepare(self.target, self.new, probe=lambda _: [])

    def helper(self, tx):
        job = tx.job
        (job/'helper/python').mkdir(parents=True)
        (job/'helper/python/python.exe').touch()
        (job/'helper/start.py').touch()
        write_json(job/'helper-start.json', {'state': 'started', 'pid': 1})
        for name, args in [('恢复原版本.cmd',''), ('继续SDK更新.cmd',' --after')]:
            (job/name).write_bytes(RECOVERY_COMMAND.format(args).encode('ascii'))

    def read(self):
        return inspect_transaction(self.tx.job, target=self.target)

    def test_rediscover_original_job_without_a_result_file_or_sdk_connection(self):
        self.assertEqual(discover_transactions(self.target), [])
        self.helper(self.tx)
        other = ProgramUpdate.prepare(package(self.root/'Other', 'other'), self.new, probe=lambda _: [])
        self.helper(other)
        self.tx.state('prepared', sdk_transition={}, sdk_prepared={'cn': {}})
        rows = discover_transactions(self.target)
        self.assertEqual([row['transaction'] for row in rows], [str(self.tx.job)])
        self.assertEqual(rows[0]['stage'], 'preparing_sdk')
        self.assertEqual(rows[0]['prepared_profiles'], ['cn'])
        self.assertEqual(set(rows[0]['actions']), {'before','after'})
        self.assertIsNone(rows[0]['helper_result'])
        with patch('autozzz.__main__.ROOT', self.target):
            for args, expected in [([], {'transactions': rows}), ([str(self.tx.job)], rows[0])]:
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(['--program-update-status', *args]), 0)
                self.assertEqual(json.loads(output.getvalue()), expected)

    def test_mixed_selectors_allow_both_and_directory_exchange_allows_only_before(self):
        self.helper(self.tx)
        self.tx.state('installed', sdk_phase='armed', sdk_commit={})
        self.assertEqual(self.read()['stage'], 'publishing')
        self.assertEqual(set(self.read()['actions']), {'before','after'})
        self.tx.state('old_moved')
        self.target.rename(self.tx.backup)
        self.assertFalse(self.target.exists())
        self.assertEqual(discover_transactions(self.target)[0]['stage'], 'exchanging')
        self.assertEqual(set(self.read()['actions']), {'before'})
        self.tx.state('rolled_back', sdk_phase='restoring')
        self.assertEqual(self.read()['stage'], 'restoring')
        self.assertEqual(set(self.read()['actions']), {'before'})
        (self.tx.job/'恢复原版本.cmd').write_bytes(b'changed')
        self.assertEqual(self.read()['actions'], {})

    def test_outer_commit_waits_for_its_gates_and_preserves_terminal_result(self):
        self.helper(self.tx)
        profiles, planned = {}, {}
        for profile in ('cn','global'):
            control = self.local/'AutoZZZ/OneDragon'/profile
            profiles[profile] = dict(control=str(control), source=str(control))
            planned[profile] = dict(target=None, before=None, after=None)
        binding = dict(profiles=profiles)
        plan = dict(format=1, binding=tree_hash(binding), profiles=planned)
        self.tx.state('completed', sdk_phase='committed', sdk_transition=binding, sdk_commit=plan)
        coordinator = ProgramSdkCommit(self.tx)
        gate = coordinator._gate('global')
        gate.parent.mkdir(parents=True)
        gate.write_bytes(coordinator._gate_bytes('global'))
        row = self.read()
        self.assertEqual(row['stage'], 'finishing')
        self.assertEqual(row['remaining_gates'], ['global'])
        self.assertEqual(set(row['actions']), {'after'})
        write_json(gate, {'format':2, 'job':self.tx.job.name})
        self.assertEqual(self.read()['stage'], 'needs_review')
        self.assertEqual(self.read()['actions'], {})
        write_json(gate, {'format':2, 'job':'.autozzz-update-'+'a'*32})
        write_json(self.tx.job/'helper-result.json', {'state':'failed'})
        write_json(self.tx.job/'recovery-result.json', {'state':'completed'})
        row = self.read()
        self.assertEqual(row['stage'], 'completed')
        self.assertEqual(row['actions'], {})
        self.assertEqual(row['recovery_result']['state'], 'completed')

    def test_ui_displays_durable_stage_and_opens_only_the_exact_job_folder(self):
        self.helper(self.tx)
        self.tx.state('installed', sdk_phase='armed', sdk_commit={})
        view = ProgramUpdateRecoveryWindow.__new__(ProgramUpdateRecoveryWindow)
        view.rows = {self.tx.job.name: self.read()}
        view.table = SimpleNamespace(selection=lambda: (self.tx.job.name,))
        view.detail, view.folder = Mock(), Mock()
        with patch('autozzz.program_update_recovery_ui.tr', side_effect=lambda text:text), \
             patch('autozzz.program_update_recovery_ui.ROOT', self.target), \
             patch('autozzz.program_update_recovery_ui.os.startfile', create=True) as open_folder:
            view.show_selected()
            text = view.detail.set.call_args.args[0]
            self.assertIn('提交区服选择', text)
            self.assertIn('恢复原版本.cmd', text)
            self.assertIn('继续SDK更新.cmd', text)
            view.open_folder()
            open_folder.assert_called_once_with(str(self.tx.job))


if __name__ == '__main__':
    unittest.main()
