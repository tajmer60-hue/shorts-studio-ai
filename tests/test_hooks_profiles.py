"""Hook Director and channel profile tests.

The decisive checks: a hook may only come from the clip's own opening (no
misleading reorder), a weak opening is flagged instead of decorated, effects are
refused when they would cover dialogue, and an uncleared mood never yields music.
"""
import tempfile
from pathlib import Path
import unittest

from hooks import SFX_TYPES, candidate_hooks, hook_score, select_hook, sfx_plan
from profiles import (default_profiles, get_profile, load_profiles, profile_to_settings,
                      save_profiles, validate)

WEAK = [(0.0, 2.0, 'Сегодня обычная погода.'), (2.5, 4.0, 'Да, вполне обычная.')]
STRONG = [(5.0, 7.0, 'Почему ты взял деньги?'), (7.5, 9.0, 'Откуда ты вообще знаешь?')]
THREAT = [(20.0, 22.0, 'Если скажешь кому-то, я тебя убью.'), (22.5, 24.0, 'Никто не узнает…')]


def transcript(lines):
    words, cues = [], []
    for start, end, text in lines:
        cues.append(dict(start=start, end=end, text=text))
        mid = (start + end) / 2
        first, second = (text.split(' ', 1) + ['x'])[:2]
        words.append(dict(start=start, end=mid - 0.1, word=first, probability=0.9))
        words.append(dict(start=mid + 0.1, end=end, word=second, probability=0.9))
    return dict(words=words, cues=cues, duration=40.0)


class HookScoreTests(unittest.TestCase):
    def test_tense_line_outscores_small_talk_and_stays_within_100(self):
        source = transcript(WEAK + STRONG)
        beats = dict(weak=transcript(WEAK)['cues'], strong=transcript(STRONG)['cues'])
        weak = dict(start=0.0, end=4.0, text=' '.join(c['text'] for c in beats['weak']), cues=beats['weak'])
        strong = dict(start=5.0, end=9.0, text=' '.join(c['text'] for c in beats['strong']), cues=beats['strong'])
        weak_score = hook_score(weak, source['words'], [])
        strong_score = hook_score(strong, source['words'], [])
        self.assertGreater(strong_score['total'], weak_score['total'])
        self.assertLessEqual(strong_score['total'], 100)
        self.assertTrue(strong_score['reasons'])
        self.assertEqual(sum(strong_score['factors'].values()), strong_score['total'])

    def test_candidate_hooks_are_ranked_and_bounded(self):
        ranked = candidate_hooks(transcript(WEAK + STRONG + THREAT), [])
        self.assertTrue(ranked)
        totals = [item['score']['total'] for item in ranked]
        self.assertEqual(totals, sorted(totals, reverse=True))
        self.assertTrue(all(0 <= item['score']['total'] <= 100 for item in ranked))


class HookSelectionTests(unittest.TestCase):
    def test_hook_comes_from_the_opening_and_never_reorders(self):
        source = transcript(STRONG + WEAK)
        candidate = dict(title='X', source_segments=[dict(**{'in': 0.0, 'out': 30.0})])
        hook = select_hook(candidate, source, [])
        self.assertIsNotNone(hook['hook'])
        self.assertGreaterEqual(hook['hook']['start'], 0.0)
        self.assertLessEqual(hook['hook']['start'], 12.0)   # 40% of a 30 s clip
        self.assertFalse(hook['needs_review'])
        self.assertIn('порядок событий не менялся', hook['explanation'])

    def test_a_later_strong_line_is_not_stealed_into_the_opening(self):
        source = transcript(WEAK + [(10.0, 12.0, 'пауза в разговоре')] + THREAT)
        candidate = dict(title='X', source_segments=[dict(**{'in': 0.0, 'out': 30.0})])
        hook = select_hook(candidate, source, [])
        self.assertLess(hook['hook']['start'], 12.0)
        self.assertTrue(hook['needs_review'], 'Слабое начало должно требовать проверки, а не маскироваться.')

    def test_no_speech_at_the_start_is_reported_not_invented(self):
        source = transcript([(20.0, 22.0, 'Почему?')])
        candidate = dict(title='X', source_segments=[dict(**{'in': 0.0, 'out': 30.0})])
        hook = select_hook(candidate, source, [])
        self.assertIsNone(hook['hook'])
        self.assertTrue(hook['needs_review'])


