"""QC tests on real FFmpeg artifacts with deliberately broken properties."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from qc import MAX_FIX_ATTEMPTS, auto_fixes, measure, qc_report, run_qc_with_fixes
from render import executable


def render(path, audio='sine=frequency=440:duration=4', volume=None, letterbox=False,
           silence_head=False):
    vf = 'scale=720:720,pad=720:1280:0:280:color=black' if letterbox else 'scale=720:1280'
    af = []
    if silence_head:
        af.append("volume=0:enable='lt(t,2)'")
    if volume:
        af.append(f'volume={volume}')
    command = [executable('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi',
               '-i', 'testsrc2=size=320x180:rate=30:duration=4',
               '-f', 'lavfi', '-i', audio, '-vf', vf]
    if af:
        command += ['-af', ','.join(af)]
    command += ['-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(path)]
    subprocess.run(command, check=True, capture_output=True)
    return path


class MeasureTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def test_clean_render_passes_every_structural_check(self):
        m = measure(render(self.root / 'clean.mp4'))
        self.assertFalse(m['black_bars'])
        self.assertFalse(m['opening_silent'])
        self.assertTrue(m['has_audio'])
        self.assertLessEqual(m['max_db'], 0.0)

    def test_letterbox_is_detected_as_black_bars(self):
        m = measure(render(self.root / 'bars.mp4', letterbox=True))
        self.assertTrue(m['black_bars'])
        self.assertLess(m['crop_h'], 1280 - 8)

    def test_overdriven_mix_is_detected_as_clipping(self):
        m = measure(render(self.root / 'hot.mp4', volume='20dB'))
        self.assertGreaterEqual(m['max_db'], -0.5)

    def test_silent_opening_is_detected(self):
        m = measure(render(self.root / 'quiet.mp4', silence_head=True))
        self.assertTrue(m['opening_silent'])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def test_scores_are_bounded_and_explained(self):
        video = render(self.root / 'clean.mp4')
        report = qc_report(video, plan=dict(duration=4.0, segments=[]), words=[], cues=[],
                           censor=dict(conflicts=[]), hook=dict(score=60), reframe_used=True)
        for name, value in report['scores'].items():
            self.assertGreaterEqual(value, 0.0, name)
            self.assertLessEqual(value, 100.0, name)
        self.assertTrue(all(c['explanation'] for c in report['checks']))
        self.assertTrue(report['limitations'])
        self.assertEqual(report['needs_review'], [])

    def test_broken_artifact_produces_review_items_not_silence(self):
        video = render(self.root / 'bars.mp4', letterbox=True, volume='20dB', silence_head=True)
        report = qc_report(video, hook=dict(score=10))
        names = {c['name'] for c in report['checks'] if not c['ok']}
        self.assertIn('no_black_bars', names)
        self.assertIn('no_clipping', names)
        self.assertIn('hook_audible', names)
        self.assertTrue(report['needs_review'])
        self.assertLess(report['scores']['visual'], 100)
        self.assertLess(report['scores']['audio'], 100)

    def test_word_cuts_are_recomputed_from_the_transcript(self):
        video = render(self.root / 'clean.mp4')
        words = [dict(start=1.0, end=2.0, word='слово')]
        plan = dict(duration=4.0, segments=[dict(source_in=1.5, source_out=3.0, output_in=0.0)])
        report = qc_report(video, plan=plan, words=words)
        cut = next(c for c in report['checks'] if c['name'] == 'no_word_cuts')
        self.assertFalse(cut['ok'])
        self.assertEqual(cut['value'], 1)


class FixLoopTests(unittest.TestCase):
    def test_clipping_offers_exactly_one_fix(self):
        report = dict(checks=[dict(name='no_clipping', ok=False), dict(name='av_sync', ok=True)])
        self.assertEqual(len(auto_fixes(report)), 1)
        self.assertEqual(auto_fixes(report)[0]['action'], 'lower_music_db')
        clean = dict(checks=[dict(name='no_clipping', ok=True)])
        self.assertEqual(auto_fixes(clean), [])

    def test_the_fix_loop_is_capped_and_reports_unresolved(self):
        calls = []

        def render_always_hot(fixes=None):
            calls.append(fixes)
            with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
                return render(Path(temp) / 'hot.mp4', volume='20dB')

        def check(video):
            return qc_report(video, hook=dict(score=50))

        # The artifact is re-rendered into a fresh temp dir each call, so run the
        # loop against a stable copy instead.
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            hot = render(Path(temp) / 'hot.mp4', volume='20dB')
            report = run_qc_with_fixes(lambda fixes=None: hot, check)
        self.assertLessEqual(report['attempts'], MAX_FIX_ATTEMPTS)
        self.assertIn('no_clipping', report['unresolved'])


if __name__ == '__main__':
    unittest.main()
