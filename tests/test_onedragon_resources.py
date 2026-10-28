"""确认范围与消费清单的纯逻辑检查，不操作游戏。"""
import unittest
from types import SimpleNamespace

from autozzz.onedragon_resources import ResourceConfirmation, resource_actions, resource_fingerprint, resource_confirmation_label


class ResourceConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.actions = [dict(id="disc", app_id="drive_disc_dismantle", text="拆解A及以下", requires_confirmation=True)]
        self.confirmation = ResourceConfirmation()
        self.confirmation.prepare(self.actions, "prepared-settings")

    def test_confirmation_is_required_and_consumed_once(self):
        with self.assertRaisesRegex(ValueError, "确认"):
            self.confirmation.consume(None, "prepared-settings")
        token = self.confirmation.token
        self.confirmation.consume(token, "prepared-settings")
        self.assertEqual(self.confirmation.accepted, ["disc"])
        with self.assertRaisesRegex(ValueError, "复用"):
            self.confirmation.consume(token, "prepared-settings")

    def test_confirmation_does_not_authorize_changed_settings(self):
        token = self.confirmation.token
        with self.assertRaisesRegex(ValueError, "配置已变化"):
            self.confirmation.consume(token, "changed-settings")
        self.assertEqual(self.confirmation.token, token)

    def test_reprepare_invalidates_previous_confirmation(self):
        old = self.confirmation.token
        self.confirmation.prepare(self.actions, "prepared-settings")
        self.assertNotEqual(old, self.confirmation.token)
        with self.assertRaises(ValueError):
            self.confirmation.verify(old, "prepared-settings")

    def test_confirmation_snapshot_is_not_mutated_by_callers(self):
        self.actions[0]["text"] = "changed"
        snapshot = self.confirmation.status()
        snapshot["actions"][0]["text"] = "changed-again"
        self.assertEqual(self.confirmation.status()["actions"][0]["text"], "拆解A及以下")

    def test_free_actions_need_no_confirmation_and_reject_stale_token(self):
        self.confirmation.prepare([dict(id="hosting", requires_confirmation=False)], "free")
        self.confirmation.consume(None, "free")
        with self.assertRaises(ValueError):
            self.confirmation.verify("previous-sensitive-token", "free")
        for token in (True, [], 0):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "文本或空值"):
                self.confirmation.verify(token, "free")


class ResourceActionTests(unittest.TestCase):
    def test_discarded_items_expand_scope_beyond_quality(self):
        config = SimpleNamespace(dismantle_level="A及以下", dismantle_abandon=True)
        action, = resource_actions("drive_disc_dismantle", config)
        self.assertIn("额外包含全部已弃置", action["text"])
        self.assertIn("无法恢复", action["text"])

    def test_hosting_does_not_claim_ignored_manual_or_pawnshop_actions(self):
        config = SimpleNamespace(auto_manage_enabled=True, yum_cha_sin=True, yum_cha_sin_period_refresh=True,
                                 good_goods_purchase_enabled=False, boo_box_purchase_enabled=False,
                                 pawnshop_crest_enabled=True, pawnshop_omnicoin_enabled=True)
        actions = resource_actions("suibian_temple", config)
        self.assertEqual([item["id"] for item in actions], ["suibian_temple:hosting"])
        self.assertFalse(actions[0]["requires_confirmation"])

    def test_manufacturing_and_procurement_still_consume_without_shopping(self):
        config = SimpleNamespace(auto_manage_enabled=False, yum_cha_sin=True, yum_cha_sin_period_refresh=False,
                                 good_goods_purchase_enabled=False, boo_box_purchase_enabled=False)
        actions = resource_actions("suibian_temple", config)
        self.assertEqual(len(actions), 2)
        self.assertTrue(all(item["requires_confirmation"] for item in actions))
        self.assertIn("刷新关闭", actions[1]["text"])

    def test_invalid_switch_and_quality_are_not_silently_interpreted(self):
        with self.assertRaises(ValueError):
            resource_actions("drive_disc_dismantle", SimpleNamespace(dismantle_level="invalid", dismantle_abandon=False))
        with self.assertRaises(ValueError):
            resource_actions("suibian_temple", SimpleNamespace(auto_manage_enabled="false"))

    def test_fingerprint_tracks_actual_property_values(self):
        class Config:
            enabled = False
            @property
            def good_goods_purchase_enabled(self): return self.enabled
        config = Config()
        before = resource_fingerprint(config)
        config.enabled = True
        self.assertNotEqual(resource_fingerprint(config), before)

    def test_irreversible_notice_remains_in_confirmation_control_outside_scrolling_list(self):
        disc = dict(app_id="drive_disc_dismantle", requires_confirmation=True)
        shop = dict(app_id="suibian_temple", requires_confirmation=True)
        self.assertIn("无法恢复", resource_confirmation_label([disc]))
        self.assertIn("消费并拆解", resource_confirmation_label([shop, disc]))
        self.assertNotIn("拆解", resource_confirmation_label([shop]))


if __name__ == "__main__": unittest.main()