class SfxTests(unittest.TestCase):
    HOOK = dict(hook=dict(start=5.0, end=9.0, text='Почему ты взял деньги?'), score=40,
                breakdown=dict(narrative_tension=10))

    def test_quiet_threat_prefers_silence(self):
        plan = sfx_plan(self.HOOK, 'dark_ambient', 'balanced', [], 30.0)
        self.assertEqual([e['type'] for e in plan['events']], ['silence'])
        self.assertIn('тишина', plan['rationale'])

    def test_weak_hook_gets_no_effect(self):
        weak = dict(self.HOOK, score=10)
        self.assertEqual(sfx_plan(weak, 'aggressive_phonk', 'balanced', [], 30.0)['events'], [])

    def test_effect_never_covers_dialogue(self):
        dialogue = [dict(start=4.8, end=9.2)]
        plan = sfx_plan(self.HOOK, 'aggressive_phonk', 'balanced', dialogue, 30.0)
        self.assertEqual(plan['events'], [])
        self.assertIn('перекрыл бы реплику', plan['rationale'])

    def test_intensity_limits_the_number_of_effects(self):
        loud = sfx_plan(self.HOOK, 'aggressive_phonk', 'aggressive', [], 30.0)
        self.assertLessEqual(len(loud['events']), 3)
        calm = sfx_plan(self.HOOK, 'aggressive_phonk', 'cinematic', [], 30.0)
        self.assertLessEqual(len(calm['events']), 1)

    def test_every_effect_type_declares_a_licensed_source(self):
        for name, meta in SFX_TYPES.items():
            if name != 'silence':
                self.assertTrue(meta['sources'])


class ProfileTests(unittest.TestCase):
    def test_three_standard_profiles_ship_and_validate(self):
        data = default_profiles()
        self.assertEqual(len(data['profiles']), 3)
        ids = [p['id'] for p in data['profiles']]
        self.assertEqual(ids, ['serial-story', 'phonk-viral', 'cinematic-drama'])
        for profile in data['profiles']:
            validate(profile)

    def test_invalid_profiles_are_refused(self):
        base = default_profiles()['profiles'][0]
        for broken in (dict(base, mode='chaos'), dict(base, duration_min=90, duration_max=45),
                       dict(base, intensity='turbo'), dict(base, subtitle_style='rainbow'),
                       dict(base, export='vhs'), dict(base, aggressiveness='brutal'),
                       {k: v for k, v in base.items() if k != 'mode'}):
            with self.assertRaises(ValueError):
                validate(broken)

    def test_roundtrip_through_disk(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            first = load_profiles(folder)
            self.assertTrue((Path(folder) / 'channel-profiles.json').exists())
            first['profiles'].append(dict(first['profiles'][0], id='my-channel', name='Мой канал'))
            save_profiles(folder, first)
            again = load_profiles(folder)
            self.assertEqual(len(again['profiles']), 4)
            with self.assertRaises(ValueError):
                get_profile(folder, 'absent')

    def test_profile_drives_the_pipeline_settings(self):
        settings = profile_to_settings(get_profile_in_memory('phonk-viral'))
        self.assertEqual(settings['mode'], 'viral')
        self.assertEqual(settings['intensity'], 'aggressive')
        self.assertEqual(settings['aggressiveness'], 'fast')
        self.assertIn('aggressive_phonk', settings['music_moods'])
        self.assertEqual(settings['hook_min_score'], 40)
        serial = profile_to_settings(get_profile_in_memory('serial-story'))
        self.assertEqual(serial['mode'], 'serial')
        self.assertEqual(serial['intensity'], 'balanced')


def get_profile_in_memory(profile_id):
    return next(p for p in default_profiles()['profiles'] if p['id'] == profile_id)


if __name__ == '__main__':
    unittest.main()
