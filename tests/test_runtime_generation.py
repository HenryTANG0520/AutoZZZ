from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

from autozzz.onedragon_execution import OneDragonExecution
from autozzz.onedragon_runtime import RuntimePaths,RuntimeUnavailable
from autozzz.runtime_generation import RuntimeGeneration,selection_generation
from autozzz.onedragon_worker import validate_worker_request


class RuntimeGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name);self.selector=self.data/'autozzz/sdk-selection.json'

    def switch(self):
        self.selector.parent.mkdir(exist_ok=True);self.selector.write_text('{"kind":"candidate"}')

    def test_pre_activation_worker_cannot_write_or_input_after_switch_but_can_stop(self):
        guard=RuntimeGeneration(self.data,selection_generation(self.data))
        guard.check_request('configuration_save');guard.check_execution()
        self.switch()
        for action in (guard.check_execution,lambda:guard.check_request('configuration_save'),lambda:guard.check_request('mcp_action_prepare')):
            with self.assertRaisesRegex(RuntimeError,'SDK 已切换'):action()
        for name in ('stop_execution','pause_execution','execution_status','mcp_stop'):
            guard.check_request(name)
        self.selector.unlink();guard.check_execution()

    def test_candidate_worker_detects_other_candidate_and_removed_selector(self):
        self.switch();guard=RuntimeGeneration(self.data,selection_generation(self.data))
        self.selector.write_text('{"kind":"stable"}')
        with self.assertRaises(RuntimeError):guard.check_execution()
        self.selector.unlink()
        with self.assertRaises(RuntimeError):guard.check_execution()

    def test_pre_selector_activation_journal_blocks_new_and_prepared_workers(self):
        guard=RuntimeGeneration(self.data,'-')
        pending=self.data/'autozzz/sdk-activation.json';pending.parent.mkdir();pending.write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'待核对事务'):guard.check_execution()
        RuntimeGeneration(self.data,'-',True).check_request('sdk_recover_activation')
        local=self.data/'local';profile=local/'AutoZZZ/OneDragon/cn'
        (profile/'autozzz').mkdir(parents=True);(profile/'autozzz/sdk-activation.json').write_text('{}')
        with patch.dict('os.environ',{'LOCALAPPDATA':str(local)}):
            with self.assertRaisesRegex(ValueError,'待核对事务'):
                RuntimePaths.discover(self.data/'product')
            self.assertTrue(RuntimePaths.discover(self.data/'product',use_selection=False).management_only)
    def test_recovery_connection_can_probe_and_rollback_but_cannot_run_or_edit(self):
        guard=RuntimeGeneration(self.data,'-',True);self.switch()
        for name in ('catalog','policies','policy_validate','sdk_rollback','source_catalog'):guard.check_request(name)
        for name in ('initialize_execution','prepare_application','start_application','policy_save','mcp_start'):
            with self.assertRaises(RuntimeError):guard.check_request(name)
        with self.assertRaises(RuntimeError):guard.check_execution()

    def test_already_prepared_execution_rechecks_before_submit_and_each_input(self):
        guard=RuntimeGeneration(self.data,'-');self.switch()
        execution=OneDragonExecution.__new__(OneDragonExecution)
        execution.ctx=NS(autozzz_runtime_guard=guard.check_execution)
        operation=Mock()
        with self.assertRaises(RuntimeError):execution._submit('running',operation)
        with self.assertRaises(RuntimeError):execution._validate_task_input()
        operation.assert_not_called()

    def test_real_launch_arguments_pin_generation_and_candidate_data(self):
        paths=RuntimePaths(self.data/'source',self.data/'python.exe',self.data/'profile',stable_data=self.data,generation='-')
        self.switch();args=paths.identity_arguments()
        self.assertEqual(args[args.index('--selection-generation')+1],'-')
        self.assertIn('--management-only',replace(paths,management_only=True).identity_arguments())
        selected=NS(data=self.data/'candidate')
        with self.assertRaisesRegex(RuntimeUnavailable,'资料目录'):
            replace(paths,selection=selected,generation=selection_generation(self.data)).verify()

    def test_real_worker_rotation_routing_allows_bound_capture_and_preserves_control(self):
        rotation=Mock()
        service=NS(runtime_guard=RuntimeGeneration(self.data,'-'),_rotation_sealed=False,_rotation_guard=rotation)
        for method in ('global_capture_status','global_capture_capture'):
            validate_worker_request(service,method)
        self.assertEqual(rotation.check.call_count,2)
        with self.assertRaisesRegex(RuntimeError,'轮换期间'):
            validate_worker_request(service,'policy_save')
        rotation.check.side_effect=RuntimeError('stale account binding')
        with self.assertRaisesRegex(RuntimeError,'stale account'):
            validate_worker_request(service,'global_capture_capture')
        for method in ('execution_status','pause_execution','stop_execution'):
            validate_worker_request(service,method)
        service._rotation_sealed=True
        with self.assertRaisesRegex(RuntimeError,'已收尾'):
            validate_worker_request(service,'global_capture_status')


if __name__=='__main__':unittest.main()
