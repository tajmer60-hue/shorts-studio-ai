from pathlib import Path
import tempfile
import unittest
from render import publish_no_replace


class PublishTests(unittest.TestCase):
    def test_concurrent_output_is_preserved(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            source, target = Path(folder) / 'temporary.mp4', Path(folder) / 'final.mp4'
            source.write_bytes(b'new render')
            target.write_bytes(b'created by someone else')
            with self.assertRaises(FileExistsError):
                publish_no_replace(source, target)
            self.assertEqual(target.read_bytes(), b'created by someone else')
            self.assertEqual(source.read_bytes(), b'new render')

    def test_new_output_is_published(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            source, target = Path(folder) / 'temporary.mp4', Path(folder) / 'final.mp4'
            source.write_bytes(b'new render')
            publish_no_replace(source, target)
            self.assertEqual(target.read_bytes(), b'new render')
