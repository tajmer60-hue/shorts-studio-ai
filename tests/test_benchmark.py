"""Benchmark metric tests. Pure functions, no network and no media."""
import json
from pathlib import Path
import tempfile
import unittest

from benchmark import evaluate, markdown_table, run_benchmark, score_plan

WORDS = [dict(start=1.0, end=2.0, word='первое'), dict(start=5.0, end=6.0, word='второе'),
         dict(start=40.0, end=41.0, word='третье')]


def plan(*candidates):
    return dict(schema=1, candidates=[dict(title=t, rationale=r, source_segments=segs)
                                      for t, r, segs in candidates])


class ScoreTests(unittest.TestCase):
    def test_clean_plan_scores_zero_on_every_defect_metric(self):
        result = score_plan(plan(('Часть 1', 'Ясное объяснение выбора.' * 3,
                                  [dict(**{'in': 0.0, 'out': 30.0})])), WORDS, 0.0, 60.0)
        self.assertEqual(result['word_cuts'], 0)
        self.assertEqual(result['overlaps'], 0)
        self.assertEqual(result['out_of_window'], 0)
        self.assertTrue(result['within_window'])
        self.assertTrue(result['parts_in_source_order'])
        self.assertEqual(result['candidates'], 1)
        self.assertEqual(result['clips_in_target_range'], 1)
        self.assertAlmostEqual(result['coverage'], 0.5)

    def test_cut_through_a_recognized_word_is_counted(self):
        # 1.5 slices "первое" (1.0-2.0); 30.0 touches nothing.
        result = score_plan(plan(('X', 'why', [dict(**{'in': 1.5, 'out': 30.0})])), WORDS, 0.0, 60.0)
        self.assertEqual(result['word_cuts'], 1)

    def test_both_edges_are_checked(self):
        result = score_plan(plan(('X', 'why', [dict(**{'in': 1.5, 'out': 5.5})])), WORDS, 0.0, 60.0)
        self.assertEqual(result['word_cuts'], 2)

    def test_coverage_is_a_union_not_a_sum(self):
        overlapping = plan(('A', 'why', [dict(**{'in': 0.0, 'out': 30.0})]),
                           ('B', 'why', [dict(**{'in': 20.0, 'out': 40.0})]))
        result = score_plan(overlapping, WORDS, 0.0, 80.0)
        self.assertAlmostEqual(result['coverage'], 0.5)
        self.assertEqual(result['overlaps'], 1)

    def test_footage_outside_the_analyzed_window_is_reported(self):
        result = score_plan(plan(('X', 'why', [dict(**{'in': 55.0, 'out': 70.0})])), WORDS, 0.0, 60.0)
        self.assertEqual(result['out_of_window'], 1)
        self.assertFalse(result['within_window'])

    def test_parts_out_of_story_order_are_flagged(self):
        result = score_plan(plan(('Позже', 'why', [dict(**{'in': 40.0, 'out': 50.0})]),
                                 ('Раньше', 'why', [dict(**{'in': 0.0, 'out': 10.0})])), WORDS, 0.0, 60.0)
        self.assertFalse(result['parts_in_source_order'])

    def test_clips_outside_the_target_length_are_not_counted_as_good(self):
        result = score_plan(plan(('Короткий', 'why', [dict(**{'in': 0.0, 'out': 5.0})]),
                                 ('Длинный', 'why', [dict(**{'in': 6.0, 'out': 200.0})])), [], 0.0, 300.0)
        self.assertEqual(result['clips_in_target_range'], 0)

    def test_rejects_a_non_object_plan(self):
        with self.assertRaises(ValueError):
            score_plan('not a plan', WORDS, 0, 60)


class BrokenPlanner:
    PROVIDER = 'broken'

    def __init__(self, error):
        self.error = error

    def propose(self, packet, frames=(), cancelled=None):
        raise self.error


class WorkingPlanner:
    PROVIDER = 'fake'

    def __init__(self, result, ledger=None):
        self.result = result
        self.ledger = ledger

    def propose(self, packet, frames=(), cancelled=None):
        return self.result


class HarnessTests(unittest.TestCase):
    PACKET = dict(duration=60.0, source_in=0.0, source_out=60.0)

    def test_a_refusal_is_recorded_not_raised(self):
        row = evaluate('bad-model', BrokenPlanner(QuotaError('лимит')), self.PACKET, [], WORDS)
        self.assertFalse(row['ok'])
        self.assertIn('лимит', row['error'])
        self.assertEqual(row['model'], 'bad-model')

    def test_working_model_is_measured_and_cached(self):
        good = plan(('Часть 1', 'объяснение', [dict(**{'in': 0.0, 'out': 30.0})]))
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            row = evaluate('good-model', WorkingPlanner(good), self.PACKET, [], WORDS, cache=folder)
            self.assertTrue(row['ok'])
            self.assertEqual(row['candidates'], 1)
            self.assertEqual(row['word_cuts'], 0)
            self.assertEqual(row['provider'], 'fake')
            saved = json.loads((Path(folder) / 'good-model.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['model'], 'good-model')

    def test_table_states_that_metrics_are_not_a_view_forecast(self):
        good = plan(('Часть 1', 'объяснение', [dict(**{'in': 0.0, 'out': 30.0})]))
        rows, table = run_benchmark([('a', WorkingPlanner(good)), ('b', BrokenPlanner(RuntimeError('x')))],
                                    self.PACKET, [], WORDS)
        self.assertEqual(len(rows), 2)
        self.assertIn('не является прогнозом просмотров', table)
        self.assertIn('Рез.слов', table)
        self.assertIn('НЕТ', table)
        self.assertTrue(table.startswith('|'))

    def test_empty_candidate_list_is_refused(self):
        with self.assertRaises(ValueError):
            run_benchmark([], self.PACKET, [], WORDS)


class QuotaError(Exception):
    pass


if __name__ == '__main__':
    unittest.main()
