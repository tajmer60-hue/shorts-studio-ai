"""Beat Sync Engine: rhythm analysis and story-first cut snapping.

Audio analysis is local (numpy STFT; librosa is used when present but never
required). The engine measures BPM, beat grid, intensity and drops, then offers
cut times snapped to beats.

The rule from the specification is enforced in code: a cut is moved to a beat
only when the shift is small, the story boundary allows it, and the new time
does not cross a recognized word or another cut. Music serves the scene; when a
line or a dramatic pause matters more, the beat loses.
"""
from pathlib import Path
import hashlib
import json
import math
import subprocess
import uuid
import wave

import numpy as np

from render import executable
from runtime_support import source_key
from speech import atomic_json

SR = 22050
HOP = 512
N_FFT = 2048

# Editing intensity presets. They live in the channel profile.
INTENSITY = {
    'cinematic': dict(snap_to_beats=False, max_shift=0.0, duck_db=-6.0, sfx_max=1,
                      music_level_db=-18.0, note='Спокойный, атмосферный: ритм не диктует склейки.'),
    'balanced': dict(snap_to_beats=True, max_shift=0.20, duck_db=-10.0, sfx_max=2,
                     music_level_db=-14.0, note='Динамичный, но естественный: склейки тянутся к битам.'),
    'aggressive': dict(snap_to_beats=True, max_shift=0.25, duck_db=-12.0, sfx_max=3,
                       music_level_db=-12.0, note='Быстрые склейки и активные акценты; сюжет всё равно первичен.'),
}


