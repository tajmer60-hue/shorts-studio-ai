"""Music licensing and Beat Sync tests.

The licensing checks are the point: an uncleared reference must never become
playable, and with no licensed file the pipeline must stay silent rather than
pirate a track. The rhythm checks run on a synthetic 120 BPM click track.
"""
from pathlib import Path
import math
import tempfile
import unittest
import wave

import numpy as np

from beat_sync import (INTENSITY, align_music_to_drop, beat_grid, duck_expression,
                       estimate_tempo, intensity_envelope, drop_times, music_chain,
                       onset_envelope, snap_cuts)
from music import (CLAIMED_PLATFORM, NOT_CLEARED, default_catalog, import_track,
                   license_ok, load_catalog, match_tracks, style_hints)


def write_click_track(path, bpm=120.0, seconds=4.0, sr=22050, drop_at=2.0):
    """Impulses on the beat; energy doubles after drop_at to create a drop."""
    total = int(seconds * sr)
    samples = np.zeros(total, dtype=np.float32)
    period = int(sr * 60.0 / bpm)
    for start in range(0, total - 200, period):
        gain = 1.0 if start / sr >= drop_at else 0.45
        samples[start:start + 120] += gain * np.hanning(120).astype(np.float32)
    pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sr)
        stream.writeframes(pcm.tobytes())
    return path


