from pathlib import Path
import tempfile
import unittest
from jobs import JobStore


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'jobs.sqlite3'

    def test_recovery_retains_pending_and_marks_running(self):
        store = JobStore(self.path)
        first = store.add(dict(target='first.mp4'))
        second = store.add(dict(target='second.mp4'))
        store.update(first, 'running')
        store.close()
        store = JobStore(self.path)
        try:
            self.assertEqual({row['id']: row['status'] for row in store.rows()},
                             {first: 'interrupted', second: 'pending'})
            self.assertEqual(store.next()['id'], second)
        finally:
            store.close()

    def test_duplicate_active_target_is_rejected(self):
        store = JobStore(self.path)
        try:
            store.add(dict(target='same.mp4'))
            with self.assertRaises(ValueError):
                store.add(dict(target='same.mp4'))
        finally:
            store.close()
