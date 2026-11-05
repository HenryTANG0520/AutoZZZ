from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from autozzz.overlay import GameLogOverlay
from autozzz.trace_preferences import DEFAULTS,TracePreferences,validate


class LogPanelTests(unittest.TestCase):
    def test_old_four_panel_preferences_load_without_writing(self):
        value=deepcopy(DEFAULTS);value['panels'].pop('log');value['panels']['state']['x']=233
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'preferences.json';path.write_text(json.dumps(value),encoding='utf-8')
            original=path.read_bytes();loaded=TracePreferences(path).load()
            self.assertEqual(loaded['panels']['state']['x'],233)
            self.assertEqual(loaded['panels']['log'],DEFAULTS['panels']['log'])
            self.assertEqual(path.read_bytes(),original)

    def test_log_layout_has_same_validation_as_trace_panels(self):
        for key,value in (('mode','unknown'),('opacity',0),('font_size',True),('desktop_x',40000),('visible','true')):
            draft=deepcopy(DEFAULTS);draft['panels']['log'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate(draft)

    def test_desktop_edits_preserve_other_windows_latest_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=TracePreferences(Path(temporary)/'preferences.json');store.load()
            overlay=GameLogOverlay.__new__(GameLogOverlay);overlay.preference_store=store
            other=TracePreferences(store.path);value=other.load();value['panels']['decision']['opacity']=42
            other.save(value)
            overlay.save_panel('log',dict(desktop_x=230,mode='desktop',opacity=53))
            saved=TracePreferences(store.path).load()
            self.assertEqual(saved['panels']['decision']['opacity'],42)
            self.assertEqual(saved['panels']['log']['desktop_x'],230)
            self.assertEqual(saved['panels']['log']['opacity'],53)
            self.assertTrue(overlay._dirty)
