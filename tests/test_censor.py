"""Smart Censor tests. Detection quality is not asserted here: these checks prove
that nothing is muted or blurred without an explicit user approval, that neutral
vocabulary is not censored, and that fabricated regions are rejected."""
import json
from pathlib import Path
import tempfile
import unittest

from censor import (audio_ops, blur_filtergraph, clamp_box, dictionary, merge_tracks,
                    profanity_hits, remap_ops, scan_packet, validate_scan, volume_filter)

WORDS = [
    dict(start=0.0, end=0.4, word='Ну', probability=0.95),
    dict(start=0.5, end=1.1, word='сука', probability=0.93),
    dict(start=1.2, end=1.8, word='поставь', probability=0.97),
    dict(start=1.9, end=2.3, word='сумку', probability=0.96),
    dict(start=2.4, end=2.9, word='блядь', probability=0.90),
    dict(start=3.0, end=3.6, word='хренотень', probability=0.94),
    dict(start=3.7, end=4.2, word='мудрый', probability=0.98),
    dict(start=4.3, end=4.8, word='хулиган', probability=0.97),
    dict(start=5.0, end=5.5, word='ебать', probability=0.28),
    dict(start=6.0, end=6.5, word='you', probability=0.99),
    dict(start=6.6, end=7.2, word='fucking', probability=0.92),
    dict(start=8.0, end=8.6, word='кзаапп', probability=0.21),
]


class ProfanityTests(unittest.TestCase):
    def setUp(self):
        self.hits = profanity_hits(WORDS)
        self.by_word = {hit['word']: hit for hit in self.hits}

    def test_inflected_roots_are_found(self):
        for word in ('сука', 'блядь', 'ебать', 'fucking'):
            self.assertIn(word, self.by_word, word)

    def test_neutral_vocabulary_is_not_censored(self):
        for word in ('сумку', 'мудрый', 'хулиган', 'поставь', 'Ну', 'you'):
            self.assertNotIn(word, self.by_word, word)

    def test_low_asr_confidence_is_flagged_not_silently_muted(self):
        matched = self.by_word['ебать']
        self.assertEqual(matched['confidence'], 'low')
        self.assertEqual(matched['term'], 'еба')
        self.assertFalse(matched['approved'])
        self.assertIn('неуверенно', matched['reason'])
        self.assertEqual(self.by_word['сука']['confidence'], 'high')

    def test_unmatched_uncertain_word_becomes_a_review_item(self):
        hit = self.by_word['кзаапп']
        self.assertIsNone(hit['term'])
        self.assertEqual(hit['confidence'], 'low')
        self.assertIn('не уверен', hit['reason'])

    def test_user_dictionary_extends_and_overrides(self):
        lexicon = dictionary()
        lexicon['extra'] = ['хренотень']
        lexicon['whitelist'] = lexicon['whitelist'] + ['сука']
        hits = {hit['word'] for hit in profanity_hits(WORDS, lexicon)}
        self.assertIn('хренотень', hits)
        self.assertNotIn('сука', hits)

    def test_dictionary_file_is_created_with_a_version(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            path = Path(folder) / 'censor.json'
            data = dictionary(path)
            self.assertEqual(data['schema'], 1)
            self.assertTrue(path.exists())
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['schema'], 1)
            with self.assertRaises(ValueError):
                dictionary(Path(__file__))

    def test_words_without_valid_timestamps_are_skipped(self):
        broken = [dict(start=None, end=1.0, word='сука'), dict(start=2.0, end=2.0, word='блядь'),
                  dict(start=float('nan'), end=3.0, word='сука')]
        self.assertEqual(profanity_hits(broken), [])


