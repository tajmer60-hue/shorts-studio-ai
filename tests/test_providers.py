"""Provider layer tests. No network: the transport callable is injected.

A mocked HTTP response proves request construction, consent, budget, quota
handling, caching and validation. It never proves model quality; that requires
the real acceptance run described in API_ACCEPTANCE.md.
"""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from providers import (ConsentRequired, FallbackChain, GeminiPlanner, GroqPlanner, Ledger,
                       ProviderUnavailable, QuotaExhausted, ResponseInvalid, build_chain,
                       estimate_cost, reserve_cost)


class FakeTransport:
    """Returns scripted responses and records what would have been sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload, headers, timeout=90):
        self.calls.append(dict(url=url, payload=payload, headers=headers))
        item = self.responses.pop(0) if self.responses else self.responses_default
        if isinstance(item, Exception):
            raise item
        return item

    responses_default = (200, {}, b'{}')

    @property
    def count(self):
        return len(self.calls)


def gemini_response(plan, tokens=1234):
    body = dict(candidates=[dict(finishReason='STOP', content=dict(parts=[dict(text=json.dumps(plan))]))],
                usageMetadata=dict(promptTokenCount=tokens, totalTokenCount=tokens + 20))
    return 200, {'x-ratelimit-remaining-requests': '19'}, json.dumps(body).encode()


def groq_response(plan, tokens=900):
    body = dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(plan)))],
                usage=dict(prompt_tokens=tokens, total_tokens=tokens + 30))
    return 200, {'x-ratelimit-remaining-requests': '997', 'x-ratelimit-remaining-tokens': '7000'}, \
        json.dumps(body).encode()


GOOD_PLAN = dict(schema=1, candidates=[dict(title='Ссора из-за денег', rationale='Конфликт с ясной развязкой.',
                                            source_segments=[dict(**{'in': 1.0, 'out': 9.0})])])


class ConsentTests(unittest.TestCase):
    def test_missing_authorization_never_builds_a_request(self):
        transport = FakeTransport([])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            planner = GeminiPlanner('secret', folder, 1, False, transport=transport)
            with self.assertRaises(ConsentRequired):
                planner.propose(dict(duration=30))
            with self.assertRaises(ConsentRequired):
                GeminiPlanner('', folder, 1, True, transport=transport).propose(dict(duration=30))
            with self.assertRaises(ConsentRequired):
                GeminiPlanner('secret', folder, 0, True, transport=transport).propose(dict(duration=30))
        self.assertEqual(transport.count, 0)

    def test_chain_without_keys_is_refused(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            with self.assertRaises(ConsentRequired):
                build_chain({}, folder, True)
            with self.assertRaises(ConsentRequired):
                build_chain(dict(gemini_key='k', budget=1), folder, False)

    def test_cancellation_happens_before_the_request(self):
        transport = FakeTransport([])
        stop = threading.Event()
        stop.set()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            with self.assertRaises(Exception):
                GeminiPlanner('secret', folder, 1, True, transport=transport).propose(dict(duration=30), cancelled=stop)
        self.assertEqual(transport.count, 0)


class CostTests(unittest.TestCase):
    def test_verified_tariff_is_priced(self):
        result = estimate_cost('gemini-2.5-flash-lite', 'x' * 4000)
        self.assertTrue(result['known'])
        self.assertGreater(result['usd'], 0)
        self.assertIn('ai.google.dev', result['source'])

    def test_unverified_tariff_is_not_invented(self):
        for model in ('openai/gpt-oss-120b', 'whisper-large-v3-turbo', 'unknown-model'):
            result = estimate_cost(model, 'x' * 4000)
            self.assertFalse(result['known'], model)
            self.assertIsNone(result['usd'], model)
        _estimate, amount = reserve_cost('openai/gpt-oss-120b', 'x' * 4000)
        self.assertGreater(amount, 0)

    def test_budget_is_refused_before_any_request(self):
        transport = FakeTransport([])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            planner = GeminiPlanner('secret', folder, 0.001, True, transport=transport)
            with self.assertRaises(QuotaExhausted):
                planner.propose(dict(duration=30))
        self.assertEqual(transport.count, 0)

    def test_reserve_exceeds_estimate_and_ledger_persists(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            ledger = Ledger(Path(folder) / 'usage.sqlite3', 5)
            request_id = ledger.begin('gemini', 'm', 'k', 0.5)
            ledger.finish(request_id, 'completed', reported_usd=0.01, tokens=10)
            self.assertAlmostEqual(ledger.used(), 0.5)
            self.assertAlmostEqual(ledger.remaining(), 4.5)
            ledger.close()
            reopened = Ledger(Path(folder) / 'usage.sqlite3', 5)
            self.assertEqual(len(reopened.rows()), 1)
            self.assertEqual(reopened.rows()[0]['status'], 'completed')
            reopened.close()


class GeminiTests(unittest.TestCase):
    def test_valid_plan_is_validated_cached_and_key_is_not_persisted(self):
        transport = FakeTransport([gemini_response(GOOD_PLAN)])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            planner = GeminiPlanner('private-key', folder, 1, True, transport=transport)
            plan = planner.propose(dict(duration=30))
            self.assertEqual(plan['candidates'][0]['title'], 'Ссора из-за денег')
            self.assertFalse(plan['candidates'][0]['approved'])
            self.assertTrue(plan['candidates'][0]['needs_review'])
            self.assertEqual(planner.last_quota['remaining_requests'], 19)
            payload = transport.calls[0]['payload']
            self.assertEqual(payload['generationConfig']['responseMimeType'], 'application/json')
            self.assertIn('x-goog-api-key', transport.calls[0]['headers'])
            self.assertNotIn('private-key', json.dumps(payload))
            planner.propose(dict(duration=30))
            self.assertEqual(transport.count, 1)
            for path in Path(folder).rglob('*'):
                if path.is_file():
                    self.assertNotIn('private-key', path.read_text(encoding='utf-8', errors='ignore'))

    def test_out_of_source_timecodes_are_rejected_and_not_cached(self):
        bad = dict(schema=1, candidates=[dict(title='X', rationale='Y',
                                              source_segments=[dict(**{'in': 0.0, 'out': 999.0})])])
        transport = FakeTransport([gemini_response(bad), gemini_response(bad)])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            planner = GeminiPlanner('secret', folder, 1, True, transport=transport)
            with self.assertRaises(ValueError):
                planner.propose(dict(duration=30))
            self.assertEqual(planner.ledger.rows()[0]['status'], 'error')

    def test_non_chronological_edit_is_rejected(self):
        bad = dict(schema=1, candidates=[dict(title='X', rationale='Y', source_segments=[
            dict(**{'in': 10.0, 'out': 20.0}), dict(**{'in': 1.0, 'out': 5.0})])])
        transport = FakeTransport([gemini_response(bad)])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            with self.assertRaises(ValueError):
                GeminiPlanner('secret', folder, 1, True, transport=transport).propose(dict(duration=30))

    def test_quota_error_reports_retry_after_and_saves_progress(self):
        transport = FakeTransport([(429, {'retry-after': '37'}, b'{}')])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            planner = GeminiPlanner('secret', folder, 1, True, transport=transport)
            with self.assertRaises(QuotaExhausted) as caught:
                planner.propose(dict(duration=30))
            self.assertEqual(caught.exception.retry_after, 37)
            self.assertEqual(planner.ledger.rows()[0]['status'], 'quota')

    def test_timeout_keeps_reservation_and_never_retries_internally(self):
        transport = FakeTransport([TimeoutError()])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            planner = GeminiPlanner('secret', folder, 1, True, transport=transport)
            with self.assertRaises(ProviderUnavailable):
                planner.propose(dict(duration=30))
            self.assertEqual(transport.count, 1)
            self.assertEqual(planner.ledger.rows()[0]['status'], 'unknown')
            self.assertGreater(planner.ledger.used(), 0)

    def test_oversized_packet_is_refused_before_encoding(self):
        transport = FakeTransport([])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            planner = GeminiPlanner('secret', folder, 1, True, transport=transport)
            with self.assertRaises(ResponseInvalid):
                planner.propose(dict(duration=30, dialogue='слово' * 60000))
            with self.assertRaises(ResponseInvalid):
                planner.propose(dict(duration=30), frames=[(float(i), __file__) for i in range(13)])
        self.assertEqual(transport.count, 0)

    def test_frames_are_sent_as_bounded_inline_images(self):
        transport = FakeTransport([gemini_response(GOOD_PLAN)])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            root = Path(folder)
            frames = []
            for index in range(3):
                image = root / f'frame-{index}.jpg'
                image.write_bytes(b'\xff\xd8\xff\xe0' + bytes(index) * 400)
                frames.append((float(index), str(image)))
            GeminiPlanner('secret', root, 1, True, transport=transport).propose(dict(duration=30), frames)
            parts = transport.calls[0]['payload']['contents'][0]['parts']
            self.assertEqual(sum(1 for part in parts if 'inline_data' in part), 3)
            self.assertEqual(parts[1]['inline_data']['mime_type'], 'image/jpeg')


class GroqTests(unittest.TestCase):
    def test_frames_are_refused_because_the_model_cannot_see(self):
        transport = FakeTransport([])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            planner = GroqPlanner('secret', folder, 1, True, transport=transport)
            with self.assertRaises(ResponseInvalid):
                planner.propose(dict(duration=30), frames=[(0.0, __file__)])
        self.assertEqual(transport.count, 0)

    def test_live_quota_headers_are_recorded(self):
        transport = FakeTransport([groq_response(GOOD_PLAN)])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            planner = GroqPlanner('secret', folder, 1, True, transport=transport)
            plan = planner.propose(dict(duration=30))
            self.assertEqual(plan['candidates'][0]['title'], 'Ссора из-за денег')
            self.assertEqual(planner.last_quota['remaining_requests'], 997)
            self.assertEqual(planner.last_quota['remaining_tokens'], 7000)
            self.assertEqual(json.loads(planner.ledger.rows()[0]['quota'])['remaining_requests'], 997)
            self.assertEqual(transport.calls[0]['url'], 'https://api.groq.com/openai/v1/chat/completions')
            self.assertEqual(transport.calls[0]['payload']['response_format'], dict(type='json_object'))

    def test_fenced_json_is_accepted_without_evaluating_it(self):
        fenced = '```json\n' + json.dumps(GOOD_PLAN) + '\n```'
        body = dict(choices=[dict(finish_reason='stop', message=dict(content=fenced))])
        transport = FakeTransport([(200, {}, json.dumps(body).encode())])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            plan = GroqPlanner('secret', folder, 1, True, transport=transport).propose(dict(duration=30))
            self.assertEqual(len(plan['candidates']), 1)

    def test_truncated_answer_is_not_retried(self):
        body = dict(choices=[dict(finish_reason='length', message=dict(content='{"schema":1'))])
        transport = FakeTransport([(200, {}, json.dumps(body).encode())])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            with self.assertRaises(ResponseInvalid):
                GroqPlanner('secret', folder, 1, True, transport=transport).propose(dict(duration=30))
            self.assertEqual(transport.count, 1)


class FallbackTests(unittest.TestCase):
    def test_quota_on_primary_falls_back_and_records_the_switch(self):
        gemini = GeminiPlanner('k1', self.folder.name, 1, True,
                               transport=FakeTransport([(429, {'retry-after': '60'}, b'{}')]))
        groq = GroqPlanner('k2', self.folder.name, 1, True, transport=FakeTransport([groq_response(GOOD_PLAN)]))
        chain = FallbackChain([gemini, groq])
        plan = chain.propose(dict(duration=30))
        self.assertEqual(plan['meta']['provider'], 'groq')
        self.assertEqual([a['status'] for a in plan['meta']['attempts']], ['QuotaExhausted', 'completed'])

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.folder = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def test_vision_request_skips_text_only_provider(self):
        groq_transport = FakeTransport([])
        groq = GroqPlanner('k2', self.folder.name, 1, True, transport=groq_transport)
        gemini = GeminiPlanner('k1', self.folder.name, 1, True, transport=FakeTransport([gemini_response(GOOD_PLAN)]))
        image = self.folder / 'frame.jpg'
        image.write_bytes(b'\xff\xd8\xff\xe0' + bytes(7) * 300)
        chain = FallbackChain([groq, gemini])
        self.assertTrue(chain.supports_vision)
        plan = chain.propose(dict(duration=30), frames=[(1.0, str(image))])
        self.assertEqual(plan['meta']['provider'], 'gemini')
        self.assertEqual(groq_transport.count, 0)

    def test_all_providers_failing_reports_every_reason(self):
        gemini = GeminiPlanner('k1', self.folder.name, 1, True, transport=FakeTransport([(503, {}, b'')]))
        groq = GroqPlanner('k2', self.folder.name, 1, True,
                           transport=FakeTransport([(429, {'retry-after': '5'}, b'{}')]))
        with self.assertRaises(ProviderUnavailable) as caught:
            FallbackChain([gemini, groq]).propose(dict(duration=30))
        self.assertIn('gemini', str(caught.exception))
        self.assertIn('groq', str(caught.exception))

    def test_empty_chain_is_refused(self):
        with self.assertRaises(ConsentRequired):
            FallbackChain([None, None])


if __name__ == '__main__':
    unittest.main()
