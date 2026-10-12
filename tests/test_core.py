"""Offline safety regression tests; all Win32 input is mocked."""
from __future__ import annotations

import ctypes
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autozzz.app import Context, Runner, launch_game
from autozzz.config import GAME_PATHS, Settings
from autozzz.models import AutomationError, FocusLost, RunReport, Scene, Stopped, Token
from autozzz.policy import coffee_is_free, guard_click, guard_scene
from autozzz import windows
from autozzz.workflows import DailyWorkflow, TASK_LABELS
from autozzz.vision import Vision

TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "runs" / "offline-tests"


def token(text: str, x: float = 0.5, y: float = 0.5) -> Token:
    return Token(text, 0.95, (x - 0.02, y - 0.01, x + 0.02, y + 0.01))


def scene(*labels: str) -> Scene:
    return Scene([token(label) for label in labels], geometry=windows.Geometry(0, 0, 1920, 1080),
                 captured_at=time.monotonic())


def context_for(frame: Scene) -> tuple[Context, Runner, Mock]:
    runner = Runner(Settings())
    window = Mock()
    window.capture.return_value = (object(), frame.geometry)
    vision = Mock()
    vision.read.return_value = frame
    context = Context(runner, window, vision)
    context.last_scene = frame
    context.sleep = Mock()
    return context, runner, window


class ConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)

    def test_default_configuration_has_only_non_combat_tasks(self):
        settings = Settings()
        settings.validate()
        self.assertEqual(settings.server, "cn")
        self.assertEqual(settings.game_path, GAME_PATHS["cn"])
        self.assertTrue(all(getattr(settings, name) for name in ("scratch", "coffee", "video", "daily")))
        self.assertFalse(any(name in Settings.__dataclass_fields__ for name in
                             ("battle", "allow_stamina_spend", "stamina_limit", "rounds")))

    def test_server_selection_changes_path_together(self):
        settings = Settings()
        for server in ("global", "cn"):
            settings.select_server(server)
            settings.validate()
            self.assertEqual(settings.server, server)
            self.assertEqual(settings.game_path, GAME_PATHS[server])

    def test_official_client_path_cannot_be_reported_as_other_server(self):
        for server, other in (("cn", "global"), ("global", "cn")):
            with self.subTest(server=server):
                with self.assertRaises(ValueError):
                    Settings(server=server, game_path=GAME_PATHS[other]).validate()

    def test_normalized_official_path_cannot_bypass_server_match(self):
        other_path = Path(GAME_PATHS["global"])
        spelling = str(other_path.parent / ".." / other_path.parent.name / other_path.name)
        self.assertEqual(Path(spelling).resolve(), other_path.resolve())
        with self.assertRaises(ValueError):
            Settings(server="cn", game_path=spelling).validate()

    def test_wrong_executable_is_rejected(self):
        for path in (r"D:\Games\StarRail.exe", r"D:\Games\ZenlessZoneZero.exe.bak", "", None):
            with self.subTest(path=path), self.assertRaises(ValueError):
                Settings(game_path=path).validate()

    def test_executable_name_is_case_insensitive(self):
        Settings(game_path=GAME_PATHS["cn"].upper()).validate()

    def test_unknown_server_is_rejected(self):
        for server in ("us", "", None):
            with self.subTest(server=server), self.assertRaises(ValueError):
                Settings(server=server).validate()

    def test_task_and_launch_flags_require_real_booleans(self):
        for field in ("scratch", "coffee", "video", "daily", "auto_launch"):
            for value in (1, "false", None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    Settings(**{field: value}).validate()

    def test_nonfinite_numeric_values_are_rejected(self):
        for field in ("action_delay", "transition_timeout", "ocr_confidence"):
            for value in (True, float("inf"), float("nan"), "1"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    Settings(**{field: value}).validate()

    def test_no_tasks_is_rejected(self):
        with self.assertRaises(ValueError):
            Settings(scratch=False, coffee=False, video=False, daily=False).validate()

    def test_unknown_legacy_battle_fields_are_rejected_on_load(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            path = Path(directory) / "config.json"
            for payload in ({"battle": True}, {"allow_stamina_spend": True}, {"stamina_limit": 240}, []):
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    Settings.load(path)

    def test_save_load_preserves_settings_and_has_no_temp_left(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            path = Path(directory) / "nested" / "config.json"
            settings = Settings()
            settings.select_server("global")
            settings.coffee = False
            settings.save(path)
            self.assertEqual(Settings.load(path), settings)
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_invalid_save_does_not_overwrite_valid_configuration(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            path = Path(directory) / "config.json"
            Settings().save(path)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                Settings(action_delay=float("nan")).save(path)
            self.assertEqual(path.read_bytes(), before)


class PolicyTests(unittest.TestCase):
    def test_paid_stamina_and_combat_labels_are_never_clicked(self):
        for label in ("购买电量", "补充电量", "使用以太电池", "兑换菲林", "购买菲林",
                      "开始战斗", "开始挑战", "出战", "作战开始", "调频", "十连抽取",
                      "角色升级", "音擎升级", "驱动盘强化"):
            frame = scene(label)
            with self.subTest(label=label), self.assertRaises(AutomationError):
                guard_click(frame, frame.tokens[0])

    def test_stamina_confirmation_popup_blocks_buttons_and_coordinates(self):
        for label in ("是否消耗菲林", "电量不足", "使用以太电池", "确认支付"):
            frame = scene(label, "确认")
            for target in (frame.tokens[1], (0.5, 0.5)):
                with self.subTest(label=label, target=target), self.assertRaises(AutomationError):
                    guard_click(frame, target)

    def test_coordinate_click_cannot_bypass_visible_battle_button(self):
        frame = scene("开始战斗")
        with self.assertRaises(AutomationError):
            guard_click(frame, frame.tokens[0].center)

    def test_free_coffee_confirmation_is_allowed(self):
        frame = scene()
        frame.tokens = [token("咖啡", 0.75, 0.3), token("今日免费", 0.75, 0.75), token("确认", 0.8, 0.9)]
        self.assertTrue(coffee_is_free(frame))
        guard_click(frame, frame.tokens[-1])

    def test_coffee_free_label_does_not_authorize_premium_currency(self):
        for label in ("菲林", "付费", "充值"):
            with self.subTest(label=label):
                self.assertFalse(coffee_is_free(scene("咖啡", "今日免费", label)))

    def test_not_free_text_is_not_a_free_coffee_proof(self):
        frame = scene()
        frame.tokens = [token("Coffee", 0.75, 0.3), token("Not free", 0.75, 0.75)]
        self.assertFalse(coffee_is_free(frame))

    def test_free_advertisement_outside_price_region_does_not_authorize_coffee(self):
        frame = scene()
        frame.tokens = [token("今日免费", 0.2, 0.2), token("1500丁尼", 0.75, 0.75)]
        self.assertFalse(coffee_is_free(frame))

    def test_calibrated_tin_master_dialogue_authorizes_only_daily_special(self):
        frame = calibrated_coffee_dialogue()
        self.assertTrue(coffee_is_free(frame))
        self.assertFalse(frame.has("今日免费"), "This calibrated dialogue is itself the zero-cost proof")

    def test_tin_master_daily_dialogue_requires_npc_question_and_special_option(self):
        for omitted in ("汀曼大师", "想喝点什么？", "来杯汀曼特调！"):
            frame = calibrated_coffee_dialogue()
            frame.tokens = [t for t in frame.tokens if t.text != omitted]
            with self.subTest(omitted=omitted):
                self.assertFalse(coffee_is_free(frame))

    def test_other_drink_alone_is_not_free_daily_special(self):
        frame = calibrated_coffee_dialogue()
        frame.tokens = [t for t in frame.tokens if t.text != "来杯汀曼特调！"]
        self.assertTrue(frame.has("来杯鲜萃红茶！"))
        self.assertFalse(coffee_is_free(frame))

    def test_duplicate_special_options_do_not_authorize_dialogue_redemption(self):
        frame = calibrated_coffee_dialogue()
        frame.tokens.append(token("来杯汀曼特调！", .86, .67))
        self.assertFalse(coffee_is_free(frame))

    def test_paid_and_premium_markers_override_calibrated_daily_dialogue(self):
        for label in ("菲林", "付费", "充值", "非免费", "不免费", "Not free"):
            frame = calibrated_coffee_dialogue()
            frame.tokens.append(token(label, .8, .72))
            with self.subTest(label=label):
                self.assertFalse(coffee_is_free(frame))

    def test_daily_dialogue_anchors_in_wrong_regions_do_not_authorize_redemption(self):
        for misplaced in ("汀曼大师", "想喝点什么？", "来杯汀曼特调！"):
            frame = calibrated_coffee_dialogue()
            frame.tokens = [token(t.text, .2, .2) if t.text == misplaced else t for t in frame.tokens]
            with self.subTest(misplaced=misplaced):
                self.assertFalse(coffee_is_free(frame))

    def test_option_with_added_price_is_not_the_exact_daily_special(self):
        frame = calibrated_coffee_dialogue()
        frame.tokens = [Token("来杯汀曼特调（1500丁尼）", t.score, t.box)
                        if t.text == "来杯汀曼特调！" else t for t in frame.tokens]
        self.assertFalse(coffee_is_free(frame))

    def test_rewards_remain_usable_on_known_safe_page(self):
        frame = scene("活跃度", "领取")
        guard_scene(frame)
        guard_click(frame, frame.tokens[-1])


class ScreenshotSafetyTests(unittest.TestCase):
    def test_expired_scene_does_not_reach_native_input(self):
        frame = scene("领取")
        frame.captured_at -= 9
        context, _, window = context_for(frame)
        for action in (lambda: context.click(frame, frame.tokens[0]), lambda: context.key("esc", frame),
                       lambda: context.move(frame, (0.5, 0.5))):
            with self.subTest(action=action), self.assertRaises(AutomationError):
                action()
        window.click.assert_not_called()
        window.key.assert_not_called()
        window.move.assert_not_called()

    def test_other_scene_identity_is_not_accepted(self):
        frame = scene("领取")
        context, _, window = context_for(frame)
        other = scene("领取")
        with self.assertRaises(AutomationError):
            context.click(other, other.tokens[0])
        window.click.assert_not_called()

    def test_foreign_token_does_not_supply_coordinates_for_current_scene(self):
        frame = scene("领取")
        context, _, window = context_for(frame)
        old_target = token("领取", 0.8, 0.8)
        with self.assertRaises(AutomationError):
            context.click(frame, old_target)
        window.click.assert_not_called()

    def test_pause_resume_invalidates_pre_pause_scene(self):
        frame = scene("领取")
        context, runner, window = context_for(frame)
        runner.toggle_pause()
        runner.toggle_pause()
        with self.assertRaises(AutomationError):
            context.click(frame, frame.tokens[0])
        window.click.assert_not_called()

    def test_pause_during_ocr_does_not_revalidate_old_pixels(self):
        frame = scene("领取")
        context, runner, _ = context_for(frame)
        def read(*_):
            runner.toggle_pause()
            runner.toggle_pause()
            return frame
        context.vision.read.side_effect = read
        with self.assertRaises(AutomationError):
            context.snapshot()

    def test_slow_capture_and_ocr_age_starts_before_capture(self):
        frame = scene("领取")
        context, _, window = context_for(frame)
        with patch("autozzz.app.time.monotonic", side_effect=(100.0, 110.0)):
            captured = context.snapshot()
            with self.assertRaises(AutomationError):
                context.click(captured, captured.tokens[0])
        window.click.assert_not_called()

    def test_click_invalidates_scene_for_second_click(self):
        frame = scene("领取")
        context, _, window = context_for(frame)
        context.click(frame, frame.tokens[0])
        with self.assertRaises(AutomationError):
            context.click(frame, frame.tokens[0])
        self.assertEqual(window.click.call_count, 1)

    def test_attack_and_overlong_movement_never_reach_input(self):
        frame = scene("六分街")
        context, _, window = context_for(frame)
        for name, duration in (("space", 0.08), ("j", 0.08), ("w", 5.0)):
            with self.subTest(name=name, duration=duration), self.assertRaises(AutomationError):
                context.key(name, frame, duration)
        window.key.assert_not_called()

    def test_calibrated_f3_and_f4_are_allowed_and_invalidate_old_scene(self):
        for name in ("f3", "f4"):
            frame = scene("大世界")
            context, _, window = context_for(frame)
            with self.subTest(name=name):
                context.key(name, frame, duration=.08)
                window.key.assert_called_once_with(name, .08, context.input_checkpoint)
                self.assertIsNone(context.last_scene)
                with self.assertRaises(AutomationError):
                    context.key(name, frame, duration=.08)
                self.assertEqual(window.key.call_count, 1)

    def test_uncalibrated_function_key_still_never_reaches_input(self):
        frame = scene("大世界")
        context, _, window = context_for(frame)
        with self.assertRaises(AutomationError):
            context.key("f5", frame, duration=.08)
        window.key.assert_not_called()

    def test_drag_requires_scratch_page(self):
        frame = scene("录像店")
        context, _, window = context_for(frame)
        with self.assertRaises(AutomationError):
            context.drag(frame, (0.4, 0.4), (0.6, 0.6))
        window.drag.assert_not_called()

    def test_drag_rejects_expired_scratch_page(self):
        frame = scene("每日可刮取一次")
        frame.captured_at -= 9
        context, _, window = context_for(frame)
        with self.assertRaises(AutomationError):
            context.drag(frame, (0.4, 0.4), (0.6, 0.6))
        window.drag.assert_not_called()

    def test_stop_checkpoint_is_responsive_without_any_input(self):
        runner = Runner(Settings())
        runner.stop()
        with self.assertRaises(Stopped):
            runner.checkpoint()


class WindowSelectionTests(unittest.TestCase):
    def test_same_named_clients_are_selected_by_full_path(self):
        paths = {11: GAME_PATHS["cn"], 22: GAME_PATHS["global"], 33: r"D:\Other\StarRail.exe"}
        api = Mock()
        api.IsWindowVisible.return_value = True
        api.EnumWindows.side_effect = lambda callback, _: [callback(hwnd, 0) for hwnd in paths]
        geometry = windows.Geometry(0, 0, 1920, 1080)
        with patch.object(windows, "user32", api), patch.object(windows, "process_path", side_effect=paths.get), \
                patch.object(windows, "geometry", return_value=geometry):
            self.assertEqual(windows.find_game(GAME_PATHS["cn"]).hwnd, 11)
            self.assertEqual(windows.find_game(GAME_PATHS["global"]).hwnd, 22)

    def test_missing_selected_client_does_not_use_other_server(self):
        api = Mock()
        api.IsWindowVisible.return_value = True
        api.EnumWindows.side_effect = lambda callback, _: callback(22, 0)
        with patch.object(windows, "user32", api), \
                patch.object(windows, "process_path", return_value=GAME_PATHS["global"]), \
                patch.object(windows, "geometry", return_value=windows.Geometry(0, 0, 1920, 1080)):
            with self.assertRaises(AutomationError):
                windows.find_game(GAME_PATHS["cn"])

    def test_window_guard_rejects_focus_loss_and_baseline_change(self):
        api = Mock()
        api.IsWindow.return_value = True
        api.IsIconic.return_value = False
        api.GetForegroundWindow.return_value = 1
        api.GetSystemMetrics.side_effect = lambda index: {76: 0, 77: 0, 78: 3840, 79: 2160}[index]
        game = windows.GameWindow.__new__(windows.GameWindow)
        game.hwnd, game.baseline = 1, windows.Geometry(0, 0, 1920, 1080)
        with patch.object(windows, "user32", api), patch.object(windows, "geometry", return_value=game.baseline) as read:
            self.assertEqual(game.guard(), game.baseline)
            api.GetForegroundWindow.return_value = 2
            with self.assertRaises(FocusLost):
                game.guard()
            api.GetForegroundWindow.return_value = 1
            read.return_value = windows.Geometry(5, 0, 1920, 1080)
            with self.assertRaises(AutomationError):
                game.guard()


class ClientLaunchSafetyTests(unittest.TestCase):
    def test_other_official_client_prevents_new_client_launch(self):
        for selected, other in (("cn", "global"), ("global", "cn")):
            settings = Settings()
            settings.select_server(selected)
            def found(path):
                if path == GAME_PATHS[other]:
                    return Mock()
                raise AutomationError("Selected client is absent")
            with self.subTest(selected=selected), patch("autozzz.app.find_game", side_effect=found) as find, \
                    patch("autozzz.app.launch") as launch:
                with self.assertRaisesRegex(AutomationError, "multi game detected"):
                    launch_game(settings)
                self.assertEqual([call.args[0] for call in find.call_args_list],
                                 [GAME_PATHS[selected], GAME_PATHS[other]])
                launch.assert_not_called()

    def test_absent_clients_allow_exact_selected_launch_and_normalized_path(self):
        for server in ("cn", "global"):
            for uppercase in (False, True):
                settings = Settings()
                settings.select_server(server)
                if uppercase:
                    settings.game_path = settings.game_path.upper()
                with self.subTest(server=server, uppercase=uppercase), \
                        patch("autozzz.app.find_game", side_effect=AutomationError("No client")) as find, \
                        patch("autozzz.app.launch", return_value="started") as launch:
                    self.assertEqual(launch_game(settings), "started")
                    self.assertEqual(find.call_count, 2, "The selected official path is not checked again as the other server")
                    launch.assert_called_once_with(settings.game_path)

    def test_running_selected_client_is_focused_without_process_creation(self):
        settings = Settings()
        settings.select_server("global")
        existing = Mock()
        with patch("autozzz.app.find_game", return_value=existing) as preflight, \
                patch.object(windows, "find_game", return_value=existing) as find, \
                patch.object(Path, "is_file", return_value=True), patch.object(windows.subprocess, "Popen") as spawn:
            launch_game(settings)
            preflight.assert_called_once_with(settings.game_path)
            find.assert_called_once_with(settings.game_path)
            existing.focus.assert_called_once_with()
            spawn.assert_not_called()

    def test_invalid_server_path_fails_before_window_query_or_launch(self):
        settings = Settings(server="cn", game_path=GAME_PATHS["global"])
        with patch("autozzz.app.find_game") as find, patch("autozzz.app.launch") as launch:
            with self.assertRaises(ValueError):
                launch_game(settings)
            find.assert_not_called()
            launch.assert_not_called()


class HotkeyTests(unittest.TestCase):
    def test_held_f9_only_pauses_once_while_game_has_focus(self):
        runner = Runner(Settings())
        runner._window = Mock(hwnd=11)
        runner._window.emergency_pressed.return_value = False
        runner._monitor_end = Mock()
        runner._monitor_end.wait.side_effect = (False, False, True)
        api = Mock()
        api.GetAsyncKeyState.return_value = 0x8000
        api.GetForegroundWindow.return_value = 11
        with patch.object(windows, "user32", api):
            runner._hotkeys()
        self.assertTrue(runner._pause.is_set())
        self.assertEqual(runner.input_epoch, 1)

    def test_f10_stops_when_other_window_has_focus(self):
        runner = Runner(Settings())
        runner._window = Mock(hwnd=11)
        runner._window.emergency_pressed.return_value = True
        runner._monitor_end = Mock()
        runner._monitor_end.wait.side_effect = (False, True)
        api = Mock()
        api.GetAsyncKeyState.return_value = 0
        api.GetForegroundWindow.return_value = 99
        with patch.object(windows, "user32", api):
            runner._hotkeys()
        self.assertTrue(runner._stop.is_set())


class ResetBoundaryTests(unittest.TestCase):
    def test_cn_before_reset_keeps_previous_day(self):
        runner = Runner(Settings())
        runner.report.reset_day = "2026-10-10"
        with patch("autozzz.app.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 11, 3, 59, tzinfo=timezone(timedelta(hours=8)))
            runner.checkpoint()
        self.assertFalse(runner._stop.is_set())

    def test_cn_at_reset_stops_before_next_action(self):
        runner = Runner(Settings())
        runner.report.reset_day = "2026-10-10"
        with patch("autozzz.app.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 11, 4, 0, tzinfo=timezone(timedelta(hours=8)))
            with self.assertRaises(Stopped):
                runner.checkpoint()
        self.assertTrue(runner._stop.is_set())

    def test_pause_across_reset_is_rechecked_before_resume(self):
        runner = Runner(Settings())
        runner.report.reset_day = "2026-10-10"
        runner._pause.set()
        stop = Mock()
        stop.is_set.return_value = False
        runner._stop = stop
        with patch("autozzz.app.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 11, 3, 59, tzinfo=timezone(timedelta(hours=8)))
            def resume(_):
                clock.now.return_value = datetime(2026, 10, 11, 4, 1, tzinfo=timezone(timedelta(hours=8)))
                runner._pause.clear()
                return False
            stop.wait.side_effect = resume
            with self.assertRaises(Stopped):
                runner.checkpoint()


class HeldInputTests(unittest.TestCase):
    def make_window(self):
        game = windows.GameWindow.__new__(windows.GameWindow)
        game.hwnd, game.baseline = 1, windows.Geometry(0, 0, 1920, 1080)
        game.guard = Mock(return_value=game.baseline)
        game.send = Mock()
        game.move = Mock()
        game.emergency_pressed = Mock(return_value=False)
        return game

    def test_key_is_released_when_stop_raises_during_hold(self):
        game = self.make_window()
        api = Mock()
        api.MapVirtualKeyW.return_value = 17
        with patch.object(windows, "user32", api):
            with self.assertRaises(Stopped):
                game.key("w", duration=0.2, checkpoint=Mock(side_effect=Stopped("stop")))
        event = ctypes.cast(api.SendInput.call_args.args[1], ctypes.POINTER(windows.INPUT))[0]
        self.assertEqual(event.type, 1)
        self.assertTrue(event.ki.dwFlags & 0x2)

    def test_unity_navigation_key_uses_virtual_key_and_matching_release(self):
        game = self.make_window()
        api = Mock()
        api.MapVirtualKeyW.return_value = 17
        with patch.object(windows, "user32", api):
            game.key("w", duration=0.001)
        pressed = game.send.call_args.args[0][0]
        released = ctypes.cast(api.SendInput.call_args.args[1], ctypes.POINTER(windows.INPUT))[0]
        self.assertEqual((pressed.type, pressed.ki.wVk), (1, 0x57))
        self.assertFalse(pressed.ki.dwFlags & 0x8, "Current ZZZ calibration requires virtual-key input")
        self.assertEqual(released.ki.wVk, pressed.ki.wVk)
        self.assertTrue(released.ki.dwFlags & 0x2)

    def test_f3_and_f4_use_exact_windows_vk_and_matching_release(self):
        for name, vk in (("f3", 0x72), ("f4", 0x73)):
            game = self.make_window()
            api = Mock()
            api.MapVirtualKeyW.return_value = 61
            with self.subTest(name=name), patch.object(windows, "user32", api):
                game.key(name, duration=.001)
                api.MapVirtualKeyW.assert_called_once_with(vk, 0)
                pressed = game.send.call_args.args[0][0]
                released = ctypes.cast(api.SendInput.call_args.args[1], ctypes.POINTER(windows.INPUT))[0]
                self.assertEqual((pressed.type, pressed.ki.wVk, pressed.ki.dwFlags), (1, vk, 0))
                self.assertEqual((released.type, released.ki.wVk, released.ki.dwFlags), (1, vk, 2))
                api.SendInput.assert_called_once()

    def test_f3_and_f4_are_released_even_when_stop_interrupts_hold(self):
        for name, vk in (("f3", 0x72), ("f4", 0x73)):
            game = self.make_window()
            api = Mock()
            api.MapVirtualKeyW.return_value = 61
            with self.subTest(name=name), patch.object(windows, "user32", api):
                with self.assertRaises(Stopped):
                    game.key(name, duration=.2, checkpoint=Mock(side_effect=Stopped("stop")))
                released = ctypes.cast(api.SendInput.call_args.args[1], ctypes.POINTER(windows.INPUT))[0]
                self.assertEqual((released.type, released.ki.wVk, released.ki.dwFlags), (1, vk, 2))

    def test_drag_is_released_when_stop_raises(self):
        game = self.make_window()
        api = Mock()
        with patch.object(windows, "user32", api):
            with self.assertRaises(Stopped):
                game.drag((0.4, 0.4), (0.6, 0.6), game.baseline, Mock(side_effect=Stopped("stop")))
        event = ctypes.cast(api.SendInput.call_args.args[1], ctypes.POINTER(windows.INPUT))[0]
        self.assertEqual(event.type, 0)
        self.assertTrue(event.mi.dwFlags & 0x4)

    def test_partial_click_input_releases_mouse_before_raising(self):
        game = self.make_window()
        game.send = types.MethodType(windows.GameWindow.send, game)
        api = Mock()
        api.GetSystemMetrics.side_effect = lambda index: {76: 0, 77: 0, 78: 3840, 79: 2160}[index]
        api.SendInput.side_effect = (1, 1)
        with patch.object(windows, "user32", api):
            with self.assertRaises(AutomationError):
                game.click(0.5, 0.5, game.baseline)
        self.assertEqual(api.SendInput.call_count, 2, "Partial move/down/up must issue a cleanup mouse-up")
        released = ctypes.cast(api.SendInput.call_args.args[1], ctypes.POINTER(windows.INPUT))[0]
        self.assertEqual(released.type, 0)
        self.assertTrue(released.mi.dwFlags & 0x4)

    def assert_pause_releases_input(self, drag: bool):
        game = self.make_window()
        frame = scene("每日可刮取一次" if drag else "六分街")
        context, runner, _ = context_for(frame)
        context.window = game
        released = threading.Event()
        held = threading.Event()
        errors = []
        api = Mock()
        api.MapVirtualKeyW.return_value = 17
        api.SendInput.side_effect = lambda *_: released.set() or 1
        def sent(*_):
            runner.toggle_pause()
            held.set()
        game.send.side_effect = sent
        def work():
            try:
                if drag:
                    context.drag(frame, (0.4, 0.4), (0.6, 0.6))
                else:
                    context.key("w", frame, duration=0.4)
            except AutomationError as error:
                errors.append(error)
        with patch.object(windows, "user32", api):
            thread = threading.Thread(target=work, daemon=True)
            thread.start()
            try:
                self.assertTrue(held.wait(1), "Fixture must reach mocked input-down")
                released_before_stop = released.wait(0.3)
            finally:
                runner.stop()
                thread.join(2)
            self.assertFalse(thread.is_alive(), "Stopped input worker must exit")
            self.assertTrue(released_before_stop, "F9 pause must release held input before waiting for resume")

    def test_pause_releases_held_movement_key_before_waiting(self):
        self.assert_pause_releases_input(drag=False)

    def test_pause_releases_scratch_mouse_before_waiting(self):
        self.assert_pause_releases_input(drag=True)


class ReportTests(unittest.TestCase):
    def test_report_serializes_server_reset_day_and_evidence(self):
        report = RunReport(server="global", game_path=GAME_PATHS["global"], reset_day="unverified-global-reset",
                           evidence=["scratch.png"], engagement=300)
        data = json.loads(json.dumps(report.to_dict()))
        self.assertEqual(data["server"], "global")
        self.assertEqual(data["game_path"], GAME_PATHS["global"])
        self.assertEqual(data["reset_day"], "unverified-global-reset")
        self.assertEqual(data["evidence"], ["scratch.png"])
        self.assertEqual(data["stamina_spent"], 0)


class WorkflowContext:
    """Observed frames and recorded actions, without any game/window API."""
    def __init__(self, frames):
        self.frames = list(frames)
        self.settings = Settings()
        self.report = RunReport()
        self.actions = []
        self.targets = []
        self.evidence_frames = []
        self.template = Mock(return_value=None)

    def snapshot(self):
        if len(self.frames) > 1:
            return self.frames.pop(0)
        return self.frames[0]

    def click(self, frame, target):
        guard_click(frame, target)
        self.targets.append(target)
        self.actions.append(("click", target.text if isinstance(target, Token) else target))

    def key(self, name, frame, **_):
        guard_scene(frame)
        self.actions.append(("key", name))

    def drag(self, frame, start, end):
        guard_scene(frame)
        self.actions.append(("drag", start, end))

    def move(self, frame, target):
        self.actions.append(("move", target))

    def sleep(self, _):
        pass

    def wait(self, predicate, **_):
        for _ in range(10):
            frame = self.snapshot()
            if predicate(frame):
                return frame
        raise AutomationError("Offline fixture did not observe the expected transition")

    def evidence(self, frame, name):
        self.evidence_frames.append((frame, name))
        self.report.evidence.append(name)


def handbook(engagement=None, *, claimed=False, task=None):
    frame = scene()
    frame.tokens = [token("训练", 0.4, 0.16), token("日常", 0.55, 0.16), token("每日任务", 0.3, 0.4)]
    if engagement is not None:
        frame.tokens.append(token(f"今日活跃度{engagement}/400", 0.35, 0.28))
    if claimed:
        frame.tokens.append(token("全部奖励已领取", 0.60, 0.28))
    if task:
        x = 0.64 if "刮" in task else (0.82 if "营业" in task else 0.47)
        frame.tokens.extend((token(task, x, 0.46), token("进度：1/1", x, 0.415)))
    return frame


def captured_daily_cards():
    """Sanitized OCR geometry from the 2026-10-11 19:54 CN calibration frame.

    Player identifiers and unrelated balances are deliberately omitted. The
    truncated/wrapped task titles retain the observed OCR text and rectangles.
    """
    values = (
        ("目标", (0.40104, 0.15093, 0.43333, 0.18519)),
        ("日常", (0.49948, 0.15000, 0.52969, 0.18148)),
        ("训练", (0.59531, 0.15093, 0.62865, 0.18519)),
        ("作战", (0.69323, 0.15093, 0.72604, 0.18519)),
        ("战术", (0.79010, 0.15000, 0.82396, 0.18519)),
        ("今日最大活跃度", (0.22448, 0.24537, 0.32031, 0.27315)),
        (" 400", (0.24635, 0.27963, 0.29844, 0.31204)),
        ("200", (0.51823, 0.29259, 0.54792, 0.32222)),
        ("300", (0.66146, 0.29352, 0.68958, 0.32037)),
        ("400", (0.80365, 0.29352, 0.83229, 0.32037)),
        ("进度：0/1", (0.43229, 0.40000, 0.50521, 0.43056)),
        ("进度：0/1", (0.60052, 0.40185, 0.67292, 0.42963)),
        ("进度：0/1", (0.76823, 0.40185, 0.84063, 0.42963)),
        ("品尝1次咖啡", (0.43958, 0.44537, 0.51406, 0.47407)),
        ("进行一次刮刮乐、占", (0.60729, 0.44537, 0.72344, 0.47315)),
        ("卜或抽取饼铺盲盒", (0.60938, 0.47500, 0.71042, 0.50000)),
        ("开启今日录像店经", (0.77448, 0.44537, 0.87760, 0.47315)),
        ("推荐任务", (0.20938, 0.47778, 0.26198, 0.50370)),
        ("·完成「区域巡防」0/3", (0.21302, 0.50741, 0.34635, 0.53241)),
        ("积分：1050/2100", (0.20938, 0.44537, 0.32344, 0.47315)),
        ("丽都周纪", (0.23906, 0.78981, 0.29167, 0.81759)),
        ("前往", (0.48750, 0.78889, 0.51771, 0.81852)),
        ("前往", (0.65469, 0.78889, 0.68490, 0.81852)),
        ("前往", (0.82240, 0.78889, 0.85260, 0.81944)),
    )
    frame = scene()
    frame.tokens = [Token(label, 0.99, box) for label, box in values]
    return frame


def captured_m_map():
    """Selected non-personal tokens from the 20:13 M-map calibration result."""
    values = (
        ("布亚斯特城区", (0.23073, 0.28981, 0.31927, 0.31944)),
        ("澄辉坪", (0.93542, 0.28889, 0.98333, 0.32037)),
        ("奥蒙德研究站", (0.56198, 0.80556, 0.65156, 0.83519)),
        ("录像店", (0.93594, 0.80463, 0.98385, 0.83704)),
        ("未来邦布研究室", (0.07656, 0.95556, 0.14844, 0.97778)),
        ("吼吼饼铺", (0.24688, 0.95463, 0.28958, 0.97963)),
        ("片刻闲", (0.40781, 0.95370, 0.44063, 0.97963)),
        ("录像店营业点", (0.54896, 0.95463, 0.61094, 0.97963)),
    )
    frame = scene()
    frame.tokens = [Token(label, 0.99, box) for label, box in values]
    return frame


def world_with_npc(name=None, *, interaction=False):
    frame = scene()
    frame.tokens = [token("F2", 0.9, 0.05), token("Tab", 0.1, 0.95)]
    if name:
        frame.tokens.append(token(name, 0.6, 0.5))
    if interaction:
        frame.tokens.append(token("F", 1450 / 1920, 1030 / 1080))
    return frame


def calibrated_coffee_dialogue():
    """Relevant CN 20:24 coffee wording, with no player or inventory data."""
    frame = scene()
    frame.tokens = [token("汀曼大师", .5, .745), token("想喝点什么？", .5, .82),
                    token("来杯汀曼特调！", .86, .57), token("来杯鲜萃红茶！", .86, .65)]
    return frame


def calibrated_coffee_reward():
    frame = scene()
    frame.tokens = [token("获得", .50, .30), token("电量", .50, .47), token("确认", .50, .68)]
    return frame


def calibrated_video_dialogue():
    frame = scene()
    frame.tokens = [token("阿玖", .50, .745), token("店里的录像带已经更新啦。", .50, .82),
                    token("查看经营状况", .86, .60)]
    return frame


def calibrated_video_panel(*, operating=False):
    frame = scene()
    frame.tokens = [token("经营状况", .75, .05), token("选择宣传员", .25, .8),
                    token("正在营业" if operating else "开始营业", .86, .90)]
    return frame


def calibrated_video_opened():
    frame = scene()
    frame.tokens = [token("诚信经营，丁尼为本！", .50, .45), token("确认", .50, .60)]
    return frame


def cached_global_account():
    """Fictitious masked account on the specifically calibrated global entry."""
    frame = scene()
    frame.tokens = [token("HOYOVERSE", .5, .32), token("account****@example.test", .5, .45),
                    token("进入游戏", .5, .58)]
    return frame


def game_start_page():
    frame = scene()
    frame.tokens = [token("点击进入游戏", .5, .82)]
    return frame


def captured_global_shortcut_hud():
    """Only non-personal key labels from the 20:52 global client screenshot."""
    values = (
        ("F1", (.7552083333, .1074074074, .7776041667, .1314814815)),
        ("F4", (.8005208333, .1083333333, .8203125000, .1296296296)),
        ("F2", (.8432291667, .1074074074, .8651041667, .1314814815)),
        ("F3", (.8947916667, .1083333333, .9114583333, .1305555556)),
        ("Tab", (.0359375000, .9481481481, .0609375000, .9712962963)),
    )
    frame = scene()
    frame.tokens = [Token(label, .99, box) for label, box in values]
    return frame


def captured_battlepass_tabs():
    frame = scene()
    frame.tokens = [token("丽都城募", .2, .1), token("等级回馈", .3, .15), token("成长任务", .5, .15),
                    token("城募赠礼", .7, .15), token("兑换", .8, .85), token("购买城募赠礼", .8, .9)]
    return frame


def sanitized_reward_image(fractions, *, scale=1):
    """Artificial BGR fire samples at the real reward geometry; no player pixels.

    Green occupies a controlled fraction of each calibrated patch. Keeping the
    rest of the image dark makes off-ROI colors unable to affect the readings.
    """
    import numpy as np

    frame = captured_daily_cards()
    height, width = 1080 * scale, 1920 * scale
    image = np.full((height, width, 3), 12, dtype=np.uint8)
    for x, fraction in zip((.3905, .5330, .6751, .8184), fractions):
        patch_image = image[round(.263 * height):round(.290 * height),
                            round((x - .012) * width):round((x + .012) * width)]
        count = round(patch_image.shape[0] * patch_image.shape[1] * fraction)
        indices = np.arange(count)
        patch_image[indices // patch_image.shape[1], indices % patch_image.shape[1]] = (40, 220, 40)
    frame.image = image
    frame.geometry = windows.Geometry(0, 0, width, height)
    return frame


class WorkflowVerificationTests(unittest.TestCase):
    def test_unknown_initial_page_stops_without_navigation_input(self):
        context = WorkflowContext([scene("无法识别的页面")])
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        self.assertEqual(context.actions, [])
        self.assertIn("unknown_page", context.report.evidence)

    def test_completed_handbook_tasks_do_not_repeat_daily_actions(self):
        for method, label in (("scratch", "刮刮卡"), ("coffee", "喝咖啡"), ("video", "开始营业")):
            with self.subTest(method=method):
                context = WorkflowContext([handbook(task=label)])
                result = getattr(DailyWorkflow(context), method)()
                self.assertEqual(result.status, "ok")
                self.assertEqual(context.actions, [])
                self.assertEqual(context.report.stamina_spent, 0)

    def test_video_already_operating_is_idempotent(self):
        frame = scene()
        frame.tokens = [token("经营状况", 0.75, 0.05), token("正在营业", 0.85, 0.9)]
        context = WorkflowContext([frame])
        result = DailyWorkflow(context).video()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [])

    def test_three_scratch_drags_without_result_remain_partial(self):
        frame = scene()
        frame.tokens = [token("每日可刮取一次", 0.5, 0.65)]
        context = WorkflowContext([frame] * 5 + [handbook()])
        result = DailyWorkflow(context).scratch()
        self.assertEqual(result.status, "partial")
        self.assertEqual(sum(action[0] == "drag" for action in context.actions), 3)
        self.assertFalse(context.report.daily_verified)

    def test_unverified_coffee_price_never_clicks_order(self):
        coffee = scene()
        coffee.tokens = [token("点单", 0.92, 0.9), token("汀曼特调", 0.45, 0.7), token("1500丁尼", 0.8, 0.7)]
        context = WorkflowContext([coffee])
        result = DailyWorkflow(context).coffee()
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "点单"), context.actions)
        self.assertEqual(context.report.stamina_spent, 0)

    def test_free_order_without_result_never_reports_coffee_completed(self):
        coffee = scene()
        coffee.tokens = [token("点单", 0.92, 0.9), token("汀曼特调", 0.45, 0.7), token("今日免费", 0.8, 0.7)]
        world = scene()
        world.tokens = [token("星期日", 0.1, 0.05), token("按自己的步调度过这一天", 0.1, 0.2)]
        context = WorkflowContext([coffee, coffee, coffee, world, handbook()])
        result = DailyWorkflow(context).coffee()
        self.assertEqual(result.status, "partial")
        self.assertIn(("click", "点单"), context.actions)

    def test_travel_without_visible_npc_does_not_press_interact(self):
        map_frame = scene()
        map_frame.tokens = [token("六分街", 0.4, 0.5), token("录像店", 0.65, 0.5), token("报刊亭", 0.5, 0.95)]
        confirm = scene()
        confirm.tokens = map_frame.tokens + [token("确认", 0.59, 0.6)]
        world = scene()
        world.tokens = [token("星期日", 0.1, 0.05), token("按自己的步调度过这一天", 0.1, 0.2)]
        context = WorkflowContext([map_frame, map_frame, confirm, world])
        result = DailyWorkflow(context)._travel("scratch", "六分街", "报刊亭", ("嗷呜", "报刊亭"))
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("key", "f"), context.actions)

    def test_reward_threshold_numbers_are_not_current_engagement(self):
        frame = handbook()
        frame.tokens.extend(token(str(value), x, 0.28) for value, x in
                            ((100, 0.4), (200, 0.55), (300, 0.70), (400, 0.83)))
        self.assertIsNone(DailyWorkflow(WorkflowContext([frame]))._engagement(frame))

    def test_daily_300_is_partial_and_does_not_claim_success(self):
        frame = handbook(300)
        context = WorkflowContext([frame])
        result = DailyWorkflow(context).daily()
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.report.engagement, 300)
        self.assertFalse(context.report.daily_verified)

    def test_daily_400_with_explicit_all_claimed_is_verified(self):
        frame = handbook(400, claimed=True)
        context = WorkflowContext([frame])
        result = DailyWorkflow(context).daily()
        self.assertEqual(result.status, "ok")
        self.assertTrue(context.report.daily_verified)
        self.assertEqual(context.report.engagement, 400)

    def test_daily_400_without_claim_evidence_remains_partial(self):
        frame = handbook(400)
        context = WorkflowContext([frame])
        result = DailyWorkflow(context).daily()
        self.assertEqual(result.status, "partial")
        self.assertFalse(context.report.daily_verified)

    def test_conflicting_current_engagement_readings_are_unverified(self):
        frame = handbook(400)
        frame.tokens.append(token("当前活跃度300/400", 0.55, 0.31))
        self.assertIsNone(DailyWorkflow(WorkflowContext([frame]))._engagement(frame))

    def test_four_reward_marks_do_not_override_explicit_lower_engagement(self):
        frame = handbook(300)
        frame.tokens.extend(token("已领取", x, 0.28) for x in (0.43, 0.56, 0.69, 0.82))
        context = WorkflowContext([frame])
        self.assertFalse(DailyWorkflow(context)._daily_verified(frame))
        self.assertEqual(context.report.engagement, 300)

    def test_incomplete_aggregate_claim_counter_is_not_all_claimed(self):
        frame = handbook(400)
        frame.tokens.append(token("今日奖励已领取0/4", 0.6, 0.28))
        self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._daily_verified(frame))

    def test_four_claim_labels_clustered_in_one_tier_do_not_prove_all_rewards(self):
        frame = handbook(400)
        frame.tokens.extend(token("已领取", 0.8, y) for y in (0.25, 0.26, 0.27, 0.28))
        context = WorkflowContext([frame])
        self.assertFalse(DailyWorkflow(context)._daily_verified(frame))

    def test_four_distinct_reward_tiers_can_prove_all_claimed(self):
        frame = handbook(400)
        frame.tokens.extend(token("已领取", x, 0.28) for x in (0.43, 0.56, 0.69, 0.82))
        context = WorkflowContext([frame])
        self.assertTrue(DailyWorkflow(context)._daily_verified(frame))

    def test_last_tier_template_alone_does_not_prove_all_rewards(self):
        frame = handbook()
        context = WorkflowContext([frame])
        context.template.side_effect = lambda _frame, _name, area, _threshold: (
            (0.82, 0.28, 0.99) if area[0] <= 0.82 <= area[2] and area[1] <= 0.28 <= area[3] else None)
        self.assertFalse(DailyWorkflow(context)._daily_verified(frame))


class CoffeeRewardVerificationTests(unittest.TestCase):
    def test_separate_obtained_energy_and_confirm_fields_identify_coffee_reward(self):
        frame = calibrated_coffee_reward()
        workflow = DailyWorkflow(WorkflowContext([frame]))
        self.assertTrue(workflow._coffee_reward(frame))
        self.assertTrue(workflow._result_screen(frame))
        self.assertTrue(workflow._known(frame))

    def test_each_separate_reward_field_is_required(self):
        for omitted in ("获得", "电量", "确认"):
            frame = calibrated_coffee_reward()
            frame.tokens = [t for t in frame.tokens if t.text != omitted]
            with self.subTest(omitted=omitted):
                self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._coffee_reward(frame))

    def test_reward_fields_outside_popup_are_not_coffee_evidence(self):
        for misplaced in ("获得", "电量", "确认"):
            frame = calibrated_coffee_reward()
            frame.tokens = [token(t.text, .1, .1) if t.text == misplaced else t for t in frame.tokens]
            with self.subTest(misplaced=misplaced):
                self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._coffee_reward(frame))

    def test_other_currency_and_energy_warning_are_not_separate_energy_reward(self):
        for replacement in ("丁尼", "电量不足", "获得电量", "当前电量"):
            frame = calibrated_coffee_reward()
            frame.tokens = [Token(replacement, t.score, t.box) if t.text == "电量" else t
                            for t in frame.tokens]
            with self.subTest(replacement=replacement):
                self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._coffee_reward(frame))

    def test_resume_on_reward_saves_popup_and_confirmation_result(self):
        reward, after = calibrated_coffee_reward(), world_with_npc("汀曼大师")
        context = WorkflowContext([reward, after])
        result = DailyWorkflow(context).coffee()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [("click", "确认")])
        self.assertEqual(context.evidence_frames, [(reward, "coffee_reward"), (after, "coffee_verified")])
        self.assertEqual(context.report.stamina_spent, 0)
        self.assertFalse(context.report.daily_verified)

    def test_safe_dialogue_never_selects_red_tea_and_confirms_energy_reward(self):
        dialogue, reward, after = calibrated_coffee_dialogue(), calibrated_coffee_reward(), world_with_npc()
        context = WorkflowContext([dialogue, reward, after])
        result = DailyWorkflow(context).coffee()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [("click", "来杯汀曼特调！"), ("click", "确认")])
        self.assertEqual(context.evidence_frames, [(dialogue, "coffee_daily_dialogue"),
                                                  (reward, "coffee_reward"), (after, "coffee_verified")])
        self.assertEqual(context.report.stamina_spent, 0)

    def test_duplicate_daily_special_options_never_send_drink_click(self):
        frame = calibrated_coffee_dialogue()
        frame.tokens.append(token("来杯汀曼特调！", .86, .67))
        context = WorkflowContext([frame])
        result = DailyWorkflow(context).coffee()
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.actions, [])

    def test_paid_dialogue_never_sends_drink_click(self):
        frame = calibrated_coffee_dialogue()
        frame.tokens.append(token("付费", .8, .72))
        context = WorkflowContext([frame])
        result = DailyWorkflow(context).coffee()
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.actions, [])

    def test_stopped_confirmation_retains_original_popup_proof_without_verified_status(self):
        reward = calibrated_coffee_reward()
        context = WorkflowContext([reward])
        context.snapshot = Mock(side_effect=[reward, Stopped("Stopped after confirmation")])
        with self.assertRaises(Stopped):
            DailyWorkflow(context).coffee()
        self.assertEqual(context.actions, [("click", "确认")])
        self.assertEqual(context.evidence_frames, [(reward, "coffee_reward")])
        self.assertNotIn("coffee_verified", context.report.evidence)