class AudioOpTests(unittest.TestCase):
    def test_unapproved_hits_never_produce_intervals(self):
        hits = profanity_hits(WORDS)
        self.assertEqual(audio_ops(hits), [])
        self.assertIsNone(volume_filter(audio_ops(hits)))

    def test_approved_hits_snap_to_word_boundaries(self):
        hits = profanity_hits(WORDS)
        for hit in hits:
            if hit['confidence'] == 'high':
                hit['approved'] = True
        ops = audio_ops(hits, padding=0.02)
        words = {tuple(round(value, 3) for value in (op['start'], op['end'])) for op in ops}
        self.assertIn((0.48, 1.12), words)
        expression = volume_filter(ops)
        self.assertIn('volume=0', expression)
        self.assertIn('between(t,0.480,1.120)', expression)

    def test_uncertain_word_cannot_be_auto_approved(self):
        hits = profanity_hits(WORDS)
        uncertain = next(hit for hit in hits if hit['term'] is None)
        uncertain['approved'] = True
        with self.assertRaises(ValueError):
            audio_ops([uncertain])

    def test_adjacent_intervals_merge_and_padding_is_bounded(self):
        hits = [dict(start=1.0, end=1.5, word='a', term='a', approved=True, confidence='high'),
                dict(start=1.52, end=2.0, word='b', term='b', approved=True, confidence='high')]
        self.assertEqual(len(audio_ops(hits, padding=0.02)), 1)
        with self.assertRaises(ValueError):
            audio_ops(hits, padding=5)
        with self.assertRaises(ValueError):
            audio_ops(hits, mode='delete')

    def test_bleep_mode_is_not_silenced_by_the_mute_filter(self):
        ops = [dict(start=1.0, end=2.0, mode='bleep', word='x')]
        self.assertIsNone(volume_filter(ops))


class RemapTests(unittest.TestCase):
    SEGMENTS = [dict(source_in=10.0, source_out=20.0, output_in=0.0),
                dict(source_in=40.0, source_out=50.0, output_in=10.0)]

    def test_intervals_move_into_output_time(self):
        result = remap_ops([dict(start=12.0, end=13.0, mode='mute', word='x')], self.SEGMENTS)
        self.assertEqual(result['ops'][0]['start'], 2.0)
        self.assertEqual(result['ops'][0]['end'], 3.0)
        self.assertEqual(result['conflicts'], [])

    def test_interval_split_by_a_cut_is_reported(self):
        result = remap_ops([dict(start=19.0, end=21.0, mode='mute', word='x')], self.SEGMENTS)
        self.assertEqual(result['ops'], [])
        self.assertIn('границу монтажа', result['conflicts'][0]['reason'])

    def test_interval_outside_every_segment_is_reported(self):
        result = remap_ops([dict(start=25.0, end=26.0, mode='mute', word='x')], self.SEGMENTS)
        self.assertEqual(result['ops'], [])
        self.assertIn('не попал', result['conflicts'][0]['reason'])


