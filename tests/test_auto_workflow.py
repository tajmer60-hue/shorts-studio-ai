"""Workflow wiring tests: provider selection, consent gates and profanity review.

No network and no media: these prove that a request cannot be prepared without
authorization and that detection never turns into an automatic mute.
"""
from pathlib import Path
import tempfile
import unittest

from auto_workflow import PROVIDERS, build_planner, review_profanity
from cloud_planner import OpenRouterPlanner
from providers import (ConsentRequired, FREE_VISION_MODEL, FallbackChain, GeminiPlanner,
                       GroqPlanner, QuotaExhausted)


class FakeQuota:
    """Stands in for providers.FreeQuota so these tests never touch the network."""

    def __init__(self, remaining):
        self.remaining = remaining
        self.checked = []

    def ensure(self, needed=1, force=False):
        self.checked.append(needed)
        if self.remaining < needed:
            raise QuotaExhausted(f'Бесплатный лимит исчерпан: осталось {self.remaining}.')

    def snapshot(self, force=False):
        return dict(remaining=self.remaining, limit=50, used=50 - self.remaining,
                    is_free_tier=True, source='fake')


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

    def build(self, settings, authorized=True, budget=1.0, quota=None):
        return build_planner(settings, self.folder, authorized, budget, quota=quota)

    def test_consent_gates_come_first(self):
        settings = dict(provider='gemini', gemini_key='k')
        with self.assertRaises(ConsentRequired):
            self.build(settings, authorized=False)
        with self.assertRaises(ConsentRequired):
            self.build(settings, budget=0)

    def test_unknown_provider_is_refused(self):
        with self.assertRaises(ValueError):
            self.build(dict(provider='skynet', gemini_key='k'))
        self.assertEqual(len(PROVIDERS), 5)

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

    def test_free_provider_needs_no_budget_but_needs_training_consent(self):
        planner = self.build(dict(provider='free', openrouter_key='k', training_allowed=True),
                             budget=0.0, quota=FakeQuota(36))
        self.assertTrue(planner.free)
        self.assertEqual(planner.model, FREE_VISION_MODEL)
        self.assertEqual(planner.reserve_estimate(), 0.0)
        self.assertEqual(planner.data_collection, 'allow')

    def test_free_provider_without_training_consent_is_refused(self):
        with self.assertRaises(ConsentRequired):
            self.build(dict(provider='free', openrouter_key='k'), budget=0.0, quota=FakeQuota(36))

    def test_free_provider_without_a_key_is_refused(self):
        with self.assertRaises(ConsentRequired):
            self.build(dict(provider='free', training_allowed=True), budget=0.0, quota=FakeQuota(36))

    def test_auto_prefers_free_only_when_training_is_allowed(self):
        permissive = self.build(dict(provider='auto', openrouter_key='k', gemini_key='g',
                                     training_allowed=True), budget=1.0, quota=FakeQuota(36))
        self.assertIsInstance(permissive, FallbackChain)
        self.assertTrue(permissive.planners[0].free)
        # A user who forbade training must not be routed to a free endpoint.
        strict = self.build(dict(provider='auto', openrouter_key='k', gemini_key='g'),
                            budget=1.0, quota=FakeQuota(36))
        members = strict.planners if isinstance(strict, FallbackChain) else [strict]
        self.assertFalse(any(getattr(p, 'free', False) for p in members))

    def test_whole_episode_is_checked_against_the_daily_free_quota(self):
        quota = FakeQuota(5)
        planner = self.build(dict(provider='free', openrouter_key='k', training_allowed=True),
                             budget=0.0, quota=quota)
        with self.assertRaises(QuotaExhausted):
            planner.quota.ensure(14)
        self.assertEqual(quota.checked, [14])
        planner.quota.ensure(3)
        self.assertEqual(quota.checked, [14, 3])


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
