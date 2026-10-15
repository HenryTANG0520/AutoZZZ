"""Overlay layout and configuration checks without desktop input."""
import json
from pathlib import Path
import tempfile
import unittest

from autozzz.config import Settings
from autozzz.overlay import overlay_bounds


class OverlaySettingsTests(unittest.TestCase):
    def test_overlay_follows_game_client_including_secondary_monitor(self):
        self.assertEqual(overlay_bounds(100, 200, 1920, 1080), (120, 988, 576, 227))
        self.assertEqual(overlay_bounds(-1920, 0, 1920, 1080), (-1900, 788, 576, 227))
        self.assertIsNone(overlay_bounds(0, 0, 800, 450))

    def test_legacy_configuration_defaults_to_enabled_and_can_disable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{}", encoding="utf-8")
            self.assertTrue(Settings.load(path).overlay_logs)
            Settings(overlay_logs=False).save(path)
            self.assertFalse(Settings.load(path).overlay_logs)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["overlay_logs"] = "false"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overlay_logs"):
                Settings.load(path)
