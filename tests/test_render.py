from pathlib import Path
import tempfile
import unittest
from render import build_command


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.folder.cleanup)
        self.source = Path(self.folder.name) / 'film with spaces & symbols.mkv'
        self.source.touch()
        self.target = Path(self.folder.name) / 'short.mp4'

    def command(self, **kwargs):
        params = dict(source=self.source, target=self.target, start=10, duration=30,
                      total=100, mode='fit', ffmpeg='ffmpeg')
        params.update(kwargs)
        return build_command(**params)

    def test_paths_are_separate_arguments_and_audio_is_optional(self):
        command = self.command()
        self.assertIn(str(self.source), command)
        self.assertIn('0:a:0?', command)
        self.assertIn('-n', command)
        self.assertEqual(command[-1], str(self.target))

    def test_invalid_ranges(self):
        for changes in [dict(start=-1), dict(duration=0), dict(duration=101),
                        dict(start=float('nan')), dict(duration=float('inf'))]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.command(**changes)

    def test_existing_files_are_never_overwritten(self):
        with self.assertRaises(ValueError):
            self.command(target=self.source)
        self.target.touch()
        with self.assertRaises(ValueError):
            self.command()

    def test_crop_and_fit(self):
        self.assertIn('pad=1080:1920', self.command()[self.command().index('-vf') + 1])
        crop = self.command(mode='crop')
        self.assertIn('crop=1080:1920', crop[crop.index('-vf') + 1])


if __name__ == '__main__':
    unittest.main()
