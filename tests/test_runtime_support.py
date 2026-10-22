import unittest
from unittest.mock import Mock, patch
from runtime_support import ProbeCache, check_disk, RESERVE_BYTES

class RuntimeTests(unittest.TestCase):
    def test_completed_probe_reused_but_new_identity_recomputed(self):
        cache = ProbeCache(':memory:')
        self.addCleanup(cache.close)
        compute = Mock(return_value=12.5)
        self.assertEqual(cache.duration('source-v1', compute), 12.5)
        cache.duration('source-v1', compute)
        self.assertEqual(compute.call_count, 1)
        cache.duration('source-v2', compute)
        self.assertEqual(compute.call_count, 2)

    def test_failed_probe_is_not_checkpointed(self):
        cache = ProbeCache(':memory:')
        self.addCleanup(cache.close)
        with self.assertRaises(ValueError):
            cache.duration('source', Mock(side_effect=ValueError('probe failed')))
        self.assertEqual(cache.duration('source', lambda: 2), 2)

    def test_invalid_probe_result_is_rejected(self):
        cache = ProbeCache(':memory:')
        self.addCleanup(cache.close)
        for result in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                cache.duration('source', lambda: result)

    def test_low_disk_and_live_reserve(self):
        with patch('runtime_support.shutil.disk_usage', return_value=Mock(free=RESERVE_BYTES - 1)):
            with self.assertRaises(ValueError):
                check_disk('.')
        with patch('runtime_support.shutil.disk_usage', return_value=Mock(free=RESERVE_BYTES + 1)):
            check_disk('.')
            with self.assertRaises(ValueError):
                check_disk('.', 30)

    def test_log_tail_reads_only_bounded_suffix(self):
        import io
        from runtime_support import read_log_tail
        buffer = io.BytesIO(b'x' * 100000 + b'last error')
        with patch('builtins.open', return_value=buffer):
            self.assertEqual(read_log_tail('unused', 10), 'last error')
