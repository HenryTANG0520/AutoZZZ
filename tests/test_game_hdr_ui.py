import copy
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from autozzz.game_hdr_ui import HdrWindow


class HdrUiTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        def cleanup():self.root.update_idletasks();self.root.destroy()
        self.addCleanup(cleanup)
        self.pending=[]
        def run(operation,success):self.owner.busy=True;self.pending.append((operation,success))
        self.owner=SimpleNamespace(root=self.root,busy=False,closing=False,closed=False,_run=run,call=lambda *args,**kwargs:(args,kwargs))
        self.window=HdrWindow(self.owner);self.window.window.withdraw()
        self.value=dict(path='fixture/ZenlessZoneZero.exe',value=dict(exists=False),pending=None,revision='initial',journal_digest=None)
    def finish(self,value=None):
        self.pending.pop()[1](value or self.value);self.owner.busy=False;self.window.poll();self.root.update_idletasks()

    def test_read_then_buttons_and_busy_request_cannot_latch_disabled(self):
        self.finish();self.assertEqual(str(self.window.buttons[1]['state']),'normal')
        self.owner.busy=True;self.window.reload()
        self.assertFalse(self.pending)
        self.owner.busy=False;self.window.poll();self.assertEqual(str(self.window.buttons[0]['state']),'normal')

    def test_failed_request_retains_read_retry(self):
        self.pending.clear();self.owner.busy=False;self.window.poll()
        self.assertEqual(str(self.window.buttons[0]['state']),'normal')
        self.assertEqual(str(self.window.buttons[1]['state']),'disabled')

    def test_pending_record_disables_manual_changes_and_requires_confirmation(self):
        value=dict(self.value,pending=dict(before=dict(exists=False),after=dict(exists=True,type=1,data='2096')),
                   journal_digest='hash',journal_path='fixture/journal.json',conflict=True)
        self.finish(value)
        self.assertEqual(str(self.window.buttons[1]['state']),'disabled');self.assertEqual(str(self.window.buttons[3]['state']),'normal')
        with patch('autozzz.game_hdr_ui.messagebox.askyesno',return_value=False):self.window.recover()
        self.assertFalse(self.pending)
        with patch('autozzz.game_hdr_ui.messagebox.askyesno',return_value=True):self.window.recover()
        operation,_=self.pending[0];args,kwargs=operation('client')
        self.assertEqual(args,('client','game_hdr_recover'));self.assertEqual(kwargs,dict(journal_digest='hash',confirmed=True))

    def test_owner_close_destroys_child_without_registry_action(self):
        self.finish();self.owner.closing=True;self.window.poll()
        self.assertFalse(self.window.window.winfo_exists());self.assertFalse(self.pending)

    def test_minimum_layout_buttons_fit(self):
        self.finish();window=self.window.window;window.deiconify();window.geometry('460x360');self.root.update()
        for button in self.window.buttons:
            x=button.winfo_rootx()-window.winfo_rootx()
            self.assertGreaterEqual(x,0);self.assertLess(x+button.winfo_width(),window.winfo_width())


if __name__=='__main__':unittest.main()