class StartupReadOnlyWaitTests(unittest.TestCase):
    def starting_context(self, frames):
        context = WorkflowContext(frames)
        context.settings.select_server("global")
        context.settings.auto_launch = True
        context.wait = Mock(wraps=context.wait)
        context.snapshot = Mock(wraps=context.snapshot)
        return context

    def test_startup_black_frames_wait_for_world_without_any_input(self):
        context = self.starting_context([scene(), scene(), world_with_npc()])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [])
        self.assertEqual(context.snapshot.call_count, 3)
        self.assertEqual(context.wait.call_count, 1)
        self.assertEqual(context.wait.call_args.kwargs["timeout"], context.settings.transition_timeout)

    def test_startup_black_then_saved_account_follows_only_calibrated_entry_buttons(self):
        login, start, world = cached_global_account(), game_start_page(), world_with_npc()
        context = self.starting_context([scene(), scene(), login, start, start, world, world])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "进入游戏"), ("click", "点击进入游戏")])
        self.assertEqual(context.evidence_frames, [(login, "cached_account_login")])

    def test_startup_black_then_unique_title_entry_uses_existing_launch_flow(self):
        context = self.starting_context([scene(), game_start_page(), world_with_npc(), world_with_npc()])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "点击进入游戏")])

    def test_startup_known_calibrated_pages_allow_escape_only_after_loading(self):
        for known in (captured_m_map(), captured_daily_cards()):
            context = self.starting_context([scene(), known, world_with_npc()])
            with self.subTest(page=known.text[:20]):
                self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
                self.assertEqual(context.actions, [("key", "esc")])

    def test_real_local_cold_launch_black_frame_can_wait_for_world_when_available(self):
        import cv2
        import numpy as np

        root = Path(__file__).resolve().parents[1]
        path = root / "dist/AutoZZZ/runs/20261011_210443_378720/210448_265498_start.png"
        if not path.is_file():
            self.skipTest("Optional failed cold-start capture is not distributed; portable black frames cover startup")
        black = scene()
        black.image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(black.image)
        context = self.starting_context([black, black, world_with_npc()])
        vision = Vision.__new__(Vision)
        context.template.side_effect = lambda frame, name, area, threshold: (
            vision.template(frame, root / "assets/templates" / (name + ".png"), area, threshold)
            if frame.image is not None else None)
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [])

    def test_real_context_startup_wait_is_bounded_and_never_sends_input(self):
        black = scene()
        context, runner, window = context_for(black)
        runner.settings.select_server("global")
        runner.settings.auto_launch = True
        runner.settings.transition_timeout = 10
        context.template = Mock(return_value=None)
        context.snapshot = Mock(return_value=black)
        context.evidence = Mock()
        workflow = DailyWorkflow(context)
        with patch.object(context, "wait", wraps=context.wait) as wait:
            with self.assertRaises(AutomationError):
                workflow.ensure_main()
            self.assertEqual(wait.call_count, 1)
            self.assertEqual(wait.call_args.kwargs["timeout"], 10)
            self.assertLessEqual(context.snapshot.call_count, 17)
            # A failed first launch cannot reopen the startup grace period.
            with self.assertRaises(AutomationError):
                workflow.ensure_main()
            self.assertEqual(wait.call_count, 1)
        window.click.assert_not_called()
        window.key.assert_not_called()
        window.move.assert_not_called()

    def test_manual_unknown_page_still_stops_immediately_without_startup_wait(self):
        context = WorkflowContext([scene(), world_with_npc()])
        context.wait = Mock(wraps=context.wait)
        context.snapshot = Mock(wraps=context.snapshot)
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        context.wait.assert_not_called()
        self.assertEqual(context.snapshot.call_count, 1)
        self.assertEqual(context.actions, [])

    def test_later_unknown_page_after_main_never_reuses_initial_wait(self):
        context = self.starting_context([world_with_npc()])
        workflow = DailyWorkflow(context)
        self.assertEqual(workflow.ensure_main().status, "ok")
        context.frames = [scene("开始战斗"), world_with_npc()]
        with self.assertRaises(AutomationError):
            workflow.ensure_main()
        context.wait.assert_not_called()
        self.assertEqual(context.actions, [])

    def test_unknown_transition_later_in_first_call_does_not_get_startup_grace(self):
        context = self.starting_context([captured_m_map(), scene("开始挑战")])
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        context.wait.assert_not_called()
        self.assertEqual(context.actions, [("key", "esc")])

    def test_initial_credentials_consent_or_update_stop_even_over_known_background(self):
        for label in ("密码", "验证码", "扫码", "请同意用户协议", "更新", "下载",
                      "Password", "Captcha", "Agree to terms", "Update", "Download"):
            blocked = world_with_npc()
            blocked.tokens.append(token(label))
            context = self.starting_context([blocked])
            with self.subTest(label=label), self.assertRaises(AutomationError):
                DailyWorkflow(context).ensure_main()
            context.wait.assert_not_called()
            self.assertEqual(context.actions, [])

    def test_credentials_appearing_during_startup_wait_stop_on_that_frame(self):
        for label in ("密码", "验证码", "请同意用户协议", "更新", "Password", "Captcha"):
            blocked = world_with_npc()
            blocked.tokens.append(token(label))
            context = self.starting_context([scene(), blocked, world_with_npc()])
            with self.subTest(label=label), self.assertRaises(AutomationError):
                DailyWorkflow(context).ensure_main()
            self.assertEqual(context.snapshot.call_count, 2)
            self.assertEqual(context.actions, [])

    def test_ambiguous_saved_account_during_startup_never_gets_a_click(self):
        login = cached_global_account()
        login.tokens.append(token("进入游戏", .5, .62))
        context = self.starting_context([scene(), login])
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        self.assertEqual(context.actions, [])
        self.assertEqual(context.wait.call_count, 1)


