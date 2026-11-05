"""Closing the parent must retain children that still own native processes."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from autozzz.gui import AutoZZZWindow


class QuietWindow(AutoZZZWindow):
    @property
    def dirty(self):return False


class MaintenanceShutdownTests(unittest.TestCase):
    def window(self):
        ui=QuietWindow.__new__(QuietWindow)
        ui.closing=ui.busy=ui.running=False
        ui.worker=ui.pip_controller=None
        ui.root=Mock()
        ui.startup_updates=SimpleNamespace(worker_alive=False,close=Mock())
        ui._destroy_root=Mock()
        return ui

    def test_parent_waits_for_each_maintenance_worker_after_close(self):
        for name in ('gamepad_install_window','source_workspace_window','devtools_window','environment_tools_window'):
            with self.subTest(name=name):
                ui=self.window()
                worker=SimpleNamespace(is_alive=Mock(return_value=True))
                child=SimpleNamespace(closed=False,close=Mock(),worker=worker)
                setattr(ui,name,child)
                ui._close()
                child.close.assert_called_once()
                ui.root.withdraw.assert_called_once()
                ui._destroy_root.assert_not_called()
                child.closed=True
                self.assertTrue(ui._maintenance_workers_alive())
                worker.is_alive.return_value=False
                self.assertFalse(ui._maintenance_workers_alive())

    def test_finished_maintenance_does_not_hold_parent_open(self):
        ui=self.window()
        ui.source_workspace_window=SimpleNamespace(closed=True,worker=SimpleNamespace(is_alive=lambda:False))
        ui._close()
        ui._destroy_root.assert_called_once()


if __name__=='__main__':unittest.main()
