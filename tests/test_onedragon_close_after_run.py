"""Run an actual one-dragon close handoff using a controlled window fixture."""

from concurrent.futures import Future
from contextlib import contextmanager
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from autozzz.onedragon_control import ExecutionControl, WindowState
from autozzz.onedragon_execution import OneDragonExecution

if __package__:
    from . import test_onedragon_rotation_session as rotation_fixture
else:
    import test_onedragon_rotation_session as rotation_fixture


class CloseAfterRunTests(unittest.TestCase):
    def start(self):
        engine = object.__new__(OneDragonExecution)
        self.engine = engine
        self.flushing = threading.Event()
        self.finish_flush = threading.Event()
        self.watcher_exited = threading.Event()
        self.steps = []
        self.errors = []

        def flush():
            self.flushing.set()
            if not self.finish_flush.wait(5):
                raise TimeoutError("fixture notification flush was not released")

        def watch(stop):
            stop.wait()
            self.watcher_exited.set()

        def request_close(checkpoint, submit):
            checkpoint()

            def post():
                self.steps.append("post-close")
                return True

            submit(post)
            return SimpleNamespace(exited=lambda: True, close=lambda: None)

        @contextmanager
        def run_scope():
            self.steps.append("lease-acquired")
            try:
                yield
            finally:
                self.steps.append("lease-released")

        def submit(state, operation):
            engine.state = state

            def run():
                try:
                    operation()
                except Exception as error:
                    self.errors.append(error)

            engine.worker = threading.Thread(target=run)
            self.addCleanup(self.join)
            engine.worker.start()
            return engine.status()

        window = SimpleNamespace(bound=SimpleNamespace(hwnd=100, request_close=request_close),
                                 process_id=20, identity_valid=lambda: True)
        window.clear_binding = lambda: setattr(window, "bound", None)
        engine.ctx = SimpleNamespace(
            ready_for_application=True,
            run_context=SimpleNamespace(is_app_registered=lambda _: True, is_app_internal=lambda _: False),
            controller=SimpleNamespace(bind=lambda _: None, game_win=window,
                                       probe=lambda: WindowState(True, True, (0, 0, 1920, 1080))),
            game_account_config=SimpleNamespace(game_path="fixture"),
            push_service=SimpleNamespace(reset_results=lambda: None, pending=True, flush=flush),
            download_cancel=threading.Event(),
        )
        engine.cancelled, engine.manual_pause = threading.Event(), threading.Event()
        engine.lock = threading.RLock()
        engine._input_ready = False
        engine.state, engine.error = "prepared", ""
        engine.client_exit, engine.client_exit_result, engine.run_lease = None, {}, None
        engine.rotation_guard = None
        engine.control = ExecutionControl(engine._probe, lambda: None, lambda: None, lambda: None, lambda: None)
        engine.control.input_validator = engine._validate_task_input
        engine.prepared_app, engine.prepared_hash = "one_dragon", "fixture-config"
        engine.prepared_requirements, engine.prepared_applications = {}, []
        engine.uses_charge, engine.route_debug, engine.resource_control = False, None, None
        engine.resource_confirmation = Mock()
        engine.configuration_digest = lambda: "fixture-config"
        engine.script_control, engine.redemption_control, engine.login_control, engine.daily_control = [Mock() for _ in range(4)]
        engine.battle_control = SimpleNamespace(allowed=True)
        future = Future()
        future.set_result(None)
        engine.backend = SimpleNamespace(_start_app=lambda *_: (True, future),
                                         query_status=lambda: SimpleNamespace(state="success"))
        engine._submit, engine._run_scope, engine._watch = submit, run_scope, watch
        engine.status = lambda: {"state": engine.state, "busy": engine.busy}
        group = Mock()
        group.close.side_effect = lambda: self.steps.append("group-cleaned")
        patcher = patch("autozzz.onedragon_group.GroupRunControl", return_value=group)
        patcher.start()
        self.addCleanup(patcher.stop)

        engine.start_application("one_dragon", close_game_on_success=True)
        self.assertTrue(self.flushing.wait(4))

    def join(self):
        self.finish_flush.set()
        self.engine.worker.join(4)
        self.assertFalse(self.engine.busy)
        self.assertFalse(self.errors)

    def test_success_closes_once_after_notifications_and_with_the_same_lease(self):
        self.start()
        self.assertEqual(self.engine.status(), {"state": "running", "busy": True})
        self.assertFalse(self.watcher_exited.is_set())
        self.assertEqual(self.steps, ["lease-acquired"])
        self.join()
        self.assertEqual(self.steps, ["lease-acquired", "group-cleaned", "post-close", "lease-released"])
        self.assertEqual(self.engine.state, "success")
        self.assertEqual(self.engine.client_exit_result, {
            "requested": True, "process_exited": True, "window_handle": 100, "process_id": 20,
        })

    def test_stop_during_notification_flush_prevents_the_close_request(self):
        self.start()
        self.assertEqual(self.engine.stop(), {"state": "stopping", "busy": True})
        self.assertTrue(self.engine.cancelled.is_set())
        self.join()
        self.assertEqual(self.engine.state, "stopped")
        self.assertEqual(self.steps, ["lease-acquired", "group-cleaned", "lease-released"])
        self.assertFalse(self.engine.client_exit_result)


class RotationCloseAfterRunTests(unittest.TestCase):
    fixture = rotation_fixture.RotationSessionTests
    setUp = fixture.setUp
    close = fixture.close
    preview = fixture.preview
    start = fixture.start
    wait = fixture.wait
    terminal = fixture.terminal

    def test_success_closes_the_returned_clients_exact_identity_once(self):
        for transition in self.broker.plan["transitions"]:
            transition["restart_client"] = True
        self.preview()
        self.start(close_game_on_success=True)
        self.assertEqual(self.terminal()["state"], "success")

        stages = self.session.report["stages"]
        logins = [item for item in stages if item["method"] == "start_login"]
        self.assertEqual([item["instance_idx"] for item in logins], [1, 2, 1])
        identities = [item["execution"]["window_identity"] for item in logins]
        self.assertEqual(len({item["process_id"] for item in identities}), 3)
        closes = [entry for entry in self.broker.calls if entry[1] == "prepare_client_close"]
        self.assertEqual([entry[2] for entry in closes], [1, 2, 1])
        self.assertEqual([entry[3]["expected_window"] for entry in closes], identities)
        self.assertTrue(self.session.report["returned_to_start"])
        self.assertTrue(self.session.report["close_game_on_success"])
        self.assertEqual([item["state"] for item in self.session.report["accounts"]], ["success", "success"])
        self.assertEqual(stages[-1]["method"], "start_client_close")
        self.assertTrue(stages[-1]["execution"]["client_exit"]["process_exited"])

    def test_failed_account_skips_final_close(self):
        self.broker.failed_group = 1
        self.preview()
        self.start(close_game_on_success=True)
        self.assertEqual(self.terminal()["state"], "failed")
        self.assertEqual(self.broker.app_starts, [1, 2])
        self.assertTrue(self.session.report["returned_to_start"])
        self.assertFalse(any(entry[1] in ("prepare_client_close", "start_client_close") for entry in self.broker.calls))
