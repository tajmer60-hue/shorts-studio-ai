"""Style profile tests: measurements only, and honest about what is not measured."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

from render import executable
from style_profile import (cut_density, parse_volume, profile_from_metrics,
                           profile_from_reference, rank_with_style, style_targets)

METRICS = dict(duration=100.0, cuts=[2.0, 6.0, 50.0, 52.0, 54.0, 56.0, 58.0],
               estimated_shot_lengths=[2.0, 2.0, 2.0, 2.0, 2.0, 44.0, 42.0],
               black_intervals=[], subtitle_streams=[])
VOLUME = 'mean_volume: -13.8 dB\nmax_volume: 0.0 dB\n'


class ProfileTests(unittest.TestCase):
    def test_profile_is_built_from_measurements_only(self):
        profile = profile_from_metrics(METRICS, VOLUME)
        self.assertEqual(profile['schema'], 1)
        self.assertEqual(profile['cut_count'], 7)
        self.assertAlmostEqual(profile['cut_rate'], 0.07)
        self.assertEqual(profile['median_shot_seconds'], 2.0)
        self.assertEqual(profile['first_cut_seconds'], 2.0)
        self.assertEqual(profile['loudness'], dict(mean_db=-13.8, max_db=0.0))
        self.assertTrue(0.0 <= profile['climax_position'] <= 1.0)

    def test_climax_finds_the_dense_cluster(self):
        profile = profile_from_metrics(METRICS)
        # The dense run 50-58 sits around the middle of a 100 s reference.
        self.assertGreater(profile['climax_position'], 0.4)
        self.assertLess(profile['climax_position'], 0.7)

    def test_limitations_name_everything_that_is_not_measured(self):
        text = ' '.join(profile_from_metrics(METRICS)['limitations'])
        self.assertIn('OCR', text)
        self.assertIn('биты', text)
        self.assertIn('Переходы не классифицируются', text)

    def test_invalid_duration_is_refused(self):
        with self.assertRaises(ValueError):
            profile_from_metrics(dict(duration=0, cuts=[], estimated_shot_lengths=[]))

    def test_volume_parse_tolerates_absence(self):
        self.assertEqual(parse_volume(''), dict(mean_db=None, max_db=None))


class TargetTests(unittest.TestCase):
    def test_targets_transfer_pacing_not_the_work_itself(self):
        targets = style_targets(profile_from_metrics(METRICS))
        self.assertEqual(targets['target_shot_seconds'], 2.0)
        self.assertAlmostEqual(targets['target_cut_rate'], 0.5)
        self.assertIn('темп монтажа', targets['note'])

    def test_cut_density_is_cuts_per_second_of_selected_footage(self):
        self.assertAlmostEqual(cut_density([dict(**{'in': 0.0, 'out': 10.0})], [2.0, 6.0]), 0.2)
        self.assertEqual(cut_density([], [1.0]), 0.0)

    def test_ranking_prefers_the_reference_pacing_and_says_why(self):
        profile = profile_from_metrics(METRICS)          # median shot 2 s -> 0.5 cuts/s
        matching = dict(title='Плотный', rationale='База.', score=5.0,
                        source_segments=[dict(**{'in': 50.0, 'out': 60.0})])   # 5 cuts in 10 s
        calm = dict(title='Вязкий', rationale='База.', score=5.0,
                    source_segments=[dict(**{'in': 10.0, 'out': 50.0})])        # 0 cuts in 40 s
        ranked = rank_with_style([calm, matching], profile, METRICS['cuts'])
        self.assertEqual(ranked[0]['title'], 'Плотный')
        self.assertIn('Темп:', ranked[0]['rationale'])
        self.assertIn('эталона', ranked[0]['rationale'])
        self.assertGreater(ranked[0]['style_score'], ranked[1]['style_score'])

    def test_no_target_leaves_order_untouched(self):
        profile = dict(schema=1, cut_rate=None, median_shot_seconds=None)
        candidates = [dict(title='A', score=1), dict(title='B', score=2)]
        self.assertEqual([c['title'] for c in rank_with_style(candidates, profile, [1.0])],
                         ['A', 'B'])


class ReferencePipelineTests(unittest.TestCase):
    def test_profile_is_measured_once_and_cached_by_identity(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            source = root / 'ref.mp4'
            subprocess.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi',
                            '-i', 'testsrc2=size=320x180:rate=30:duration=4',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4',
                            '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)],
                           check=True, capture_output=True)
            folder = root / 'style'
            first = profile_from_reference(source, folder)
            self.assertEqual(first['schema'], 1)
            self.assertTrue((folder / 'style-profile.json').exists())
            stamp = (folder / 'style-profile.json').stat().st_mtime_ns
            second = profile_from_reference(source, folder)
            self.assertEqual(second['source_identity'], first['source_identity'])
            self.assertEqual((folder / 'style-profile.json').stat().st_mtime_ns, stamp)
            self.assertTrue(any('OCR' in line for line in second['limitations']))


if __name__ == '__main__':
    unittest.main()
