from types import SimpleNamespace
import unittest

from autozzz.coffee_choice import choose_coffee


class CoffeeChoiceTests(unittest.TestCase):
    def test_simulation_pending_selects_red_tea(self):
        self.assertEqual(choose_coffee([dict(category_name='实战模拟室', plan_times=2, run_times=1)]), '鲜萃红茶')

    def test_other_training_and_empty_choose_eighty(self):
        for rows in ([], [dict(category_name='区域巡防')], [dict(category_name='专业挑战室')]):
            self.assertEqual(choose_coffee(rows), '汀曼特调')

    def test_completed_or_skipped_simulation_does_not_select_sixty(self):
        for extra in (dict(plan_times=1, run_times=1), dict(skipped=True)):
            self.assertEqual(choose_coffee([dict(category_name='实战模拟室', **extra)]), '汀曼特调')

    def test_mixed_and_native_plans_use_same_rule(self):
        rows = [SimpleNamespace(category_name='区域巡防'), SimpleNamespace(category_name='实战模拟室')]
        self.assertEqual(choose_coffee(rows), '鲜萃红茶')

    def test_looped_simulation_will_run_again(self):
        self.assertEqual(choose_coffee([dict(category_name='实战模拟室',plan_times=1,run_times=1)], loop=True), '鲜萃红茶')
