"""Mode algorithm tests: chronology, word safety, honesty about weak material.

The transcript is synthetic but the checks are the real acceptance criteria from
Issue #2 section 2: parts stay chronological and causally whole, pauses are
trimmed without touching words, and a lack of material is reported instead of
being padded to a quota.
"""
import unittest

from ai_contract import validate_ai_plan
from cloud_planner import unique_chronological
from edit_plan import remap_words
from modes import (MODE_PROMPTS, beats_from_transcript, hook_score, plan_for_mode,
                   safe_pause_cuts, serial_plan, viral_plan)


def transcript(lines, word_gap_inside=0.4):
    """Build words+cues from (start, end, text) lines; two words per line."""
    words, cues = [], []
    for start, end, text in lines:
        mid = start + (end - start) / 2
        half = text.split(' ', 1)
        first, second = (half + [half[0]])[:2]
        words.append(dict(start=round(start, 3), end=round(mid - word_gap_inside / 2, 3),
                          word=first, probability=0.95))
        words.append(dict(start=round(mid + word_gap_inside / 2, 3), end=round(end, 3),
                          word=second, probability=0.95))
        cues.append(dict(start=round(start, 3), end=round(end, 3), text=text))
    return dict(words=words, cues=cues, duration=max(end for _s, end, _t in lines) + 5)


LINES = [
    (0.0, 2.0, 'Почему ты взял деньги?'),
    (2.5, 4.0, 'Откуда ты вообще знаешь?'),
    (6.0, 8.0, 'Я верну всё завтра, клянусь.'),
    (8.5, 10.0, 'Прости, я не смог иначе.'),
    (12.0, 14.0, 'Просто уходи сейчас.'),
    (16.0, 18.0, 'Сегодня хорошая погода.'),
    (18.5, 20.0, 'Да, вполне обычная.'),
    (22.0, 24.0, 'Никто не узнает правду.'),
    (24.5, 26.0, 'Ты серьёзно это сказал?'),
]
SCENES = [5.5, 11.5, 15.5, 21.5]


class BeatTests(unittest.TestCase):
    def test_beats_split_on_silence_and_scene_cuts(self):
        beats = beats_from_transcript(transcript(LINES), SCENES, beat_gap=1.2)
        self.assertGreaterEqual(len(beats), 4)
        for beat in beats:
            self.assertLess(beat['start'], beat['end'])
            self.assertTrue(beat['text'].strip())

    def test_hook_score_sees_questions_and_conflict(self):
        self.assertGreater(hook_score('Почему ты взял деньги?'), 0)
        self.assertGreater(hook_score('Я верну всё завтра, клянусь.'), 0)
        self.assertEqual(hook_score('Сегодня хорошая погода.'), 0)


class PauseTests(unittest.TestCase):
    def test_cuts_never_touch_words(self):
        words = transcript(LINES)['words']
        cuts = safe_pause_cuts(words, 0.0, 30.0, threshold=0.3)
        self.assertTrue(cuts)
        for cut in cuts:
            for word in words:
                overlaps = cut['start'] < word['end'] and word['start'] < cut['end']
                self.assertFalse(overlaps, f'Вырез задел слово {word["word"]}')

    def test_edges_of_a_line_are_protected(self):
        # A pause hugging the start of the part may carry a reaction lead-in.
        words = [dict(start=0.0, end=0.2, word='а'), dict(start=0.6, end=2.0, word='б')]
        self.assertEqual(safe_pause_cuts(words, 0.0, 2.0, threshold=0.3), [])
        words_end = [dict(start=0.0, end=1.0, word='а'), dict(start=1.4, end=1.6, word='б')]
        self.assertEqual(safe_pause_cuts(words_end, 0.0, 1.8, threshold=0.3), [])

    def test_threshold_is_respected(self):
        words = [dict(start=0.0, end=1.0, word='а'), dict(start=1.4, end=2.0, word='б'),
                 dict(start=4.0, end=5.0, word='в'), dict(start=7.0, end=8.0, word='г')]
        soft = safe_pause_cuts(words, 0.0, 20.0, threshold=1.5)
        fast = safe_pause_cuts(words, 0.0, 20.0, threshold=0.3)
        self.assertLess(len(soft), len(fast))


