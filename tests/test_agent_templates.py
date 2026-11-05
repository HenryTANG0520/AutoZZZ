from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from autozzz.agent_templates import TemplateTransaction
from autozzz.agent_template_hotkeys import DEFAULTS,validate_bindings


class TemplateTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.tx=TemplateTransaction(self.root);self.path=self.root/'assets/template/battle/avatar_1_fixture/raw.png'
    def test_grouped_publication_and_archive_old_bytes(self):
        self.path.parent.mkdir(parents=True);self.path.write_bytes(b'old')
        archive=self.tx.publish({self.path:b'new'},dict(agent='fixture'),lambda:True)
        meta,files,_=self.tx.load(archive)
        self.assertEqual(files,[(self.path,b'old',b'new')]);self.assertEqual(self.path.read_bytes(),b'new')
        self.assertFalse(self.tx.pending.exists())
    def test_publish_failure_rolls_back_known_group(self):
        self.path.parent.mkdir(parents=True);self.path.write_bytes(b'old')
        other=self.path.with_name('mask.png');other.write_bytes(b'old mask');original=self.tx._put;calls=[]
        def write(path,raw):
            calls.append(path)
            if len(calls)==3:raise OSError('fixture interrupted')
            return original(path,raw)
        with patch.object(self.tx,'_put',side_effect=write),self.assertRaises(OSError):self.tx.publish({self.path:b'new',other:b'new mask'},{},lambda:True)
        self.assertEqual(self.path.read_bytes(),b'old');self.assertEqual(other.read_bytes(),b'old mask');self.assertFalse(self.tx.pending.exists())
    def test_wrong_target_and_shared_file_are_rejected(self):
        with self.assertRaises(ValueError):self.tx.publish({self.root/'config/one_dragon.yml':b'bad'},{},lambda:True)
        self.path.parent.mkdir(parents=True);self.path.write_bytes(b'old');(self.root/'shared').hardlink_to(self.path)
        with self.assertRaises(ValueError):self.tx.publish({self.path:b'new'},{},lambda:True)
        self.assertEqual(self.path.read_bytes(),b'old')
    def test_missing_or_changed_guard_cannot_publish(self):
        with self.assertRaises(ValueError):self.tx.publish({self.path:b'new'},{},lambda:False)
        self.assertFalse(self.path.exists())


class TemplateHotkeyTests(unittest.TestCase):
    def test_source_style_and_native_style_share_bindings(self):
        self.assertEqual(validate_bindings({key:value.lower() for key,value in DEFAULTS.items()}),DEFAULTS)
    def test_duplicate_reserved_and_invalid_keys_rejected(self):
        for value in ('Ctrl+F9','Alt+F10','Ctrl+Ctrl+1','Ctrl+MouseLeft','1'):
            with self.subTest(value=value),self.assertRaises(ValueError):validate_bindings(dict(DEFAULTS,battle_1=value))
        with self.assertRaises(ValueError):validate_bindings(dict(DEFAULTS,battle_1=DEFAULTS['battle_2']))


class TemplateCaptureTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.game=self.root/'ZenlessZoneZero.exe';self.game.touch();self.front=100;self.valid=True;self.rect=[1,2,1921,1082];self.captured=0;self.cleaned=0;self.released=0
        owner=self
        class Window:
            def __init__(self,*_):pass
            def init_win(self):pass
            def identity_valid(self):return owner.valid
            @property
            def win_rect(self):return SimpleNamespace(x1=owner.rect[0],y1=owner.rect[1],x2=owner.rect[2],y2=owner.rect[3])
            def clear_binding(self):owner.released+=1
        class Screenshot:
            def __init__(self,*_):pass
            def get_screenshot(self,**params):
                owner.captured+=1;owner.assertEqual(params,dict(independent=True,resize=False))
                if owner.change_geometry:owner.rect[0]+=1
                return np.full((1080,1920,3),(21,91,181),dtype=np.uint8)
            def cleanup(self):owner.cleaned+=1
        self.change_geometry=False
        self.scope=patch.dict('sys.modules',{'autozzz.onedragon_host':SimpleNamespace(BoundGameWindow=Window,WindowCapture=Screenshot)})
        self.scope.start();self.addCleanup(self.scope.stop)
    def run_capture(self,lock=None):
        from autozzz.agent_template_capture import capture
        with patch('autozzz.agent_template_capture.RunLock',return_value=nullcontext()),patch('autozzz.windows.find_game',return_value=SimpleNamespace(hwnd=100)),patch('autozzz.windows.kernel32.OpenProcess',return_value=10),patch('autozzz.windows.user32.GetWindowThreadProcessId'),patch('autozzz.windows.user32.GetForegroundWindow',side_effect=lambda:self.front),patch('autozzz.windows.user32.IsIconic',return_value=False),patch('autozzz.windows.user32.GetAsyncKeyState',return_value=0):
            return capture(self.root,str(self.game),lock=lock)
    def test_capture_png_is_rgb_and_uses_shared_hash_protocol(self):
        from autozzz.trace_capture import read_capture
        result=self.run_capture();image,_=read_capture(self.root,result)
        self.assertEqual(image.getpixel((100,100)),(21,91,181));self.assertEqual(self.captured,1)
        self.assertNotIn('epoch',result);self.assertEqual((self.cleaned,self.released),(1,1))
    def test_focus_identity_or_geometry_change_rejects_capture(self):
        for attribute,value in (('front',101),('valid',False),('change_geometry',True)):
            self.front=100;self.valid=True;self.change_geometry=False;setattr(self,attribute,value)
            with self.subTest(attribute=attribute),self.assertRaises(ValueError):self.run_capture()
        self.assertFalse(list(self.root.glob('autozzz/trace-captures/*.png')))
    def test_native_developer_capture_retains_inherited_lease_scope(self):
        owner=self
        class LeaseScope:
            entered=False;exited=False
            def __enter__(self):self.entered=True;return self
            def __exit__(self,*args):self.exited=True
        lease=LeaseScope();self.run_capture(lock=lease)
        self.assertTrue(lease.entered and lease.exited);self.assertEqual(self.captured,1)


if __name__=='__main__':unittest.main()
