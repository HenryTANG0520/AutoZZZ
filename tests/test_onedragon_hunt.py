"""免费次数必须来自明确的画面数字，不能从混杂文本猜测。"""
import unittest

from autozzz.onedragon_hunt import parse_free_times


class FreeHuntTimesTests(unittest.TestCase):
    def test_current_weekly_range(self):
        for value in range(4):
            with self.subTest(value=value):
                self.assertEqual(parse_free_times(" " + str(value) + " "), value)

    def test_unknown_or_mixed_text_is_rejected(self):
        for value in (None, "", "剩余3", "3/3", "30", "4", "-1", "1.0", "3\n0", "O"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_free_times(value)


if __name__ == "__main__": unittest.main()
