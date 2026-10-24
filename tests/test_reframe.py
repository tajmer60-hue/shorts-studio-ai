"""Smart Reframe tests: containment, aspect, speed limits, fallback honesty.

The camera math is verified with synthetic keyframes; the local saliency source
is verified against a real FFmpeg-generated clip with a moving bright rectangle.
No detector pretends to see faces: saliency is always low confidence.
"""
from pathlib import Path
import json
import subprocess
import tempfile
import threading
import unittest

from render import executable
from reframe import (ASPECT, BoxSource, ProviderBoxSource, SaliencyBoxSource, camera_path,
                     clamp_to_source, crop_containing, merge_subjects, needs_background_fill,
                     plan_reframe, subtitle_safe_rect, to_filter_expressions)

W, H = 1920, 1080


class CropTests(unittest.TestCase):
    def test_crop_contains_the_subject_and_keeps_the_aspect(self):
        crop = crop_containing((800, 300, 200, 300), W, H)
        self.assertIsNotNone(crop)
        self.assertLessEqual(crop['x'], 800)
        self.assertGreaterEqual(crop['x'] + crop['w'], 1000)
        self.assertLessEqual(crop['y'], 300)
        self.assertGreaterEqual(crop['y'] + crop['h'], 600)
        self.assertAlmostEqual(crop['w'] / crop['h'], ASPECT, places=2)
        self.assertLessEqual(crop['x'] + crop['w'], W + 1)
        self.assertLessEqual(crop['y'] + crop['h'], H + 1)

    def test_subject_wider_than_any_vertical_window_is_honest_none(self):
        # A 1500px-wide subject cannot fit a 607px-wide vertical window.
        self.assertIsNone(crop_containing((200, 300, 1500, 400), W, H))
        self.assertTrue(needs_background_fill([(200, 300, 1500, 400)], W, H))

    def test_malformed_boxes_are_rejected(self):
        for bad in ((0, 0, 0, 10), (0, 0, 10, 0), (0, 0, float('nan'), 10), (0, 0, 10, None)):
            with self.assertRaises(ValueError):
                crop_containing(bad, W, H)

    def test_two_nearby_speakers_share_one_window(self):
        boxes = [(500, 300, 150, 300), (760, 300, 150, 300)]
        crop = merge_subjects(boxes, W, H)
        self.assertIsNotNone(crop)
        self.assertLessEqual(crop['x'], 500)
        self.assertGreaterEqual(crop['x'] + crop['w'], 910)

    def test_two_distant_speakers_fall_back_instead_of_squeezing(self):
        boxes = [(50, 300, 150, 300), (1700, 300, 150, 300)]
        self.assertIsNone(merge_subjects(boxes, W, H))
        self.assertTrue(needs_background_fill(boxes, W, H))

    def test_empty_subject_list_is_not_a_crop(self):
        self.assertIsNone(merge_subjects([], W, H))

    def test_subtitle_safe_zone_is_inside_the_frame(self):
        zone = subtitle_safe_rect()
        self.assertGreaterEqual(zone['y'], 0)
        self.assertLessEqual(zone['y'] + zone['h'], 1.0)


class CameraTests(unittest.TestCase):
    LEFT = dict(x=100.0, y=0.0, w=607.0, h=1080.0)
    RIGHT = dict(x=1200.0, y=0.0, w=607.0, h=1080.0)

    def test_a_cut_between_speakers_becomes_a_move_not_a_jump(self):
        samples = [(0.0, self.LEFT), (1.0, self.LEFT), (1.01, self.RIGHT), (3.0, self.RIGHT)]
        path = camera_path(samples, 3.0, fps=30, max_pan_px_per_s=900)
        limit = 900 / 30 + 1e-6
        for a, b in zip(path, path[1:]):
            self.assertLessEqual(abs(b['x'] - a['x']), limit)
            self.assertLessEqual(abs(b['y'] - a['y']), limit)
        # The camera did travel toward the new speaker, just smoothly.
        self.assertGreater(path[-1]['x'], path[0]['x'])

    def test_aspect_is_preserved_every_frame(self):
        samples = [(0.0, self.LEFT), (2.0, self.RIGHT)]
        for frame in camera_path(samples, 2.0, fps=30):
            self.assertAlmostEqual(frame['w'] / frame['h'], ASPECT, places=6)

    def test_zoom_rate_is_limited_too(self):
        small = dict(x=500.0, y=200.0, w=300.0, h=300 / ASPECT)
        big = dict(x=200.0, y=0.0, w=607.0, h=1080.0)
        path = camera_path([(0.0, small), (0.05, big)], 1.0, fps=30, max_zoom_px_per_s=600)
        limit = 600 / 30 + 1e-6
        for a, b in zip(path, path[1:]):
            self.assertLessEqual(abs(b['w'] - a['w']), limit)

    def test_camera_never_leaves_the_frame(self):
        samples = [(0.0, dict(x=-400.0, y=-300.0, w=607.0, h=1080.0)),
                   (1.0, dict(x=5000.0, y=4000.0, w=607.0, h=1080.0))]
        for frame in clamp_to_source(camera_path(samples, 1.0, fps=30), W, H):
            self.assertGreaterEqual(frame['x'], -1e-6)
            self.assertGreaterEqual(frame['y'], -1e-6)
            self.assertLessEqual(frame['x'] + frame['w'], W + 1e-6)
            self.assertLessEqual(frame['y'] + frame['h'], H + 1e-6)

    def test_invalid_duration_is_refused(self):
        with self.assertRaises(ValueError):
            camera_path([(0.0, self.LEFT)], 0)
        with self.assertRaises(ValueError):
            camera_path([], 1.0)

    def test_expressions_cover_every_frame_exactly_once(self):
        samples = [(0.0, self.LEFT), (1.5, self.RIGHT), (3.0, self.RIGHT)]
        path = camera_path(samples, 3.0, fps=30)
        segments = to_filter_expressions(path, fps=30)
        self.assertEqual(segments[0]['frame_start'], 0)
        self.assertEqual(segments[-1]['frame_end'], len(path) - 1)
        for a, b in zip(segments, segments[1:]):
            self.assertEqual(a['frame_end'] + 1, b['frame_start'])
        self.assertAlmostEqual(segments[-1]['t_end'], 3.0, places=3)


