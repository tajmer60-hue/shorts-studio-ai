"""Synthetic benchmark window: a realistic Russian dialogue with planted beats.

Authored, not recorded, on purpose: the benchmark must be reproducible and must
not send anyone's media to a provider. Ground truth is the set of intervals that
carry conflict, revelation or a hook; everything else is deliberate filler that a
good editor should skip.
"""

# (start, end, text, kind) where kind in {'filler','strong'}
LINES = [
    (0.0, 6.0, 'В прошлой серии мы остановились на том, как герои вернулись домой.', 'filler'),
    (6.5, 12.0, 'Сегодня мы продолжим нашу историю и посмотрим, что будет дальше.', 'filler'),
    (13.0, 24.0, 'Спонсор выпуска — компания, которая делает очень хорошие стулья.', 'filler'),
    (30.0, 34.0, 'Почему ты взял деньги из кассы?', 'strong'),
    (36.0, 40.0, 'Откуда ты вообще об этом знаешь?', 'strong'),
    (42.0, 47.0, 'Я верну всё завтра, клянусь, я не смог иначе!', 'strong'),
    (49.0, 53.0, 'Ты предал не компанию, ты предал меня.', 'strong'),
    (55.0, 75.0, 'И что теперь делать, ты мне скажи, что теперь делать?', 'strong'),
    (80.0, 86.0, 'Кстати, завтра обещают дождь, надо бы взять зонт.', 'filler'),
    (88.0, 95.0, 'Да, и ещё нужно заказать бумагу для принтера, она закончилась.', 'filler'),
    (97.0, 105.0, 'Хорошо, я запишу, чтобы не забыть, запишу прямо сейчас.', 'filler'),
    (107.0, 140.0, 'И ещё водитель приедет после обеда, наверное, часов в пять.', 'filler'),
    (145.0, 150.0, 'Смотри, что я нашёл в старом сейфе отца!', 'strong'),
    (152.0, 157.0, 'Это же ключи от того самого склада, серьезно?', 'strong'),
    (159.0, 166.0, 'Погоди, но ведь склад продали ещё десять лет назад!', 'strong'),
    (168.0, 200.0, 'Значит, кто-то всё это время платил за аренду вместо нас.', 'strong'),
    (205.0, 212.0, 'Ладно, давай потом, сейчас просто поедим, я ужасно голоден.', 'filler'),
    (214.0, 222.0, 'Ты всегда голоден, это уже можно изучать отдельно.', 'filler'),
    (224.0, 260.0, 'Передай соль, пожалуйста, и хлеб, если он там ещё остался.', 'filler'),
    (265.0, 270.0, 'Если ты кому-то скажешь про ключи, я тебя убью.', 'strong'),
    (272.0, 278.0, 'Никто не узнает, ты меня знаешь, никто не узнает.', 'strong'),
    (280.0, 295.0, 'Вот и правильно, потому что обратного пути у нас уже нет.', 'strong'),
]

WINDOW_IN = 0.0
WINDOW_OUT = 300.0

GROUND_TRUTH = [(30.0, 75.0), (145.0, 200.0), (265.0, 295.0)]
FILLER = [(0.0, 24.0), (80.0, 140.0), (205.0, 260.0)]


def transcript():
    """Words and cues derived from LINES with small natural gaps inside a line."""
    words, cues = [], []
    for start, end, text, _kind in LINES:
        parts = text.split()
        cues.append(dict(start=start, end=end, text=text))
        n = len(parts)
        span = (end - start) / max(1, n)
        for i, token in enumerate(parts):
            w_start = start + i * span
            w_end = w_start + span * 0.86
            words.append(dict(start=round(w_start, 3), end=round(w_end, 3),
                              word=token, probability=0.95))
    return dict(words=words, cues=cues, duration=WINDOW_OUT)


def packet():
    t = transcript()
    return dict(duration=WINDOW_OUT, source_in=WINDOW_IN, source_out=WINDOW_OUT,
                dialogue=[c for c in t['cues'] if c['end'] > WINDOW_IN and c['start'] < WINDOW_OUT],
                task='Select coherent stories in this window. Absolute source timestamps. '
                     'Do not fabricate missing context.')


def ground_truth_hits(plan, truth=GROUND_TRUTH, filler=FILLER, iou_min=0.35):
    """How many planted beats a plan actually captured, and how much filler it kept.

    A clip counts as a hit when at least iou_min of its footage lies inside one
    planted beat. Filler ratio is the share of selected footage inside filler
    intervals. Both are reported, never merged into a single fake quality score.
    """
    hits, filler_seconds, total = set(), 0.0, 0.0
    for candidate in plan.get('candidates', []):
        for seg in candidate.get('source_segments', []):
            a, b = float(seg['in']), float(seg['out'])
            if b <= a:
                continue
            total += b - a
            for gi, (g0, g1) in enumerate(truth):
                overlap = max(0.0, min(b, g1) - max(a, g0))
                if overlap / (b - a) >= iou_min:
                    hits.add(gi)
            for f0, f1 in filler:
                filler_seconds += max(0.0, min(b, f1) - max(a, f0))
    return dict(beats_found=len(hits), beats_total=len(truth),
                filler_seconds=round(filler_seconds, 1),
                selected_seconds=round(total, 1),
                filler_ratio=round(filler_seconds / total, 3) if total else 0.0)
