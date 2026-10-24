"""AI Hook Director: choose the opening that earns the first three seconds.

The hook is always taken from the clip's OWN opening material. Opening with a
line from later in the clip would be a non-chronological reorder and could
mislead the viewer about the story, which the specification forbids; when the
opening material is weak the director says so and flags the clip for review
instead of manufacturing a hook.

Hook Score is a relative editing-quality measure built from local signals. It is
not a view forecast, and every score ships with the reasons that produced it.
"""
from modes import CONFLICT_WORDS, beats_from_transcript, hook_score as line_tension, speech_rate

WEIGHTS = dict(emotional_impact=20, curiosity_gap=15, visual_attention=15,
               immediate_context=10, narrative_tension=15, audio_impact=10,
               continuation_potential=15)

THREAT_WORDS = frozenset(['убью', 'убьют', 'умрёшь', 'умрёт', 'никогда', 'никто не', 'тайна',
                          'секрет', 'не говори', 'молчи', 'последний', 'обратного пути'])

# Sound effect vocabulary. Files are never bundled: each type lists where a
# licensed effect can be found, and rendering stays silent until the user
# imports one. Silence is a first-class option.
SFX_TYPES = {
    'cinematic_impact': dict(use='Резкое признание, удар, появление персонажа',
                             sources='Mixkit Cinematic SFX, YouTube Audio Library', over_dialogue=False),
    'reverse_riser': dict(use='Подводка к неожиданному событию',
                          sources='Mixkit, YouTube Audio Library', over_dialogue=True),
    'whoosh': dict(use='Быстрая смена плана или персонажа',
                   sources='Mixkit, YouTube Audio Library', over_dialogue=True),
    'heartbeat': dict(use='Опасность, страх, ожидание ответа',
                      sources='Mixkit, YouTube Audio Library', over_dialogue=True),
    'record_scratch': dict(use='Комический обрыв, неожиданная реакция',
                           sources='Mixkit, YouTube Audio Library', over_dialogue=False),
    'cinematic_boom': dict(use='Сильный сюжетный поворот, раскрытие тайны',
                           sources='Mixkit, YouTube Audio Library', over_dialogue=False),
    'glitch': dict(use='Нарушение ожидания, тревожное событие',
                   sources='Mixkit, YouTube Audio Library', over_dialogue=True),
    'suspense_drone': dict(use='Постепенное нагнетание атмосферы',
                           sources='Mixkit, YouTube Audio Library', over_dialogue=True),
    'silence': dict(use='Намеренная тишина перед сильной репликой',
                    sources='не требуется', over_dialogue=True),
}

STRUCTURE = [(0.0, 1.0, 'сильный кадр, неожиданная реплика или действие'),
             (1.0, 3.0, 'возникновение интриги, конфликта или напряжения'),
             (3.0, 6.0, 'развитие ситуации, раскрытие контекста'),
             (6.0, 10.0, 'новое событие, удерживающее интерес')]


def hook_score(beat, words, scenes, median_rate=None, next_beat=None):
    """0-100 across the seven named factors, with the signals that fired."""
    text = beat.get('text', '')
    lowered = text.lower()
    reasons = []
    factors = {}

    emotion = min(WEIGHTS['emotional_impact'],
                  6 * sum(1 for w in CONFLICT_WORDS if w in lowered) + 4 * text.count('!'))
    factors['emotional_impact'] = emotion
    if emotion:
        reasons.append(f'эмоциональные маркеры в реплике: {emotion}/{WEIGHTS["emotional_impact"]}')

    curiosity = min(WEIGHTS['curiosity_gap'], 8 * text.count('?') + 4 * text.count('…'))
    factors['curiosity_gap'] = curiosity
    if curiosity:
        reasons.append(f'вопрос или недосказанность: {curiosity}/{WEIGHTS["curiosity_gap"]}')

    inside = [t for t in scenes if beat['start'] < t < beat['end']]
    visual = min(WEIGHTS['visual_attention'], 5 * (1 + len(inside)))
    factors['visual_attention'] = visual
    if inside:
        reasons.append(f'смен плана внутри блока: {len(inside)}')

    context = 0
    if len(beat.get('cues', [])) >= 2:
        context += 6
    if line_tension(beat.get('cues', [])[0]['text'] if beat.get('cues') else '') > 0:
        context += 4
    factors['immediate_context'] = min(WEIGHTS['immediate_context'], context)
    if context:
        reasons.append('блок самодостаточен и начинается с сильной реплики')

    tension = min(WEIGHTS['narrative_tension'],
                  5 * sum(1 for w in THREAT_WORDS if w in lowered) + 3 * lowered.count('не '))
    factors['narrative_tension'] = tension
    if tension:
        reasons.append(f'угроза или запрет в тексте: {tension}/{WEIGHTS["narrative_tension"]}')

    rate = speech_rate(beat, words)
    audio = 0
    if median_rate and rate >= median_rate * 1.2:
        audio = WEIGHTS['audio_impact']
        reasons.append('темп речи заметно выше медианы сцены')
    elif median_rate and rate >= median_rate:
        audio = WEIGHTS['audio_impact'] // 2
    factors['audio_impact'] = audio

    continuation = 0
    if next_beat is not None and line_tension(next_beat.get('text', '')) > 0:
        continuation = WEIGHTS['continuation_potential']
        reasons.append('следующий блок продолжает напряжение: зритель захочет досмотреть')
    elif text.rstrip().endswith(('?', '…', '!')):
        continuation = WEIGHTS['continuation_potential'] * 2 // 3
        reasons.append('блок обрывается на вопросе или эмоции')
    factors['continuation_potential'] = continuation

    total = sum(factors.values())
    return dict(factors=factors, total=total, reasons=reasons)