class SerialTests(unittest.TestCase):
    def setUp(self):
        self.plan = serial_plan(transcript(LINES), SCENES, duration_range=(4.0, 12.0))

    def test_validates_against_the_shared_contract(self):
        clean = validate_ai_plan(self.plan, 40.0)
        self.assertEqual(len(clean['candidates']), len(self.plan['candidates']))
        self.assertTrue(all(c['needs_review'] for c in clean['candidates']))

    def test_parts_are_chronological_and_disjoint(self):
        spans = [(c['source_segments'][0]['in'], c['source_segments'][-1]['out'])
                 for c in self.plan['candidates']]
        self.assertEqual(spans, sorted(spans))
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            self.assertLessEqual(a1, b0 + 1e-6)

    def test_pause_cuts_survive_word_remap(self):
        words = transcript(LINES)['words']
        for candidate in self.plan['candidates']:
            cuts = candidate.get('pause_cuts', [])
            if not cuts:
                continue
            from edit_plan import build_plan
            plan = build_plan('src', candidate['source_segments'][0]['in'],
                              candidate['source_segments'][-1]['out'], cuts)
            remap_words(words, plan)          # raises if a cut splits a word

    def test_rationale_mentions_what_was_done(self):
        text = ' '.join(c['rationale'] for c in self.plan['candidates'])
        self.assertIn('Хронология сохранена', text)
        self.assertTrue('пауз' in text.lower())

    def test_weak_material_is_reported_not_padded(self):
        poor = transcript([(0.0, 2.0, 'Сегодня хорошая погода.'), (4.0, 6.0, 'Да, обычная.')])
        plan = serial_plan(poor, [], duration_range=(4.0, 12.0), minimum_parts=6)
        self.assertLess(len(plan['candidates']), 6)
        self.assertIn('Слабые моменты не добавлены', plan['reason'])

    def test_invalid_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            serial_plan(transcript(LINES), [], duration_range=(0, 5))
        with self.assertRaises(ValueError):
            serial_plan(transcript(LINES), [], aggressiveness='brutal')

    def test_empty_transcript_explains_itself(self):
        plan = serial_plan(dict(words=[], cues=[], duration=10), [])
        self.assertEqual(plan['candidates'], [])
        self.assertIn('нет распознанных реплик', plan['reason'])


class ViralTests(unittest.TestCase):
    def setUp(self):
        self.plan = viral_plan(transcript(LINES), SCENES, duration_range=(2.0, 12.0))

    def test_selects_the_tense_exchanges_and_skips_small_talk(self):
        titles = ' '.join(c['title'] for c in self.plan['candidates'])
        self.assertTrue(any(word in titles for word in ('деньги', 'Почему')))
        self.assertNotIn('погода', titles.lower())

    def test_moments_do_not_repeat_footage(self):
        result = unique_chronological(self.plan)
        self.assertEqual(result['rejected'], [])

    def test_every_rationale_lists_the_signals_that_fired(self):
        for candidate in self.plan['candidates']:
            self.assertIn('оценка', candidate['rationale'])
            self.assertGreaterEqual(candidate['score'], 2)

    def test_scores_are_labelled_as_heuristics(self):
        self.assertTrue(any('не прогноз просмотров' in line for line in self.plan['limitations']))

    def test_top_k_and_floor_are_honoured(self):
        limited = viral_plan(transcript(LINES), SCENES, duration_range=(2.0, 12.0), top_k=2)
        self.assertLessEqual(len(limited['candidates']), 2)
        strict = viral_plan(transcript(LINES), SCENES, duration_range=(2.0, 12.0), score_floor=99)
        self.assertEqual(strict['candidates'], [])
        self.assertIn('ради количества', strict['reason'])

    def test_empty_transcript_explains_itself(self):
        plan = viral_plan(dict(words=[], cues=[], duration=10), [])
        self.assertEqual(plan['candidates'], [])
        self.assertIn('не из чего', plan['reason'])


class DispatchTests(unittest.TestCase):
    def test_modes_are_distinct_algorithms(self):
        source = transcript(LINES)
        serial = plan_for_mode('serial', source, SCENES, (4.0, 12.0))
        viral = plan_for_mode('viral', source, SCENES, (2.0, 12.0))
        self.assertEqual(serial['mode'], 'serial')
        self.assertEqual(viral['mode'], 'viral')
        self.assertNotEqual(len(serial['candidates']), 0)
        self.assertNotEqual(len(viral['candidates']), 0)

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            plan_for_mode('random', transcript(LINES))

    def test_mode_prompts_are_distinct_and_guard_against_injection(self):
        import re as _re
        for name, prompt in MODE_PROMPTS.items():
            flat = _re.sub(r'\s+', ' ', prompt.lower())
            self.assertIn('untrusted source material', flat)
            self.assertIn('cut inside a recognized word', flat)
        self.assertIn('chronological', MODE_PROMPTS['serial'])
        self.assertIn('independent', MODE_PROMPTS['viral'])


if __name__ == '__main__':
    unittest.main()