class CachedGlobalLoginTests(unittest.TestCase):
    def global_context(self, frames):
        context = WorkflowContext(frames)
        context.settings.select_server("global")
        return context

    def test_global_saved_account_requires_all_calibrated_anchors(self):
        frame = cached_global_account()
        workflow = DailyWorkflow(self.global_context([frame]))
        self.assertIs(workflow._cached_login(frame), frame.tokens[-1])
        for omitted in ("HOYOVERSE", "account****@example.test", "进入游戏"):
            missing = cached_global_account()
            missing.tokens = [t for t in missing.tokens if t.text != omitted]
            with self.subTest(omitted=omitted):
                self.assertIsNone(workflow._cached_login(missing))

    def test_cache_entry_is_not_authorized_on_cn_client(self):
        frame = cached_global_account()
        context = WorkflowContext([frame])
        workflow = DailyWorkflow(context)
        self.assertIsNone(workflow._cached_login(frame))
        with self.assertRaises(AutomationError):
            workflow.ensure_main()
        self.assertEqual(context.actions, [])

    def test_unmasked_or_non_account_strings_do_not_authorize_cache_entry(self):
        for account in ("accountmasked@example.test", "account****example.test", "****"):
            frame = cached_global_account()
            frame.tokens[1] = Token(account, frame.tokens[1].score, frame.tokens[1].box)
            with self.subTest(account=account):
                self.assertIsNone(DailyWorkflow(self.global_context([frame]))._cached_login(frame))

    def test_foreign_page_with_login_words_is_rejected_without_any_input(self):
        frame = cached_global_account()
        frame.tokens[0] = Token("OTHER SERVICE", frame.tokens[0].score, frame.tokens[0].box)
        context = self.global_context([frame])
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        self.assertEqual(context.actions, [])

    def test_saved_account_anchors_outside_calibrated_regions_are_rejected(self):
        for index in range(3):
            frame = cached_global_account()
            frame.tokens[index] = token(frame.tokens[index].text, .1, .1)
            with self.subTest(index=index):
                self.assertIsNone(DailyWorkflow(self.global_context([frame]))._cached_login(frame))

    def test_credentials_updates_and_required_consent_block_cached_account_entry(self):
        for label in ("密码", "验证码", "扫码", "更新", "下载", "请同意用户协议",
                      "Password", "Verification code", "Scan QR", "Update", "Download", "Agree to terms"):
            frame = cached_global_account()
            frame.tokens.append(token(label, .5, .48))
            with self.subTest(label=label):
                self.assertIsNone(DailyWorkflow(self.global_context([frame]))._cached_login(frame))

    def test_duplicate_saved_account_enter_buttons_are_rejected_without_input(self):
        frame = cached_global_account()
        frame.tokens.append(token("进入游戏", .5, .62))
        context = self.global_context([frame])
        workflow = DailyWorkflow(context)
        self.assertIsNone(workflow._cached_login(frame))
        with self.assertRaises(AutomationError):
            workflow.ensure_main()
        self.assertEqual(context.actions, [])

    def test_duplicate_start_game_buttons_are_rejected_without_input(self):
        frame = game_start_page()
        frame.tokens.append(token("点击进入游戏", .6, .82))
        context = self.global_context([frame])
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        self.assertEqual(context.actions, [])

    def test_saved_login_then_game_start_entry_reaches_world_using_only_two_clicks(self):
        login, start, world = cached_global_account(), game_start_page(), world_with_npc()
        context = self.global_context([login, start, start, world, world])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "进入游戏"), ("click", "点击进入游戏")])
        self.assertEqual(context.evidence_frames, [(login, "cached_account_login")])

    def test_focus_activation_noop_clicks_reobserve_and_retry_before_reporting_world(self):
        login, start, world = cached_global_account(), game_start_page(), world_with_npc()
        context = self.global_context([login, login, login, start, start, start, start, world, world])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "进入游戏"), ("click", "进入游戏"),
                                          ("click", "点击进入游戏"), ("click", "点击进入游戏")])

    def test_permanent_cached_entry_noop_has_five_attempt_limit(self):
        context = self.global_context([cached_global_account()])
        context.snapshot = Mock(wraps=context.snapshot)
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        self.assertEqual(context.actions, [("click", "进入游戏")] * 5)
        self.assertLessEqual(context.snapshot.call_count, 11)

    def test_credential_challenge_after_cache_entry_never_gets_additional_input(self):
        challenge = scene("验证码", "进入游戏")
        context = self.global_context([cached_global_account(), challenge])
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        self.assertEqual(context.actions, [("click", "进入游戏")])

    def test_title_click_can_reveal_saved_account_before_world(self):
        title, login, world = game_start_page(), cached_global_account(), world_with_npc()
        context = self.global_context([title, login, login, title, title, world, world])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "点击进入游戏"), ("click", "进入游戏"),
                                          ("click", "点击进入游戏")])

    def test_fifth_title_entry_attempt_that_reaches_world_is_successful(self):
        title, world = game_start_page(), world_with_npc()
        context = self.global_context([title] * 9 + [world, world])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "点击进入游戏")] * 5)

    def test_fifth_saved_account_attempt_that_reaches_world_is_successful(self):
        login, world = cached_global_account(), world_with_npc()
        context = self.global_context([login] * 9 + [world, world])
        self.assertEqual(DailyWorkflow(context).ensure_main().status, "ok")
        self.assertEqual(context.actions, [("click", "进入游戏")] * 5)

    def test_permanent_title_entry_noop_still_stops_after_five_attempts(self):
        context = self.global_context([game_start_page()])
        context.snapshot = Mock(wraps=context.snapshot)
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).ensure_main()
        self.assertEqual(context.actions, [("click", "点击进入游戏")] * 5)
        self.assertLessEqual(context.snapshot.call_count, 11)


