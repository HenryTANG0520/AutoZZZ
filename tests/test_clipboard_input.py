import unittest
from types import SimpleNamespace

from autozzz.clipboard_input import ClipboardFailure,ClipboardPaste


class Clipboard:
    def __init__(self,value):self.value,self.sequence,self.opened,self.fail=value,0,False,False
    def OpenClipboard(self):self.opened=True
    def CloseClipboard(self):self.opened=False
    def IsClipboardFormatAvailable(self,fmt):assert self.opened and fmt==13;return self.value is not None
    def GetClipboardData(self,fmt):assert self.opened and fmt==13;return self.value
    def GetClipboardSequenceNumber(self):return self.sequence
    def EmptyClipboard(self):assert self.opened;self.value=None;self.sequence+=1
    def SetClipboardText(self,text,fmt):
        assert self.opened and fmt==13
        if self.fail and text=='fixture-secret':raise RuntimeError('fixture-secret')
        self.value=text;self.sequence+=1


class ClipboardInputTests(unittest.TestCase):
    def setUp(self):
        self.clipboard=Clipboard('original Unicode 文本');self.paste=ClipboardPaste(self.clipboard)
        self.events=[]
        self.control=SimpleNamespace(input=lambda action:action(),checkpoint=lambda **_:None)
        self.keyboard=SimpleNamespace(press=lambda key:self.events.append(('press',key)),tap=self.tap,release=lambda key:self.events.append(('release',key)))
    def tap(self,key):self.events.append(('paste',self.clipboard.value));assert key=='v'

    def test_controlled_paste_restores_prior_unicode_text(self):
        self.paste.paste(self.control,self.keyboard,'fixture-secret')
        self.assertEqual(self.clipboard.value,'original Unicode 文本')
        self.assertEqual(self.events,[('press','ctrl'),('paste','fixture-secret'),('release','ctrl')])
        self.assertFalse(self.clipboard.opened)

    def test_empty_clipboard_is_cleared_after_use(self):
        self.clipboard.value=None;self.paste.paste(self.control,self.keyboard,'fixture-secret')
        self.assertIsNone(self.clipboard.value)

    def test_interrupted_paste_releases_modifier_and_restores_text(self):
        def failed(key):raise InterruptedError('fixture pause')
        self.keyboard.tap=failed
        with self.assertRaises(InterruptedError):self.paste.paste(self.control,self.keyboard,'fixture-secret')
        self.assertEqual(self.events,[('press','ctrl'),('release','ctrl')])
        self.assertEqual(self.clipboard.value,'original Unicode 文本')

    def test_user_clipboard_change_is_not_overwritten(self):
        def replaced(key):self.clipboard.value='new user copy';self.clipboard.sequence+=1
        self.keyboard.tap=replaced;self.paste.paste(self.control,self.keyboard,'fixture-secret')
        self.assertEqual(self.clipboard.value,'new user copy')

    def test_modifier_press_failure_still_releases_and_restores_clipboard(self):
        def failed(key):raise RuntimeError('fixture raw press failure')
        self.keyboard.press=failed
        with self.assertRaises(RuntimeError):self.paste.paste(self.control,self.keyboard,'fixture-secret')
        self.assertEqual(self.events,[('release','ctrl')]);self.assertEqual(self.clipboard.value,'original Unicode 文本')

    def test_copy_failure_does_not_paste_or_echo_secret(self):
        self.clipboard.fail=True
        with self.assertRaises(ClipboardFailure) as caught:self.paste.paste(self.control,self.keyboard,'fixture-secret')
        self.assertNotIn('fixture-secret',str(caught.exception));self.assertFalse(self.events)
        self.assertEqual(self.clipboard.value,'original Unicode 文本');self.assertFalse(self.clipboard.opened)

    def test_control_rejection_happens_before_touching_clipboard(self):
        def stopped(action):raise InterruptedError('stopped')
        self.control.input=stopped
        with self.assertRaises(InterruptedError):self.paste.paste(self.control,self.keyboard,'fixture-secret')
        self.assertEqual(self.clipboard.sequence,0);self.assertEqual(self.clipboard.value,'original Unicode 文本')
