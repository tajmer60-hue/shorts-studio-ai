"""Opt-in real media checks; never download weights from tests."""
from pathlib import Path
import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch,Mock
from speech import transcribe


@unittest.skipUnless(os.environ.get('SHORTS_TEST_SPEECH_VIDEO'), 'Set SHORTS_TEST_SPEECH_VIDEO for real local ASR test')
class RealSpeechTests(unittest.TestCase):
    def test_real_words_and_rebuild_from_completed_chunks(self):
        source=Path(os.environ['SHORTS_TEST_SPEECH_VIDEO'])
        model=Path('models/faster-whisper-small')
        with tempfile.TemporaryDirectory(dir=Path('cache').resolve()) as temp:
            root=Path(temp)
            report=transcribe(source,model,root,threading.Event())
            self.assertGreater(len(report['words']),20)
            self.assertTrue(report['vad'])
            self.assertTrue(report['cues'])
            for word in report['words']:
                self.assertGreaterEqual(word['start'],0)
                self.assertGreater(word['end'],word['start'])
                self.assertLessEqual(word['end'],report['duration'])
            folder=next(p for p in root.iterdir() if p.is_dir())
            recovery=root/'recovery';chunk_folder=recovery/folder.name
            chunk_folder.mkdir(parents=True)
            for chunk in folder.glob('chunk-*.json'):shutil.copyfile(chunk,chunk_folder/chunk.name)
            model_stub=Mock()
            model_stub.transcribe.side_effect=AssertionError('Completed chunk retranscribed')
            with patch('faster_whisper.WhisperModel',return_value=model_stub):
                resumed=transcribe(source,model,recovery,threading.Event())
            self.assertEqual(resumed['words'],report['words'])
            model_stub.transcribe.assert_not_called()