class HandbookShortcutTests(unittest.TestCase):
    anchor = (.81015625, .075462963, .95366)

    def opening_context(self, world, *, server="global", anchor=True):
        daily = captured_daily_cards()
        context = WorkflowContext([world, world, world, daily, daily])
        context.settings.select_server(server)
        context.template.side_effect = lambda frame, name, _area, _threshold: (
            self.anchor if anchor and frame is world and name == "world_menu" else None)
        return context, daily

    def test_global_handbook_uses_f4_below_anchor_and_not_nearby_battlepass_f2(self):
        world = captured_global_shortcut_hud()
        context, daily = self.opening_context(world)
        self.assertIs(DailyWorkflow(context)._open_daily(), daily)
        self.assertEqual(context.actions, [("key", "f4"), ("click", "日常")])
        self.assertNotIn(("key", "f2"), context.actions)

    def test_function_keys_are_bound_to_their_visible_handbook_slot(self):
        for name in ("F1", "F2", "F3", "F4"):
            world = captured_global_shortcut_hud()
            world.tokens = [Token(name, t.score, t.box) if t.text == "F4" else t for t in world.tokens]
            context, _ = self.opening_context(world)
            with self.subTest(name=name):
                DailyWorkflow(context)._open_daily()
                self.assertEqual(context.actions, [("key", name.casefold()), ("click", "日常")])

    def test_global_missing_or_duplicate_handbook_shortcut_stops_without_input(self):
        for mutation in ("missing", "duplicate_same", "duplicate_other", "wrong_height"):
            world = captured_global_shortcut_hud()
            if mutation == "missing":
                world.tokens = [t for t in world.tokens if t.text != "F4"]
            elif mutation == "duplicate_same":
                world.tokens.append(token("F4", .81, .13))
            elif mutation == "duplicate_other":
                world.tokens.append(token("F2", .81, .13))
            else:
                world.tokens = [token("F4", .81, .2) if t.text == "F4" else t for t in world.tokens]
            context, _ = self.opening_context(world)
            with self.subTest(mutation=mutation), self.assertRaises(AutomationError):
                DailyWorkflow(context)._open_daily()
            self.assertEqual(context.actions, [])

    def test_global_missing_handbook_glyph_never_guesses_f2(self):
        context, _ = self.opening_context(captured_global_shortcut_hud(), anchor=False)
        with self.assertRaises(AutomationError):
            DailyWorkflow(context)._open_daily()
        self.assertEqual(context.actions, [])

    def test_cn_without_template_retains_f2_with_handbook_verification(self):
        context, daily = self.opening_context(captured_global_shortcut_hud(), server="cn", anchor=False)
        self.assertIs(DailyWorkflow(context)._open_daily(), daily)
        self.assertEqual(context.actions, [("key", "f2"), ("click", "日常")])

    def test_wrong_page_after_function_key_is_not_treated_as_handbook(self):
        world, battlepass = captured_global_shortcut_hud(), captured_battlepass_tabs()
        context, _ = self.opening_context(world)
        context.frames = [world, world, world, battlepass]
        with self.assertRaises(AutomationError):
            DailyWorkflow(context)._open_daily()
        self.assertEqual(context.actions, [("key", "f4")])

    def test_battlepass_is_known_only_for_escape_without_purchase_or_redemption(self):
        battlepass, world = captured_battlepass_tabs(), captured_global_shortcut_hud()
        context, _ = self.opening_context(world)
        context.frames = [battlepass, world]
        workflow = DailyWorkflow(context)
        self.assertTrue(workflow._battlepass(battlepass))
        self.assertEqual(workflow.ensure_main().status, "ok")
        self.assertEqual(context.actions, [("key", "esc")])

    def test_partial_battlepass_label_does_not_authorize_escape(self):
        incomplete = scene("丽都城募", "等级回馈")
        context = WorkflowContext([incomplete])
        workflow = DailyWorkflow(context)
        self.assertFalse(workflow._battlepass(incomplete))
        with self.assertRaises(AutomationError):
            workflow.ensure_main()
        self.assertEqual(context.actions, [])

    def test_recovering_from_battlepass_uses_escape_then_actual_global_handbook_key(self):
        world, battlepass = captured_global_shortcut_hud(), captured_battlepass_tabs()
        context, daily = self.opening_context(world)
        context.frames = [battlepass, battlepass, world, world, daily, daily]
        self.assertIs(DailyWorkflow(context)._open_daily(), daily)
        self.assertEqual(context.actions, [("key", "esc"), ("key", "f4"), ("click", "日常")])

    def test_real_local_global_hud_template_binds_f4_when_capture_is_available(self):
        import cv2
        import numpy as np

        root = Path(__file__).resolve().parents[1]
        path = root / "runs/check_20261011_205224/screen.png"
        if not path.is_file():
            self.skipTest("Optional global calibration capture is not distributed; sanitized key geometry covers binding")
        world = captured_global_shortcut_hud()
        world.image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(world.image)
        context, daily = self.opening_context(world)
        vision = Vision.__new__(Vision)
        context.template.side_effect = lambda frame, name, area, threshold: (
            vision.template(frame, root / "assets/templates" / (name + ".png"), area, threshold)
            if frame.image is not None else None)
        self.assertIs(DailyWorkflow(context)._open_daily(), daily)
        self.assertEqual(context.actions, [("key", "f4"), ("click", "日常")])


