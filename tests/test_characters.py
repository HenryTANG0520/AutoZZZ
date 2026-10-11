"""Offline tests for name validation and backwards-compatible settings."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autozzz.characters import CHARACTER_NAMES, canonical_promoter_name, promoter_name_aliases
from autozzz.config import Settings

TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "runs" / "offline-tests"


class PromoterNameTests(unittest.TestCase):
    def test_catalogue_names_are_unique_and_valid(self):
        self.assertEqual(len(CHARACTER_NAMES), len(set(CHARACTER_NAMES)))
        for name in CHARACTER_NAMES:
            with self.subTest(name=name):
                self.assertEqual(canonical_promoter_name(name), name)

    def test_screenshot_card_names_are_accepted(self):
        for name in ("蕾米埃尔", "爱芮", "安比", "丝琪", "邦布18"):
            with self.subTest(name=name):
                self.assertEqual(canonical_promoter_name(name), name)

    def test_version_32_agents_are_accepted(self):
        for name in ("克拉蕾", "洛克茜"):
            with self.subTest(name=name):
                self.assertEqual(canonical_promoter_name(name), name)

    def test_whitespace_default_and_explicit_name(self):
        for value in ("", "  ", "\t\n", "\u3000"):
            self.assertEqual(canonical_promoter_name(value), "")
        self.assertEqual(canonical_promoter_name("  爱芮\u3000"), "爱芮")
        self.assertEqual(promoter_name_aliases(""), ())

    def test_verified_full_names_are_normalised(self):
        for value, canonical in (("安比·德玛拉", "安比"), ("蕾米埃尔・丹", "蕾米埃尔"),
                                 ("月城柳", "柳"), ("星见雅", "雅"), ("浅羽悠真", "悠真"),
                                 ("柚叶", "浮波柚叶"), ("邦布１８", "邦布18")):
            with self.subTest(value=value):
                self.assertEqual(canonical_promoter_name(value), canonical)

    def test_character_variants_do_not_match_by_substring(self):
        self.assertEqual(canonical_promoter_name("安比"), "安比")
        self.assertEqual(canonical_promoter_name("零号安比"), "零号·安比")
        self.assertEqual(canonical_promoter_name("银心锡兵·安比"), "零号·安比")
        self.assertNotIn("安比", promoter_name_aliases("零号·安比"))
        self.assertNotIn("零号·安比", promoter_name_aliases("安比"))
        self.assertEqual(canonical_promoter_name("比利"), "比利")
        self.assertEqual(canonical_promoter_name("星徽比利"), "星徽·比利")

    def test_aliases_return_only_the_requested_character(self):
        for name in CHARACTER_NAMES:
            for alias in promoter_name_aliases(name):
                with self.subTest(name=name, alias=alias):
                    self.assertEqual(canonical_promoter_name(alias), name)

    def test_typographical_error_is_rejected_even_with_suggestion(self):
        with self.assertRaisesRegex(ValueError, "蕾米埃尔"):
            canonical_promoter_name("蕾米艾尔")
        for value in ("安", "爱 芮", "星辉比利", "阿玖", "完全不存在的角色"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_promoter_name(value)

    def test_not_yet_released_agents_are_not_assumed_available(self):
        for value in ("菲欧妮", "赛维里安"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_promoter_name(value)

    def test_non_text_values_are_rejected(self):
        for value in (None, 0, True, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_promoter_name(value)


class PromoterConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)

    def test_old_json_without_preference_defaults_to_first(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"server": "cn"}), encoding="utf-8")
            self.assertEqual(Settings.load(path).promoter, "")

    def test_valid_preference_save_load_preserves_canonical_name(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            path = Path(directory) / "config.json"
            settings = Settings(promoter="  蕾米埃尔·丹  ")
            settings.save(path)
            self.assertEqual(settings.promoter, "蕾米埃尔")
            self.assertEqual(Settings.load(path).promoter, "蕾米埃尔")

    def test_invalid_preference_does_not_overwrite_good_settings(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            path = Path(directory) / "config.json"
            Settings(promoter="爱芮").save(path)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                Settings(promoter="爱锐").save(path)
            self.assertEqual(path.read_bytes(), before)

    def test_json_preference_requires_string(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            path = Path(directory) / "config.json"
            for value in (None, False, 18, [], {}):
                path.write_text(json.dumps({"promoter": value}), encoding="utf-8")
                with self.subTest(value=value), self.assertRaises(ValueError):
                    Settings.load(path)


if __name__ == "__main__":
    unittest.main()
