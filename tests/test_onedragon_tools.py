"""工具准备的输入约束；不导入上游运行时或操作设备。"""
from types import SimpleNamespace
import unittest

from autozzz.onedragon_assistant import validate_shortcuts, virtual_key
from autozzz.onedragon_scripts import validate_definition


class ShortcutTests(unittest.TestCase):
    def test_supported_single_keys(self):
        for name, expected in (("5", 53), ("a", 65), ("F12", 123), ("numpad_7", 103),
                               ("mouse_x1", 5), ("ctrl_r", 163), ("space", 32)):
            with self.subTest(name=name):
                self.assertEqual(virtual_key(name), expected)

    def test_reserved_keys_are_rejected_case_insensitively(self):
        for name in ("F9", "f10"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "暂停和停止"):
                validate_shortcuts(SimpleNamespace(dodge_switch=name, auto_battle_switch="6"))

    def test_equivalent_or_duplicate_shortcuts_are_rejected(self):
        for first, second in (("5", "5"), ("F12", "f12"), ("a", "A")):
            with self.subTest(keys=(first, second)), self.assertRaisesRegex(ValueError, "不同快捷键"):
                validate_shortcuts(SimpleNamespace(dodge_switch=first, auto_battle_switch=second))

    def test_invalid_combo_and_names_are_rejected(self):
        for name in (None, "", "Ctrl+5", "F25", "mouse_unknown"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                virtual_key(name)

    def test_original_configured_names_are_retained(self):
        self.assertEqual(validate_shortcuts(SimpleNamespace(dodge_switch="F12", auto_battle_switch="6")), {"F12": 123, "6": 54})


class ScriptDefinitionTests(unittest.TestCase):
    def test_finite_nonnegative_durations_and_indefinite_press(self):
        validate_definition(SimpleNamespace(pre_delay=0, post_delay=.2, wait_seconds=3, btn_press=None, btn_repeat_times=1))

    def test_invalid_durations_do_not_reach_compilation(self):
        for field in ("pre_delay", "post_delay", "wait_seconds", "btn_press"):
            for value in (-1, float("nan"), float("inf"), True, "3"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_definition(SimpleNamespace(**{field: value}))

    def test_repeat_must_be_positive_integer(self):
        for value in (0, -1, 1.5, True, "2", None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "正整数"):
                validate_definition(SimpleNamespace(btn_repeat_times=value))

    def test_missing_delays_default_to_zero_but_explicit_null_is_invalid(self):
        validate_definition(SimpleNamespace())
        for field in ("pre_delay", "post_delay", "wait_seconds"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_definition(SimpleNamespace(**{field: None}))


if __name__ == "__main__":
    unittest.main()
