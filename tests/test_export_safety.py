from pathlib import Path
import queue
import threading
import unittest
from unittest.mock import patch
from app import export_worker
from render import validate_paths


class ExportSafetyTests(unittest.TestCase):
    def result(self, events):
        items = []
        while not events.empty():
            items.append(events.get_nowait())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][0], 'finished')
        return items[0][1]

    def test_precancelled_job_never_probes_or_starts_ffmpeg(self):
        stop, events = threading.Event(), queue.Queue()
        stop.set()
        with patch('app.probe') as probe, patch('app.subprocess.Popen') as popen:
            export_worker({}, stop, events)
            probe.assert_not_called()
            popen.assert_not_called()
        self.assertEqual(self.result(events)[0], 'cancelled')

    def test_existing_output_is_rejected_before_probe(self):
        root = Path(__file__).resolve().parent.parent
        params = dict(source=str(root / 'README.md'), target=str(root / 'render.py'),
                      start=0, duration=1, mode='fit')
        events = queue.Queue()
        with patch('app.probe') as probe, patch('app.subprocess.Popen') as popen:
            export_worker(params, threading.Event(), events)
            probe.assert_not_called()
            popen.assert_not_called()
        self.assertEqual(self.result(events)[0], 'failed')

    def test_malformed_job_always_sends_a_finished_event(self):
        events = queue.Queue()
        export_worker({}, threading.Event(), events)
        self.assertEqual(self.result(events)[0], 'failed')

    def test_nonexistent_output_folder_is_rejected(self):
        root = Path(__file__).resolve().parent.parent
        with self.assertRaisesRegex(ValueError, 'Папка результата'):
            validate_paths(root / 'README.md', root / '__nonexistent_output_parent__' / 'short.mp4')
