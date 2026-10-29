import json
import os
import queue
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from autozzz.run_lock import RunLock
from autozzz.task_lease import JoinedRunLease,RunLeaseOwner,LEASE_ENV


@unittest.skipUnless(sys.platform=='win32','Windows task lease')
class TaskLeaseTests(unittest.TestCase):
    def child(self,code,owner=None):
        environment=os.environ.copy();environment['PYTHONUTF8']='1'
        environment.pop(LEASE_ENV,None)
        if owner is not None:environment[LEASE_ENV]=owner.export()
        process=subprocess.Popen([sys._base_executable,'-u','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,text=True,encoding='utf-8',env=environment,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=10)
        self.addCleanup(cleanup);return process

    def line(self,process):
        observed=queue.Queue()
        thread=threading.Thread(target=lambda:observed.put(process.stdout.readline()),daemon=True)
        thread.start()
        try:value=observed.get(timeout=10)
        except queue.Empty:self.fail('owned lease fixture did not respond')
        thread.join(1);return value.strip()

    def test_default_worker_does_not_attach_without_explicit_descriptor(self):
        with patch.dict(os.environ,{},clear=True):self.assertIsNone(JoinedRunLease.from_environment())

    def test_owner_excludes_other_jobs_and_descriptor_expires(self):
        owner=RunLeaseOwner()
        with self.assertRaisesRegex(RuntimeError,'已释放'):owner.export()
        with owner:
            value=json.loads(owner.export());self.assertEqual(value['parent_pid'],os.getpid())
            with self.assertRaises(RuntimeError):RunLock().acquire()
        with self.assertRaisesRegex(RuntimeError,'已释放'):owner.export()
        with self.assertRaisesRegex(RuntimeError,'新的任务锁'):owner.acquire()
        with RunLock():pass

    def test_non_descendant_cannot_attach_even_with_live_descriptor(self):
        with RunLeaseOwner() as owner:
            with self.assertRaisesRegex(RuntimeError,'父进程身份不符'):JoinedRunLease(owner.export())

    def test_creation_time_change_rejects_same_parent_pid(self):
        with RunLeaseOwner() as owner:
            value=json.loads(owner.export());value['parent_created']+=1
            child=self.child('from autozzz.task_lease import JoinedRunLease; JoinedRunLease('+repr(json.dumps(value))+')')
            _,error=child.communicate(timeout=10)
            self.assertNotEqual(child.returncode,0);self.assertIn('父进程身份不符',error)

    def test_incomplete_or_wrong_parent_descriptor_is_rejected(self):
        for value in ({},dict(version=True,parent_pid=os.getppid(),capability='x'),
                      dict(version=1,parent_pid=os.getpid(),capability='Local\\AutoZZZ.Rotation.'+'a'*32)):
            with self.subTest(value=value),self.assertRaisesRegex(RuntimeError,'交接信息无效'):JoinedRunLease(json.dumps(value))

    def test_child_retains_total_lock_after_parent_closes_owner_handle(self):
        code='''from autozzz.task_lease import JoinedRunLease,LEASE_ENV
from autozzz.run_lock import RunLock
import os,sys
lease=JoinedRunLease.from_environment()
assert LEASE_ENV not in os.environ
try:
 with lease.scope():
  try:RunLock().acquire();raise AssertionError('other job entered')
  except RuntimeError:pass
  print('joined',flush=True)
  sys.stdin.readline()
  lease.check()
finally:lease.close()
'''
        owner=RunLeaseOwner().acquire()
        try:
            child=self.child(code,owner);self.assertEqual(self.line(child),'joined')
            owner.close()
            with self.assertRaises(RuntimeError):RunLock().acquire()
            child.communicate('finish\n',timeout=10);self.assertEqual(child.returncode,0)
            with RunLock():pass
        finally:owner.close()

    def test_stale_descriptor_cannot_join_another_current_task(self):
        owner=RunLeaseOwner().acquire();descriptor=owner.export();owner.close()
        with RunLock():
            code='from autozzz.task_lease import JoinedRunLease; JoinedRunLease('+repr(descriptor)+')'
            child=self.child(code);_,error=child.communicate(timeout=10)
            self.assertNotEqual(child.returncode,0);self.assertIn('交接已结束',error)

    def test_same_lease_still_allows_only_one_game_execution(self):
        first_code='''from autozzz.task_lease import JoinedRunLease
import sys
lease=JoinedRunLease.from_environment()
try:
 with lease.scope():
  print('executing',flush=True);sys.stdin.readline()
finally:lease.close()
'''
        second_code='''from autozzz.task_lease import JoinedRunLease
lease=JoinedRunLease.from_environment()
try:
 try:
  with lease.scope():raise AssertionError('concurrent input accepted')
 except RuntimeError:print('blocked',flush=True)
finally:lease.close()
'''
        with RunLeaseOwner() as owner:
            first=self.child(first_code,owner);self.assertEqual(self.line(first),'executing')
            second=self.child(second_code,owner);output,error=second.communicate(timeout=10)
            self.assertEqual(second.returncode,0,error);self.assertEqual(output.strip(),'blocked')
            first.communicate('finish\n',timeout=10);self.assertEqual(first.returncode,0)

    def test_orphan_worker_detects_actual_parent_exit_and_releases_total_lock(self):
        worker='''from autozzz.task_lease import JoinedRunLease
import time
lease=JoinedRunLease.from_environment()
try:
 print('joined',flush=True)
 for _ in range(500):
  try:lease.check()
  except RuntimeError:
   print('owner_exit_confirmed',flush=True);break
  time.sleep(.01)
 else:raise AssertionError('owner did not exit')
finally:lease.close()
'''
        parent='''from autozzz.task_lease import RunLeaseOwner,LEASE_ENV
import subprocess,os,sys
owner=RunLeaseOwner().acquire()
environment=os.environ.copy();environment[LEASE_ENV]=owner.export()
subprocess.Popen([sys.executable,'-u','-c',WORKER],env=environment,stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
sys.stdin.readline()
os._exit(0)
'''.replace('WORKER',repr(worker))
        parent_process=self.child(parent);self.assertEqual(self.line(parent_process),'joined')
        output,error=parent_process.communicate('exit\n',timeout=10)
        self.assertEqual(parent_process.returncode,0,error);self.assertIn('owner_exit_confirmed',output)
        with RunLock():pass
