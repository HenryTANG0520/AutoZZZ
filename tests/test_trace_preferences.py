from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autozzz.trace_preferences import DEFAULTS, TracePreferences, calibrated_box, panel_bounds, validate


class TracePreferenceTests(unittest.TestCase):
    def test_calibration_matches_source_mapping_and_clips(self):
        settings=deepcopy(DEFAULTS);settings.update(scale_x=1.2,offset_x=10,scale_y=.5,offset_y=-20)
        self.assertEqual(calibrated_box(dict(x1=100,y1=100,x2=2000,y2=1200),(1920,1080),(960,540),settings),(70,5,960,280))

    def test_layout_stays_inside_small_and_large_game_windows(self):
        panel=dict(x=1919,y=1079,w=800,h=500)
        for size in ((960,540),(3840,2160),(80,40)):
            x,y,w,h=panel_bounds(panel,size)
            self.assertGreaterEqual(min(x,y),0);self.assertLessEqual(x+w,size[0]);self.assertLessEqual(y+h,size[1])

    def test_defaults_are_independent_and_read_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'new'/'settings.json';store=TracePreferences(path);value=store.load()
            value['panels']['state']['x']=1
            self.assertEqual(store.load(),DEFAULTS);self.assertFalse(path.parent.exists())

    def test_save_reload_conflict_and_failed_write_preserve_previous(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';a=TracePreferences(path);b=TracePreferences(path);a.load();b.load()
            value=deepcopy(DEFAULTS);value['metrics']['ocr_ms']=False;a.save(value)
            with self.assertRaises(ValueError):b.save(DEFAULTS)
            original=path.read_bytes()
            with patch('autozzz.trace_preferences.os.replace',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):a.save(DEFAULTS)
            self.assertEqual(path.read_bytes(),original);self.assertEqual(list(path.parent.iterdir()),[path])
            self.assertEqual(TracePreferences(path).load(),value)

    def test_invalid_numeric_boolean_and_unknown_fields_rejected(self):
        for field,value in (('scale_x',float('nan')),('offset_x',True),('offset_y',10**1000),('scale_y',2),('version',True),('separate_panels',1)):
            settings=deepcopy(DEFAULTS);settings[field]=value
            with self.assertRaises(ValueError):validate(settings)
        settings=deepcopy(DEFAULTS);settings['panels']['state']['extra']=1
        with self.assertRaises(ValueError):validate(settings)

    def test_malformed_and_oversized_files_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            for content in ('{',' '*32769,json.dumps({'version':2})):
                path.write_text(content,encoding='utf-8')
                with self.assertRaises(ValueError):TracePreferences(path).load()


if __name__=='__main__':unittest.main()
