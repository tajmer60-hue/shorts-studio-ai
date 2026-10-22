from pathlib import Path
import json
import os
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from unittest.mock import patch, Mock

from pipeline import export_worker, digest
from render import executable, probe

CRASH_SCRIPT = '''
import json,os,queue,sys,threading
from pipeline import export_worker
p=json.loads(sys.argv[1]); stage=sys.argv[2]
def crash(current):
    if current == stage: os._exit(77)
export_worker(p,threading.Event(),queue.Queue(),crash)
'''


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / 'cache' if (Path.cwd() / 'cache').exists() else Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        real = os.environ.get('SHORTS_TEST_VIDEO')
        self.source = Path(real) if real else self.root / 'source.mp4'
        if not real:
            subprocess.run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=320x180:rate=24:duration=2', '-c:v', 'libx264', str(self.source)],
                check=True, capture_output=True, timeout=30)
        self.original = digest(self.source)

    def params(self, name):
        return dict(source=str(self.source.resolve()), target=str(self.root / (name + '.mp4')),
            start=0, duration=1, mode='fit', _checkpoint_db=str(self.root / (name + '.sqlite3')))

    def run_export(self, params):
        events = queue.Queue()
        export_worker(params, threading.Event(), events)
        values = []
        while not events.empty():
            values.append(events.get_nowait())
        return next(v for k, v in values if k == 'finished')

    def state(self, params):
        with closing(sqlite3.connect(params['_checkpoint_db'])) as db:
            return json.loads(db.execute('SELECT state FROM export_stages').fetchone()[0])

    def test_process_crash_at_each_completed_stage(self):
        for stage in ('probe', 'rendering', 'encoding', 'render', 'qc', 'after_publish', 'published'):
            with self.subTest(stage=stage):
                params = self.params(stage)
                result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', CRASH_SCRIPT,
                    json.dumps(params), stage], capture_output=True, timeout=45)
                self.assertEqual(result.returncode, 77, result.stderr)
                if stage not in ('probe', 'rendering', 'encoding'):
                    # Recovery must not call FFmpeg or probe again after validated QC.
                    with patch('pipeline.subprocess.Popen', side_effect=AssertionError('render repeated')):
                        if stage in ('qc', 'after_publish', 'published'):
                            with patch('pipeline.probe', side_effect=AssertionError('probe repeated')):
                                finished = self.run_export(params)
                        else:
                            # probe() itself uses Popen, so provide real measured duration.
                            with patch('pipeline.probe', return_value=1):
                                finished = self.run_export(params)
                else:
                    finished = self.run_export(params)
                self.assertEqual(finished[0], 'completed', finished)
                self.assertEqual(self.state(params)['stage'], 'published')
                self.assertAlmostEqual(probe(params['target']), 1, delta=.15)
                subprocess.run([executable('ffmpeg'), '-v', 'error', '-i', params['target'],
                    '-f', 'null', '-'], capture_output=True, check=True, timeout=30)
                self.assertEqual(digest(self.source), self.original)

    def test_foreign_target_is_not_accepted_after_crash(self):
        params = self.params('foreign')
        result = subprocess.run([sys.executable, '-B', '-c', CRASH_SCRIPT, json.dumps(params), 'qc'],
                                capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 77)
        target = Path(params['target'])
        target.write_bytes(b'foreign file')
        self.assertEqual(self.run_export(params)[0], 'failed')
        self.assertEqual(target.read_bytes(), b'foreign file')

    def test_low_disk_stops_before_render(self):
        params = self.params('lowdisk')
        with patch('runtime_support.shutil.disk_usage', return_value=Mock(free=1)):
            self.assertEqual(self.run_export(params)[0], 'failed')
        self.assertFalse(Path(params['target']).exists())

    def test_damaged_source_fails_without_output(self):
        params = self.params('broken')
        broken = self.root / 'broken-source.mp4'
        broken.write_bytes(b'not a movie')
        params['source'] = str(broken)
        self.assertEqual(self.run_export(params)[0], 'failed')
        self.assertEqual(broken.read_bytes(), b'not a movie')