def read_mono(path, max_seconds=None):
    """Decode to mono float32 without loading more than needed."""
    wav = Path(str(path) + '.beat.wav')
    args = [executable('ffmpeg'), '-v', 'error', '-nostdin', '-y', '-i', str(path),
            '-vn', '-ac', '1', '-ar', str(SR), '-c:a', 'pcm_s16le']
    if max_seconds:
        args += ['-t', str(max_seconds)]
    args += [str(wav)]
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=600,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with wave.open(str(wav), 'rb') as stream:
            raw = stream.readframes(stream.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        wav.unlink(missing_ok=True)


def onset_envelope(samples):
    """Spectral flux: half-wave rectified magnitude difference across frames."""
    if samples.size < N_FFT:
        return np.zeros(1, dtype=np.float32)
    window = np.hanning(N_FFT).astype(np.float32)
    count = max(1, (samples.size - N_FFT) // HOP)
    flux = np.zeros(count, dtype=np.float32)
    previous = None
    for index in range(count):
        frame = samples[index * HOP:index * HOP + N_FFT] * window
        magnitude = np.abs(np.fft.rfft(frame))
        if previous is not None:
            flux[index] = np.sum(np.maximum(0.0, magnitude - previous))
        previous = magnitude
    return flux


def estimate_tempo(env, bpm_min=60.0, bpm_max=200.0):
    """Autocorrelation of the onset envelope over integer lags, refined.

    Searching integer BPM values quantises badly (several tempos map to the same
    lag), so we score every plausible lag and refine the peak parabolically.
    """
    if env.size < 8:
        return None
    centered = env - env.mean()
    fps = SR / HOP
    lag_min = max(2, int(round(fps * 60.0 / bpm_max)))
    lag_max = int(round(fps * 60.0 / bpm_min))
    if lag_max >= centered.size:
        lag_max = centered.size - 1
    best_lag, best = None, -math.inf
    scores = {}
    for lag in range(lag_min, lag_max + 1):
        score = float(np.dot(centered[lag:], centered[:-lag]))
        scores[lag] = score
        if score > best:
            best, best_lag = score, lag
    if best_lag is None or best <= 0:
        return None
    refined = float(best_lag)
    if best_lag - 1 in scores and best_lag + 1 in scores:
        a, b, c = scores[best_lag - 1], scores[best_lag], scores[best_lag + 1]
        denominator = a - 2 * b + c
        if denominator < -1e-9:
            refined = best_lag + 0.5 * (a - c) / denominator
    bpm = 60.0 * fps / refined
    return round(min(bpm_max, max(bpm_min, bpm)), 2)


def beat_grid(env, bpm):
    """Beat times in seconds: the phase of the tempo grid with most onset energy."""
    if not bpm:
        return []
    fps = SR / HOP
    period = fps * 60.0 / bpm
    if period < 1:
        return []
    candidates = int(math.ceil(period))
    scores = [float(env[int(p):: int(round(period))].sum()) for p in range(candidates)]
    phase = int(np.argmax(scores)) if scores else 0
    beats = []
    position = phase
    while position < env.size:
        beats.append(round(position / fps, 3))
        position += period
    return beats


def intensity_envelope(samples, frame_seconds=0.25):
    """RMS energy per frame: the material for drop detection and ducking checks."""
    size = max(1, int(SR * frame_seconds))
    count = samples.size // size
    if count == 0:
        return np.zeros(1, dtype=np.float32), frame_seconds
    shaped = samples[:count * size].reshape(count, size)
    return np.sqrt((shaped ** 2).mean(axis=1)).astype(np.float32), frame_seconds


def drop_times(intensity, frame_seconds, threshold=1.35, cooldown=2.0):
    """Points where energy jumps above the preceding local level: musical drops."""
    if intensity.size < 8:
        return []
    window = max(2, int(2.0 / frame_seconds))
    found = []
    last = -cooldown
    for index in range(window, intensity.size):
        previous = float(intensity[max(0, index - window):index].mean())
        if previous <= 1e-4:
            continue
        if intensity[index] > previous * threshold:
            time = index * frame_seconds
            if time - last >= cooldown:
                found.append(round(time, 3))
                last = time
    return found


def analyze_track(path, cache_dir, cancelled=None, max_seconds=None):
    """Cached rhythm analysis. Identical files are never analysed twice."""
    path = Path(path)
    folder = Path(cache_dir)
    folder.mkdir(parents=True, exist_ok=True)
    identity = source_key(path)
    key = hashlib.sha256(json.dumps([identity, SR, HOP, max_seconds, 'beat-v1']).encode()).hexdigest()
    report = folder / (key + '.json')
    if report.exists():
        return json.loads(report.read_text(encoding='utf-8'))
    if cancelled is not None and cancelled.is_set():
        raise ValueError('Отменено.')
    samples = read_mono(path, max_seconds)
    env = onset_envelope(samples)
    bpm = estimate_tempo(env)
    beats = beat_grid(env, bpm)
    intensity, frame_seconds = intensity_envelope(samples)
    result = dict(schema=1, source=str(path), source_identity=identity, sample_rate=SR,
                  bpm=bpm, beats=beats, drops=drop_times(intensity, frame_seconds),
                  duration=round(samples.size / SR, 3),
                  limitations=['Темп и биты оценены спектральным потоком; полиритмия и медленные '
                               'вступления могут сбивать автокорреляцию.',
                               'Анализ не различает музыку и диалог: анализируйте музыкальный файл, '
                               'а не смешанную дорожку.'])
    atomic_json(report, result)
    return result


def snap_cuts(cuts, beats, words=(), max_shift=0.20, min_gap=0.3):
    """Move story cuts onto nearby beats without ever breaking speech or order.

    A cut that has no beat within max_shift stays where the story put it. That
    refusal is the difference between editing to music and cutting on every kick.
    """
    if not beats:
        return list(cuts), []
    beat_set = np.asarray(sorted(beats), dtype=float)
    moved, kept = [], []
    previous = None
    for cut in sorted(float(c) for c in cuts):
        distances = np.abs(beat_set - cut)
        index = int(np.argmin(distances))
        target = float(beat_set[index])
        ok = (distances[index] <= max_shift
              and not any(w['start'] < target < w['end'] for w in words)
              and (previous is None or target - previous >= min_gap))
        if ok:
            moved.append(dict(source=round(cut, 3), snapped=round(target, 3),
                              shift=round(target - cut, 3)))
            previous = target
        else:
            kept.append(round(cut, 3))
            previous = cut
    return moved, kept


def db_to_linear(db):
    return 10.0 ** (db / 20.0)


def duck_expression(dialogue, dip_db=-10.0, base_db=0.0):
    """Per-frame LINEAR gain expression: music dips under every dialogue interval.

    FFmpeg evaluates a volume expression as a linear multiplier; a "dB" suffix is
    only accepted on plain constants, never inside if(), so the conversion is
    done here.
    """
    if not dialogue:
        return None
    inner = f'{db_to_linear(base_db):.4f}'
    for interval in sorted(dialogue, key=lambda d: d['start'], reverse=True):
        inner = (f"if(between(t,{interval['start']:.3f},{interval['end']:.3f}),"
                 f"{db_to_linear(dip_db):.4f},{inner})")
    return inner


def music_chain(duration, dialogue=(), intensity='balanced', fade=0.6):
    """Full music filter: level, ducking under dialogue, fades, overload limit."""
    if intensity not in INTENSITY:
        raise ValueError('Неизвестная интенсивность монтажа: ' + str(intensity))
    preset = INTENSITY[intensity]
    parts = [f'volume={preset["music_level_db"]:.2f}dB']
    duck = duck_expression(dialogue, preset['duck_db'])
    if duck:
        parts.append(f"volume=volume='{duck}':eval=frame")
    parts.append(f'afade=t=in:d={fade:.2f}')
    parts.append(f'afade=t=out:st={max(0.0, duration - fade):.2f}:d={fade:.2f}')
    parts.append('alimiter=limit=0.89')
    return ','.join(parts)


def align_music_to_drop(plan_drop, music_drops, tolerance=1.5):
    """Suggest shifting the visual climax onto the nearest musical drop."""
    if not music_drops or plan_drop is None:
        return None
    nearest = min(music_drops, key=lambda d: abs(d - plan_drop))
    if abs(nearest - plan_drop) <= tolerance:
        return dict(visual=round(plan_drop, 3), musical=nearest,
                    shift=round(nearest - plan_drop, 3))
    return None
