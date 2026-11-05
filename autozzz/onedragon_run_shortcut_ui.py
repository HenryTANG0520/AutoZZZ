"""Start a prepared application through its existing enabled Start button."""
from __future__ import annotations

from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

from .run_hotkeys import MOUSE_CODES, RunKeyEvents, run_bindings


class PreparedRunShortcut:
    def __init__(self, window, *, source_factory=RunKeyEvents, clock=time.monotonic):
        self.view = window
        self.source_factory = source_factory
        self.clock = clock
        self.source = None
        self.bindings = None
        self.context = None
        self.armed_at = 0.
        self.press = None

    def configure(self, settings):
        bindings = run_bindings(settings['start'], settings['stop'])
        if bindings == self.bindings:
            return
        self.close()
        self.source = self.source_factory(bindings)
        self.bindings = bindings

    def close(self):
        if self.source is not None:
            self.source.close()
            self.source.join()
            self.source = None
        self.bindings = self.context = self.press = None

    def _active_context(self):
        view = self.view
        if (view.prepared and not view.busy and not view.closing and not view.ui.closing
                and view.start_button.instate(['!disabled']) and view.window.winfo_viewable()
                and view.window.grab_current() is None):
            return view.last_state
        return None

    def _eligible(self, edge):
        from .windows import W, user32, process_path

        hwnd = user32.GetForegroundWindow()
        if not hwnd or hwnd != edge.foreground:
            return False
        get_ancestor = user32.GetAncestor
        get_ancestor.argtypes = (W.HWND, W.UINT)
        get_ancestor.restype = W.HWND
        own_window = get_ancestor(self.view.window.winfo_id(), 2)
        if hwnd == own_window:
            if edge.action == 'f9':
                return True
            if edge.code in MOUSE_CODES:
                return False  # An ordinary click in the form is not a Start command.
            focus = self.view.window.focus_get()
            editors = (tk.Entry, tk.Text, tk.Spinbox, ttk.Entry, ttk.Combobox, ttk.Spinbox)
            return not isinstance(focus, editors)
        if edge.source != 'hook':
            return False
        expected = self.view.game_path.get()
        if self.view.app_id == '_rotation':
            expected = (self.view.session.plan or {}).get('origin', {}).get('game_path')
        if not expected:
            return False
        observed = process_path(hwnd)
        if not observed or Path(observed).name.casefold() != 'zenlesszonezero.exe':
            return False
        try:
            return Path(observed).resolve() == Path(expected).resolve()
        except (OSError, RuntimeError, ValueError):
            return False

    def poll(self):
        if self.source is None:
            return
        now = self.clock()
        context = self._active_context()
        if context is not self.context:
            self.context, self.armed_at, self.press = context, now, None
        edges = self.source.drain()
        if any(edge.down and not edge.injected and (edge.action == 'f10'
                or edge.action == 'stop' and edge.source == 'hook') for edge in edges):
            self.press = None
            if getattr(self.view.finish_display, 'state', '') == 'pending':
                self.view.cancel_finish()
            return
        if context is None:
            return
        for edge in edges:
            if edge.action not in ('f9', 'start') or edge.injected:
                continue
            if edge.source == 'polling' and edge.action != 'f9':
                continue
            identity = edge.generation, edge.press_id, edge.foreground
            fresh = self.armed_at <= edge.created and now - edge.created <= .5
            if edge.down:
                self.press = identity if fresh and self._eligible(edge) else None
            elif identity == self.press:
                self.press = None
                if fresh and self._eligible(edge) and self._active_context() is context:
                    self.view.start_button.invoke()
                    return
