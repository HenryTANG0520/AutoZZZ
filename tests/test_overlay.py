"""Overlay layout and configuration checks without desktop input."""
from collections import deque
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from autozzz.config import Settings
from autozzz.onedragon_log import decode_worker_log, log_event, overlay_state_event
from autozzz.overlay import GameLogOverlay, overlay_bounds


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

    def test_explicit_rebind_to_same_window_discards_old_logs_and_linger(self):
        target = None
        overlay = object.__new__(GameLogOverlay)
        overlay.closed = False
        overlay.status = ""
        overlay.lines = deque(maxlen=40)
        overlay.game_handle = lambda: target
        overlay._hide = Mock()

        overlay.begin("正在连接")
        overlay.append("初次绑定前的日志")
        target = (101, 201)
        self.assertEqual(overlay.sync_target(), target)
        self.assertIn("初次绑定前的日志", overlay.lines[-1].text)
        overlay.finish("已完成")

        overlay._hide.reset_mock()
        target = None
        self.assertIsNone(overlay.sync_target())
        overlay._hide.assert_called_once_with()
        overlay.append("解绑期间的日志")
        self.assertFalse(overlay.lines)
        self.assertEqual(overlay.linger_until, 0)

        target = (101, 201)
        self.assertEqual(overlay.sync_target(), target)
        self.assertFalse(overlay.lines)
        self.assertEqual(overlay.linger_until, 0)
        self.assertEqual(overlay.status, "等待当前游戏状态")
        overlay.append("重新绑定后的日志")
        self.assertIn("重新绑定后的日志", overlay.lines[-1].text)

    def test_legacy_stderr_uses_connection_binding_only_with_legacy_window_state(self):
        legacy_binding = "legacy:" + "a" * 32
        legacy_state = {"window_handle": 101, "window_identity": {"window_handle": 101, "process_id": 201}}
        legacy_log = log_event(decode_worker_log("旧版日志", legacy_binding))
        self.assertEqual(legacy_log["overlay_log_binding"], legacy_binding)
        self.assertEqual(overlay_state_event(legacy_state, legacy_binding)["legacy_log_binding"], legacy_binding)
        for modern_field in ("window_process_created", "overlay_log_binding"):
            state = {**legacy_state, modern_field: None}
            self.assertNotIn("legacy_log_binding", overlay_state_event(state, legacy_binding))
        self.assertNotIn("legacy_log_binding", overlay_state_event({"window_handle": None}, legacy_binding))

    def test_current_worker_stderr_does_not_fall_back_to_legacy_connection(self):
        legacy_binding = "legacy:" + "a" * 32
        modern_binding = "b" * 32
        marked = decode_worker_log(f"\x1eAutoZZZ-overlay:{modern_binding}:当前日志", legacy_binding)
        unbound = decode_worker_log("\x1eAutoZZZ-overlay:-:启动日志", legacy_binding)
        self.assertEqual(log_event(marked), dict(type="log", message="当前日志", overlay_log_binding=modern_binding))
        self.assertEqual(log_event(unbound), dict(type="log", message="启动日志", overlay_log_binding=None))
