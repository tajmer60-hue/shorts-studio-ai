"""Intro / recap / end-credits detection from several independent signals.

Issue #2 section 5 forbids a single-rule detector and forbids treating dialogue
subtitles as credits. Therefore every region here needs at least one structural
signal (position, black frames, dialogue absence), and text markers alone are
never enough for credits. A black frame in the middle of an episode is usually a
scene transition, so it is only PROPOSED, never auto-skipped.

Policies:
- auto_skip: removed from planning by default (recap, opening theme, credits)
- propose:   shown for review, kept unless the user decides otherwise
- keep:      never touched
"""
from pathlib import Path
import re
import subprocess

from render import executable
from runtime_support import read_log_tail

SCHEMA = 1
AUTO_SKIP = 'auto_skip'
PROPOSE = 'propose'
KEEP = 'keep'

# Strong markers only. Weak words like "раньше" appear in ordinary dialogue and
# would make the detector eat real scenes.
RECAP_MARKERS = ('ранее в', 'в прошлой серии', 'в предыдущей серии', 'в прошлый раз',
                 'previously on', 'last time on', 'recap:')
CREDIT_MARKERS = ('режиссёр', 'сценарий:', 'оператор', 'продюсер', 'в ролях',
                  'directed by', 'written by', 'executive producer', 'cast:')


def black_regions(source, minimum=0.6, cancelled=None):
    """FFmpeg blackdetect: a structural signal, never sufficient on its own."""
    log = Path(str(source) + '.blackdetect.log')
    command = [executable('ffmpeg'), '-hide_banner', '-nostdin', '-protocol_whitelist', 'file,pipe',
               '-i', str(source), '-vf', f'blackdetect=d={minimum}:pix_th=0.10',
               '-an', '-f', 'null', '-']
    found = []
    try:
        with log.open('w', encoding='utf-8') as stderr:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stderr,
                                       text=True, encoding='utf-8', errors='replace',
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            while process.poll() is None:
                if cancelled is not None and cancelled.wait(0.2):
                    process.terminate()
                    process.wait(timeout=5)
                    raise ValueError('Отменено.')
            if process.returncode not in (0, None) and process.returncode != 0:
                raise ValueError('blackdetect завершился с ошибкой: ' + read_log_tail(log)[-800:])
        for line in log.read_text(encoding='utf-8', errors='replace').splitlines():
            match = re.search(r'black_start:([0-9.]+).*?black_end:([0-9.]+)', line)
            if match:
                found.append((float(match.group(1)), float(match.group(2))))
    finally:
        log.unlink(missing_ok=True)
    return found


def dialogue_gaps(transcript, duration, minimum=8.0):
    """Long stretches without recognized dialogue: possible theme or credits zones."""
    cues = sorted(transcript.get('cues', []), key=lambda c: c['start'])
    gaps = []
    cursor = 0.0
    for cue in cues:
        if cue['start'] - cursor >= minimum:
            gaps.append((cursor, cue['start']))
        cursor = max(cursor, cue['end'])
    if duration - cursor >= minimum:
        gaps.append((cursor, duration))
    return gaps


def _marked_regions(transcript, markers, limit, share_note):
    regions = []
    current = None
    for cue in sorted(transcript.get('cues', []), key=lambda c: c['start']):
        if cue['start'] > limit:
            break
        text = (cue.get('text') or '').lower()
        if any(marker in text for marker in markers):
            if current and cue['start'] - current[1] <= 3.0:
                current = (current[0], cue['end'], current[2] + [cue['text']])
            else:
                if current:
                    regions.append(current)
                current = (cue['start'], cue['end'], [cue['text']])
    if current:
        regions.append(current)
    return regions


def recap_regions(transcript, duration, head_share=0.2):
    """Recap text inside the first fifth of the runtime. Text plus position."""
    return [dict(kind='recap', start=round(a, 3), end=round(b, 3), policy=AUTO_SKIP,
                 signals=[f'маркеры пересказа в первых {int(head_share * 100)}% хронометража',
                          'реплики: ' + ' / '.join(t[:40] for t in texts[:2])])
            for a, b, texts in _marked_regions(transcript, RECAP_MARKERS, duration * head_share, head_share)]


