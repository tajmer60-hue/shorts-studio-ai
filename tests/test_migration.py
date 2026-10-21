from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from migrate_to_d import copy_verified, guarded_remove, manifest


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'test.txt').write_text('keep me', encoding='utf-8')

    def test_copy_and_verify_preserves_source(self):
        target = self.root / 'destination'
        snapshot = copy_verified(self.source, target)
        self.assertEqual(snapshot, manifest(target))
        self.assertEqual((self.source / 'test.txt').read_text(encoding='utf-8'), 'keep me')

    def test_existing_destination_is_never_overwritten(self):
        target = self.root / 'destination'
        target.mkdir()
        with self.assertRaises(RuntimeError):
            copy_verified(self.source, target)

    def test_cleanup_outside_fixed_allowlist_is_rejected(self):
        with patch('migrate_to_d.shutil.rmtree') as remove:
            with self.assertRaises(RuntimeError):
                guarded_remove(self.source, self.root / 'destination', manifest(self.source))
            remove.assert_not_called()
        self.assertTrue(self.source.exists())
