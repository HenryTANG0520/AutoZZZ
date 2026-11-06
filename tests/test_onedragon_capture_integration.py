from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from autozzz import global_capture, onedragon_route_recorder as recorder
from autozzz.onedragon_control import WindowState
from autozzz.onedragon_library import LibraryStore
from autozzz.onedragon_map_capture import MapCaptureJob


class CaptureIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.game_path = root / "ZenlessZoneZero.exe"
        self.game_path.touch()
        self.service = SimpleNamespace(data=root, ctx=SimpleNamespace(current_instance_idx=1), _assert_idle=lambda: None)
        self.job = MapCaptureJob(self.service)
        self.job.draft_id = "draft-a"
        self.job.draft_version = 1
        self.focused = True
        self.probes = 0
        self.cleanup_count = 0
        self.geometry = (0, 0, 3840, 2160)
        self.calc_entered, self.calc_resume = self.gate()
        self.stop_entered, self.stop_resume = self.gate()
        self.cleanup_entered, self.cleanup_resume = self.gate()
        self.exit_entered, self.exit_resume = self.gate()
        self.job._calculate = self.calculate
        self.job._release = self.release
        self.job._watch = lambda stopped: None
        harness = self

        class Controller:
            def __init__(self, ctx, control):
                self.control = control

            def bind(self, path):
                pass

            @property
            def game_win(self):
                return harness.bound_window()

            def probe(self):
                harness.probes += 1
                return WindowState(True, harness.focused, harness.geometry)

            def screenshot(self):
                return self.control.observe(lambda: (0, "fixture image"))

            def cleanup_after_app_shutdown(self):
                harness.cleanup_entered.set()
                if not harness.cleanup_resume.wait(5):
                    raise RuntimeError("fixture did not release controller cleanup")
                harness.cleanup_count += 1

        host = ModuleType("autozzz.onedragon_host")
        host.ManagedController = Controller
        self.enterContext(patch.dict(sys.modules, {host.__name__: host}))
        self.enterContext(patch.object(recorder, "RunLock", self.run_lock))
        self.enterContext(patch.object(recorder, "profile_lock", lambda path: nullcontext()))
        self.enterContext(patch.object(global_capture, "standalone_window", self.reopen_window))
        self.enterContext(patch.object(global_capture, "process_created", lambda window: 51))
        self.addCleanup(self.finish)

    @staticmethod
    def gate():
        entered, resume = threading.Event(), threading.Event()
        resume.set()
        return entered, resume

    def finish(self):
        for gate in (self.calc_resume, self.stop_resume, self.cleanup_resume, self.exit_resume):
            gate.set()
        if self.job.worker is not None:
            self.job.worker.join(5)
            self.assertFalse(self.job.worker.is_alive())

    def bound_window(self):
        x, y, w, h = self.geometry
        return SimpleNamespace(bound=SimpleNamespace(path=str(self.game_path), hwnd=41), process_id=42,
                               identity_valid=lambda: True,
                               win_rect=SimpleNamespace(x1=x, y1=y, x2=x+w, y2=y+h))

    @contextmanager
    def reopen_window(self, path):
        self.assertEqual(path, str(self.game_path))
        yield self.bound_window()

    @contextmanager
    def run_lock(self):
        try:
            yield
        finally:
            self.exit_entered.set()
            if not self.exit_resume.wait(5):
                raise RuntimeError("fixture did not release task lock")

    def calculate(self, *_):
        self.calc_entered.set()
        if not self.calc_resume.wait(5):
            raise RuntimeError("fixture did not release calculation")
        return {"mask": "fixture mask", "views": ["view a", "view b"]}

    def release(self):
        if self.job.control.stopped:
            self.stop_entered.set()
            if not self.stop_resume.wait(5):
                raise RuntimeError("fixture did not release input cleanup")

    def start(self):
        return self.job._start(None, None, str(self.game_path))["job_id"]

    def test_success_is_withheld_through_cleanup_and_task_lock_exit(self):
        self.cleanup_resume.clear()
        self.exit_resume.clear()
        job_id = self.start()
        self.assertTrue(self.cleanup_entered.wait(3))
        during_cleanup = self.job.status(job_id)
        self.assertTrue(during_cleanup["busy"])
        self.assertNotEqual(during_cleanup["state"], "success")
        self.assertIsNone(during_cleanup["result"])
        self.cleanup_resume.set()
        self.assertTrue(self.exit_entered.wait(3))
        during_unlock = self.job.status(job_id)
        self.assertTrue(during_unlock["busy"])
        self.assertIsNone(during_unlock["result"])
        self.exit_resume.set()
        self.finish()
        self.assertEqual(self.job.status(job_id)["state"], "success")
        self.assertEqual(self.cleanup_count, 1)

    def test_cancel_does_not_hold_the_job_lock_during_input_release(self):
        self.calc_resume.clear()
        self.stop_resume.clear()
        job_id = self.start()
        self.assertTrue(self.calc_entered.wait(3))
        cancelled = []
        cancel = threading.Thread(target=lambda: cancelled.append(self.job.cancel(job_id)), daemon=True)
        cancel.start()
        self.assertTrue(self.stop_entered.wait(3))
        reported = threading.Event()
        statuses = []

        def query():
            statuses.append(self.job.status(job_id))
            reported.set()

        poll = threading.Thread(target=query, daemon=True)
        poll.start()
        try:
            self.assertTrue(reported.wait(1), "status waited on cancellation's device release")
            self.assertTrue(statuses[0]["busy"])
            self.assertIsNone(statuses[0]["result"])
        finally:
            self.stop_resume.set()
            self.calc_resume.set()
            cancel.join(3)
            poll.join(3)
        self.finish()
        self.assertTrue(cancelled)
        terminal = self.job.status(job_id)
        self.assertEqual(terminal["state"], "cancelled")
        self.assertIsNone(terminal["result"])

    def test_fatal_observer_error_during_teardown_prevents_success(self):
        def fatal_after_frame(stopped):
            if stopped.wait(3):
                self.job.control.stop("fixture observer failed after final frame")

        self.job._watch = fatal_after_frame
        job_id = self.start()
        self.finish()
        status = self.job.status(job_id)
        self.assertEqual(status["state"], "failed")
        self.assertIn("observer failed", status["error"])
        self.assertIsNone(status["result"])
        self.assertEqual(self.cleanup_count, 1)

    def test_observer_start_failure_still_cleans_up_the_bound_controller(self):
        thread_class = threading.Thread

        def make_thread(*args, **kwargs):
            thread = thread_class(*args, **kwargs)
            if thread.name == "AutoZZZ-Route-Hotkeys":
                def fail_start():
                    raise RuntimeError("fixture observer could not start")
                thread.start = fail_start
            return thread

        with patch.object(recorder.threading, "Thread", make_thread):
            job_id = self.start()
            self.finish()
        status = self.job.status(job_id)
        self.assertEqual(status["state"], "failed")
        self.assertIn("observer could not start", status["error"])
        self.assertIsNone(status["result"])
        self.assertEqual(self.cleanup_count, 1)

    def test_completed_map_result_is_readable_without_foreground(self):
        job_id = self.start()
        self.finish()
        self.focused = False
        observed_probes = self.probes
        status = self.job.status(job_id)
        self.assertEqual(status["state"], "success")
        draft = {"draft_id": self.job.draft_id, "version": 1}
        self.assertEqual(self.job.result_for(draft, job_id)[0], status["result"])
        self.assertEqual(self.job.result_for(draft, status["result"])[0], status["result"])
        self.assertEqual(self.probes, observed_probes)
        self.geometry = (0, 0, 2560, 1440)
        with self.assertRaisesRegex(ValueError, "尺寸已变化"):
            self.job.result_for(draft, job_id)


