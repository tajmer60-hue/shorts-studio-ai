"""Real FFmpeg verification of targeted profanity muting inside a montage.

The source tone is continuous, so any silence in the output can only come from
the censor filter. The interval is checked in OUTPUT time after a two-segment
splice, which also proves the source-to-output remap.
"""
from pathlib import Path
import json
import re
import subprocess
import tempfile
import threading
import unittest

from censor import audio_ops, profanity_hits
from montage_render import render_montage
from render import executable


def silences(path, noise='-40dB', minimum=0.15):
    result = subprocess.run([executable('ffmpeg'), '-hide_banner', '-nostdin', '-i', str(path),
                             '-af', f'silencedetect=noise={noise}:d={minimum}', '-f', 'null', '-'],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    log = result.stderr
    found = []
    start = None
    for line in log.splitlines():
        match = re.search(r'silence_start: ([-0-9.]+)', line)
        if match:
            start = float(match.group(1))
        match = re.search(r'silence_end: ([-0-9.]+)', line)
        if match and start is not None:
            found.append((start, float(match.group(1))))
            start = None
    return found


class CensorRenderTests(unittest.TestCase):
    def test_approved_word_is_muted_in_output_time_and_dialogue_survives(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            source = root / 'tone.mp4'
            subprocess.run([executable('ffmpeg'), '-v', 'error', '-y',
                            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=3',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=3',
                            '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)],
                           check=True, capture_output=True)
            words = [dict(start=0.2, end=0.5, word='первый', probability=0.99),
                     dict(start=2.2, end=2.6, word='блядь', probability=0.95),
                     dict(start=2.7, end=2.9, word='второй', probability=0.98)]
            hits = profanity_hits(words)
            self.assertEqual([hit['word'] for hit in hits], ['блядь'])
            for hit in hits:
                hit['approved'] = True
            ops = audio_ops(hits, padding=0.02)
            self.assertEqual(len(ops), 1)
            # Source interval 2.18-2.62 lands in segment 2..3 whose output starts at 1.0.
            self.assertAlmostEqual(ops[0]['start'], 2.18, places=2)
            candidate = dict(title='Проверка цензуры', rationale='Тест точечного глушения.',
                             source_segments=[dict(**{'in': 0.0, 'out': 1.0}), dict(**{'in': 2.0, 'out': 3.0})])
            transcript = dict(words=words)
            stop = threading.Event()
            video = render_montage(source, candidate, transcript, root / 'renders', stop, censor_ops=ops)
            data = json.loads(subprocess.check_output([executable('ffprobe'), '-v', 'error',
                                                       '-show_format', '-show_streams', '-of', 'json', str(video)],
                                                      text=True, timeout=60))
            self.assertAlmostEqual(float(data['format']['duration']), 2.0, delta=0.2)
            self.assertTrue(any(stream['codec_type'] == 'audio' for stream in data['streams']))
            found = silences(video)
            self.assertTrue(found, 'Одобренный интервал не заглушён.')
            hit_window = [pair for pair in found if pair[0] <= 1.30 and pair[1] >= 1.50]
            self.assertTrue(hit_window, f'Тишина не попала в выходное окно 1.18-1.62: {found}')
            # Everything outside the muted word must still carry audio.
            total_silence = sum(end - start for start, end in found)
            self.assertLess(total_silence, 1.0, 'Заглушено больше, чем одно слово.')
            record = json.loads((video.parent / 'montage.json').read_text(encoding='utf-8'))
            self.assertEqual(len(record['censor']['ops']), 1)
            self.assertAlmostEqual(record['censor']['ops'][0]['start'], 1.18, places=2)
            self.assertEqual(record['censor']['conflicts'], [])
            self.assertTrue(any('авторские права' in line for line in record['censor']['limitations']))

    def test_unapproved_hits_leave_the_audio_untouched(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            source = root / 'tone.mp4'
            subprocess.run([executable('ffmpeg'), '-v', 'error', '-y',
                            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=2',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
                            '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)],
                           check=True, capture_output=True)
            words = [dict(start=0.5, end=0.9, word='сука', probability=0.95)]
            hits = profanity_hits(words)
            self.assertEqual(audio_ops(hits), [])
            candidate = dict(title='Без цензуры', rationale='Ничего не одобрено.',
                             source_segments=[dict(**{'in': 0.0, 'out': 2.0})])
            video = render_montage(source, candidate, dict(words=words), root / 'renders',
                                   threading.Event(), censor_ops=audio_ops(hits))
            self.assertEqual(silences(video), [])

    def test_conflict_between_censor_and_cut_is_reported(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            source = root / 'tone.mp4'
            subprocess.run([executable('ffmpeg'), '-v', 'error', '-y',
                            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=4',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4',
                            '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)],
                           check=True, capture_output=True)
            candidate = dict(title='Конфликт', rationale='Граница режет слово.',
                             source_segments=[dict(**{'in': 0.0, 'out': 1.0}), dict(**{'in': 2.0, 'out': 3.0})])
            # An interval crossing the 1.0 cut cannot be muted as a whole.
            ops = [dict(start=0.8, end=1.4, mode='mute', word='блядь')]
            with self.assertRaises(ValueError) as caught:
                render_montage(source, candidate, dict(words=[]), root / 'renders',
                               threading.Event(), censor_ops=ops)
            self.assertIn('Цензура не согласована', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
