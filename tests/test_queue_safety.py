import os
import unittest
from jobs import JobStore


class QueueSafetyTests(unittest.TestCase):
    def setUp(self):
        self.store = JobStore(':memory:')
        self.addCleanup(self.store.close)

    def test_retry_rejects_target_reserved_by_another_active_job(self):
        old = self.store.add(dict(target='same.mp4'))
        self.store.update(old, 'failed')
        self.store.add(dict(target='./same.mp4'))
        with self.assertRaises(ValueError):
            self.store.retry(old)
        self.assertEqual(next(row for row in self.store.rows() if row['id'] == old)['status'], 'failed')

    def test_retry_requeues_retryable_job(self):
        job = self.store.add(dict(target='retry.mp4'))
        self.store.update(job, 'interrupted')
        self.store.retry(job)
        self.assertEqual(self.store.next()['id'], job)

    def test_retry_cannot_requeue_running_or_completed_job(self):
        for status in ('pending', 'running', 'completed'):
            with self.subTest(status=status):
                job = self.store.add(dict(target=status + '.mp4'))
                self.store.update(job, status)
                with self.assertRaises(ValueError):
                    self.store.retry(job)

    def test_pending_cancel_skips_job_without_affecting_the_next(self):
        first = self.store.add(dict(target='first.mp4'))
        second = self.store.add(dict(target='second.mp4'))
        self.assertTrue(self.store.cancel_pending(first))
        self.assertEqual(self.store.next()['id'], second)
        self.assertFalse(self.store.cancel_pending(first))

    def test_pending_cancel_cannot_change_a_running_job(self):
        job = self.store.add(dict(target='running.mp4'))
        self.store.update(job, 'running')
        self.assertFalse(self.store.cancel_pending(job))
        self.assertEqual(self.store.rows()[0]['status'], 'running')

    @unittest.skipUnless(os.name == 'nt', 'Windows path semantics')
    def test_windows_target_case_is_not_a_new_output_name(self):
        self.store.add(dict(target='Some-Short.mp4'))
        with self.assertRaises(ValueError):
            self.store.add(dict(target='SOME-SHORT.MP4'))
