"""Intro/recap/credits detector tests.

The decisive checks are the negative ones: dialogue subtitles must never become
credits, a mid-episode black frame must not be auto-skipped, and a recap marker
spoken in the middle of the episode must not delete a real scene.
"""
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from intros import (AUTO_SKIP, PROPOSE, apply_exclusions, black_regions, credit_regions,
                    detect_exclusions, dialogue_gaps, opening_regions, overlap_fraction,
                    recap_regions)
from render import executable


def make_black_sandwich(root):
    source = root / 'sandwich.mp4'
    subprocess.run([executable('ffmpeg'), '-v', 'error', '-y',
                    '-f', 'lavfi', '-i', 'color=c=black:s=64x64:d=1',
                    '-f', 'lavfi', '-i', 'color=c=gray:s=64x64:d=2',
                    '-f', 'lavfi', '-i', 'color=c=black:s=64x64:d=1',
                    '-filter_complex', '[0][1][2]concat=n=3:v=1:a=0',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)],
                   check=True, capture_output=True)
    return source


def make_plain(root):
    source = root / 'plain.mp4'
    subprocess.run([executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi',
                    '-i', 'color=c=gray:s=64x64:d=4', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(source)], check=True, capture_output=True)
    return source


TRANSCRIPT = dict(cues=[
    dict(start=0.5, end=4.0, text='В прошлой серии мы остановились на возвращении домой.'),
    dict(start=30.0, end=34.0, text='Почему ты взял деньги?'),
    dict(start=36.0, end=40.0, text='Я верну всё завтра.'),
    dict(start=200.0, end=204.0, text='Кстати, в прошлой серии было то же самое.'),
], duration=240.0)


class BlackTests(unittest.TestCase):
    def test_black_at_both_ends_is_detected(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            source = make_black_sandwich(Path(temp))
            found = black_regions(source, cancelled=threading.Event())
            self.assertTrue(found, 'Чёрные кадры не найдены.')
            self.assertLess(found[0][0], 1.0)
            self.assertGreater(found[-1][1], 3.0)

    def test_plain_video_has_no_black(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            source = make_plain(Path(temp))
            self.assertEqual(black_regions(source, cancelled=threading.Event()), [])

    def test_cancel_is_honoured(self):
        stop = threading.Event()
        stop.set()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            source = make_black_sandwich(Path(temp))
            with self.assertRaises(ValueError):
                black_regions(source, cancelled=stop)


class RegionTests(unittest.TestCase):
    def test_recap_is_found_only_in_the_head(self):
        regions = recap_regions(TRANSCRIPT, 240.0)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]['policy'], AUTO_SKIP)
        self.assertLess(regions[0]['end'], 48.0)

    def test_dialogue_gaps_see_middle_and_tail(self):
        gaps = dialogue_gaps(TRANSCRIPT, 240.0, minimum=8.0)
        self.assertTrue(any(g[0] <= 4.0 and g[1] >= 30.0 for g in gaps), gaps)
        self.assertTrue(any(g[1] > 230.0 for g in gaps), gaps)

    def test_tail_with_black_is_credits_tail_without_black_is_only_proposed(self):
        blacks = [(235.0, 240.0)]
        credits = credit_regions(TRANSCRIPT, 240.0, [], blacks, minimum=15.0)
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0]['policy'], AUTO_SKIP)
        plain = credit_regions(TRANSCRIPT, 240.0, [], [], minimum=15.0)
        self.assertEqual(len(plain), 1)
        self.assertEqual(plain[0]['policy'], PROPOSE)
        self.assertTrue(any('немая' in s or 'проверьте' in s for s in plain[0]['signals']))

    def test_dialogue_subtitles_can_never_become_credits(self):
        # Dialogue runs until two seconds before the end: no tail, no credits.
        talky = dict(cues=[dict(start=0.0, end=10.0, text='привет'),
                           dict(start=236.0, end=238.0, text='пока')], duration=240.0)
        self.assertEqual(credit_regions(talky, 240.0, [], [(238.5, 240.0)], minimum=15.0), [])

    def test_opening_theme_needs_structure_not_just_silence(self):
        late = dict(cues=[dict(start=25.0, end=30.0, text='начали')], duration=240.0)
        with_black = opening_regions(late, 240.0, [(0.0, 25.0)], [(0.0, 2.0)], minimum=10.0)
        self.assertEqual(with_black[0]['policy'], AUTO_SKIP)
        without = opening_regions(late, 240.0, [], [], minimum=10.0)
        self.assertEqual(without[0]['policy'], PROPOSE)
        early = dict(cues=[dict(start=2.0, end=5.0, text='сразу речь')], duration=240.0)
        self.assertEqual(opening_regions(early, 240.0, [], [], minimum=10.0), [])


class IntegrationTests(unittest.TestCase):
    def test_mid_episode_black_is_proposed_not_skipped(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            source = make_black_sandwich(Path(temp))
            result = detect_exclusions(source, dict(cues=[], duration=4.0), 4.0,
                                       cancelled=threading.Event(), use_black=True)
            mid = [r for r in result['regions'] if r['kind'] == 'black_gap']
            self.assertTrue(all(r['policy'] == PROPOSE for r in mid))

    def test_apply_exclusions_drops_skipped_and_flags_proposed(self):
        regions = [dict(kind='end_credits', start=200.0, end=240.0, policy=AUTO_SKIP, signals=['хвост']),
                   dict(kind='black_gap', start=100.0, end=110.0, policy=PROPOSE, signals=['чёрный'])]
        plan = dict(candidates=[
            dict(title='В титрах', source_segments=[dict(**{'in': 205.0, 'out': 230.0})]),
            dict(title='Чистая сцена', source_segments=[dict(**{'in': 30.0, 'out': 60.0})]),
            dict(title='На чёрном', source_segments=[dict(**{'in': 100.0, 'out': 110.0})])])
        result = apply_exclusions(regions, plan)
        titles = [c['title'] for c in result['candidates']]
        self.assertNotIn('В титрах', titles)
        self.assertIn('Чистая сцена', titles)
        flagged = next(c for c in result['candidates'] if c['title'] == 'На чёрном')
        self.assertTrue(flagged['review_flags'])
        self.assertEqual(result['rejected'][0]['title'], 'В титрах')

    def test_overlap_fraction_is_a_share_of_selected_footage(self):
        self.assertAlmostEqual(overlap_fraction([dict(**{'in': 0.0, 'out': 10.0})],
                                                dict(start=5.0, end=15.0, policy=AUTO_SKIP)), 0.5)
        self.assertEqual(overlap_fraction([], dict(start=0.0, end=9.0, policy=AUTO_SKIP)), 0.0)


if __name__ == '__main__':
    unittest.main()
