import threading
from types import SimpleNamespace
import unittest

from autozzz.onedragon_worker import close_service


class WorkerShutdownTests(unittest.TestCase):
    def test_context_cleanup_waits_for_original_execution_thread_even_when_stop_raises(self):
        release=threading.Event();finished=[];cleaned=[]
        def work():release.wait(3);finished.append(True)
        worker=threading.Thread(target=work);worker.start()
        def stop():release.set();raise RuntimeError('fixture stop callback failed')
        def cleanup():
            self.assertFalse(worker.is_alive());self.assertEqual(finished,[True]);cleaned.append(True)
        service=SimpleNamespace(_execution=SimpleNamespace(stop=stop,worker=worker),close=cleanup)
        try:
            with self.assertRaisesRegex(RuntimeError,'stop callback failed'):close_service(service)
            self.assertEqual(cleaned,[True])
        finally:release.set();worker.join(4)

    def test_read_only_worker_still_cleans_context(self):
        cleaned=[]
        close_service(SimpleNamespace(_execution=None,close=lambda:cleaned.append(True)))
        self.assertEqual(cleaned,[True])
