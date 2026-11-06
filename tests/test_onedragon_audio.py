from concurrent.futures import Future
from contextvars import ContextVar
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from autozzz.onedragon_audio import _RecorderLifecycle


class AudioRecorderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.recorder = SimpleNamespace(running=False, clear_audio=Mock())
        self.epoch = ContextVar('audio_epoch', default='original')
        self.streams = []
        self.lifecycle = _RecorderLifecycle(self.recorder, Mock(), SimpleNamespace(submit=self.submit))
        utils = SimpleNamespace(handle_future_result=Mock())
        imports = patch.dict(sys.modules, {'one_dragon.utils.thread_utils': utils})
        imports.start()
        self.addCleanup(imports.stop)

    def submit(self, _):
        future = Future()
        future.set_running_or_notify_cancel()
        self.streams.append((future, self.epoch.get()))
        return future

    def pause_and_request_resume(self):
        self.lifecycle.start()
        self.lifecycle.stop()
        token = self.epoch.set('resumed')
        try:
            self.lifecycle.start()
        finally:
            self.epoch.reset(token)

    def test_resume_waits_for_drain_and_uses_fresh_observation_context(self):
        self.pause_and_request_resume()
        self.assertEqual(len(self.streams), 1)
        check = Mock(return_value=True)
        self.assertFalse(self.lifecycle.check(check, 1))
        check.assert_not_called()

        self.streams[0][0].set_result(None)

        self.assertEqual([epoch for _, epoch in self.streams], ['original', 'resumed'])
        self.assertEqual(self.recorder.clear_audio.call_count, 2)
        self.assertTrue(self.lifecycle.check(check, 2))
        check.assert_called_once_with(2)

    def test_stop_revokes_a_resume_while_the_previous_stream_drains(self):
        self.pause_and_request_resume()
        self.lifecycle.stop()
        self.streams[0][0].set_result(None)

        self.assertFalse(self.recorder.running)
        self.assertIsNone(self.lifecycle.worker)
        self.assertEqual(len(self.streams), 1)