class LicensingTests(unittest.TestCase):
    def test_references_are_style_hints_not_playable_tracks(self):
        catalog = default_catalog()
        self.assertTrue(catalog['tracks'])
        for track in catalog['tracks']:
            self.assertFalse(track['playable'])
            self.assertIn(track['license'], (NOT_CLEARED, CLAIMED_PLATFORM))
            self.assertFalse(license_ok(track))

    def test_with_no_licensed_file_the_renderer_gets_nothing(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            catalog = load_catalog(folder)
            self.assertEqual(match_tracks(catalog, 'aggressive_phonk'), [])
            self.assertTrue(style_hints(catalog, 'aggressive_phonk'))
            self.assertTrue((Path(folder) / 'music-catalog.json').exists())

    def test_import_requires_a_real_file_and_a_license(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            with self.assertRaises(ValueError):
                import_track(folder, Path(folder) / 'absent.mp3', 'X', 'Y', 'CC-BY', '', ['none'])
            audio = write_click_track(Path(folder) / 'mine.wav')
            with self.assertRaises(ValueError):
                import_track(folder, audio, 'X', 'Y', '', '', ['none'])
            with self.assertRaises(ValueError):
                import_track(folder, audio, 'X', 'Y', 'CC-BY', '', ['not-a-mood'])

    def test_imported_licensed_track_becomes_matchable(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            audio = write_click_track(Path(folder) / 'mine.wav')
            entry = import_track(folder, audio, 'My Bed', 'Me', 'Mixkit Free License',
                                 'https://mixkit.co', ['aggressive_phonk'],
                                 attribution_required=False)
            self.assertTrue(license_ok(entry))
            catalog = load_catalog(folder)
            matched = match_tracks(catalog, 'aggressive_phonk')
            self.assertEqual([t['id'] for t in matched], [entry['id']])
            self.assertEqual(match_tracks(catalog, 'emotional'), [])


class RhythmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        cls.track = write_click_track(Path(cls._temp.name) / 'click.wav')
        from beat_sync import read_mono
        cls.samples = read_mono(cls.track)
        cls.env = onset_envelope(cls.samples)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_tempo_is_recovered_from_a_click_track(self):
        bpm = estimate_tempo(self.env)
        self.assertIsNotNone(bpm)
        self.assertAlmostEqual(bpm, 120.0, delta=4.0)

    def test_beat_grid_spacing_matches_the_tempo(self):
        beats = beat_grid(self.env, estimate_tempo(self.env))
        gaps = [b - a for a, b in zip(beats, beats[1:])]
        self.assertTrue(gaps)
        self.assertAlmostEqual(sum(gaps) / len(gaps), 0.5, delta=0.05)

    def test_drop_is_found_where_energy_jumps(self):
        intensity, step = intensity_envelope(self.samples)
        drops = drop_times(intensity, step)
        self.assertTrue(drops, 'Дроп не найден.')
        self.assertAlmostEqual(drops[0], 2.0, delta=0.6)

    def test_snap_moves_only_nearby_cuts_and_never_onto_words(self):
        beats = [0.0, 0.5, 1.0, 1.5, 2.0]
        words = [dict(start=0.95, end=1.05, word='слово')]
        moved, kept = snap_cuts([0.55, 1.0, 3.7], beats, words, max_shift=0.2)
        self.assertEqual([m['source'] for m in moved], [0.55])
        self.assertAlmostEqual(moved[0]['snapped'], 0.5)
        # 1.0 sits inside a word, 3.7 has no beat nearby: both stay put.
        self.assertEqual(kept, [1.0, 3.7])

    def test_snap_never_collides_cuts(self):
        moved, kept = snap_cuts([0.48, 0.52], [0.0, 0.5, 1.0], max_shift=0.2, min_gap=0.3)
        self.assertEqual(len(moved), 1)
        self.assertEqual(kept, [0.52])

    def test_duck_expression_covers_every_dialogue_interval(self):
        expression = duck_expression([dict(start=1.0, end=2.0), dict(start=5.0, end=6.0)], -10)
        self.assertIn('between(t,1.000,2.000)', expression)
        self.assertIn('between(t,5.000,6.000)', expression)
        self.assertIsNone(duck_expression([]))

    def test_music_chain_limits_level_and_fades_and_refuses_unknown_intensity(self):
        chain = music_chain(10.0, [dict(start=1.0, end=2.0)], 'balanced')
        self.assertIn('alimiter', chain)
        self.assertIn('afade=t=in', chain)
        self.assertIn('afade=t=out', chain)
        self.assertIn('eval=frame', chain)
        self.assertIn('-14.00dB', chain)
        with self.assertRaises(ValueError):
            music_chain(10.0, intensity='chaos')

    def test_presets_order_by_aggression(self):
        # Aggressive ducks the music deeper under dialogue and allows more SFX.
        self.assertGreater(INTENSITY['cinematic']['duck_db'], INTENSITY['aggressive']['duck_db'])
        self.assertLess(INTENSITY['cinematic']['sfx_max'], INTENSITY['aggressive']['sfx_max'])
        self.assertFalse(INTENSITY['cinematic']['snap_to_beats'])
        self.assertTrue(INTENSITY['aggressive']['snap_to_beats'])

    def test_climax_alignment_only_within_tolerance(self):
        self.assertEqual(align_music_to_drop(5.0, [4.5, 9.0], tolerance=1.5),
                         dict(visual=5.0, musical=4.5, shift=-0.5))
        self.assertIsNone(align_music_to_drop(5.0, [9.0], tolerance=1.5))


class LicensingTierTests(unittest.TestCase):
    """Library v1 is claimed, not verified: the tiers must behave differently."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.folder = Path(self._temp.name)
        self.audio = write_click_track(Path(self.folder) / 'bed.wav')

    def tearDown(self):
        self._temp.cleanup()

    def attach(self, entry, license_id):
        entry = dict(entry, file=str(self.audio), license=license_id)
        return entry

    def test_library_v1_ships_claimed_and_unmixable_without_a_file(self):
        catalog = default_catalog()
        v1 = [t for t in catalog['tracks'] if t['license'] == CLAIMED_PLATFORM]
        self.assertEqual(len(v1), 20)
        ids = {t['id'] for t in v1}
        self.assertIn('f01', ids)
        self.assertIn('c10', ids)
        for track in v1:
            self.assertFalse(license_ok(track))
            self.assertEqual(track['commercial'], 'requires_verification')
            self.assertIn('YouTube Shorts', track['platform_restrictions'])
            self.assertIsNone(track['file'])

    def test_claimed_track_is_mixable_only_non_commercial_on_listed_platforms(self):
        entry = self.attach(default_catalog()['tracks'][-1], CLAIMED_PLATFORM)
        self.assertTrue(license_ok(entry, commercial=False, platform='YouTube Shorts'))
        self.assertTrue(license_ok(entry, commercial=False, platform='TikTok'))
        self.assertFalse(license_ok(entry, commercial=True, platform='YouTube Shorts'))
        self.assertFalse(license_ok(entry, commercial=False, platform='Telegram'))
        self.assertFalse(license_ok(entry, commercial=True))

    def test_verified_track_allows_commercial_and_not_cleared_never_does(self):
        from music import VERIFIED, NOT_CLEARED
        verified = self.attach(default_catalog()['tracks'][-1], VERIFIED)
        self.assertTrue(license_ok(verified, commercial=True, platform='anywhere'))
        reference = self.attach(default_catalog()['tracks'][0], NOT_CLEARED)
        self.assertFalse(license_ok(reference, commercial=False, platform='YouTube Shorts'))

    def test_match_tracks_respects_commercial_intent(self):
        from music import CLAIMED_PLATFORM, save_catalog
        catalog = load_catalog(self.folder)
        entry = next(t for t in catalog['tracks'] if t['id'] == 'f01')
        entry['file'] = str(self.audio)
        save_catalog(self.folder, catalog)
        catalog = load_catalog(self.folder)
        self.assertTrue(match_tracks(catalog, 'dark_phonk', commercial=False,
                                     platform='YouTube Shorts'))
        self.assertEqual(match_tracks(catalog, 'dark_phonk', commercial=True), [])

    def test_migration_from_schema_2_keeps_imports_and_adds_library_v1(self):
        from music import save_catalog, catalog_path
        catalog = default_catalog()
        catalog['tracks'] = [t for t in catalog['tracks'] if t['license'] == NOT_CLEARED][:2]
        catalog['schema'] = 2
        save_catalog(self.folder, dict(catalog))
        # force schema 2 on disk
        import json as _json
        catalog_path(self.folder).write_text(_json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
        migrated = load_catalog(self.folder)
        self.assertEqual(migrated['schema'], 3)
        self.assertTrue(any(t['license'] == CLAIMED_PLATFORM for t in migrated['tracks']))
        self.assertEqual(len([t for t in migrated['tracks'] if t['license'] == NOT_CLEARED]), 2)


class LibraryV2MergeTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.folder = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def v2(self, file_path=None):
        return dict(library_version='v2.0', tracks=dict(funk_references=[dict(
            track_id='F01', title='Montagem Coma (Slowed)', artist='ANDROMEDA, elysian.',
            style='Funk / Bass', license_status='DEFAULT_LIBRARY_OK', file_path=file_path,
            is_non_commercial=True, usage_scope=['TikTok', 'Shorts'],
            warning='Commercial use requires manual verification.')]))

    def test_claimed_paths_that_do_not_exist_are_not_trusted(self):
        from music import merge_library_v2
        report = merge_library_v2(self.folder, self.v2('/music/funk/F01_montagem_coma.mp3'))
        self.assertEqual(report['mixable'], 0)
        catalog = load_catalog(self.folder)
        entry = next(t for t in catalog['tracks'] if t['id'] == 'f01')
        self.assertIsNone(entry['file'])
        self.assertEqual(entry['license'], CLAIMED_PLATFORM)
        self.assertTrue(entry['non_commercial_only'])
        self.assertEqual(entry['platform_restrictions'], ['TikTok', 'Shorts'])

    def test_an_existing_file_becomes_mixable_non_commercial_only(self):
        from music import merge_library_v2
        audio = write_click_track(Path(self.folder) / 'f01.mp3')
        merge_library_v2(self.folder, self.v2(str(audio)))
        catalog = load_catalog(self.folder)
        entry = next(t for t in catalog['tracks'] if t['id'] == 'f01')
        self.assertTrue(license_ok(entry, commercial=False, platform='TikTok'))
        self.assertFalse(license_ok(entry, commercial=True, platform='TikTok'))

    def test_malformed_library_is_refused(self):
        from music import merge_library_v2
        with self.assertRaises(ValueError):
            merge_library_v2(self.folder, dict(tracks='nope'))


if __name__ == '__main__':
    unittest.main()