def credit_regions(transcript, duration, gaps, blacks, minimum=15.0):
    """Tail after the last dialogue. Requires length AND (black frames or credit
    markers); a long silent dramatic ending without black frames is only proposed."""
    cues = sorted(transcript.get('cues', []), key=lambda c: c['start'])
    if not cues:
        return []
    last = max(c['end'] for c in cues)
    tail = duration - last
    if tail < minimum:
        return []
    has_black = any(b0 < duration and b1 > last for b0, b1 in blacks)
    tail_cues = [c for c in cues if c['start'] >= last - 1e-6]
    has_marker = any(any(m in (c.get('text') or '').lower() for m in CREDIT_MARKERS) for c in tail_cues)
    signals = [f'хвост без реплик {tail:.1f} с после последней реплики']
    if has_black:
        signals.append('чёрные кадры в хвосте')
    if has_marker:
        signals.append('текстовые маркеры титров')
    policy = AUTO_SKIP if (has_black or has_marker) else PROPOSE
    if policy == PROPOSE:
        signals.append('нет чёрных кадров и маркеров: возможно, немая драматичная концовка — проверьте')
    return [dict(kind='end_credits', start=round(last, 3), end=round(duration, 3),
                 policy=policy, signals=signals)]


def opening_regions(transcript, duration, gaps, blacks, minimum=10.0):
    """Head before the first dialogue: title sequence or cold open."""
    cues = sorted(transcript.get('cues', []), key=lambda c: c['start'])
    if not cues:
        return []
    first = min(c['start'] for c in cues)
    if first < minimum:
        return []
    has_black = any(b0 < first for b0, b1 in blacks)
    in_gap = any(g0 <= 0.0 and g1 >= first for g0, g1 in gaps)
    signals = [f'начало без реплик {first:.1f} с до первой реплики']
    if has_black:
        signals.append('чёрные кадры в начале')
    if in_gap:
        signals.append('участок без диалога (вероятно, тема/заставка)')
    policy = AUTO_SKIP if (has_black or in_gap) else PROPOSE
    if policy == PROPOSE:
        signals.append('структурных признаков заставки мало: возможно, холодное начало — проверьте')
    return [dict(kind='opening_theme', start=0.0, end=round(first, 3), policy=policy, signals=signals)]


def detect_exclusions(source, transcript, duration, cancelled=None, use_black=True):
    """Combine the signals into reviewable regions."""
    blacks = black_regions(source, cancelled=cancelled) if use_black else []
    gaps = dialogue_gaps(transcript, duration)
    regions = recap_regions(transcript, duration)
    regions += opening_regions(transcript, duration, gaps, blacks)
    regions += credit_regions(transcript, duration, gaps, blacks)
    regions += [dict(kind='black_gap', start=round(a, 3), end=round(b, 3), policy=PROPOSE,
                     signals=['чёрные кадры внутри эпизода: обычно переход между сценами, '
                              'поэтому не удаляется автоматически'])
                for a, b in blacks if a > 1.0 and b < duration - 1.0]
    regions.sort(key=lambda r: r['start'])
    return dict(schema=SCHEMA, blacks=[list(b) for b in blacks], regions=regions,
                limitations=['Детектор работает по речи, чёрным кадрам и позиции; встроенный '
                             'в видео текст титров не читается (OCR не подключён).',
                             'Субтитры с диалогом не могут быть приняты за титры: регионы титров '
                             'требуют отсутствия речи или чёрных кадров.',
                             'Всё с политикой propose требует решения человека.'])


def overlap_fraction(segments, region):
    total = 0.0
    for segment in segments:
        a, b = segment['in'], segment['out']
        total += max(0.0, min(b, region['end']) - max(a, region['start']))
    span = sum(max(0.0, s['out'] - s['in']) for s in segments)
    return total / span if span else 0.0


def apply_exclusions(regions, plan):
    """Drop candidates that live inside auto-skip regions; flag proposed ones."""
    skipped = [r for r in regions if r['policy'] == AUTO_SKIP]
    proposed = [r for r in regions if r['policy'] == PROPOSE]
    accepted, rejected = [], []
    for candidate in plan.get('candidates', []):
        segments = candidate.get('source_segments', [])
        hit = next((r for r in skipped if overlap_fraction(segments, r) >= 0.5), None)
        if hit is not None:
            rejected.append(dict(title=candidate.get('title'),
                                 reason=f'Материал внутри региона «{hit["kind"]}» '
                                        f'({hit["start"]:.0f}-{hit["end"]:.0f} с): ' + hit['signals'][0]))
            continue
        near = next((r for r in proposed if overlap_fraction(segments, r) >= 0.5), None)
        if near is not None:
            candidate = dict(candidate)
            candidate['review_flags'] = candidate.get('review_flags', []) + [
                f'Пересекает регион «{near["kind"]}» с политикой propose: проверьте.']
        accepted.append(candidate)
    return dict(schema=SCHEMA, candidates=accepted, rejected=rejected)
