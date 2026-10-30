import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

from autozzz.program_update import ProgramUpdate
from autozzz.program_update_handoff import UpdateHandoff
from autozzz.program_updates import ProgramUpdates
from tests.test_program_update import package


class UpdateLifecycleTests(unittest.TestCase):
    def test_cancel_and_close_wait_through_transient_preflight_observation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);old=package(base/'AutoZZZ','old');new=package(base/'new','new')
            ctx=NS(push_service=NS(secrets=[]));service=NS(source=old/'onedragon/source',data=base/'profile',ctx=ctx,_ctx=ctx,_assert_idle=lambda:None)
            manager=ProgramUpdates(service);started=threading.Event();allowed=threading.Event()
            class Process:
                returncode=None;failed=False
                def poll(self):
                    started.set()
                    if not self.failed:self.failed=True;raise OSError('transient observation')
                    if not allowed.is_set():return None
                    self.returncode=0;return 0
            with patch('autozzz.program_updates.subprocess.Popen',return_value=Process()) as spawn:
                state=manager._start('restore',lambda:manager._prepare(new));self.assertTrue(started.wait(5))
                manager.cancel(state['job_id']);closer=threading.Thread(target=manager.close);closer.start()
                time.sleep(.2);self.assertTrue(manager.busy and closer.is_alive());self.assertEqual(spawn.call_count,1)
                allowed.set();closer.join(5);self.assertFalse(closer.is_alive());self.assertFalse(manager.busy)
            self.assertEqual(manager.status(state['job_id'])['state'],'cancelled')

    def test_handoff_observation_error_does_not_release_or_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            handoff=UpdateHandoff.__new__(UpdateHandoff);handoff.process=NS(poll=Mock(side_effect=[OSError('temporary'),None,0]));handoff.lease=NS(close=Mock())
            handoff.receipt=Path(folder)/'ready';handoff.ready=False
            self.assertFalse(handoff.poll());self.assertFalse(handoff.poll());handoff.lease.close.assert_not_called()
            with self.assertRaises(RuntimeError):handoff.poll()
            handoff.lease.close.assert_called_once()

    def test_cancel_preserves_lease_until_original_helper_has_exited(self):
        with tempfile.TemporaryDirectory() as folder:
            handoff=UpdateHandoff.__new__(UpdateHandoff);handoff.process=NS(poll=Mock(side_effect=[OSError('temporary'),None,0]));handoff.lease=NS(close=Mock());handoff.cancel_file=Path(folder)/'cancel'
            self.assertFalse(handoff.cancel());self.assertFalse(handoff.cancel());handoff.lease.close.assert_not_called()
            self.assertTrue(handoff.cancel());handoff.lease.close.assert_called_once()

    def test_main_window_remains_alive_until_helper_acknowledges_ready(self):
        from autozzz.gui import AutoZZZWindow
        from autozzz.onedragon_bundle import digest
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);old=package(base/'AutoZZZ','old');new=package(base/'new','new');tx=ProgramUpdate.prepare(old,new,probe=lambda _:[])
            root=Mock();root.tk.splitlist.return_value=[];ui=AutoZZZWindow.__new__(AutoZZZWindow);ui.root=root
            ui.pending_program_update=dict(transaction=str(tx.job),journal_hash=digest(tx.journal))
            handoff=NS(start=Mock(),poll=Mock(side_effect=[False,True]))
            with patch('autozzz.gui.ROOT',old),patch('autozzz.program_update_handoff.UpdateHandoff',return_value=handoff):
                ui._destroy_root();root.destroy.assert_not_called();handoff.start.assert_called_once()
                ui._destroy_root();root.destroy.assert_called_once();handoff.start.assert_called_once()

    def test_start_failure_waits_for_cancellation_and_keeps_specific_error(self):
        from autozzz.gui import AutoZZZWindow
        from autozzz.onedragon_bundle import digest
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);old=package(base/'AutoZZZ','old');new=package(base/'new','new');tx=ProgramUpdate.prepare(old,new,probe=lambda _:[])
            root=Mock();root.tk.splitlist.return_value=[];ui=AutoZZZWindow.__new__(AutoZZZWindow);ui.root=root
            ui.pending_program_update=dict(transaction=str(tx.job),journal_hash=digest(tx.journal))
            handoff=NS(start=Mock(side_effect=OSError('specific start error')),cancel=Mock(side_effect=[False,True]))
            with patch('autozzz.gui.ROOT',old),patch('autozzz.program_update_handoff.UpdateHandoff',return_value=handoff),patch('autozzz.gui.messagebox.showerror') as error:
                ui._destroy_root();ui._destroy_root();root.destroy.assert_not_called();error.assert_not_called()
                ui._destroy_root();root.destroy.assert_called_once();self.assertIn('specific start error',error.call_args.args[1])


if __name__=='__main__':unittest.main()
