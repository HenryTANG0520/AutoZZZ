"""验证真实管道的等待、故障与进程退出；不依赖上游环境或游戏。"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from autozzz.onedragon_runtime import (
    OneDragonClient, PROTOCOL_VERSION, UPSTREAM_REVISION, RequestPending,
    RuntimePaths, RemoteError, RuntimeUnavailable,
)


WORKER = '''import sys,json,time,os
count=0
close_delay=0
for line in sys.stdin:
 r=json.loads(line); m=r['method']; p=r['params']
 if m=='hello':
  time.sleep(float(os.environ.get('AUTOZZZ_FIXTURE_HELLO_DELAY','0')))
  result={'protocol':1,'revision':REVISION,'pid':os.getpid()}
 elif m=='delay':
  count+=1; time.sleep(.15); result=count
 elif m=='count': result=count
 elif m=='error':
  print(json.dumps({'id':r['id'],'error':'配置无效'}),flush=True); continue
 elif m=='malformed':
  print('broken protocol',flush=True); continue
 elif m=='missing_result':
  print(json.dumps({'id':r['id']}),flush=True); continue
 elif m=='exit': break
 elif m=='close_delay': close_delay=p['seconds']; result=True
 elif m=='logs':
  print('x'*100000,file=sys.stderr,flush=True); result='ok'
 else: result=None
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
time.sleep(close_delay)
'''


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.script = root / "worker.py"
        self.script.write_text("REVISION=" + repr(UPSTREAM_REVISION) + "\n" + WORKER, encoding="utf-8")
        self.paths = RuntimePaths(root / "source", Path(sys.executable), root / "data")
        self.real_popen = subprocess.Popen
        self.prepare = patch.object(RuntimePaths, "prepare").start()
        self.popen = patch("autozzz.onedragon_runtime.subprocess.Popen", side_effect=self.spawn).start()
        self.client = OneDragonClient(self.paths)
        self.client.start()

    def spawn(self, _command, **kwargs):
        return self.real_popen([sys.executable, "-X", "utf8", "-u", str(self.script)], **kwargs)

    def tearDown(self):
        try:
            self.client.close()
        finally:
            patch.stopall()
            self.temp.cleanup()

    def test_timeout_retains_request_without_repeating_mutation(self):
        request = self.client.submit("delay")
        with self.assertRaises(RequestPending) as caught:
            self.client.result(request, timeout=.001)
        self.assertEqual(caught.exception.request_id, request)
        self.assertTrue(self.client.alive)
        self.assertEqual(self.client.result(request, timeout=2), 1)
        self.assertEqual(self.client.result(request, timeout=0), 1)
        self.assertEqual(self.client.call("count"), 1)
        self.assertEqual(self.popen.call_count, 1)

    def test_ordinary_worker_does_not_inherit_a_foreign_task_lease(self):
        from autozzz.task_lease import LEASE_ENV
        import os
        with patch.dict(os.environ,{LEASE_ENV:'fixture foreign lease'}):
            other=OneDragonClient(self.paths)
            try:other.start()
            finally:other.close()
        self.assertNotIn(LEASE_ENV,self.popen.call_args.kwargs['env'])

    def test_start_timeout_finishes_same_hello_without_spawning_another_worker(self):
        import os
        before=self.popen.call_count
        with patch.dict(os.environ,{'AUTOZZZ_FIXTURE_HELLO_DELAY':'.15'}):
            other=OneDragonClient(self.paths)
            try:
                with self.assertRaises(RequestPending):other.start(timeout=.001)
                hello=other.finish_start(timeout=3)
                self.assertEqual(other.finish_start(timeout=0),hello)
                self.assertEqual(len(other._replies),1)
                self.assertEqual(self.popen.call_count,before+1)
                self.assertTrue(other.alive)
            finally:other.close()

    def test_missing_worker_acknowledgement_rejects_requested_lease(self):
        from types import SimpleNamespace
        other=OneDragonClient(self.paths,run_lease=SimpleNamespace(export=lambda:'fixture requested lease'))
        with self.assertRaisesRegex(RuntimeUnavailable,'未确认轮换任务锁'):other.start()
        self.assertFalse(other.alive)

    def test_remote_error_does_not_restart_process(self):
        with self.assertRaisesRegex(RemoteError, "配置无效"):
            self.client.call("error")
        self.assertEqual(self.client.call("count"), 0)
        self.assertEqual(self.popen.call_count, 1)

    def test_invalid_frame_fails_pending_request_and_rejects_new_work(self):
        with self.assertRaisesRegex(RemoteError, "响应无效"):
            self.client.call("malformed")
        with self.assertRaises(RuntimeUnavailable):
            self.client.call("count")

    def test_terminal_process_fails_request(self):
        with self.assertRaisesRegex(RemoteError, "通信已结束"):
            self.client.call("exit")

    def test_response_without_a_result_cannot_be_reported_as_success(self):
        with self.assertRaisesRegex(RemoteError, "响应结构无效"):
            self.client.call("missing_result")

    def test_stderr_is_drained_even_if_log_callback_raises(self):
        def fail(_line):
            raise RuntimeError("日志控件已关闭")
        self.client.log = fail
        self.assertEqual(self.client.call("logs"), "ok")

    def test_shutdown_timeout_can_wait_again_on_same_process(self):
        self.client.call("close_delay", seconds=.2)
        with self.assertRaises(RequestPending):
            self.client.close(timeout=.001)
        self.assertTrue(self.client.alive)
        self.client.close(timeout=2)
        self.assertFalse(self.client.alive)
        self.assertEqual(self.popen.call_count, 1)

    def test_running_request_cannot_be_forgotten(self):
        request = self.client.submit("delay")
        with self.assertRaises(RuntimeError):
            self.client.forget(request)
        self.client.result(request)
        self.client.forget(request)
        with self.assertRaises(KeyError):
            self.client.result(request)

    def test_large_message_is_rejected_before_writing(self):
        with self.assertRaises(ValueError):
            self.client.call("delay", text="x" * (2 * 1024 * 1024))
        self.assertEqual(self.client.call("count"), 0)

    def test_client_does_not_restart_after_close(self):
        self.client.close()
        with self.assertRaises(RuntimeError):
            self.client.start()

    @unittest.skipUnless(sys.platform == "win32", "Windows venv launcher")
    def test_launcher_exit_does_not_hide_a_live_worker(self):
        with patch.object(self.client.process, "poll", return_value=0):
            self.assertTrue(self.client.alive)
            self.assertEqual(self.client.call("count"), 0)
        self.assertIsInstance(self.client.worker_pid, int)


class ProfileTests(unittest.TestCase):
    def test_initialization_does_not_overwrite_existing_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, data = root / "source", root / "data"
            for folder in (source, data):
                (folder / "config").mkdir(parents=True)
            (source / "config" / "project.yml").write_text("upstream")
            (source / "config" / "another.yml").write_text("new")
            (data / "config" / "project.yml").write_text("user changes")
            paths = RuntimePaths(source, Path(sys.executable), data)
            with patch.object(RuntimePaths, "verify"), patch("autozzz.onedragon_runtime.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, b"config/project.yml\0config/another.yml\0")
                paths.prepare()
                paths.prepare()
            self.assertEqual((data / "config" / "project.yml").read_text(), "user changes")
            self.assertEqual((data / "config" / "another.yml").read_text(), "new")
            self.assertEqual((source / "config" / "project.yml").read_text(), "upstream")
            self.assertEqual(run.call_count, 1)
            self.assertEqual(json.loads((data / "autozzz-runtime.json").read_text())["revision"], UPSTREAM_REVISION)

    def test_profile_from_another_version_requires_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "autozzz-runtime.json").write_text('{"revision":"old"}')
            paths = RuntimePaths(data / "source", Path(sys.executable), data)
            with patch.object(RuntimePaths, "verify"), self.assertRaisesRegex(RuntimeUnavailable, "迁移"):
                paths.prepare()


if __name__ == "__main__":
    unittest.main()
