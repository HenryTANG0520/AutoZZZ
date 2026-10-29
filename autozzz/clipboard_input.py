"""受控粘贴；只在剪贴板仍属于本次输入时清理或恢复 Unicode 文本。"""
from __future__ import annotations

import time


class ClipboardFailure(RuntimeError):
    pass


class ClipboardPaste:
    FORMAT = 13

    def __init__(self, backend=None):
        if backend is None:
            import win32clipboard
            backend = win32clipboard
        self.backend = backend

    def _open(self):
        for attempt in range(3):
            try:
                self.backend.OpenClipboard()
                return
            except Exception:
                if attempt < 2:time.sleep(.05)
        raise ClipboardFailure('无法打开剪贴板，请检查是否被其他程序占用。') from None

    def _copy(self, text):
        self._open()
        previous,changed = None,False
        try:
            if self.backend.IsClipboardFormatAvailable(self.FORMAT):
                value = self.backend.GetClipboardData(self.FORMAT)
                if isinstance(value,str):previous=value
            self.backend.EmptyClipboard();changed=True
            self.backend.SetClipboardText(text,self.FORMAT)
            return previous,self.backend.GetClipboardSequenceNumber()
        except Exception:
            if changed:
                try:
                    self.backend.EmptyClipboard()
                    if previous is not None:self.backend.SetClipboardText(previous,self.FORMAT)
                except Exception:raise ClipboardFailure('剪贴板写入失败，原文本未能恢复。') from None
            raise ClipboardFailure('无法写入剪贴板，尚未粘贴登录信息。') from None
        finally:self.backend.CloseClipboard()

    def _restore(self, previous, sequence, text):
        self._open()
        try:
            if self.backend.GetClipboardSequenceNumber()!=sequence:return
            if not self.backend.IsClipboardFormatAvailable(self.FORMAT):return
            if self.backend.GetClipboardData(self.FORMAT)!=text:return
            self.backend.EmptyClipboard()
            if previous is not None:self.backend.SetClipboardText(previous,self.FORMAT)
        except Exception:raise ClipboardFailure('无法清理或还原本次剪贴板文本。') from None
        finally:self.backend.CloseClipboard()

    def paste(self, control, keyboard, text):
        if not isinstance(text,str) or len(text)>4096 or '\0' in text:raise ClipboardFailure('输入文本格式无效。')
        previous,sequence=control.input(lambda:self._copy(text))
        try:
            try:
                keyboard.press('ctrl')
                keyboard.tap('v')
            finally:keyboard.release('ctrl')
            control.checkpoint(require_frame=True)
        finally:self._restore(previous,sequence,text)
