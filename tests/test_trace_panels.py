from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from autozzz.trace_preferences import DEFAULTS,TracePreferences,validate
from autozzz.trace_capture import compose_overlay


class TracePanelTests(unittest.TestCase):
    def test_legacy_file_migrates_without_writing_and_preserves_geometry(self):
        import json
        old={key:deepcopy(DEFAULTS[key]) for key in ('offset_x','offset_y','scale_x','scale_y','separate_panels','hotkeys','metrics')}
        old.update(version=1,panels={key:{field:item[field] for field in ('x','y','w','h','font_size')} for key,item in DEFAULTS['panels'].items()})
        old['panels']['state']['x']=123;old['hotkeys']['capture']='';old['metrics']['ocr_ms']=False
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'prefs.json';path.write_text(json.dumps(old),encoding='utf-8');before=path.read_bytes()
            store=TracePreferences(path);new=store.load()
            self.assertEqual(path.read_bytes(),before);self.assertEqual(new['version'],2)
            self.assertEqual(new['panels']['state']['x'],123);self.assertEqual(new['hotkeys']['capture'],'');self.assertFalse(new['metrics']['ocr_ms'])
            store.save(new);self.assertEqual(TracePreferences(path).load(),new)

    def test_invalid_float_positions_opacity_and_color_rejected(self):
        for field,value in (('opacity',0),('opacity',True),('desktop_x',float('nan')),('mode','other'),('collapsed',1)):
            settings=deepcopy(DEFAULTS);settings['panels']['state'][field]=value
            with self.assertRaises(ValueError):validate(settings)
        settings=deepcopy(DEFAULTS);settings['text_color']='red'
        with self.assertRaises(ValueError):validate(settings)

    def test_per_panel_alpha_and_desktop_exclusion_in_composite(self):
        from PIL import Image
        import time
        data=dict(standard_size=[1920,1080],items=dict(decision=[dict(created=time.time(),trigger='T',operation='O',status='S')]))
        settings=deepcopy(DEFAULTS);settings['separate_panels']=True
        panel=settings['panels']['decision'];panel.update(x=0,y=0,w=400,h=210,opacity=100)
        source=Image.new('RGB',(1920,1080),(220,180,100));layers=dict(state=False,decision=True,timeline=False,perf=False,vision=False)
        opaque=compose_overlay(source,data,{},layers,{},settings)
        self.assertEqual(opaque.getpixel((390,200)),(16,24,32))
        panel['opacity']=20;transparent=compose_overlay(source,data,{},layers,{},settings)
        self.assertNotEqual(transparent.getpixel((390,200)),opaque.getpixel((390,200)))
        panel['mode']='desktop';desktop=compose_overlay(source,data,{},layers,{},settings)
        self.assertEqual(desktop.getpixel((390,200)),source.getpixel((390,200)))


if __name__=='__main__':unittest.main()
