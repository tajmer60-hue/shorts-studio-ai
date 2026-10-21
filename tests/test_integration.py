from pathlib import Path
import json
import os
import queue
import subprocess
import tempfile
import threading
import unittest

from render import executable
from app import export_worker
from analyze_reference import analyze
from PySide6.QtWidgets import QApplication
from app import Studio, configure_font
from PySide6.QtGui import QFontMetrics


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = executable('ffmpeg')
            cls.ffprobe = executable('ffprobe')
        except ValueError as error:
            raise unittest.SkipTest(str(error))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'тест with spaces.mp4'

    def generate(self, audio=True):
        command = [self.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24:duration=2']
        if audio:
            command += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:a', 'aac']
        command += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(self.source)]
        subprocess.run(command, check=True, capture_output=True, timeout=30)

    def export(self, mode='fit', cancelled=False):
        params = dict(source=str(self.source), target=str(self.folder / f'{mode}.mp4'),
                      start=0.25, duration=1, mode=mode)
        stop = threading.Event()
        if cancelled:
            stop.set()
        events = queue.Queue()
        export_worker(params, stop, events)
        result = None
        while not events.empty():
            kind, value = events.get_nowait()
            if kind == 'finished':
                result = value
        return params, result

    def test_real_export_dimensions_duration_audio_and_cleanup(self):
        self.generate()
        params, result = self.export()
        self.assertEqual(result[0], 'completed', result)
        data = json.loads(subprocess.check_output([self.ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', params['target']], text=True))
        video = next(s for s in data['streams'] if s['codec_type'] == 'video')
        self.assertEqual((video['width'], video['height']), (1080, 1920))
        self.assertAlmostEqual(float(data['format']['duration']), 1, delta=0.15)
        self.assertTrue(any(s['codec_type'] == 'audio' for s in data['streams']))
        self.assertTrue(self.source.exists())
        self.assertFalse(list(self.folder.glob('*.partial.*')))

    def test_crop_without_audio_and_existing_output_protection(self):
        self.generate(audio=False)
        params, result = self.export(mode='crop')
        self.assertEqual(result[0], 'completed', result)
        original = Path(params['target']).read_bytes()
        _, result = self.export(mode='crop')
        self.assertEqual(result[0], 'failed')
        self.assertEqual(Path(params['target']).read_bytes(), original)

    def test_cancel_before_export(self):
        self.generate()
        params, result = self.export(cancelled=True)
        self.assertEqual(result[0], 'cancelled')
        self.assertFalse(Path(params['target']).exists())
        self.assertFalse(list(self.folder.glob('*.partial.*')))

    def test_cancel_after_export_starts(self):
        self.generate()
        params = dict(source=str(self.source), target=str(self.folder / 'cancel.mp4'),
                      start=0, duration=2, mode='fit')
        stop, events = threading.Event(), queue.Queue()
        worker = threading.Thread(target=export_worker, args=(params, stop, events))
        worker.start()
        kind, value = events.get(timeout=15)
        self.assertEqual(kind, 'progress')
        stop.set()
        worker.join(timeout=15)
        self.assertFalse(worker.is_alive())
        result = None
        while not events.empty():
            kind, value = events.get_nowait()
            if kind == 'finished':
                result = value
        self.assertEqual(result[0], 'cancelled', result)
        self.assertFalse(Path(params['target']).exists())
        self.assertFalse(list(self.folder.glob('*.partial.*')))

    def test_reference_pipeline_with_synthetic_media(self):
        self.generate()
        report = self.folder / 'report.md'
        metrics = analyze(self.source, report, self.folder / 'artifacts')
        self.assertGreater(metrics['duration'], 1.9)
        self.assertTrue(report.exists())
        self.assertTrue((self.folder / 'artifacts/contact-sheet.jpg').is_file())


class GuiTests(unittest.TestCase):
    def test_window_initialization_and_close(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        application = QApplication.instance() or QApplication([])
        configure_font(application)
        if os.name == 'nt':
            self.assertTrue(QFontMetrics(application.font()).inFontUcs4(ord('Я')))
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            window = Studio(folder)
            application.processEvents()
            self.assertEqual(window.table.rowCount(), 0)
            window.close()
