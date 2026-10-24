"""Real FFmpeg proof that Smart Reframe tracks and zooms instead of dead-centring.

A 640x360 clip carries an 80x80 bright object near the left edge. A centred or
fit-inside render would show that object small or cut off; the reframe render
must crop to it, so the object occupies a large share of the vertical frame.
"""
from pathlib import Path
import json
import subprocess
import tempfile
import threading
import unittest

import numpy as np

from montage_render import render_montage
from reframe import plan_reframe
from render import executable


def bright_fraction(video, at_seconds):
    import cv2
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
        png = Path(temp) / 'frame.png'
        subprocess.run([executable('ffmpeg'), '-v', 'error', '-y', '-ss', str(at_seconds),
                        '-i', str(video), '-frames:v', '1', str(png)],
                       check=True, capture_output=True)
        image = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        return float((image > 128).mean())


def make_clip(root, frames=30):
    import cv2
    folder = root / 'frames'
    folder.mkdir()
    for index in range(frames):
        x = 80 + int(360 * index / (frames - 1))
        image = np.zeros((360, 640), dtype=np.uint8)
        image[140:220, x:x + 80] = 255
        cv2.imwrite(str(folder / f'frame-{index:03d}.png'), image)
    source = root / 'subject.mp4'
    subprocess.run([executable('ffmpeg'), '-v', 'error', '-y', '-framerate', '30',
                    '-i', str(folder / 'frame-%03d.png'),
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
                    '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', '-shortest',
                    str(source)], check=True, capture_output=True)
    return source


class ReframeRenderTests(unittest.TestCase):
    def test_camera_follows_the_subject_and_zooms_to_it(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            source = make_clip(root)
            # Subject positions are known from how the clip was built.
            samples = [(0.0, [(80.0, 140.0, 80.0, 80.0)]),
                       (0.5, [(260.0, 140.0, 80.0, 80.0)]),
                       (1.0, [(440.0, 140.0, 80.0, 80.0)])]
            plan = plan_reframe(640, 360, samples, 1.0, fps=30)
            self.assertEqual(plan['background_fill_times'], [])
            candidate = dict(title='Камера ведёт объект', rationale='Проверка сопровождения.',
                             source_segments=[dict(**{'in': 0.0, 'out': 1.0})])
            video = render_montage(source, candidate, dict(words=[]), root / 'renders',
                                   threading.Event(), reframe=plan)
            data = json.loads(subprocess.check_output([executable('ffprobe'), '-v', 'error',
                                                       '-show_format', '-show_streams', '-of', 'json',
                                                       str(video)], text=True, timeout=60))
            stream = next(s for s in data['streams'] if s['codec_type'] == 'video')
            self.assertEqual((stream['width'], stream['height']), (720, 1280))
            self.assertAlmostEqual(float(data['format']['duration']), 1.0, delta=0.2)
            # The object fills a large share of the tracked vertical frame...
            tracked = bright_fraction(video, 0.5)
            self.assertGreater(tracked, 0.05, f'Объект не приближен: доля {tracked:.4f}')
            # ...while the same object is tiny in a fit-inside (background fill) render.
            filled = render_montage(source, dict(candidate, title='Без сопровождения'),
                                    dict(words=[]), root / 'renders_fill', threading.Event())
            self.assertLess(bright_fraction(filled, 0.5), 0.03)
            self.assertGreater(tracked, bright_fraction(filled, 0.5) * 3)

    def test_untrackable_subject_falls_back_to_fill_not_black_bars(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            source = make_clip(root)
            # A subject spanning the whole width cannot fit a vertical window.
            samples = [(0.0, [(0.0, 100.0, 640.0, 200.0)]), (1.0, [(0.0, 100.0, 640.0, 200.0)])]
            plan = plan_reframe(640, 360, samples, 1.0, fps=30)
            self.assertEqual(plan['background_fill_times'], [0.0, 1.0])
            candidate = dict(title='Широкий план', rationale='Проверка фона.',
                             source_segments=[dict(**{'in': 0.0, 'out': 1.0})])
            video = render_montage(source, candidate, dict(words=[]), root / 'renders',
                                   threading.Event(), reframe=plan)
            data = json.loads(subprocess.check_output([executable('ffprobe'), '-v', 'error',
                                                       '-show_streams', '-of', 'json', str(video)],
                                                      text=True, timeout=60))
            stream = next(s for s in data['streams'] if s['codec_type'] == 'video')
            self.assertEqual((stream['width'], stream['height']), (720, 1280))
            # Background fill keeps content across the whole frame: no black bars.
            self.assertGreater(bright_fraction(video, 0.5), 0.0)


if __name__ == '__main__':
    unittest.main()