class VisualTests(unittest.TestCase):
    def test_box_is_clamped_inside_the_frame_with_a_margin(self):
        box = clamp_box(dict(x=0.02, y=0.6, w=0.2, h=0.3), 1920, 1080)
        self.assertLessEqual(box['x'] + box['w'], 1.0001)
        self.assertLessEqual(box['y'] + box['h'], 1.0001)
        self.assertLess(box['x'], 0.02)
        self.assertGreater(box['w'], 0.2)
        self.assertGreater(box['h'], 0.3)

    def test_box_touching_the_edge_stays_inside_after_margin(self):
        box = clamp_box(dict(x=0.0, y=0.0, w=1.0, h=1.0), 1920, 1080)
        self.assertEqual((box['x'], box['y']), (0.0, 0.0))
        self.assertLessEqual(box['w'], 1.0)
        self.assertLessEqual(box['h'], 1.0)
        self.assertGreaterEqual(box['w'], 16 / 1920)

    def test_malformed_boxes_are_rejected(self):
        for bad in (dict(x=1.2, y=0, w=0.1, h=0.1), dict(x=0, y=0, w=0, h=0.1),
                    dict(x=0, y=0, w='big', h=0.1), dict(x=0, y=0, w=True, h=0.1), 'not-a-box',
                    dict(x=-0.1, y=0, w=0.2, h=0.2)):
            with self.assertRaises(ValueError):
                clamp_box(bad, 1920, 1080)

    def test_nearby_keyframes_merge_into_one_track(self):
        masks = [dict(t=1.0, category='blood', confidence='high', box=clamp_box(dict(x=.4, y=.4, w=.1, h=.1), 1920, 1080)),
                 dict(t=1.5, category='blood', confidence='high', box=clamp_box(dict(x=.43, y=.42, w=.1, h=.1), 1920, 1080)),
                 dict(t=9.0, category='nudity', confidence='low', box=clamp_box(dict(x=.1, y=.1, w=.2, h=.2), 1920, 1080))]
        tracks = merge_tracks(masks)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(len(tracks[0]['keyframes']), 2)
        self.assertTrue(all(track['needs_review'] for track in tracks))
        self.assertFalse(any(track['approved'] for track in tracks))
        self.assertEqual(tracks[1]['confidence'], 'low')

    def test_nothing_is_blurred_without_approval(self):
        tracks = merge_tracks([dict(t=1.0, category='blood', confidence='high',
                                    box=clamp_box(dict(x=.4, y=.4, w=.2, h=.2), 1920, 1080))])
        self.assertIsNone(blur_filtergraph(tracks, 10))
        tracks[0]['approved'] = True
        graph, output = blur_filtergraph(tracks, 10)
        self.assertEqual(output, '[m0]')
        self.assertIn('boxblur=22', graph)
        self.assertIn("enable='between(t,", graph)
        # The base image is split, not blurred: full-screen blur must be impossible.
        self.assertIn('[0:v]split=2[base][src0]', graph)
        self.assertNotIn('[base]boxblur', graph)
        with self.assertRaises(ValueError):
            blur_filtergraph(tracks, 0)

    def test_moving_region_produces_a_time_dependent_expression(self):
        tracks = merge_tracks([
            dict(t=1.0, category='blood', confidence='high', box=clamp_box(dict(x=.40, y=.3, w=.15, h=.15), 1920, 1080)),
            dict(t=2.0, category='blood', confidence='high', box=clamp_box(dict(x=.48, y=.34, w=.15, h=.15), 1920, 1080))])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0]['keyframes']), 2)
        tracks[0]['approved'] = True
        graph, _output = blur_filtergraph(tracks, 10)
        self.assertIn('if(lt(t,', graph)
        self.assertNotIn('\\,', graph)
        self.assertIn("enable='between(t,0.850,2.150)'", graph)

    def test_scan_packet_is_bounded(self):
        packet = scan_packet([(1.0, Path(__file__))], 1920, 1080)
        self.assertEqual(packet['schema'], 1)
        self.assertIn('normalized', packet['coordinates'])
        with self.assertRaises(ValueError):
            scan_packet([], 1920, 1080)
        with self.assertRaises(ValueError):
            scan_packet([(float(i), Path(__file__)) for i in range(13)], 1920, 1080)

    def test_fabricated_and_out_of_range_regions_are_rejected(self):
        good = dict(x=.4, y=.4, w=.1, h=.1)
        with self.assertRaises(ValueError):
            validate_scan(dict(schema=2, masks=[]), 60, [1.0])
        with self.assertRaises(ValueError):
            validate_scan(dict(schema=1, masks='nope'), 60, [1.0])
        for mask in (dict(t=999, category='blood', box=good), dict(t='soon', category='blood', box=good),
                     dict(t=30.0, category='politics', box=good), dict(t=30.0, category='blood'),
                     dict(t=7.5, category='blood', box=good)):
            with self.assertRaises(ValueError):
                validate_scan(dict(schema=1, masks=[mask]), 60, [1.0, 30.0])

    def test_validated_scan_keeps_review_flags_and_limitations(self):
        result = validate_scan(dict(schema=1, masks=[dict(t=30.0, category='blood', confidence='HIGH',
                                                          box=dict(x=.4, y=.4, w=.1, h=.1))]), 60, [30.0])
        mask = result['masks'][0]
        self.assertEqual(mask['confidence'], 'low')
        self.assertTrue(any('не считается безопасным' in line for line in result['limitations']))
        self.assertTrue(any('авторские права' in line for line in result['limitations']))


