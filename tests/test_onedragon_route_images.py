import base64
import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from autozzz.onedragon_route_recorder import decode_client_image, encode_client_image


class RouteImageTests(unittest.TestCase):
    def test_client_image_roundtrip_uses_upstream_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"client.png"
            Image.new("RGB",(2560,1440),(91,115,133)).save(path)
            result=decode_client_image(encode_client_image(path))
            self.assertEqual(result.shape,(1080,1920,3))
            self.assertLess(abs(int(result[0,0,0])-91),3)

    def test_desktop_or_partial_capture_is_not_stretched_to_client(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"desktop.png"
            Image.new("RGB",(1920,1200)).save(path)
            with self.assertRaisesRegex(ValueError,"16:9"):encode_client_image(path)

    def test_protocol_checks_header_dimensions_and_encoding(self):
        data=io.BytesIO();Image.new("RGB",(16,9)).save(data,format="PNG")
        for value in ("!bad!",base64.b64encode(data.getvalue()).decode(),"a"*2_000_000):
            with self.subTest(value=value[:30]),self.assertRaises(ValueError):decode_client_image(value)
