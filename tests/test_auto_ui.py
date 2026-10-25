"""Offscreen GUI checks for provider selection and the censor review queue.

These verify consent gating and that detection is shown as an unchecked proposal.
They do not start a worker thread and do not touch the network.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from auto_ui import AutoStudio
from auto_workflow import PROVIDERS
from app import configure_font

HITS = [dict(index=1, start=12.4, end=13.0, word='сука', term='сука', confidence='high',
             approved=False, reason='Совпадение с корнем «сука». Проверьте контекст и смысл.'),
        dict(index=5, start=48.0, end=48.6, word='кзаапп', term=None, confidence='low',
             approved=False, reason='ASR не уверен в слове; проверьте, нужен ли mute.')]


class AutoUiTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        configure_font(self.app)
        self._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self._temp.name)
        (self.root / 'data').mkdir()
        self.window = AutoStudio(self.root / 'data')

    def tearDown(self):
        self.window.close()
        self._temp.cleanup()

    def test_every_provider_is_selectable(self):
        self.assertEqual([self.window.provider.itemText(i) for i in range(self.window.provider.count())],
                         list(PROVIDERS))

    def test_worker_error_is_not_replaced_by_success(self):
        self.window.events.put(('error','FFmpeg failed'))
        self.window.poll()
        self.assertIn('FFmpeg failed',self.window.status.text())
        self.assertNotIn('Подготовлено',self.window.status.text())
        self.assertEqual(self.window.progress.value(),0)

    def test_saved_key_load_does_not_authorize_upload(self):
        with patch('auto_ui.load_key',return_value='test-saved-credential'):
            window=AutoStudio(self.root/'data')
        try:
            self.assertEqual(window.keys['openrouter_key'].text(),'test-saved-credential')
            self.assertFalse(window.allowed.isChecked())
            self.assertFalse(window.training.isChecked())
            self.assertEqual(window.budget.value(),0)
            self.assertNotIn('test-saved-credential',window.connection_status.text())
        finally:
            window.close()

    def test_settings_can_scroll_on_laptop_screen(self):
        self.window.resize(1180,720)
        self.window.show();self.app.processEvents()
        self.assertGreater(self.window.scroll.verticalScrollBar().maximum(),0)
        self.window.scroll.ensureWidgetVisible(self.window.save)
        self.app.processEvents()
        self.assertGreater(self.window.scroll.verticalScrollBar().value(),0)

    def test_switching_provider_hides_the_keys_that_are_not_used(self):
        self.window.provider_changed('auto')
        self.assertTrue(all(row.isVisibleTo(row.parentWidget()) for row in self.window.key_rows.values()))
        self.window.provider_changed('gemini')
        self.assertTrue(self.window.key_rows['gemini_key'].isVisibleTo(self.window.key_rows['gemini_key'].parentWidget()))
        self.assertFalse(self.window.key_rows['groq_key'].isVisibleTo(self.window.key_rows['groq_key'].parentWidget()))
        self.assertFalse(self.window.key_rows['openrouter_key'].isVisibleTo(self.window.key_rows['openrouter_key'].parentWidget()))

    def test_missing_source_is_refused_without_a_worker(self):
        self.window.source.setText(str(self.root / 'absent.mp4'))
        self.window.begin()
        self.assertIn('существующий видеофайл', self.window.status.text())
        self.assertFalse(self.window.busy)

    def test_authorized_run_without_a_key_never_starts(self):
        video = self.root / 'source.mp4'
        video.write_bytes(b'not really a video')
        self.window.source.setText(str(video))
        self.window.allowed.setChecked(True)
        self.window.budget.setValue(1.0)
        self.window.provider.setCurrentText('gemini')
        with patch('auto_ui.threading.Thread') as thread:
            self.window.begin()
            thread.assert_not_called()
        self.assertFalse(self.window.busy)
        self.assertIn('gemini_key', self.window.status.text())

    def test_zero_budget_is_refused_even_with_a_key(self):
        video = self.root / 'source.mp4'
        video.write_bytes(b'not really a video')
        self.window.source.setText(str(video))
        self.window.allowed.setChecked(True)
        self.window.provider.setCurrentText('gemini')
        self.window.keys['gemini_key'].setText('secret')
        self.window.budget.setValue(0)
        with patch('auto_ui.threading.Thread') as thread:
            self.window.begin()
            thread.assert_not_called()
        self.assertIn('лимит проекта', self.window.status.text())
        self.assertNotIn('secret', self.window.status.text())

    def test_detected_words_are_unchecked_proposals(self):
        project = self.root / 'project'
        project.mkdir()
        (project / 'censor_review.json').write_text(
            json.dumps(dict(schema=1, lexicon={}, hits=HITS, approved_ops=[], limitations=[]),
                       ensure_ascii=False), encoding='utf-8')
        self.window.load_censor_review(project)
        self.assertEqual(self.window.profanity.count(), 2)
        self.assertTrue(self.window.apply_censor.isEnabled())
        for index in range(self.window.profanity.count()):
            item = self.window.profanity.item(index)
            self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)
            self.assertFalse(item.data(Qt.ItemDataRole.UserRole)['approved'])
        self.assertIn('сука', self.window.profanity.item(0).text())
        self.assertIn('сомнительно', self.window.profanity.item(1).text())
        self.assertIn('12', self.window.profanity.item(0).text())

    def test_missing_review_file_is_handled(self):
        self.window.load_censor_review(self.root / 'absent')
        self.assertEqual(self.window.profanity.count(), 1)
        self.assertFalse(self.window.apply_censor.isEnabled())

    def test_rebuild_without_a_selection_changes_nothing(self):
        project = self.root / 'project'
        project.mkdir()
        (project / 'censor_review.json').write_text(
            json.dumps(dict(schema=1, lexicon={}, hits=HITS, approved_ops=[], limitations=[]),
                       ensure_ascii=False), encoding='utf-8')
        self.window.load_censor_review(project)
        with patch('auto_ui.threading.Thread') as thread:
            self.window.rebuild_with_censor()
            thread.assert_not_called()
        self.assertIn('Отметьте слова', self.window.status.text())
        self.assertTrue(all(hit['approved'] is False for hit in HITS))

    def test_uncertain_word_cannot_be_muted_by_a_tick_alone(self):
        project = self.root / 'project'
        project.mkdir()
        uncertain = [dict(HITS[1])]
        (project / 'censor_review.json').write_text(
            json.dumps(dict(schema=1, lexicon={}, hits=uncertain, approved_ops=[], limitations=[]),
                       ensure_ascii=False), encoding='utf-8')
        self.window.load_censor_review(project)
        self.window.profanity.item(0).setCheckState(Qt.CheckState.Checked)
        warnings = []
        with patch('auto_ui.QMessageBox.warning', side_effect=lambda *a, **k: warnings.append(a[2])), \
             patch('auto_ui.threading.Thread') as thread:
            self.window.rebuild_with_censor()
            thread.assert_not_called()
        self.assertEqual(len(warnings), 1)
        self.assertIn('неуверенное', warnings[0].lower())


    def test_free_is_the_default_and_needs_no_budget(self):
        self.assertEqual(self.window.provider.currentText(), 'free')
        self.window.provider_changed('free')
        self.assertFalse(self.window.budget.isEnabled())
        self.assertEqual(self.window.budget.value(), 0)
        self.assertTrue(self.window.training.isVisibleTo(self.window.training.parentWidget()))
        self.assertIn('запросами в сутки', self.window.quota_label.text())

    def test_training_consent_is_only_offered_for_free_routes(self):
        self.window.provider_changed('gemini')
        self.assertFalse(self.window.training.isVisibleTo(self.window.training.parentWidget()))
        self.assertTrue(self.window.budget.isEnabled())
        self.window.provider_changed('auto')
        self.assertTrue(self.window.training.isVisibleTo(self.window.training.parentWidget()))

    def test_free_run_without_training_consent_is_refused(self):
        video = self.root / 'source.mp4'
        video.write_bytes(b'not really a video')
        self.window.source.setText(str(video))
        self.window.allowed.setChecked(True)
        self.window.provider.setCurrentText('free')
        self.window.provider_changed('free')
        self.window.keys['openrouter_key'].setText('secret')
        self.window.training.setChecked(False)
        with patch('auto_ui.threading.Thread') as thread:
            self.window.begin()
            thread.assert_not_called()
        self.assertIn('обработку данных', self.window.status.text())
        self.assertFalse(self.window.busy)

    def test_consent_and_provider_reach_the_worker_settings(self):
        video = self.root / 'source.mp4'
        video.write_bytes(b'not really a video')
        self.window.source.setText(str(video))
        self.window.allowed.setChecked(True)
        self.window.provider.setCurrentText('free')
        self.window.provider_changed('free')
        self.window.keys['openrouter_key'].setText('secret')
        self.window.training.setChecked(True)
        captured = {}
        with patch('auto_ui.threading.Thread') as thread:
            thread.side_effect = lambda target, daemon: captured.update(target=target) or FakeThread()
            self.window.begin()
        with patch('auto_ui.create_clips') as create:
            create.return_value = dict(prepared=True, cloud=False, project='')
            captured['target']()
            settings = create.call_args.kwargs['settings']
        self.assertEqual(settings['provider'], 'free')
        self.assertTrue(settings['training_allowed'])
        self.assertEqual(settings['budget'], 0)
        self.assertEqual(settings['openrouter_key'], 'secret')
        self.assertNotIn('secret', self.window.status.text())


    def test_mode_selector_defaults_to_serial_and_reaches_settings(self):
        self.assertEqual(self.window.mode.currentData(), 'serial')
        self.assertIn('Хронология сохраняется', self.window.mode_note.text())
        self.window.mode.setCurrentIndex(1)
        self.assertEqual(self.window.mode.currentData(), 'viral')
        self.assertIn('Независимые', self.window.mode_note.text())

    def test_inverted_duration_range_is_refused(self):
        video = self.root / 'source.mp4'
        video.write_bytes(b'not really a video')
        self.window.source.setText(str(video))
        self.window.allowed.setChecked(True)
        self.window.provider.setCurrentText('gemini')
        self.window.keys['gemini_key'].setText('secret')
        self.window.budget.setValue(1.0)
        self.window.dur_min.setValue(90)
        self.window.dur_max.setValue(45)
        with patch('auto_ui.threading.Thread') as thread:
            self.window.begin()
            thread.assert_not_called()
        self.assertIn('граница длительности', self.window.status.text())

    def test_mode_and_pacing_reach_the_worker_settings(self):
        video = self.root / 'source.mp4'
        video.write_bytes(b'not really a video')
        self.window.source.setText(str(video))
        self.window.mode.setCurrentIndex(1)
        self.window.aggr.setCurrentIndex(2)
        self.window.dur_min.setValue(30)
        self.window.dur_max.setValue(60)
        captured = {}
        with patch('auto_ui.threading.Thread') as thread:
            thread.side_effect = lambda target, daemon: captured.update(target=target) or FakeThread()
            self.window.begin()
        with patch('auto_ui.create_clips') as create:
            create.return_value = dict(prepared=True, cloud=False, project='')
            captured['target']()
            settings = create.call_args.kwargs['settings']
        self.assertEqual(settings['mode'], 'viral')
        self.assertEqual(settings['aggressiveness'], 'fast')
        self.assertEqual((settings['duration_min'], settings['duration_max']), (30.0, 60.0))
        self.assertTrue(settings['reframe'])


class FakeThread:
    def start(self):
        pass


if __name__ == '__main__':
    unittest.main()
