from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np

from autozzz.onedragon_pip import PipService


class PipTests(unittest.TestCase):
    def setUp(self):
        self.service=PipService(Path('fixture'))
        self.foreground=30;self.iconic=False;self.valid=True;self.events=[]
        self.image=np.tile(np.arange(32,dtype=np.uint8),(18,1));self.image=np.stack([self.image]*3,axis=2)
        self.window=NS(bound=NS(hwnd=10),process_id=20,identity_valid=lambda:self.valid,
                       clear_binding=lambda:self.events.append('clear'))
        self.worker=NS(pause=lambda:self.events.append('pause'),resume=lambda:self.events.append('resume'),stop=lambda:self.events.append('stop'))
        self.capture=NS(get_screenshot=lambda **_:self.image,cleanup=lambda:self.events.append('cleanup'))
        self.service._bind=self.bind
        for name,callback in (('GetForegroundWindow',lambda:self.foreground),('IsIconic',lambda _:self.iconic),
                              ('SetForegroundWindow',lambda hwnd:self.events.append(('activate',hwnd))),('ShowWindow',lambda *args:self.events.append(('show',args)))):
            mock=patch('autozzz.windows.user32.'+name,side_effect=callback);mock.start();self.addCleanup(mock.stop)
        self.service._encode=lambda image:dict(width=32,height=18,format='jpeg',data='fixture')

    def bind(self):
        self.service.window=self.window;self.service.capture=self.capture;self.service.worker=self.worker
        self.service._clear_frame()

    def start(self):return self.service.start('cn',r'E:\miHoYo Launcher\games\ZenlessZoneZero Game\ZenlessZoneZero.exe')

    def frame(self):
        image=self.service._capture()
        self.assertIsNotNone(image);self.service._on_frame(image)

    def test_background_frame_uses_source_callback_without_activating_game(self):
        self.assertEqual(self.start()['state'],'loading');self.frame()
        state=self.service.status();self.assertTrue(state['visible']);self.assertIn('image',state)
        self.assertNotIn('image',self.service.status(state['sequence']))
        self.assertFalse(any(isinstance(event,tuple) for event in self.events))

    def test_foreground_hides_and_next_background_resumes(self):
        self.start();self.frame();self.foreground=10
        self.assertEqual(self.service.status()['state'],'foreground');self.assertIsNone(self.service.frame)
        self.foreground=30;self.assertEqual(self.service.status()['state'],'loading');self.frame()
        self.assertTrue(self.service.status()['visible'])

    def test_right_close_dismisses_until_game_is_foreground_again(self):
        self.start();self.frame();self.service.dismiss()
        self.assertEqual(self.service.status()['state'],'dismissed')
        self.foreground=10;self.service.status();self.foreground=30;self.service.status();self.frame()
        self.assertTrue(self.service.status()['visible'])

    def test_minimize_waits_without_restoring_game(self):
        self.start();self.frame();self.iconic=True
        self.assertEqual(self.service.status()['state'],'minimized');self.assertIsNone(self.service._capture())
        self.assertFalse(any(isinstance(event,tuple) for event in self.events))

    def test_closed_game_stops_original_capture_before_rebinding(self):
        self.start();self.valid=False
        def wait():raise RuntimeError('fixture no window')
        self.service._bind=wait;self.service.next_bind=0
        state=self.service.status();self.assertEqual(state['state'],'waiting')
        self.assertLess(self.events.index('stop'),self.events.index('cleanup'));self.assertLess(self.events.index('cleanup'),self.events.index('clear'))

    def test_old_frame_is_discarded_after_visibility_epoch_changes(self):
        self.start();image=self.service._capture();self.service.dismiss();self.service._on_frame(image)
        self.assertIsNone(self.service.frame)

    def test_black_or_failed_capture_clears_previous_image(self):
        self.start();self.frame();self.image=np.zeros_like(self.image)
        self.assertIsNone(self.service._capture());self.assertIsNone(self.service.frame)
        self.capture.get_screenshot=lambda **_:(_ for _ in ()).throw(RuntimeError('failed fixture capture'))
        self.assertIsNone(self.service._capture());self.assertIn('failed fixture capture',self.service.error)

    def test_encoding_new_frame_cannot_clear_current_response(self):
        self.start();self.frame()
        def encode(image):self.service._on_frame(image);return dict(data='old response')
        self.service._encode=encode
        self.assertEqual(self.service.status()['image']['data'],'old response')
        self.assertIsNone(self.service.encoded)

    def test_explicit_click_uses_bound_identity_once(self):
        self.start();epoch=self.service.epoch
        self.service.activate(epoch);self.assertEqual(self.events[-1],('activate',10))
        self.valid=False
        with self.assertRaises(ValueError):self.service.activate(epoch)
        self.assertEqual(self.events.count(('activate',10)),1)

    def test_stop_prevents_rebinding_and_old_activation(self):
        self.start();epoch=self.service.epoch;self.service.stop()
        self.assertEqual(self.service.status()['state'],'off')
        with self.assertRaises(ValueError):self.service.activate(epoch)
        self.assertIsNone(self.service._capture())

    def test_preferences_validate_ranges_and_types(self):
        for values in (dict(width=319),dict(width=True),dict(x=float('inf')),dict(enabled='true'),dict(other=1)):
            with self.subTest(values=values),self.assertRaises(ValueError):self.service._validate(values)

    def test_presentation_preferences_do_not_invalidate_run_or_rotation_snapshot(self):
        from autozzz.onedragon_execution import OneDragonExecution
        from autozzz.rotation_snapshot import snapshot,tracked
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'config').mkdir();game=root/'config/game.yml';game.write_text('game')
            pip=root/'config/pip.yml';pip.write_text('width: 480')
            before=snapshot(root);digest=OneDragonExecution.configuration_digest(NS(data=root))
            pip.write_text('width: 700')
            self.assertEqual(snapshot(root),before);self.assertEqual(OneDragonExecution.configuration_digest(NS(data=root)),digest)
            self.assertFalse(tracked('config/pip.yml'));self.assertTrue(tracked('config/01/pip.yml'))
            game.write_text('changed');self.assertNotEqual(snapshot(root),before)

    def test_large_noisy_frame_stays_within_json_protocol_limit(self):
        import base64,json
        from autozzz.onedragon_runtime import MAX_MESSAGE_BYTES
        image=np.random.default_rng(42).integers(0,256,(2160,3840,3),dtype=np.uint8)
        result=PipService._encode(image)
        self.assertLessEqual(result['width'],1920);self.assertLessEqual(result['height'],1080)
        self.assertLessEqual(len(base64.b64decode(result['data'])),1_250_000)
        self.assertLess(len(json.dumps(result).encode()),MAX_MESSAGE_BYTES)


if __name__=='__main__':unittest.main()