class VideoFlowVerificationTests(unittest.TestCase):
    def test_new_ajiu_dialogue_requires_npc_in_calibrated_region(self):
        frame = calibrated_video_dialogue()
        self.assertTrue(DailyWorkflow(WorkflowContext([frame]))._video_dialog(frame))
        for mutation in ("missing", "wrong_region", "wrong_npc"):
            invalid = calibrated_video_dialogue()
            if mutation == "missing":
                invalid.tokens = [t for t in invalid.tokens if t.text != "阿玖"]
            elif mutation == "wrong_region":
                invalid.tokens = [token("阿玖", .1, .1) if t.text == "阿玖" else t for t in invalid.tokens]
            else:
                invalid.tokens = [Token("路人", t.score, t.box) if t.text == "阿玖" else t for t in invalid.tokens]
            with self.subTest(mutation=mutation):
                self.assertFalse(DailyWorkflow(WorkflowContext([invalid]))._video_dialog(invalid))

    def test_opening_dialogue_waits_through_black_frames_without_extra_input(self):
        context = WorkflowContext([calibrated_video_dialogue(), scene(), scene(),
                                   calibrated_video_panel(operating=True)])
        context.wait = Mock(wraps=context.wait)
        result = DailyWorkflow(context).video()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [("click", "查看经营状况")])
        self.assertEqual(context.wait.call_args.kwargs["timeout"], context.settings.transition_timeout)

    def test_permanent_black_loading_reaches_bounded_timeout_without_more_clicks(self):
        context = WorkflowContext([calibrated_video_dialogue(), scene()])
        context.wait = Mock(wraps=context.wait)
        context.snapshot = Mock(wraps=context.snapshot)
        with self.assertRaises(AutomationError):
            DailyWorkflow(context).video()
        self.assertEqual(context.actions, [("click", "查看经营状况")])
        self.assertEqual(context.wait.call_count, 1)
        self.assertEqual(context.wait.call_args.kwargs["timeout"], context.settings.transition_timeout)
        self.assertLessEqual(context.snapshot.call_count, 11)

    def test_previous_ledger_closes_by_text_template_or_recognized_escape(self):
        for method in ("ocr", "template", "escape"):
            ledger = scene("昨日账本")
            if method == "ocr":
                ledger.tokens.append(token("关闭", .71, .2))
            context = WorkflowContext([ledger, calibrated_video_panel(operating=True)])
            if method == "template":
                context.template.side_effect = lambda _frame, name, _roi, _threshold: (
                    (.71, .2, .99) if name == "close" else None)
            with self.subTest(method=method):
                self.assertEqual(DailyWorkflow(context).video().status, "ok")
                expected = [("click", "关闭")] if method == "ocr" else (
                    [("click", (.71, .2))] if method == "template" else [("key", "esc")])
                self.assertEqual(context.actions, expected)

    def test_empty_slots_recommendation_and_business_confirmation_finish_in_world(self):
        dialogue, blank, ledger = calibrated_video_dialogue(), scene(), scene("昨日账本")
        ledger.tokens.append(token("关闭", .71, .2))
        empty, ready = calibrated_video_panel(), calibrated_video_panel()
        selection = scene()
        selection.tokens = [token("上架筛选", .60, .07), token("推荐上架", .73, .94)]
        confirmation = scene()
        confirmation.tokens = [token("确认开始营业", .5, .45), token("确认", .59, .60)]
        opened, world = calibrated_video_opened(), world_with_npc()
        context = WorkflowContext([dialogue, blank, ledger, empty, selection, selection,
                                   ready, confirmation, opened, blank, world])
        anchor = (1200 / 1920, 780 / 1080, .99)
        context.template.side_effect = lambda frame, name, _roi, _threshold: (
            anchor if frame is empty and name == "video_slot_plus" else None)
        context.wait = Mock(wraps=context.wait)
        workflow = DailyWorkflow(context)
        # Promoter selection has its own full replay; this test isolates tape and opening flow.
        workflow._configure_video_promoter = Mock(side_effect=lambda panel: panel)
        result = workflow.video()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [("click", "查看经营状况"), ("click", "关闭"),
                                          ("click", anchor[:2]), ("click", "推荐上架"), ("key", "esc"),
                                          ("click", "开始营业"), ("click", "确认"), ("click", "确认")])
        self.assertEqual(context.evidence_frames, [(empty, "video_empty_slots"),
                                                  (opened, "video_opened"), (world, "video_verified")])
        self.assertEqual(context.wait.call_count, 3)
        self.assertTrue(all(call.kwargs["timeout"] == context.settings.transition_timeout
                            for call in context.wait.call_args_list))
        slot_calls = [call.args for call in context.template.call_args_list if call.args[1] == "video_slot_plus"]
        self.assertTrue(slot_calls)
        self.assertEqual(slot_calls[0][2], (1100 / 1920, 650 / 1080, 1320 / 1920, 900 / 1080))
        self.assertEqual(context.report.stamina_spent, 0)
        self.assertFalse(context.report.daily_verified)

    def test_fixed_opened_message_can_return_to_operating_panel(self):
        opened, panel = calibrated_video_opened(), calibrated_video_panel(operating=True)
        context = WorkflowContext([opened, scene(), panel])
        result = DailyWorkflow(context).video()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [("click", "确认")])
        self.assertEqual(context.evidence_frames, [(opened, "video_opened"), (panel, "video_verified")])

    def test_opened_message_without_confirmation_stops_without_input(self):
        frame = calibrated_video_opened()
        frame.tokens = [t for t in frame.tokens if t.text != "确认"]
        context = WorkflowContext([frame])
        self.assertEqual(DailyWorkflow(context).video().status, "partial")
        self.assertEqual(context.actions, [])

    def test_opened_message_with_two_confirmations_does_not_pick_either(self):
        frame = calibrated_video_opened()
        frame.tokens.append(token("确认", .60, .60))
        context = WorkflowContext([frame])
        self.assertEqual(DailyWorkflow(context).video().status, "partial")
        self.assertEqual(context.actions, [])

    def test_business_payment_popup_blocks_second_click(self):
        paid = scene("确认支付", "确认")
        context = WorkflowContext([calibrated_video_panel(), paid])
        workflow = DailyWorkflow(context)
        workflow._configure_video_promoter = Mock(side_effect=lambda panel: panel)
        with self.assertRaises(AutomationError):
            workflow.video()
        self.assertEqual(context.actions, [("click", "开始营业")])

    def test_unexpected_battle_confirmation_is_not_business_confirmation(self):
        battle = scene()
        battle.tokens = [token("开始战斗", .5, .45), token("确认", .59, .6)]
        context = WorkflowContext([calibrated_video_panel(), battle])
        workflow = DailyWorkflow(context)
        workflow._configure_video_promoter = Mock(side_effect=lambda panel: panel)
        result = workflow.video()
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.actions, [("click", "开始营业")])

    def test_missing_owned_video_or_promoter_remains_partial_without_purchase(self):
        for missing in ("还未上架录像带", "还未选择宣传员"):
            frame = scene()
            frame.tokens = [token(missing, .5, .45), token("确认", .5, .60)]
            context = WorkflowContext([frame, world_with_npc()])
            with self.subTest(missing=missing):
                self.assertEqual(DailyWorkflow(context).video().status, "partial")
                self.assertEqual(context.actions, [("click", "确认")])


