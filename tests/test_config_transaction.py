import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autozzz.config_transaction import atomic_apply, config_revision


class Config:
    def __init__(self, path):
        self.file_path = str(path)
        self.data = {"enabled": False}
        self.rows = []

    def _get_write_path(self):
        return self.file_path

    def update(self, name, value):
        self.data[name] = value
        self.save()

    def save(self):
        self.data["rows"] = self.rows
        Path(self._get_write_path()).write_text(json.dumps(self.data), encoding="utf-8")


class ConfigurationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.target = self.root/"settings.yml"
        self.config = Config(self.target)
        self.config.save()
        self.original = self.target.read_bytes()
        self.revision = config_revision(self.config)

    def apply(self):
        self.config.update("enabled", True)
        self.config.rows = ["plan"]  # Specialized save must synchronize this collection.

    def test_domain_changes_publish_together_using_specialized_save(self):
        import os
        with patch("autozzz.config_transaction.os.replace", wraps=os.replace) as publish:
            atomic_apply(self.config, self.revision, self.apply, self.root)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(json.loads(self.target.read_text()), {"enabled": True, "rows": ["plan"]})
        self.assertNotIn("save", vars(self.config))
        self.assertNotIn("_get_write_path", vars(self.config))

    def test_failed_publish_keeps_original_file_and_in_memory_state(self):
        with patch("autozzz.config_transaction.os.replace", side_effect=OSError("locked")):
            with self.assertRaisesRegex(OSError, "locked"):
                atomic_apply(self.config, self.revision, self.apply, self.root)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.config.data, {"enabled": False, "rows": []})
        self.assertEqual(self.config.rows, [])
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_stale_revision_never_calls_domain_setters(self):
        self.target.write_text("other edit")
        with self.assertRaisesRegex(ValueError, "其他窗口"):
            atomic_apply(self.config, self.revision, lambda: self.fail("must not apply"), self.root)
        self.assertEqual(self.target.read_text(), "other edit")

    def test_edit_during_serialization_is_not_overwritten(self):
        original_save = self.config.save
        def interrupted_save():
            original_save()
            self.target.write_text("concurrent edit")
        self.config.save = interrupted_save
        with self.assertRaisesRegex(ValueError, "保存期间"):
            atomic_apply(self.config, self.revision, self.apply, self.root)
        self.assertEqual(self.target.read_text(), "concurrent edit")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_serialization_failure_leaves_no_partial_file(self):
        def invalid():
            self.config.update("enabled", True)
            self.config.rows = [object()]
        with self.assertRaises(TypeError):
            atomic_apply(self.config, self.revision, invalid, self.root)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.config.rows, [])
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_path_outside_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "数据目录"):
            atomic_apply(self.config, self.revision, self.apply, self.root/"another-profile")
        self.assertEqual(self.target.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
