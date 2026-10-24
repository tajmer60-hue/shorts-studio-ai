"""Free-tier path tests: live quota, training consent, and zero-budget requests.

No network. Both the key endpoint and the chat endpoint are injected.
"""
import json
from pathlib import Path
import tempfile
import unittest

from cloud_planner import MODEL_VISION, OpenRouterPlanner
from providers import (ConsentRequired, FREE_SAFETY_MODEL, FREE_VISION_MODEL, FreeQuota,
                       QuotaExhausted, ResponseInvalid, is_free_model)

FREE_MODEL = FREE_VISION_MODEL
PAID_MODEL = 'google/gemini-2.5-flash-lite'
GOOD = dict(schema=1, candidates=[dict(title='Ссора', rationale='Конфликт с развязкой.',
                                       source_segments=[dict(**{'in': 1.0, 'out': 40.0})])])


def key_transport(remaining=36, limit=50, used=14):
    calls = []

    def transport(url, headers, timeout=30):
        calls.append(url)
        body = dict(data=dict(is_free_tier=True, limit_remaining=None,
                              free_model_daily_requests=dict(used=used, limit=limit, remaining=remaining)))
        return 200, {}, json.dumps(body).encode()

    transport.calls = calls
    return transport


def chat_transport(plan=GOOD):
    calls = []
    body = dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(plan, ensure_ascii=False)))],
                usage=dict(cost=0, total_tokens=1234))

    def transport(url, payload, headers, timeout=90):
        calls.append(payload)
        return 200, {}, json.dumps(body).encode()

    transport.calls = calls
    return transport


class FreeModelDetectionTests(unittest.TestCase):
    def test_free_variants_are_recognised(self):
        self.assertTrue(is_free_model(FREE_MODEL))
        self.assertTrue(is_free_model(FREE_SAFETY_MODEL))
        self.assertTrue(is_free_model('anything/model:free'))

    def test_paid_models_are_not_free(self):
        self.assertFalse(is_free_model(PAID_MODEL))
        self.assertFalse(is_free_model('deepseek/deepseek-v4.1-flash'))
        self.assertFalse(is_free_model('totally-unknown-model'))

    def test_vision_table_marks_text_only_models(self):
        self.assertTrue(MODEL_VISION[FREE_MODEL])
        self.assertFalse(MODEL_VISION['deepseek/deepseek-v4-flash-0731'])
        self.assertFalse(MODEL_VISION['openai/gpt-oss-120b'])


class FreeQuotaTests(unittest.TestCase):
    def test_remaining_is_read_from_the_provider(self):
        quota = FreeQuota('k', transport=key_transport(remaining=36))
        info = quota.snapshot()
        self.assertEqual(info['remaining'], 36)
        self.assertEqual(info['limit'], 50)
        self.assertTrue(info['is_free_tier'])
        self.assertEqual(info['source'], 'GET /api/v1/key')

    def test_snapshot_is_cached_within_the_refresh_window(self):
        transport = key_transport()
        quota = FreeQuota('k', transport=transport, refresh_seconds=60)
        quota.snapshot()
        quota.snapshot()
        self.assertEqual(len(transport.calls), 1)
        quota.snapshot(force=True)
        self.assertEqual(len(transport.calls), 2)

    def test_exhausted_quota_refuses_before_sending(self):
        quota = FreeQuota('k', transport=key_transport(remaining=0, limit=50, used=50))
        with self.assertRaises(QuotaExhausted) as caught:
            quota.ensure(1)
        self.assertIn('00:00 UTC', str(caught.exception))
        self.assertIn('Прогресс сохранён', str(caught.exception))

    def test_unknown_remaining_is_warned_not_assumed_fine(self):
        def transport(url, headers, timeout=30):
            return 200, {}, json.dumps(dict(data=dict(is_free_tier=True))).encode()
        quota = FreeQuota('k', transport=transport)
        quota.ensure(1)
        self.assertIn('не проверен', quota.warning)

    def test_provider_error_is_reported(self):
        def transport(url, headers, timeout=30):
            return 401, {}, b'{}'
        with self.assertRaises(Exception):
            FreeQuota('k', transport=transport).snapshot()

    def test_key_is_required(self):
        with self.assertRaises(ConsentRequired):
            FreeQuota('', transport=key_transport()).snapshot()


class FreePlannerTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.folder = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def make(self, model=FREE_MODEL, budget=0.0, authorized=True, training=True,
             data_collection=None, quota=None, transport=None):
        return OpenRouterPlanner('secret-key', self.folder, budget, authorized, model=model,
                                 training_allowed=training, data_collection=data_collection,
                                 quota=quota if quota is not None else FreeQuota('secret-key', transport=key_transport()),
                                 transport=transport or chat_transport())

    def test_free_model_requires_explicit_training_consent(self):
        with self.assertRaises(ConsentRequired) as caught:
            self.make(training=False)
        self.assertIn('обработку данных', str(caught.exception))

    def test_free_model_forces_data_collection_allow(self):
        planner = self.make()
        self.assertEqual(planner.data_collection, 'allow')
        self.assertTrue(planner.free)
        with self.assertRaises(ConsentRequired):
            self.make(data_collection='deny')

    def test_paid_model_keeps_training_denied_by_default(self):
        planner = self.make(model=PAID_MODEL, budget=1.0, training=False)
        self.assertEqual(planner.data_collection, 'deny')
        self.assertFalse(planner.free)

    def test_free_model_needs_no_invented_budget(self):
        transport = chat_transport()
        planner = self.make(budget=0.0, transport=transport)
        self.assertEqual(planner.reserve_estimate(), 0.0)
        plan = planner.propose(dict(duration=60, source_in=0, source_out=60))
        self.assertEqual(plan['candidates'][0]['title'], 'Ссора')
        self.assertNotIn('max_price', transport.calls[0]['provider'])
        self.assertEqual(transport.calls[0]['model'], FREE_MODEL)

    def test_paid_model_still_caps_the_price(self):
        transport = chat_transport()
        planner = self.make(model=PAID_MODEL, budget=1.0, transport=transport)
        planner.propose(dict(duration=60))
        self.assertIn('max_price', transport.calls[0]['provider'])
        self.assertGreater(planner.reserve_estimate(), 0)

    def test_quota_is_checked_before_the_request_is_built(self):
        transport = chat_transport()
        planner = self.make(quota=FreeQuota('secret-key', transport=key_transport(remaining=0)),
                            transport=transport)
        with self.assertRaises(QuotaExhausted):
            planner.propose(dict(duration=60))
        self.assertEqual(len(transport.calls), 0)

    def test_text_only_model_refuses_frames_instead_of_guessing(self):
        planner = self.make(model='deepseek/deepseek-v4-flash-0731', budget=1.0)
        self.assertFalse(planner.VISION)
        with self.assertRaises(ResponseInvalid) as caught:
            planner.propose(dict(duration=60), frames=[(1.0, __file__)])
        self.assertIn('не принимает изображения', str(caught.exception))

    def test_key_never_reaches_persistent_files(self):
        self.make(transport=chat_transport()).propose(dict(duration=60))
        files = list(self.folder.glob('*.json'))
        self.assertTrue(files)
        for path in files:
            self.assertNotIn('secret-key', path.read_text(encoding='utf-8'))

    def test_usage_ledger_marks_free_requests(self):
        self.make(transport=chat_transport()).propose(dict(duration=60))
        ledger = json.loads((self.folder / 'usage.json').read_text(encoding='utf-8'))
        self.assertTrue(ledger[0]['free'])
        self.assertEqual(ledger[0]['reserved_usd'], 0.0)
        self.assertEqual(ledger[0]['status'], 'completed')
        self.assertEqual(ledger[0]['model'], FREE_MODEL)


if __name__ == '__main__':
    unittest.main()
