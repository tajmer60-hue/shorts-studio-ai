"""Objective comparison of candidate models on one and the same clip.

Every metric here is a measurable fact about the returned plan, checked against
the local ASR transcript and the analyzed window. None of them is a virality
forecast or a taste judgement: `word_cuts == 0` does not mean the edit is good,
it means the model did not slice a recognized word in half.

The point is to choose a paid fallback from evidence instead of from price or
marketing, as Issue #2 section 7 requires.
"""
from pathlib import Path
import json
import time

TARGET_MIN = 25.0
TARGET_MAX = 90.0


def _spans(plan):
    out = []
    for index, candidate in enumerate(plan.get('candidates') or []):
        for segment in candidate.get('source_segments') or []:
            out.append((index, float(segment['in']), float(segment['out'])))
    return out


def _covered(spans):
    """Union length of selected footage, so overlaps are not counted twice."""
    merged = []
    for _index, a, b in sorted(spans, key=lambda item: item[1]):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return sum(b - a for a, b in merged), merged


def score_plan(plan, words, window_in, window_out, target_min=TARGET_MIN, target_max=TARGET_MAX):
    """Contract-adherence metrics for one model answer."""
    if not isinstance(plan, dict):
        raise ValueError('План не является объектом.')
    candidates = plan.get('candidates') or []
    spans = _spans(plan)
    window = max(1e-6, float(window_out) - float(window_in))
    covered, merged = _covered(spans)

    # Both edges of every segment matter: a cut on either side of a recognized
    # word produces a half-spoken syllable.
    word_cuts = 0
    for _index, start, end in spans:
        for edge in (start, end):
            word_cuts += sum(1 for w in words if w['start'] < edge < w['end'])

    overlaps = sum(1 for i, (_ci, a1, b1) in enumerate(spans)
                   for _cj, a2, b2 in spans[i + 1:] if max(a1, a2) < min(b1, b2))
    out_of_window = sum(1 for _i, a, b in spans if a < window_in - 1e-6 or b > window_out + 1e-6)

    durations = []
    for candidate in candidates:
        total = sum(float(s['out']) - float(s['in']) for s in candidate.get('source_segments') or [])
        durations.append(total)
    in_range = sum(1 for value in durations if target_min <= value <= target_max)
    ordered = sorted(durations)
    rationales = [len((c.get('rationale') or '').strip()) for c in candidates]

    # Serial Cutter needs Part 1..N in story order, so check whether the order the
    # model returned matches the order of the footage it chose.
    firsts = [float(c['source_segments'][0]['in']) for c in candidates if c.get('source_segments')]
    parts_in_source_order = firsts == sorted(firsts)

    return dict(candidates=len(candidates),
                segments=len(spans),
                word_cuts=word_cuts,
                overlaps=overlaps,
                out_of_window=out_of_window,
                coverage=round(covered / window, 4),
                median_clip_seconds=round(ordered[len(ordered) // 2], 2) if ordered else 0.0,
                clips_in_target_range=in_range,
                within_window=all(a >= window_in - 1e-6 and b <= window_out + 1e-6 for _i, a, b in spans),
                parts_in_source_order=parts_in_source_order,
                median_rationale_chars=int(sorted(rationales)[len(rationales) // 2]) if rationales else 0,
                unique_titles=len({(c.get('title') or '').strip() for c in candidates}))


def evaluate(name, planner, packet, frames, words, cache=None):
    """Run one candidate and measure it. A refusal is a result, not a crash."""
    window_in = float(packet.get('source_in', 0.0))
    window_out = float(packet.get('source_out', packet['duration']))
    started = time.monotonic()
    error = None
    plan = None
    try:
        plan = planner.propose(packet, frames)
    except Exception as failure:                      # noqa: BLE001 - recorded, not hidden
        error = f'{type(failure).__name__}: {failure}'
    seconds = round(time.monotonic() - started, 2)
    row = dict(model=name, ok=plan is not None, error=error, seconds=seconds)
    if plan is not None:
        row.update(score_plan(plan, words, window_in, window_out))
        meta = plan.get('meta') or {}
        row['provider'] = meta.get('provider', getattr(planner, 'PROVIDER', '?'))
    ledger = getattr(planner, 'ledger', None)
    if ledger is None:
        rows = []
    elif hasattr(ledger, 'rows'):
        rows = ledger.rows()
    elif isinstance(ledger, list):
        rows = ledger
    else:
        rows = []
    row['reserved_usd'] = round(sum(float(r.get('reserved_usd') or 0) for r in rows), 6)
    row['reported_usd'] = round(sum(float(r.get('reported_usd') or 0) for r in rows if r.get('reported_usd')), 6)
    if cache is not None:
        from speech import atomic_json
        atomic_json(Path(cache) / (name.replace('/', '_').replace(':', '_') + '.json'), row)
    return row


COLUMNS = [('model', 'Модель', 34), ('ok', 'Ответ', 6), ('seconds', 'Сек', 7),
           ('candidates', 'Клип', 5), ('word_cuts', 'Рез.слов', 9), ('overlaps', 'Повт.', 6),
           ('out_of_window', 'Вне окна', 9), ('coverage', 'Покрыт.', 8),
           ('clips_in_target_range', 'В диапазоне', 12), ('median_clip_seconds', 'Мед.сек', 8),
           ('reported_usd', 'Цена $', 8)]


def markdown_table(rows):
    """Render results as Markdown. Lower word_cuts/overlaps is better; the rest is context."""
    header = '| ' + ' | '.join(title for _key, title, _w in COLUMNS) + ' |'
    line = '|' + '|'.join('---' for _ in COLUMNS) + '|'
    body = []
    for row in rows:
        cells = []
        for key, _title, _w in COLUMNS:
            value = row.get(key)
            if isinstance(value, bool):
                value = 'да' if value else 'НЕТ'
            elif isinstance(value, float):
                value = f'{value:.4f}'.rstrip('0').rstrip('.')
            cells.append('' if value is None else str(value))
        body.append('| ' + ' | '.join(cells) + ' |')
    notes = ['', 'Как читать:', '- **Рез.слов** — сколько границ монтажа разрезало распознанное слово. '
             'Ноль означает, что реплики не оборваны на полуслове. Главный технический показатель.',
             '- **Повт.** — пересечения исходных кадров между клипами; **Вне окна** — попытки выбрать '
             'материал, которого модель не видела.',
             '- **Покрыт.** — доля окна, попавшая в отбор. Близко к 1.0 обычно значит «нарезал всё подряд», '
             'а не «выбрал лучшее».',
             '- Ответ «НЕТ» с текстом ошибки — тоже результат: модель отказала, не прошла валидацию '
             'или упёрлась в квоту.',
             '', 'Ни одна из этих метрик не является прогнозом просмотров. Окончательное решение — '
             'просмотр смонтированного ролика человеком.']
    return '\n'.join([header, line] + body + notes)


def run_benchmark(planners, packet, frames, words, cache=None):
    """Same packet and same frames for every candidate: only the model changes."""
    if not planners:
        raise ValueError('Нет ни одного кандидата для сравнения.')
    rows = [evaluate(name, planner, packet, frames, words, cache) for name, planner in planners]
    return rows, markdown_table(rows)