class PlanTests(unittest.TestCase):
    def test_plan_reports_fill_times_and_frame_count(self):
        good = dict(x=500.0, y=0.0, w=607.0, h=1080.0)
        samples = [(0.0, [(600, 300, 150, 300)]), (1.0, [(600, 300, 150, 300)]),
                   (2.0, [(100, 300, 1500, 400)])]
        plan = plan_reframe(W, H, samples, 3.0, fps=30)
        self.assertEqual(plan['frames'], 90)
        self.assertEqual(plan['target'], [1080, 1920])
        self.assertEqual(plan['background_fill_times'], [2.0])
        self.assertEqual(len(plan['path']), 90)
        self.assertTrue(plan['limitations'])
        self.assertTrue(any('низкая уверенность' in line for line in plan['limitations']))

    def test_plan_without_any_subject_still_yields_a_valid_path(self):
        plan = plan_reframe(W, H, [(0.0, []), (1.0, [])], 1.0, fps=30)
        self.assertEqual(plan['frames'], 30)
        for frame in plan['path']:
            self.assertGreater(frame['w'], 0)
            self.assertAlmostEqual(frame['w'] / frame['h'], ASPECT, places=2)


class SaliencyTests(unittest.TestCase):
    def test_local_saliency_follows_a_moving_object_and_stays_low_confidence(self):
        self.assertEqual(SaliencyBoxSource.CONFIDENCE, 'low')
        import numpy as np
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            frames = root / 'frames'
            frames.mkdir()
            import cv2 as _cv2
            for index in range(30):
                x = 80 + int(360 * index / 29)
                image = np.zeros((360, 640), dtype=np.uint8)
                image[140:220, x:x + 80] = 255
                _cv2.imwrite(str(frames / f'frame-{index:03d}.png'), image)
            source = root / 'moving.mp4'
            subprocess.run([executable('ffmpeg'), '-v', 'error', '-y', '-framerate', '30',
                            '-i', str(frames / 'frame-%03d.png'), '-c:v', 'libx264',
                            '-pix_fmt', 'yuv420p', str(source)], check=True, capture_output=True)
            boxes = SaliencyBoxSource(grid=8).sample(source, [0.0, 0.5, 0.9],
                                                     640, 360, threading.Event())
            self.assertEqual(len(boxes), 3)
            xs = []
            for _stamp, frame_boxes in boxes:
                self.assertTrue(frame_boxes, 'Существенная область не найдена.')
                xs.append(frame_boxes[0][0])
            # The bright rectangle moves right across the samples.
            self.assertGreater(xs[-1], xs[0])

    def test_cancel_is_honoured(self):
        stop = threading.Event()
        stop.set()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            source = Path(temp) / 'moving.mp4'
            subprocess.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi',
                            '-i', 'color=c=black:size=64x64:rate=30:duration=1',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)],
                           check=True, capture_output=True)
            with self.assertRaises(ValueError):
                SaliencyBoxSource().sample(source, [0.1], 64, 64, stop)

    def test_provider_source_refuses_planners_without_a_json_contract(self):
        class NoJson:
            pass
        with self.assertRaises(ValueError):
            ProviderBoxSource(NoJson()).sample('x.mp4', [0.0], W, H)

    def test_base_source_is_abstract(self):
        with self.assertRaises(NotImplementedError):
            BoxSource().sample('x', [0], W, H)


if __name__ == '__main__':
    unittest.main()