class FakeSafetyPlanner:
    """Stands in for a vision provider. Records the prompt it was given."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def propose_json(self, packet, frames=(), cancelled=None, task_prompt=None):
        self.calls.append(dict(packet=packet, frames=list(frames), prompt=task_prompt))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class TextOnlyPlanner:
    def propose(self, packet, frames=(), cancelled=None):
        return dict(schema=1, candidates=[])


class ScanTests(unittest.TestCase):
    FRAMES = [(1.0, 'a.jpg'), (30.0, 'b.jpg')]
    BOX = dict(x=0.4, y=0.4, w=0.1, h=0.1)

    def scan(self, answer):
        from censor import scan_with_provider
        planner = FakeSafetyPlanner(answer)
        result = scan_with_provider(planner, self.FRAMES, 1920, 1080, 60)
        return result, planner

    def test_provider_without_a_json_contract_is_refused(self):
        from censor import scan_with_provider
        with self.assertRaises(ValueError):
            scan_with_provider(TextOnlyPlanner(), self.FRAMES, 1920, 1080, 60)

    def test_prompt_forbids_treating_dialogue_subtitles_as_credits(self):
        from censor import SAFETY_PROMPT
        self.assertIn('dialogue subtitles', SAFETY_PROMPT)
        self.assertIn('NOT end credits', SAFETY_PROMPT)
        self.assertIn('untrusted', SAFETY_PROMPT)

    def test_valid_scan_returns_tracks_that_need_review(self):
        result, planner = self.scan(dict(schema=1, masks=[
            dict(t=1.0, category='blood', confidence='high', box=self.BOX),
            dict(t=30.0, category='nudity', confidence='low', box=self.BOX)]))
        self.assertEqual(len(result['masks']), 2)
        self.assertEqual(len(result['tracks']), 2)
        self.assertTrue(all(track['needs_review'] for track in result['tracks']))
        self.assertFalse(any(track['approved'] for track in result['tracks']))
        self.assertEqual(result['frames_sent'], 2)
        self.assertIn('ручного подтверждения', result['note'])
        self.assertEqual(planner.calls[0]['packet']['schema'], 1)
        self.assertEqual(len(planner.calls[0]['frames']), 2)

    def test_empty_answer_is_a_valid_result(self):
        result, _planner = self.scan(dict(schema=1, masks=[]))
        self.assertEqual(result['masks'], [])
        self.assertEqual(result['tracks'], [])

    def test_timestamps_that_were_never_sent_are_rejected(self):
        with self.assertRaises(ValueError):
            self.scan(dict(schema=1, masks=[dict(t=45.0, category='blood', box=self.BOX)]))

    def test_out_of_timeline_and_bad_categories_are_rejected(self):
        with self.assertRaises(ValueError):
            self.scan(dict(schema=1, masks=[dict(t=999.0, category='blood', box=self.BOX)]))
        with self.assertRaises(ValueError):
            self.scan(dict(schema=1, masks=[dict(t=1.0, category='politics', box=self.BOX)]))
        with self.assertRaises(ValueError):
            self.scan(dict(schema=2, masks=[]))

    def test_box_outside_the_frame_is_rejected(self):
        with self.assertRaises(ValueError):
            self.scan(dict(schema=1, masks=[dict(t=1.0, category='blood',
                                                 box=dict(x=1.5, y=0.0, w=0.2, h=0.2))]))

    def test_provider_failure_propagates_instead_of_looking_safe(self):
        from censor import scan_with_provider
        planner = FakeSafetyPlanner(RuntimeError('модель недоступна'))
        with self.assertRaises(RuntimeError):
            scan_with_provider(planner, self.FRAMES, 1920, 1080, 60)


if __name__ == '__main__':
    unittest.main()