class CapturedDailyCardTests(unittest.TestCase):
    def test_captured_three_zero_progress_cards_are_incomplete(self):
        frame = captured_daily_cards()
        workflow = DailyWorkflow(WorkflowContext([frame]))
        for task in ("coffee", "scratch", "video"):
            with self.subTest(task=task):
                self.assertFalse(workflow._task_complete(frame, TASK_LABELS[task]))
        self.assertIsNone(workflow._engagement(frame), "Today's maximum 400 is not current activity")

    def test_completed_progress_belongs_only_to_its_own_card(self):
        for completed, progress_x in (("coffee", 0.46875), ("scratch", 0.63672), ("video", 0.80443)):
            frame = captured_daily_cards()
            frame.tokens = [Token("进度：1/1", t.score, t.box)
                            if t.text == "进度：0/1" and abs(t.center[0] - progress_x) < 0.02 else t
                            for t in frame.tokens]
            workflow = DailyWorkflow(WorkflowContext([frame]))
            for task in ("coffee", "scratch", "video"):
                with self.subTest(completed=completed, task=task):
                    self.assertEqual(workflow._task_complete(frame, TASK_LABELS[task]), task == completed)

    def test_weekly_progress_and_completion_never_complete_daily_card(self):
        frame = captured_daily_cards()
        frame.tokens.extend((token("品尝1次咖啡", 0.28, 0.5), token("进度：1/1", 0.28, 0.415),
                             token("已完成", 0.28, 0.75)))
        self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._task_complete(frame, TASK_LABELS["coffee"]))

    def test_status_in_another_card_cannot_complete_horizontal_title_row(self):
        frame = handbook()
        frame.tokens.extend((token("品尝1次咖啡", 0.47, 0.5), token("已完成", 0.82, 0.5)))
        self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._task_complete(frame, TASK_LABELS["coffee"]))

    def test_conflicting_progress_in_same_card_is_unverified(self):
        frame = captured_daily_cards()
        frame.tokens.append(token("进度：1/1", 0.47, 0.42))
        self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._task_complete(frame, TASK_LABELS["coffee"]))

    def test_unique_go_button_is_bound_to_selected_daily_card(self):
        for task, expected_x, destination in (("coffee", 0.502605, "点单"),
                                              ("scratch", 0.669795, "嗷呜"),
                                              ("video", 0.8375, "经营状况")):
            with self.subTest(task=task):
                frame = captured_daily_cards()
                frame.tokens.append(token("前往", 0.28, 0.805))
                context = WorkflowContext([frame, frame, scene(destination)])
                result = DailyWorkflow(context)._daily_jump(task)
                self.assertIsInstance(result, Scene)
                self.assertEqual(context.targets[-1].text, "前往")
                self.assertAlmostEqual(context.targets[-1].center[0], expected_x, places=5)

    def test_two_matching_task_columns_do_not_choose_either_go_button(self):
        frame = captured_daily_cards()
        frame.tokens.append(token("品尝1次咖啡", 0.64, 0.5))
        context = WorkflowContext([frame])
        result = DailyWorkflow(context)._daily_jump("coffee")
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "前往"), context.actions)

    def test_two_go_buttons_in_same_card_are_rejected(self):
        frame = captured_daily_cards()
        frame.tokens.append(token("前往", 0.5, 0.825))
        context = WorkflowContext([frame])
        result = DailyWorkflow(context)._daily_jump("coffee")
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "前往"), context.actions)

    def test_missing_daily_go_button_does_not_use_other_card_or_weekly_button(self):
        frame = captured_daily_cards()
        frame.tokens = [t for t in frame.tokens if not (t.text == "前往" and t.center[0] < 0.58)]
        frame.tokens.append(token("前往", 0.28, 0.805))
        context = WorkflowContext([frame])
        result = DailyWorkflow(context)._daily_jump("coffee")
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "前往"), context.actions)

    def test_matching_title_only_in_weekly_column_does_not_authorize_jump(self):
        frame = captured_daily_cards()
        frame.tokens = [t for t in frame.tokens if t.text != "品尝1次咖啡"]
        frame.tokens.extend((token("品尝1次咖啡", 0.28, 0.5), token("前往", 0.28, 0.805)))
        context = WorkflowContext([frame])
        result = DailyWorkflow(context)._daily_jump("coffee")
        self.assertEqual(result.status, "partial")
        self.assertNotIn(("click", "前往"), context.actions)

    def test_arrival_without_bound_npc_never_moves_or_interacts(self):
        frame = captured_daily_cards()
        world = scene()
        world.tokens = [token("F2", 0.9, 0.05), token("Tab", 0.1, 0.95)]
        context = WorkflowContext([frame, frame, world])
        result = DailyWorkflow(context)._daily_jump("coffee")
        self.assertEqual(result.status, "partial")
        self.assertFalse(any(action[0] == "key" and action[1] in ("w", "f") for action in context.actions))

    def test_bound_npc_and_visible_f_prompt_allow_interaction(self):
        frame = captured_daily_cards()
        world = scene()
        world.tokens = [token("F2", 0.9, 0.05), token("Tab", 0.1, 0.95), token("汀曼"), token("F", 0.6, 0.55)]
        coffee = scene()
        coffee.tokens = [token("点单", 0.92, 0.9), token("汀曼特调", 0.45, 0.7)]
        context = WorkflowContext([frame, frame, world, coffee])
        result = DailyWorkflow(context)._daily_jump("coffee")
        self.assertIsInstance(result, Scene)
        self.assertIn(("key", "f"), context.actions)
        self.assertNotIn(("key", "w"), context.actions)

    def test_four_real_reward_slots_require_four_distinct_template_hits(self):
        frame = captured_daily_cards()
        context = WorkflowContext([frame])
        observed = ((0.39, 0.28, 0.99), (0.53, 0.28, 0.99), (0.67, 0.28, 0.99), (0.81, 0.28, 0.99))
        context.template.side_effect = lambda _frame, _name, area, _threshold: next(
            (match for match in observed if area[0] <= match[0] <= area[2] and area[1] <= match[1] <= area[3]), None)
        self.assertTrue(DailyWorkflow(context)._daily_verified(frame))
        self.assertEqual(context.report.engagement, 400)

    def test_shared_boundary_claim_label_does_not_prove_two_reward_tiers(self):
        frame = handbook(400)
        frame.tokens.extend(token("已领取", x, 0.28) for x in (0.49, 0.69, 0.82))
        self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._daily_verified(frame))

    def test_all_claimed_daily_skips_missing_task_cards_and_never_uses_weekly_go(self):
        for task in ("scratch", "coffee", "video"):
            frame = captured_daily_cards()
            frame.tokens = [t for t in frame.tokens if t.center[1] < .35]
            frame.tokens.extend(token("已领取", x, .31) for x in (.43, .56, .69, .82))
            frame.tokens.append(token("前往", .28, .805))
            context = WorkflowContext([frame])
            with self.subTest(task=task):
                result = DailyWorkflow(context)._daily_jump(task)
                self.assertEqual(result.status, "ok")
                self.assertEqual(context.report.engagement, 400)
                self.assertEqual(context.actions, [("click", "日常")])

    def test_real_local_daily_one_four_claimed_fixture_when_capture_is_available(self):
        import cv2
        import numpy as np

        root = Path(__file__).resolve().parents[1]
        path = root / "runs/live-session/20261011_203727_863101/203743_528809_daily_verified.png"
        if not path.is_file():
            self.skipTest("Optional local daily-1 capture is not distributed; portable tier fixtures cover verification")
        frame = captured_daily_cards()
        frame.tokens = [t for t in frame.tokens if t.center[1] < .35]  # Non-personal handbook anchors only.
        frame.image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(frame.image)
        vision = Vision.__new__(Vision)
        context = WorkflowContext([frame])
        context.template.side_effect = lambda observed, name, area, threshold: vision.template(
            observed, root / "assets/templates" / (name + ".png"), area, threshold)
        workflow = DailyWorkflow(context)
        self.assertEqual(workflow._engagement(frame), 400)
        self.assertTrue(workflow._daily_verified(frame))
        result = workflow.daily()
        self.assertEqual(result.status, "ok")
        self.assertTrue(context.report.daily_verified)
        self.assertEqual(context.report.engagement, 400)
        self.assertEqual(context.actions, [("click", "日常")])
        self.assertEqual(json.loads(json.dumps(context.report.to_dict()))["engagement"], 400)


class RewardFireVerificationTests(unittest.TestCase):
    def test_portable_sanitized_fire_images_read_zero_through_four_hundred(self):
        for scale in (1, 2):
            for count in range(5):
                with self.subTest(scale=scale, count=count):
                    frame = sanitized_reward_image([.36] * count + [0] * (4 - count), scale=scale)
                    workflow = DailyWorkflow(WorkflowContext([frame]))
                    pixel_reading = workflow._reward_progress(frame)
                    reading = workflow._engagement(frame)
                    self.assertIs(type(pixel_reading), int, "Image arithmetic must not leak numpy scalars")
                    self.assertIs(type(reading), int, "The public engagement reading must be a native JSON number")
                    self.assertEqual(reading, count * 100)

    def test_fire_readings_remain_native_int_in_json_run_reports(self):
        for count in range(5):
            frame = sanitized_reward_image([.36] * count + [0] * (4 - count))
            context = WorkflowContext([frame])
            workflow = DailyWorkflow(context)
            with self.subTest(count=count):
                self.assertFalse(workflow._daily_verified(frame), "Activity alone does not prove reward claims")
                self.assertIs(type(context.report.engagement), int)
                payload = json.dumps(context.report.to_dict(), ensure_ascii=False)
                decoded = json.loads(payload)
                self.assertIs(type(decoded["engagement"]), int)
                self.assertEqual(decoded["engagement"], count * 100)

    def test_real_local_calibration_fire_reading_when_capture_is_available(self):
        import cv2
        import numpy as np

        root = Path(__file__).resolve().parents[1] / "runs" / "live-session"
        captures = (
            (root / "20261011_200904_432022" / "200912_239896_scratch_daily_jump.png", 100),
            (root / "20261011_201801_725660" / "201808_144231_coffee_daily_jump.png", 200),
        )
        available = [(path, expected) for path, expected in captures if path.is_file()]
        if not available:
            self.skipTest("Optional local calibration captures are not distributed; sanitized fixtures cover this path")
        for path, expected in available:
            with self.subTest(capture=path.name):
                frame = captured_daily_cards()  # Only non-personal OCR anchors are reused.
                frame.image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(frame.image)
                context = WorkflowContext([frame])
                workflow = DailyWorkflow(context)
                self.assertEqual(workflow._engagement(frame), expected)
                self.assertFalse(workflow._daily_verified(frame))

    def test_green_pixels_outside_fire_patches_are_ignored(self):
        frame = sanitized_reward_image([0, 0, 0, 0])
        frame.image[500:800, 700:1600] = (40, 220, 40)
        self.assertEqual(DailyWorkflow(WorkflowContext([frame]))._engagement(frame), 0)

    def test_image_fallback_requires_known_handbook_and_maximum_label_in_roi(self):
        for missing in ("tabs", "maximum", "maximum_roi", "image"):
            frame = sanitized_reward_image([.36] * 4)
            if missing == "tabs":
                frame.tokens = [t for t in frame.tokens if t.text not in ("目标", "日常", "训练", "作战", "战术")]
            elif missing == "maximum":
                frame.tokens = [t for t in frame.tokens if t.text != "今日最大活跃度"]
            elif missing == "maximum_roi":
                frame.tokens = [t for t in frame.tokens if t.text != "今日最大活跃度"] + [token("今日最大活跃度")]
            else:
                frame.image = None
            with self.subTest(missing=missing):
                self.assertIsNone(DailyWorkflow(WorkflowContext([frame]))._reward_progress(frame))

    def test_non_contiguous_active_indicators_are_rejected(self):
        for fractions in ([0, .36, 0, 0], [.36, 0, .36, 0], [.36, .36, 0, .36]):
            with self.subTest(fractions=fractions):
                frame = sanitized_reward_image(fractions)
                self.assertIsNone(DailyWorkflow(WorkflowContext([frame]))._engagement(frame))

    def test_ambiguous_green_fractions_are_rejected_at_any_tier(self):
        for index in range(4):
            fractions = [.36] * index + [.08] + [0] * (3 - index)
            frame = sanitized_reward_image(fractions)
            with self.subTest(index=index):
                self.assertIsNone(DailyWorkflow(WorkflowContext([frame]))._engagement(frame))

    def test_current_ocr_takes_priority_over_disagreeing_green_pixels(self):
        frame = sanitized_reward_image([.36] * 4)
        frame.tokens.append(token("今日活跃度200/400", .35, .30))
        workflow = DailyWorkflow(WorkflowContext([frame]))
        with patch.object(workflow, "_reward_progress", wraps=workflow._reward_progress) as fallback:
            self.assertEqual(workflow._engagement(frame), 200)
            fallback.assert_not_called()

    def test_conflicting_current_ocr_never_falls_back_to_all_green_image(self):
        frame = sanitized_reward_image([.36] * 4)
        frame.tokens.extend((token("今日活跃度400/400", .35, .30),
                             token("当前活跃度300/400", .55, .31),
                             token("全部奖励已领取", .65, .31)))
        workflow = DailyWorkflow(WorkflowContext([frame]))
        with patch.object(workflow, "_reward_progress", wraps=workflow._reward_progress) as fallback:
            self.assertIsNone(workflow._engagement(frame))
            self.assertFalse(workflow._daily_verified(frame))
            fallback.assert_not_called()

    def test_two_active_fires_cannot_verify_daily_even_with_all_claimed_labels(self):
        frame = sanitized_reward_image([.36, .36, 0, 0])
        frame.tokens.extend(token("已领取", x, .31) for x in (.43, .56, .69, .82))
        context = WorkflowContext([frame])
        self.assertFalse(DailyWorkflow(context)._daily_verified(frame))
        self.assertEqual(context.report.engagement, 200)
        self.assertFalse(context.report.daily_verified)

    def test_four_active_fires_alone_do_not_prove_reward_claims(self):
        frame = sanitized_reward_image([.36] * 4)
        context = WorkflowContext([frame])
        self.assertFalse(DailyWorkflow(context)._daily_verified(frame))
        self.assertEqual(context.report.engagement, 400)

    def test_two_active_fires_return_partial_daily_without_claim_inputs(self):
        frame = sanitized_reward_image([.36, .36, 0, 0])
        context = WorkflowContext([frame])
        result = DailyWorkflow(context).daily()
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.report.engagement, 200)
        self.assertFalse(context.report.daily_verified)
        self.assertEqual([a for a in context.actions if a[0] == "click"], [("click", "日常")])


