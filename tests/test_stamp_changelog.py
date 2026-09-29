import importlib.util
import unittest
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'stamp_changelog.py'
SPEC = importlib.util.spec_from_file_location('stamp_changelog', PATH)
STAMP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAMP)


class StamperTests(unittest.TestCase):
    def test_initial_version(self):
        self.assertEqual(STAMP.next_version('# Changelog', 25), 'v0.0.1')

    def test_nine_items_bumps_patch(self):
        text = '## [v1.4.7 — August 1, 2026]\n'
        self.assertEqual(STAMP.next_version(text, 9), 'v1.4.8')
        self.assertIn('Patch bump', STAMP.bump_reason(9))

    def test_ten_items_bumps_minor(self):
        text = '## [v1.4.7 — August 1, 2026]\n'
        self.assertEqual(STAMP.next_version(text, 10), 'v1.5.0')
        self.assertIn('Minor bump', STAMP.bump_reason(10))

    def test_two_part_versions_are_supported(self):
        text = '## [v2.6 — August 1, 2026]\n'
        self.assertEqual(STAMP.next_version(text, 2), 'v2.6.1')


if __name__ == '__main__':
    unittest.main()