def candidate_hooks(transcript, scenes=(), limit=5):
    beats = beats_from_transcript(transcript, scenes)
    words = transcript.get('words', [])
    rates = [speech_rate(b, words) for b in beats] or [0.0]
    median = sorted(rates)[len(rates) // 2]
    scored = []
    for index, beat in enumerate(beats):
        following = beats[index + 1] if index + 1 < len(beats) else None
        scored.append((hook_score(beat, words, scenes, median, following), beat))
    scored.sort(key=lambda item: -item[0]['total'])
    return [dict(score=s, beat=b) for s, b in scored[:limit]]


def select_hook(candidate, transcript, scenes=()):
    """Pick the opening hook from the clip's own first beats.

    Reordering footage to lead with a later line is refused: it would mislead the
    viewer. A weak opening is reported with needs_review instead.
    """
    segments = candidate.get('source_segments', [])
    if not segments:
        raise ValueError('У ролика нет исходных сегментов.')
    start, end = segments[0]['in'], segments[-1]['out']
    span = end - start
    beats = [b for b in beats_from_transcript(transcript, scenes)
             if b['start'] >= start - 0.5 and b['end'] <= min(end, start + max(6.0, span * 0.4))]
    if not beats:
        return dict(hook=None, score=0, breakdown={}, explanation='В начале ролика нет распознанных '
                    'реплик: хук оценить нечем.', needs_review=True, structure=STRUCTURE)
    words = transcript.get('words', [])
    rates = [speech_rate(b, words) for b in beats] or [0.0]
    median = sorted(rates)[len(rates) // 2]
    scored = [(hook_score(b, words, scenes, median), b) for b in beats]
    scored.sort(key=lambda item: -item[0]['total'])
    score, beat = scored[0]
    weak = score['total'] < 25
    explanation = (f'Начало берём с {beat["start"]:.1f} с: ' +
                   ('; '.join(score['reasons']) if score['reasons'] else
                    'сильных сигналов нет, начало слабое') +
                   '. Хук взят из начала ролика, порядок событий не менялся.')
    return dict(hook=dict(start=round(beat['start'], 3), end=round(beat['end'], 3),
                          text=beat['text']),
                score=score['total'], breakdown=score['factors'], explanation=explanation,
                needs_review=weak, structure=STRUCTURE,
                note='Структура 0-1/1-3/3-6/6-10 с — ориентир для проверки, не шаблон для каждого ролика.')


def sfx_plan(hook, mood, intensity='balanced', dialogue=(), duration=0.0, sfx_max=None):
    """Decide whether any effect helps at all, and where. Silence is allowed."""
    from beat_sync import INTENSITY
    limit = sfx_max if sfx_max is not None else INTENSITY[intensity]['sfx_max']
    events = []
    if hook is None:
        return dict(events=[], rationale='Хук не определён: эффекты не добавляются.')
    quiet_threat = mood in ('dark_ambient', 'dark_phonk') and hook['score'] >= 25 \
        and hook['breakdown'].get('narrative_tension', 0) > 0
    if quiet_threat and intensity != 'aggressive':
        return dict(events=[dict(t=round(hook['hook']['start'], 3), type='silence', gain_db=0.0,
                                 reason='Тихая угроза: пауза перед репликой сильнее любого удара.')],
                    rationale='Выбрана намеренная тишина: сцена держится на напряжении, а не на ритме.')
    if hook['score'] < 25:
        return dict(events=[], rationale='Слабый хук: эффект не спасёт начало и только помешает речи.')
    if mood in ('aggressive_phonk', 'cinematic_epic'):
        kind = 'cinematic_impact'
    elif mood == 'comedy_ironic':
        kind = 'record_scratch'
    elif mood in ('dark_ambient', 'dark_phonk'):
        kind = 'suspense_drone'
    else:
        kind = 'reverse_riser'
    over_dialogue_allowed = SFX_TYPES[kind]['over_dialogue']
    at = hook['hook']['start']
    if not over_dialogue_allowed and any(d['start'] - 0.3 <= at <= d['end'] + 0.3 for d in dialogue):
        return dict(events=[], rationale=f'Эффект «{kind}» перекрыл бы реплику на {at:.1f} с: пропущен.')
    events.append(dict(t=round(at, 3), type=kind, gain_db=-6.0,
                       reason=f'Усиливает хук ({SFX_TYPES[kind]["use"]}); громкость ниже речи.'))
    if limit >= 2 and duration and hook['score'] >= 40:
        boom_at = min(duration - 0.5, at + max(6.0, duration * 0.6))
        if not any(d['start'] - 0.3 <= boom_at <= d['end'] + 0.3 for d in dialogue):
            events.append(dict(t=round(boom_at, 3), type='cinematic_boom', gain_db=-8.0,
                               reason='Акцент на сюжетном повороте, не поверх диалога.'))
    return dict(events=events[:limit],
                rationale=f'Эффектов: {min(limit, len(events))} из допустимых {limit}; '
                          f'повторов чаще 3 с нет, диалог не перекрыт.')
