"""Exercise the actual session event contract and each existing UI error handler."""
from concurrent.futures import Future
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from autozzz.onedragon_session import RuntimeSession
from autozzz.gamepad_install_ui import GamepadInstallWindow
from autozzz.agent_templates_ui import AgentTemplatesWindow
from autozzz.onedragon_accounts_ui import AccountsWindow
from autozzz.onedragon_download_ui import DownloadsWindow
from autozzz.onedragon_library_ui import LibraryWindow
from autozzz.onedragon_notify_ui import ChannelsWindow
from autozzz.program_updates_ui import ProgramUpdatesWindow
from autozzz.onedragon_run_ui import ExecutionWindow


class RpcErrorCompatibilityTests(unittest.TestCase):
    def fixture(self,event):
        messages=[];events=queue.Queue();events.put(event)
        window=NS(closed=False,closing=False,busy=True,callback=lambda *_:self.fail('failed RPC callback ran'),
            connected=True,events=events,status=NS(set=messages.append),job=None,capture=None,binding=None,
            pending_import=None,after_map_saved=None,loading_account=False,bootstrapping=False,
            window=NS(after=lambda *_:None,deiconify=lambda:None),
            ui=NS(root=NS(deiconify=lambda:None)),worker=NS(is_alive=lambda:True),
            thaw=lambda:None,buttons=lambda:None,update_buttons=lambda:None,poll_keys=lambda:None,
            write=messages.append,poll=lambda:None)
        return window,messages

    def test_actual_worker_retains_legacy_errors_and_mcp_tagged_errors(self):
        class Client:
            alive=True
            def __init__(self,*_,**__):pass
            def start(self):pass
            def submit(self,method,**params):
                if method=='rejected':raise ValueError('fixture rejected')
                return 'ok'
            def result(self,key,timeout):return {'recovered':True}
            def close(self,timeout=5):self.alive=False
        events=queue.Queue()
        with tempfile.TemporaryDirectory() as temporary,patch('autozzz.onedragon_session.OneDragonClient',Client):
            session=RuntimeSession(None,events.put,report_root=Path(temporary))
            try:
                self.assertEqual(events.get(timeout=2)['type'],'connected')
                for tag in ('gamepad_catalog','agent_template_catalog','account_list','model_download_catalog',
                            'library_catalog','notification_channels','program_catalog','run-account-list','mcp-fixture'):
                    session.send('rpc',method='rejected',tag=tag,params={})
                    event=events.get(timeout=2)
                    self.assertEqual(event['type'],'rpc_error' if tag.startswith('mcp-') else 'error')
                    self.assertEqual(event['tag'],tag);self.assertFalse(event['busy'])
                    self.assertIn('fixture rejected',event['message'])
                session.send('rpc',method='accepted',tag='gamepad_catalog',params={})
                event=events.get(timeout=2)
                self.assertEqual(event['type'],'rpc_result');self.assertTrue(event['result']['recovered'])
            finally:session.close();session.worker.join(5)
            self.assertFalse(session.worker.is_alive())

    def test_all_original_management_windows_release_busy_and_callback(self):
        for kind in (GamepadInstallWindow,AgentTemplatesWindow,AccountsWindow,DownloadsWindow,
                     LibraryWindow,ChannelsWindow,ProgramUpdatesWindow):
            with self.subTest(window=kind.__name__):
                window,messages=self.fixture(dict(type='error',tag='fixture',message='rejected',busy=False))
                kind.poll(window)
                self.assertFalse(window.busy);self.assertIsNone(window.callback)
                self.assertIn('rejected',messages)
        window,messages=self.fixture(dict(type='error',tag='run-account-list',message='rejected',busy=False))
        ExecutionWindow.poll(window)
        self.assertFalse(window.busy);self.assertIn('rejected',messages)

    def test_mcp_error_finishes_original_future_without_changing_execution_busy(self):
        window,messages=self.fixture(dict(type='rpc_error',tag='mcp-fixture',message='rejected'))
        future=Future();window.mcp_requests={'mcp-fixture':future};window.mcp_requests_lock=threading.Lock()
        ExecutionWindow.poll(window)
        self.assertTrue(window.busy)
        self.assertTrue(future.done())
        with self.assertRaisesRegex(RuntimeError,'rejected'):future.result()


if __name__=='__main__':unittest.main()
