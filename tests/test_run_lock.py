"""用 Windows 实际命名对象验证与旧 Runner 相同的互斥语义。"""
import ctypes
import subprocess
import sys
from pathlib import Path
import unittest
import uuid

from autozzz.run_lock import RunLock, profile_lock


@unittest.skipUnless(sys.platform == "win32", "Windows named mutex")
class RunLockTests(unittest.TestCase):
    def test_child_cannot_acquire_until_original_handle_is_closed(self):
        name = "Local\\AutoZZZ.RunLockTest." + uuid.uuid4().hex
        code = "from autozzz.run_lock import RunLock; import sys; lock=RunLock(sys.argv[1]); lock.acquire(); lock.close()"
        with RunLock(name):
            child = subprocess.run([sys.executable, "-c", code, name], capture_output=True, timeout=10)
            self.assertNotEqual(child.returncode, 0)
        child = subprocess.run([sys.executable, "-c", code, name], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)

    def test_rejection_closes_only_its_own_extra_handle(self):
        name = "Local\\AutoZZZ.RunLockTest." + uuid.uuid4().hex
        first, second = RunLock(name), RunLock(name)
        first.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, "另一个"):
                second.acquire()
            self.assertIsNone(second.handle)
        finally:
            first.close()
        second.acquire()
        second.close()

    def test_profile_locks_ignore_path_case_but_allow_other_profiles(self):
        root = Path.cwd()/"build"/uuid.uuid4().hex
        with profile_lock(root/"cn"):
            with self.assertRaises(RuntimeError):
                with profile_lock(Path(str(root/"CN").upper())):
                    self.fail("same profile")
            with profile_lock(root/"global"):
                pass


if __name__ == "__main__":
    unittest.main()