class RemovedTaskCardVerificationTests(unittest.TestCase):
    def after_task(self, task, engagement):
        frame = captured_daily_cards()
        frame.tokens = [t for t in frame.tokens if not any(label in t.text for label in TASK_LABELS[task])]
        if engagement is not None:
            frame.tokens.append(token(f"当前活跃度{engagement}/400", .35, .30))
        return frame

    def verify(self, task, before, after_frame):
        context = WorkflowContext([after_frame])
        workflow = DailyWorkflow(context)
        if before is not None:
            workflow._task_before[task] = before
        with patch.object(workflow, "_open_daily", return_value=after_frame):
            result = workflow._verify_task_card(task, world_with_npc(), "Needs specific daily evidence")
        return result, context

    def test_completed_card_removed_and_activity_increased_one_hundred_proves_task(self):
        for task in ("coffee", "scratch", "video"):
            with self.subTest(task=task):
                result, context = self.verify(task, 100, self.after_task(task, 200))
                self.assertEqual(result.status, "ok")
                self.assertIn("100", result.detail)
                self.assertIn("200", result.detail)
                self.assertFalse(context.report.daily_verified)

    def test_removed_card_without_both_activity_readings_remains_partial(self):
        for before, after in ((None, 200), (100, None), (None, None)):
            with self.subTest(before=before, after=after):
                result, _ = self.verify("scratch", before, self.after_task("scratch", after))
                self.assertEqual(result.status, "partial")

    def test_removed_card_without_one_hundred_increase_remains_partial(self):
        for after in (0, 100, 150):
            with self.subTest(after=after):
                result, _ = self.verify("scratch", 100, self.after_task("scratch", after))
                self.assertEqual(result.status, "partial")

    def test_activity_increase_does_not_override_present_incomplete_card(self):
        frame = captured_daily_cards()
        frame.tokens.append(token("当前活跃度200/400", .35, .30))
        result, _ = self.verify("scratch", 100, frame)
        self.assertEqual(result.status, "partial")

    def test_ambiguous_duplicate_titles_are_not_a_removed_card(self):
        frame = captured_daily_cards()
        frame.tokens.extend((token("进行一次刮刮乐", .47, .50), token("当前活跃度200/400", .35, .30)))
        result, _ = self.verify("scratch", 100, frame)
        self.assertEqual(result.status, "partial")

    def test_completed_same_column_card_needs_no_activity_ocr(self):
        frame = captured_daily_cards()
        frame.tokens = [Token("进度：1/1", t.score, t.box)
                        if t.text == "进度：0/1" and abs(t.center[0] - .63672) < .02 else t
                        for t in frame.tokens]
        result, _ = self.verify("scratch", None, frame)
        self.assertEqual(result.status, "ok")


class MapAndInteractionTests(unittest.TestCase):
    def test_captured_new_m_map_is_known_without_legacy_place_names(self):
        frame = captured_m_map()
        workflow = DailyWorkflow(WorkflowContext([frame]))
        self.assertTrue(workflow._map(frame))
        self.assertTrue(workflow._known(frame))

    def test_map_region_names_without_destination_bar_are_unverified(self):
        frame = captured_m_map()
        frame.tokens = [t for t in frame.tokens if t.center[1] < 0.9]
        self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._map(frame))

    def test_new_destination_bar_without_region_names_is_unverified(self):
        frame = captured_m_map()
        frame.tokens = [t for t in frame.tokens if t.center[1] > 0.9]
        self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._map(frame))

    def test_duplicate_single_destination_name_does_not_prove_map_bar(self):
        frame = scene()
        frame.tokens = [token("录像店", 0.4, 0.5), token("奥蒙德研究站", 0.6, 0.5),
                        token("吼吼饼铺", 0.3, 0.96), token("吼吼饼铺", 0.5, 0.96)]
        self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._map(frame))

    def test_quick_navigation_requires_all_three_anchors(self):
        anchors = [token("快捷导航", 0.86, 0.04), token("区域信息", 0.8, 0.2), token("区域切换", 0.1, 0.95)]
        for omit in (None, 0, 1, 2):
            frame = scene()
            frame.tokens = [t for index, t in enumerate(anchors) if index != omit]
            with self.subTest(omit=omit):
                self.assertEqual(DailyWorkflow(WorkflowContext([frame]))._map(frame), omit is None)

    def test_quick_navigation_title_outside_header_does_not_prove_page(self):
        frame = scene()
        frame.tokens = [token("快捷导航", 0.5, 0.5), token("区域信息", 0.8, 0.2), token("区域切换", 0.1, 0.95)]
        self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._map(frame))

    def test_known_new_map_can_return_to_world_with_one_escape(self):
        context = WorkflowContext([captured_m_map(), world_with_npc()])
        result = DailyWorkflow(context).ensure_main()
        self.assertEqual(result.status, "ok")
        self.assertEqual(context.actions, [("key", "esc")])

    def test_f2_is_not_misread_as_f_interaction(self):
        frame = world_with_npc("汀曼")
        self.assertFalse(DailyWorkflow(WorkflowContext([frame]))._interaction(frame))

    def test_visible_ocr_interaction_prompt_needs_no_template(self):
        frame = world_with_npc("汀曼", interaction=True)
        context = WorkflowContext([frame])
        self.assertTrue(DailyWorkflow(context)._interaction(frame))
        context.template.assert_not_called()

    def test_template_fallback_checks_calibrated_bottom_prompt_roi(self):
        frame = world_with_npc("汀曼")
        context = WorkflowContext([frame])
        context.template.return_value = (1450 / 1920, 1030 / 1080, 0.99)
        self.assertTrue(DailyWorkflow(context)._interaction(frame))
        args = context.template.call_args.args
        self.assertEqual(args[1], "interact_f")
        area = args[2]
        self.assertTrue(area[0] <= 1450 / 1920 <= area[2] and area[1] <= 1030 / 1080 <= area[3])
        self.assertGreater(area[1], 0.9)

    def test_template_prompt_with_bound_npc_interacts_without_movement(self):
        daily, world = captured_daily_cards(), world_with_npc("汀曼")
        coffee = scene()
        coffee.tokens = [token("点单", 0.92, 0.9), token("汀曼特调", 0.45, 0.7)]
        context = WorkflowContext([daily, daily, world, coffee])
        context.template.side_effect = lambda frame, name, _area, _threshold: (
            (1450 / 1920, 1030 / 1080, 0.99) if frame is world and name == "interact_f" else None)
        result = DailyWorkflow(context)._daily_jump("coffee")
        self.assertIsInstance(result, Scene)
        self.assertIn(("key", "f"), context.actions)
        self.assertNotIn(("key", "w"), context.actions)

    def test_f_template_without_target_npc_never_interacts(self):
        daily, world = captured_daily_cards(), world_with_npc()
        context = WorkflowContext([daily, daily, world])
        context.template.side_effect = lambda _frame, name, _area, _threshold: (
            (1450 / 1920, 1030 / 1080, 0.99) if name == "interact_f" else None)
        result = DailyWorkflow(context)._daily_jump("coffee")
        self.assertEqual(result.status, "partial")
        self.assertFalse(any(action[0] == "key" and action[1] in ("w", "f") for action in context.actions))

    def test_absent_f_prompt_limits_approach_and_never_blindly_interacts(self):
        daily, world = captured_daily_cards(), world_with_npc("汀曼")
        context = WorkflowContext([daily, daily, world, world])
        result = DailyWorkflow(context)._daily_jump("coffee")
        self.assertEqual(result.status, "partial")
        self.assertEqual(context.actions.count(("key", "w")), 1)
        self.assertNotIn(("key", "f"), context.actions)

    def test_bakery_alias_arrival_remains_partial_until_daily_verified(self):
        daily = captured_daily_cards()
        world = world_with_npc("吼吼先生", interaction=True)
        bakery = scene("每日可领取一次", "查看今天")
        context = WorkflowContext([daily, daily, daily, world, bakery])
        result = DailyWorkflow(context).scratch()
        self.assertEqual(result.status, "partial")
        self.assertIn(("key", "f"), context.actions)
        self.assertFalse(any(action[0] == "drag" for action in context.actions))
        self.assertFalse(context.report.daily_verified)

    def test_bakery_reward_then_unique_card_confirmation_requires_daily_verification(self):
        reward = scene()
        reward.tokens = [token("获得奖励", .5, .45), token("确认", .5, .65)]
        card = scene()
        card.tokens = [token("Roarin", .5, .65), token("确定", .5, .81), token("分享", .94, .88)]
        for completed in (False, True):
            daily = captured_daily_cards()
            if completed:
                daily.tokens = [Token("进度：1/1", t.score, t.box)
                                if t.text == "进度：0/1" and abs(t.center[0] - .63672) < .02 else t
                                for t in daily.tokens]
            context = WorkflowContext([reward, card, world_with_npc()])
            workflow = DailyWorkflow(context)
            with self.subTest(completed=completed), patch.object(workflow, "_open_daily", return_value=daily):
                result = workflow.scratch()
                self.assertEqual(result.status, "ok" if completed else "partial")
                self.assertEqual(context.actions, [("click", "确认"), ("click", "确定")])
                self.assertIn("bakery_reward", context.report.evidence)
                self.assertIn("bakery_card", context.report.evidence)
                self.assertFalse(context.report.daily_verified)

    def test_bakery_reward_without_calibrated_card_never_confirms_unrelated_button(self):
        reward = scene()
        reward.tokens = [token("获得奖励", .5, .45), token("确认", .5, .65)]
        context = WorkflowContext([reward, scene("确定")])
        workflow = DailyWorkflow(context)
        with patch.object(workflow, "_open_daily") as reopen:
            result = workflow.scratch()
            self.assertEqual(result.status, "partial")
            reopen.assert_not_called()
        self.assertEqual(context.actions, [("click", "确认")])

    def test_self_captured_f_template_matches_at_1080p_and_4k(self):
        import cv2
        import numpy as np

        path = Path(__file__).resolve().parents[1] / "assets" / "templates" / "interact_f.png"
        template = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(template, "The calibrated F asset must be included in source and packaging")
        vision = Vision.__new__(Vision)  # No OCR model or game is loaded.
        area = (1400 / 1920, 990 / 1080, 1500 / 1920, 1070 / 1080)
        for scale in (1, 2):
            with self.subTest(scale=scale):
                frame = world_with_npc("汀曼")
                image = np.random.default_rng(5).integers(0, 30, (1080 * scale, 1920 * scale, 3), dtype=np.uint8)
                anchor = cv2.resize(template, None, fx=scale, fy=scale)
                height, width = anchor.shape[:2]
                x, y = round(1450 * scale - width / 2), round(1030 * scale - height / 2)
                image[y:y + height, x:x + width] = anchor
                frame.image = image
                match = vision.template(frame, path, area, threshold=0.90)
                self.assertIsNotNone(match)
                self.assertAlmostEqual(match[0], 1450 / 1920, places=4)
                self.assertAlmostEqual(match[1], 1030 / 1080, places=4)

    def test_real_f_asset_outside_interaction_roi_does_not_authorize_prompt(self):
        import cv2
        import numpy as np

        path = Path(__file__).resolve().parents[1] / "assets" / "templates" / "interact_f.png"
        template = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        image = np.random.default_rng(7).integers(0, 30, (1080, 1920, 3), dtype=np.uint8)
        height, width = template.shape[:2]
        image[400:400 + height, 600:600 + width] = template
        frame = world_with_npc("汀曼")
        frame.image = image
        area = (1400 / 1920, 990 / 1080, 1500 / 1920, 1070 / 1080)
        self.assertIsNone(Vision.__new__(Vision).template(frame, path, area, threshold=0.90))


if __name__ == "__main__":
    unittest.main(verbosity=2)
