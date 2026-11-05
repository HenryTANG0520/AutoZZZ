from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
from autozzz.appearance_preferences import AppearancePreferences,DEFAULTS,contrast,effective_appearance,effective_preferences,validate
from autozzz.appearance_media import BackgroundPlayer,download_official,import_media,managed_path,official_asset,validate_url


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.base=Path(self.folder.name);self.store=AppearancePreferences(self.base/'appearance.json')

    def test_preferences_atomic_revision_and_original_protection(self):
        first=self.store.load();other=AppearancePreferences(self.store.path);other.load()
        first['accent']='#aF42d9';self.store.save(first)
        self.assertEqual(self.store.load()['accent'],'#af42d9')
        raw=self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'其他窗口'):other.save(deepcopy(DEFAULTS))
        self.assertEqual(raw,self.store.path.read_bytes())
        self.store.path.with_suffix('.json.lock').write_text('other')
        with self.assertRaisesRegex(ValueError,'正在写入'):self.store.save(first)
        self.assertEqual('other',self.store.path.with_suffix('.json.lock').read_text())

    def test_bad_config_read_only_fallback_and_chinese_default(self):
        folder=self.base/'AutoZZZ';folder.mkdir();file=folder/'appearance.json';file.write_text('{broken')
        with patch.dict(os.environ,LOCALAPPDATA=str(self.base)):
            self.assertEqual(effective_preferences(),DEFAULTS)
        self.assertEqual(file.read_text(),'{broken');self.assertEqual(DEFAULTS['language'],'zh')

    def test_accent_readable_on_both_card_surfaces_and_button(self):
        for mode,surfaces in (('dark',('#343434','#161616')),('light',('#dededb','#f9f9f6'))):
            for accent in ('#000000','#ffffff','#f4df00','#0066bb','#cc0033','#777777'):
                result=effective_appearance(DEFAULTS|dict(theme=mode,accent=accent))
                self.assertEqual(result['requested_accent'],accent)
                self.assertTrue(all(contrast(result['accent'],bg)>=4.5 for bg in surfaces),(mode,accent,result))
                self.assertGreaterEqual(contrast(result['accent'],result['accent_text']),4.5)
        self.assertEqual(effective_appearance(DEFAULTS)['accent'],'#f4df00')
        self.assertEqual(effective_appearance(DEFAULTS|dict(theme='system'),system_dark=False)['mode'],'light')

    def test_asset_schema_refuses_path_escape_or_fake_hash(self):
        for asset in (dict(file='../test.png',kind='image',sha256='a'*64),dict(file='a'*64+'.png',kind='image',sha256='b'*64)):
            with self.assertRaises(ValueError):validate(DEFAULTS|dict(assets={'custom':asset}))

    def test_single_image_copy_hash_and_original_retained(self):
        source=self.base/'image.png';Image.new('RGB',(16,10),'red').save(source)
        self.store.load();asset=import_media(source,self.store)
        self.assertEqual(len(list(self.store.asset_dir.iterdir())),1)
        self.assertEqual(source.read_bytes(),managed_path(self.store,asset).read_bytes())
        self.assertFalse(self.store.path.exists())
        managed_path(self.store,asset).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'校验'):managed_path(self.store,asset)
        self.assertTrue(source.exists())

    def test_cancelled_import_keeps_existing_asset_and_no_temp(self):
        source=self.base/'image.png';Image.new('RGB',(16,10),'red').save(source)
        old=import_media(source,self.store);before=set(self.store.asset_dir.iterdir())
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError):import_media(source,self.store,cancel)
        self.assertEqual(before,set(self.store.asset_dir.iterdir()));self.assertTrue(managed_path(self.store,old).exists())

    def test_official_metadata_uses_game_and_video_type(self):
        url='https://autopatchcn.mihoyo.com/test.webp';video='https://autopatchcn.mihoyo.com/test.webm'
        data={'data':{'games':[{'biz':'nap_cn','display':{'background':{'url':url}}}],
            'game_info_list':[{'game':{'biz':'nap_cn'},'backgrounds':[{'background':{'url':url}}, {'type':'BACKGROUND_TYPE_VIDEO','video':{'url':video}}]}]}}
        self.assertEqual(official_asset(data,'version_poster'),url);self.assertEqual(official_asset(data,'static'),url)
        self.assertEqual(official_asset(data,'dynamic'),video)
        for url in ('http://mihoyo.com/a','https://mihoyo.com.evil.test/a','https://user:pass@mihoyo.com/a','file:///tmp/a','https://mihoyo.com:8443/a'):
            with self.assertRaises(ValueError):validate_url(url)

    def test_download_bounded_before_asset_and_configuration_unchanged(self):
        class Response:
            status_code=200;headers={'Content-Length':str(3*1024*1024)}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def raise_for_status(self):pass
            def iter_content(self,*args):raise AssertionError('must reject length before read')
        class Session:
            def get(self,*args,**kwargs):return Response()
        self.store.load();self.store.save(DEFAULTS);raw=self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'大小'):download_official('static',self.store,session=Session())
        self.assertEqual(raw,self.store.path.read_bytes())

    def test_real_video_multiple_frames_pause_resume_replacement_and_close(self):
        import cv2
        import numpy as np
        path=self.base/'clip.avi';writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10,(64,48))
        self.assertTrue(writer.isOpened())
        for i in range(12):writer.write(np.full((48,64,3),(i*18,20,250-i*18),dtype=np.uint8))
        writer.release();asset=import_media(path,self.store)
        player=BackgroundPlayer();self.addCleanup(lambda:(player.close(),player.worker.join(3)))
        player.load(managed_path(self.store,asset),'video');player.visible(True)
        def wait(predicate):
            deadline=time.monotonic()+3
            while not predicate():
                self.assertLess(time.monotonic(),deadline);time.sleep(.01)
        wait(lambda:player.frame_number>=2);first=player.frame().getpixel((2,2));number=player.frame_number
        wait(lambda:player.frame_number>=number+2)
        self.assertNotEqual(first,player.frame().getpixel((2,2)))
        player.visible(False);time.sleep(.15);number=player.frame_number;time.sleep(.3);self.assertEqual(number,player.frame_number)
        player.visible(True);wait(lambda:player.frame_number>number)
        image=self.base/'other.png';Image.new('RGB',(10,10),'green').save(image)
        player.load(image,'image');wait(lambda:player.frame() is not None and player.frame().size==(10,10))
        self.assertEqual(player.frame().getpixel((1,1)),(0,128,0))
        player.close();player.worker.join(3);self.assertFalse(player.worker.is_alive())
        path.unlink();self.assertFalse(path.exists())

    def test_theme_mapping_and_transparency_default_dark_retained(self):
        from autozzz import theme
        try:
            theme.configure_appearance(DEFAULTS)
            self.assertEqual(theme.BG,'#0c0c0c')
            self.assertEqual(theme.Painter(10,10,1,background=None).finish().mode,'RGBA')
            theme.configure_appearance(DEFAULTS|dict(theme='light'))
            self.assertNotEqual(theme.color('#171717'),'#171717')
            self.assertGreaterEqual(contrast(theme.TEXT,theme.FIELD),4.5)
        finally:theme.configure_appearance(DEFAULTS)


if __name__=='__main__':unittest.main()
