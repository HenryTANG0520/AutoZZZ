from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from autozzz.onedragon_bundle import BundleError, filesystem_path, use_verified_site_packages


@unittest.skipUnless(sys.platform == 'win32', 'Windows extended dependency paths')
class RuntimeDependencyPathsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.prefix = self.root / 'env'
        self.prefix.mkdir()
        self.site = self.prefix / 'Lib/site-packages'
        self.foreign = self.root / 'env-other/Lib/site-packages'

    def test_alias_preserves_order_and_other_search_paths(self):
        original = ['', str(self.foreign), str(self.site), str(self.site / 'win32'), 'Lib/site-packages']
        expected = [*original]
        expected[2] = str(filesystem_path(self.site))
        with patch.object(sys, 'prefix', str(self.prefix)), patch.object(sys, 'path', original.copy()):
            use_verified_site_packages(self.prefix)
            self.assertEqual(sys.path, expected)
            use_verified_site_packages(self.prefix)
            self.assertEqual(sys.path, expected)

    def test_missing_site_entry_is_not_inserted(self):
        original = [str(self.foreign), 'Lib/site-packages']
        with patch.object(sys, 'prefix', str(self.prefix)), patch.object(sys, 'path', original.copy()):
            use_verified_site_packages(self.prefix)
            self.assertEqual(sys.path, original)

    def test_unexpected_interpreter_prefix_is_rejected_before_path_changes(self):
        original = [str(self.foreign), str(self.site)]
        with patch.object(sys, 'prefix', str(self.root)), patch.object(sys, 'path', original.copy()):
            with self.assertRaisesRegex(BundleError, '已核验运行时'):
                use_verified_site_packages(self.prefix)
            self.assertEqual(sys.path, original)
