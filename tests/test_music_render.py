"""Real FFmpeg verification of the licensed music mix.

Two things must hold simultaneously: profanity muting silences the DIALOGUE only
(so music keeps playing through a muted word), and the mix never clips. An
uncleared style reference must produce no music at all.
"""
from pathlib import Path
import json
import re
import subprocess
import tempfile
import threading
import unittest

import numpy as np

from censor import audio_ops, profanity_hits
from montage_render import render_montage
from music import import_track, load_catalog, style_hints
from render import executable


def max_volume(path):
    result = subprocess.run([executable('ffmpeg'), '-hide_banner', '-i', str(path),
                             '-af', 'volumedetect', '-f', 'null', '-'],
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=120)
    match = re.search(r'max_volume:\s*([-\d.]+)\s*dB', result.stderr)
    return float(match.group(1))


def silences(path, noise='-40dB', minimum=0.15):
    result = subprocess.run([executable('ffmpeg'), '-hide_banner', '-i', str(path),
                             '-af', f'silencedetect=noise={noise}:d={minimum}', '-f', 'null', '-'],
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=120)
    found, start = [], None
    for line in result.stderr.splitlines():
        match = re.search(r'silence_start: ([-0-9.]+)', line)
        if match:
            start = float(match.group(1))
        match = re.search(r'silence_end: ([-0-9.]+)', line)
        if match and start is not None:
            found.append((start, float(match.group(1))))
            start = None
    return found


def write_music(path, seconds=6.0, sr=22050):
    t = np.arange(int(seconds * sr)) / sr
    signal = 0.5 * np.sin(2 * np.pi * 220 * t) + 0.2 * np.sin(2 * np.pi * 330 * t)
    pcm = (np.clip(signal, -1, 1) * 32767).astype('<i2')
    import wave
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sr)
        stream.writeframes(pcm.tobytes())
    return path


class MusicRenderTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self._temp.name)
        self.source = self.root / 'tone.mp4'
        subprocess.run([executable('ffmpeg'), '-v', 'error', '-y',
                        '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=3',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=3',
                        '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(self.source)],
                       check=True, capture_output=True)
        self.words = [dict(start=0.2, end=0.5, word='первый', probability=0.99),
                      dict(start=2.2, end=2.6, word='блядь', probability=0.95)]
        hits = profanity_hits(self.words)
        for hit in hits:
            hit['approved'] = True
        self.ops = audio_ops(hits, padding=0.02)
        self.candidate = dict(title='Музыка и цензура', rationale='Проверка микса.',
                              source_segments=[dict(**{'in': 0.0, 'out': 1.0}), dict(**{'in': 2.0, 'out': 3.0})])

    def tearDown(self):
        self._temp.cleanup()

    def test_music_keeps_playing_through_a_muted_word_and_never_clips(self):
        music_file = write_music(self.root / 'bed.wav')
        entry = import_track(self.root, music_file, 'My Bed', 'Me', 'Mixkit Free License',
                             'https://mixkit.co', ['dark_ambient'])
        video = render_montage(self.source, self.candidate, dict(words=self.words),
                               self.root / 'renders', threading.Event(),
                               censor_ops=self.ops, music=entry, music_intensity='balanced')
        self.assertTrue(any(s['codec_type'] == 'audio'
                            for s in json.loads(subprocess.check_output(
                                [executable('ffprobe'), '-v', 'error', '-show_streams',
                                 '-of', 'json', str(video)], text=True))['streams']))
        # Without music the muted word was silent; with music it must not be.
        self.assertEqual(silences(video), [],
                         'Музыка должна звучать и там, где заглушено слово.')
        self.assertLessEqual(max_volume(video), 0.0, 'Микс перегружен.')

    def test_uncleared_reference_produces_no_music(self):
        catalog = load_catalog(self.root)
        reference = style_hints(catalog, 'dark_ambient')[0]
        video = render_montage(self.source, self.candidate, dict(words=self.words),
                               self.root / 'renders', threading.Event(),
                               censor_ops=self.ops, music=reference)
        # Silence during the muted word proves the reference was not mixed in.
        found = silences(video)
        self.assertTrue(found, 'Непроверенный трек не должен попадать в микс.')
        self.assertTrue(any(start <= 1.30 and end >= 1.50 for start, end in found))

    def test_render_without_music_is_unchanged(self):
        video = render_montage(self.source, self.candidate, dict(words=self.words),
                               self.root / 'renders', threading.Event(), censor_ops=self.ops)
        found = silences(video)
        self.assertTrue(any(start <= 1.30 and end >= 1.50 for start, end in found))


if __name__ == '__main__':
    unittest.main()