class RouteMapRevisionTests(unittest.TestCase):
    def test_revised_map_reads_current_icons_when_mtime_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            store = object.__new__(LibraryStore)
            store.root = Path(folder).resolve()
            store.areas = {"fixture": SimpleNamespace(full_id="fixture", entry=SimpleNamespace(entry_id="entry"))}
            directory = store.root / "assets/game_data/world_patrol/entry/fixture"
            directory.mkdir(parents=True)
            road, icons = directory / "road_mask.png", directory / "icon.yml"
            road.write_bytes(b"fixture road")
            old_icons = [{"icon_name": "Fixture A"}]
            icons.write_text(yaml.safe_dump(old_icons), encoding="utf-8")
            stamp = icons.stat()
            sdk = SimpleNamespace(road_mask_path=lambda area: road, icon_yaml_path=lambda area: icons,
                                  WorldPatrolLargeMapIcon=SimpleNamespace,
                                  WorldPatrolLargeMap=lambda area, road_mask, icon_list: SimpleNamespace(icon_list=icon_list))
            utils = SimpleNamespace(cv2_utils=SimpleNamespace(read_image=lambda _: SimpleNamespace(shape=(40, 40))),
                                    yaml_utils=SimpleNamespace(safe_load=yaml.safe_load))
            cache = SimpleNamespace(YamlOperator=lambda _: SimpleNamespace(data=old_icons))
            with patch.dict(sys.modules, {"zzz_od.application.world_patrol.world_patrol_area": sdk,
                                          "one_dragon.utils": utils, "one_dragon.base.config.yaml_operator": cache}):
                original = store._route_map_revision("fixture")
                self.assertEqual(store._map("fixture").icon_list[0].icon_name, "Fixture A")
                icons.write_text(yaml.safe_dump([{"icon_name": "Fixture B"}]), encoding="utf-8")
                os.utime(icons, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                self.assertEqual(icons.stat().st_mtime_ns, stamp.st_mtime_ns)
                self.assertNotEqual(store._route_map_revision("fixture"), original)
                self.assertEqual(store._map("fixture").icon_list[0].icon_name, "Fixture B")
