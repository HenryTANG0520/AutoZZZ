import queue
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

from autozzz.source_workspace_ui import SourceBranchesWindow


class SDKWindowLifecycleTests(unittest.TestCase):
    def owner(self):
        owner=object.__new__(SourceBranchesWindow)
        owner.lifecycle_lock=threading.Lock();owner.worker_finalizing=False;owner.closing=owner.worker_done=False
        owner.sdk_window=None;owner.profile='cn';owner.events=queue.Queue();owner.commands=queue.Queue()
        return owner

    def test_parent_worker_waits_for_sdk_child_before_closed_event(self):
        owner=self.owner();order=[]
        child=NS(request_close=lambda:order.append('child_cancel'),worker=NS(join=lambda:order.append('child_exited')))
        owner.sdk_window=child
        client=NS(start=Mock(side_effect=RuntimeError('control channel stopped')),close=lambda **kw:order.append('parent_sdk_closed'))
        with patch('autozzz.source_workspace_ui.OneDragonClient',return_value=client):owner.work()
        self.assertEqual(order,['parent_sdk_closed','child_cancel','child_exited'])
        self.assertTrue(owner.worker_finalizing)
        events=[]
        while not owner.events.empty():events.append(owner.events.get()[0])
        self.assertEqual(events[-1],'closed')

    def test_finalizing_parent_cannot_launch_new_sdk_child(self):
        owner=self.owner();owner.worker_finalizing=True
        with patch('autozzz.sdk_upgrade_ui.SDKUpgradeWindow')as construct:owner.open_sdk();construct.assert_not_called()


if __name__=='__main__':unittest.main()
