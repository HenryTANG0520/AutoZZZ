from types import SimpleNamespace
import unittest

from autozzz.onedragon_battle_hud import attack_hud_scope, observe_attack_hud, scoped_attack_hud


class NativeHud:
    area_btn_normal = 'normal'
    area_btn_ultimate = 'ultimate'

    def __init__(self):
        self.matches = []

    def is_normal_attack_btn_available(self, screen):
        return screen.available

    def _match_btn_ban_gray(self, screen, area, template):
        self.matches.append((area, template))
        return getattr(screen, area)


class AttackHudTests(unittest.TestCase):
    def test_legacy_context_retains_ordinary_hud_observation(self):
        battle = SimpleNamespace(is_normal_attack_btn_available=lambda screen: screen)
        observation = observe_attack_hud(battle, True, 1.0)
        self.assertTrue(observation.in_battle)
        self.assertFalse(observation.blocks_ordinary)

    def test_same_frame_reuses_native_matches_but_new_timestamp_resamples(self):
        battle = NativeHud()
        screen = SimpleNamespace(available=True, normal=False, ultimate=True)
        first = observe_attack_hud(battle, screen, 1.0)
        self.assertTrue(first.in_battle)
        self.assertTrue(first.blocks_ordinary)
        with attack_hud_scope(first):
            self.assertIs(observe_attack_hud(battle, screen, 1.0), first)
            self.assertEqual(len(battle.matches), 2)
            self.assertEqual(first.disabled_buttons([('ultimate', 'ultimate', 'btn_ban_ultimate')]),
                             {'ultimate': True})
            screen.ultimate = False
            second = observe_attack_hud(battle, screen, 1.005)
            self.assertFalse(second.blocks_ordinary)
            self.assertEqual(len(battle.matches), 4)
            self.assertIs(scoped_attack_hud(battle, screen), first)
            self.assertIsNone(scoped_attack_hud(NativeHud(), screen))
        self.assertIsNone(scoped_attack_hud(battle, screen))

    def test_matching_the_same_template_in_another_region_rechecks_pixels(self):
        battle = NativeHud()
        screen = SimpleNamespace(available=True, normal=False, ultimate=True, other=False)
        observation = observe_attack_hud(battle, screen, 1.0)
        template = 'btn_ban_ultimate'
        self.assertEqual(observation.disabled_buttons([
            ('ultimate', battle.area_btn_ultimate, template), ('other', 'other', template),
        ]), {'ultimate': True, 'other': False})
        self.assertEqual(battle.matches[-1], ('other', template))
        self.assertEqual(len(battle.matches), 3)
