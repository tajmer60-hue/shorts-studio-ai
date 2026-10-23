"""Workflow wiring tests: provider selection, consent gates and profanity review.

No network and no media: these prove that a request cannot be prepared without
authorization and that detection never turns into an automatic mute.
"""
from pathlib import Path
import tempfile
import unittest

from auto_workflow import PROVIDERS, build_planner, review_profanity
from cloud_planner import OpenRouterPlanner
from providers import ConsentRequired, FallbackChain, GeminiPlanner, GroqPlanner

WORDS = [dict(start=0.0, end=0.5, word='привет', probability=0.99),
         dict(start=0.6, end=1.1, word='сука', probability=0.95),
         dict(start=1.2, end=1.7, word='сумку', probability=0.97),
         dict(start=2.0, end=2.5, word='блядь', probability=0.30)]


class PlannerSelectionTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.folder = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def build(self, settings, authorized=True, budget=1.0):
        return build_planner(settings, self.folder, authorized, budget)

    def test_consent_gates_come_first(self):
        settings = dict(provider='gemini', gemini_key='k')
        with self.assertRaises(ConsentRequired):
            self.build(settings, authorized=False)
        with self.assertRaises(ConsentRequired):
            self.build(settings, budget=0)

    def test_unknown_provider_is_refused(self):
        with self.assertRaises(ValueError):
            self.build(dict(provider='skynet', gemini_key='k'))
        self.assertEqual(len(PROVIDERS), 4)

    def test_selected_provider_without_a_key_is_refused(self):
        with self.assertRaises(ConsentRequired):
            self.build(dict(provider='gemini'))
        with self.assertRaises(ConsentRequired):
            self.build(dict(provider='groq', gemini_key='k'))
        with self.assertRaises(ConsentRequired):
            self.build(dict(provider='openrouter'))

    def test_single_provider_is_used_directly(self):
        self.assertIsInstance(self.build(dict(provider='gemini', gemini_key='k')), GeminiPlanner)
        self.assertIsInstance(self.build(dict(provider='groq', groq_key='k')), GroqPlanner)
        self.assertIsInstance(self.build(dict(provider='openrouter', openrouter_key='k')), OpenRouterPlanner)

    def test_auto_builds_a_chain_with_a_positive_reserve(self):
        chain = self.build(dict(provider='auto', gemini_key='k1', groq_key='k2', openrouter_key='k3'))
        self.assertIsInstance(chain, FallbackChain)
        self.assertEqual(len(chain.planners), 3)
        self.assertTrue(chain.supports_vision)
        self.assertGreater(chain.reserve_estimate(), 0)
        # The dearest member sets the reservation, so a fallback cannot overspend.
        self.assertGreaterEqual(chain.reserve_estimate(),
                                max(p.reserve_estimate() for p in chain.planners
                                    if hasattr(p, 'reserve_estimate')))

    def test_auto_without_any_key_is_refused(self):
        with self.assertRaises(ConsentRequired):
            self.build(dict(provider='auto'))

    def test_models_are_configurable(self):
        planner = self.build(dict(provider='gemini', gemini_key='k', gemini_model='gemini-2.5-flash'))
        self.assertEqual(planner.model, 'gemini-2.5-flash')
        self.assertGreater(planner.reserve_estimate(), 0)


class ProfanityReviewTests(unittest.TestCase):
    def test_hits_are_proposals_and_never_approved(self):
        review = review_profanity(dict(words=WORDS))
        self.assertEqual(review['schema'], 1)
        found = {hit['word'] for hit in review['hits']}
        self.assertIn('сука', found)
        self.assertIn('блядь', found)
        self.assertNotIn('сумку', found)
        self.assertNotIn('привет', found)
        self.assertEqual(review['approved_ops'], [])
        self.assertTrue(all(hit['approved'] is False for hit in review['hits']))
        low = next(hit for hit in review['hits'] if hit['word'] == 'блядь')
        self.assertEqual(low['confidence'], 'low')
        self.assertTrue(any('Без одобрения пользователя' in line for line in review['limitations']))

    def test_empty_transcript_is_not_an_error(self):
        review = review_profanity(dict(words=[]))
        self.assertEqual(review['hits'], [])
        self.assertEqual(review['approved_ops'], [])

    def test_user_dictionary_is_persisted_and_reused(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            path = Path(folder) / 'censor.json'
            first = review_profanity(dict(words=WORDS), path)
            self.assertTrue(path.exists())
            self.assertEqual(first['lexicon']['schema'], 1)
            again = review_profanity(dict(words=WORDS), path)
            self.assertEqual(len(again['hits']), len(first['hits']))


if __name__ == '__main__':
    unittest.main()
