"""Exercise the guided workflow through actual FFmpeg preview and export."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from review_ui import ReviewStudio
from render import executable
from app import configure_font


class ReviewTests(unittest.TestCase):
    def test_preview_approval_export_and_invalidation(self):
        app=QApplication.instance() or QApplication([])
        configure_font(app)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            folder=Path(temp)
            source=Path(os.environ.get('SHORTS_TEST_REVIEW_VIDEO',folder/'source.mp4')).resolve()
            if not source.exists():
                subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i',
                    'testsrc2=size=320x180:rate=30:duration=5','-f','lavfi','-i',
                    'sine=frequency=440:duration=5','-c:v','libx264','-c:a','aac',str(source)],check=True,capture_output=True)
            (folder/'data').mkdir()
            window=ReviewStudio(folder/'data')
            window.source.setText(str(source));window.analysis_source=str(source)
            window.candidates=[dict(start=0,end=4,reason='Проверка экспорта на видео')]
            window.transcript=dict(source=str(source),cues=[dict(start=1,end=3,text='Проверочная подпись')])
            window.poll();window.catalog.setCurrentRow(0)
            window.clip_title.setText('Разговор перед обедом')
            window.output_folder.setText(str(folder))
            with patch('review_ui.QMessageBox.information'):
                window.receive_video()
            self.assertEqual(len(window.store.rows()),0)
            window.prepare_preview()
            deadline=time.monotonic()+50
            while window.analyzing and time.monotonic()<deadline:
                app.processEvents();time.sleep(.03)
            self.assertIsNotNone(window.preview_path,window.status.text())
            self.assertFalse(window.poster.pixmap().isNull())
            self.assertTrue(window.approved.isEnabled())
            window.approved.setChecked(True);window.receive_video()
            deadline=time.monotonic()+50
            while time.monotonic()<deadline:
                app.processEvents();time.sleep(.03)
                rows=window.store.rows()
                if rows and rows[0]['status'] in ('completed','failed'):break
            self.assertEqual(rows[0]['status'],'completed',rows[0]['message'])
            target=Path(json.loads(rows[0]['parameters'])['target'])
            self.assertTrue(target.with_suffix('.srt').exists())
            self.assertEqual(json.loads(target.with_suffix('.json').read_text(encoding='utf-8'))['source_in'],0)
            subprocess.run([executable('ffmpeg'),'-v','error','-i',str(target),'-f','null','-'],check=True,capture_output=True)
            artifact=os.environ.get('SHORTS_REVIEW_SCREENSHOT')
            if artifact:
                window.show();app.processEvents();window.grab().save(artifact)
            window.toggle_play()
            deadline=time.monotonic()+10
            while window.player.position()<250 and time.monotonic()<deadline:
                app.processEvents();time.sleep(.03)
            self.assertGreaterEqual(window.player.position(),250)
            window.clip_title.setText('Изменённое название')
            self.assertFalse(window.approved.isChecked())
            self.assertIsNone(window.preview_path)
            window.player.setSource(__import__('PySide6.QtCore',fromlist=['QUrl']).QUrl())
            window.close();app.processEvents()
