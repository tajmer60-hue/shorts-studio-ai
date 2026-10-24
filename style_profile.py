"""Reference Style Analyzer: turn local FFmpeg measurements into an editable style.

The raw measurements already existed in analyze_reference.py (shot lengths from
scene cuts, silence, loudness, black intervals). This module converts them into
a style profile and uses the ONE thing that transfers honestly between videos:
pacing. A reference that cuts every ~4 s should not be imitated by a clip that
holds one shot for 30 s.

What is deliberately NOT claimed: transitions are not classified, burned-in
subtitle styling is not read (no OCR), music beats are not detected. Those stay
listed as limitations instead of being faked.
"""
from pathlib import Path
import json
import re
import statistics

SCHEMA = 1


def parse_volume(text):
    """Mean/max loudness from an FFmpeg volumedetect log."""
    mean = re.search(r'mean_volume:\s*([-\d.]+)\s*dB', text or '')
    peak = re.search(r'max_volume:\s*([-\d.]+)\s*dB', text or '')
    return dict(mean_db=float(mean.group(1)) if mean else None,
                max_db=float(peak.group(1)) if peak else None)


def profile_from_metrics(metrics, volume_text=''):
    """Build the style profile from measurements only."""
    shots = [float(x) for x in metrics.get('estimated_shot_lengths', []) if float(x) > 0.02]
    cuts = [float(t) for t in metrics.get('cuts', [])]
    duration = float(metrics.get('duration') or 0.0)
    if duration <= 0:
        raise ValueError('Некорректная длительность эталона.')
    loud = parse_volume(volume_text)
    profile = dict(schema=SCHEMA, duration=duration,
                   cut_count=len(cuts),
                   cut_rate=round(len(cuts) / duration, 4),
                   median_shot_seconds=round(statistics.median(shots), 3) if shots else None,
                   mean_shot_seconds=round(statistics.mean(shots), 3) if shots else None,
                   first_cut_seconds=round(cuts[0], 3) if cuts else None,
                   climax_position=_climax_position(cuts, duration),
                   loudness=loud,
                   black_interval_count=len(metrics.get('black_intervals', [])),
                   limitations=['Переходы не классифицируются: резкие смены плана и вспышки '
                                'считаются одинаково.',
                                'Вшитые субтитры и их оформление не читаются (OCR не подключён).',
                                'Музыкальные биты не детектируются; синхронизация с ритмом не '
                                'измерена, а заявлена быть не может.'])
    return profile


def _climax_position(cuts, duration, window=0.15):
    """Relative position (0..1) of the densest cut cluster: where the edit peaks."""
    if not cuts or duration <= 0:
        return None
    best, best_at = -1, 0.5
    position = 0.0
    while position <= 1.0:
        a, b = position * duration, (position + window) * duration
        count = sum(1 for t in cuts if a <= t < b)
        if count > best:
            best, best_at = count, position + window / 2
        position += 0.05
    return round(best_at, 3)


def profile_from_reference(source, folder, report_name='reference-report.md'):
    """Run the local measurement pass once, then cache the profile by identity."""
    from runtime_support import source_key
    from speech import atomic_json
    import analyze_reference
    source = Path(source)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    identity = source_key(source)
    cache = folder / 'style-profile.json'
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding='utf-8'))
            if data.get('source_identity') == identity and data.get('schema') == SCHEMA:
                return data
        except (OSError, ValueError):
            pass
    artifacts = folder / 'artifacts'
    analyze_reference.analyze(source, folder / report_name, artifacts)
    metrics = json.loads((artifacts / 'metrics.json').read_text(encoding='utf-8'))
    volume_text = (artifacts / 'volume.log').read_text(encoding='utf-8', errors='replace') \
        if (artifacts / 'volume.log').exists() else ''
    profile = profile_from_metrics(metrics, volume_text)
    profile['source'] = str(source)
    profile['source_identity'] = identity
    atomic_json(cache, profile)
    return profile


def pacing_target(profile):
    """Cuts per second a clip should roughly hold to feel like the reference.

    The global cut rate is a poor target for clips, because a clip selects only
    the dense parts of an episode; the reference's typical shot length is the
    transferable quantity.
    """
    shot = profile.get('median_shot_seconds')
    if shot and shot > 0:
        return round(1.0 / shot, 4)
    return profile.get('cut_rate')


def style_targets(profile, duration_range=(25.0, 90.0)):
    """Editable targets derived from the reference pacing."""
    shot = profile.get('median_shot_seconds')
    return dict(target_shot_seconds=shot,
                target_cut_rate=pacing_target(profile),
                clip_duration_range=list(duration_range),
                loudness_mean_db=profile.get('loudness', {}).get('mean_db'),
                note='Переносится темп монтажа, а не чужая работа: копирование чужих '
                     'роликов не является целью и не обещается.')


def cut_density(segments, scene_cuts):
    cuts = sum(1 for t in scene_cuts for s in segments if s['in'] < t < s['out'])
    span = sum(max(0.0, s['out'] - s['in']) for s in segments)
    return cuts / span if span else 0.0


def rank_with_style(candidates, profile, scene_cuts, weight=0.35):
    """Re-rank candidates by pacing fit. Explanations state what was measured."""
    target = pacing_target(profile)
    if not target:
        return candidates
    scored = []
    for candidate in candidates:
        segments = candidate.get('source_segments', [])
        density = cut_density(segments, scene_cuts)
        fit = max(0.0, 1.0 - abs(density - target) / target) if target else 0.0
        base = float(candidate.get('score', 0) or 0)
        combined = round(base * (1 - weight) + fit * 10 * weight, 3)
        enriched = dict(candidate)
        enriched['style_score'] = round(fit, 3)
        enriched['score'] = combined
        enriched['rationale'] = (candidate.get('rationale', '') +
                                 f' Темп: {density:.2f} смен/с против {target:.2f} у эталона '
                                 f'(совпадение {fit:.0%}); вес стиля {weight:.2f}.')
        scored.append(enriched)
    scored.sort(key=lambda c: -float(c.get('score', 0)))
    return scored
