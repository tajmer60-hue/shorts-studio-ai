"""Local Windows application. No network processing of user media."""
from pathlib import Path
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import uuid

from PySide6.QtCore import QTimer, QLockFile
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QTabWidget)
from jobs import JobStore
from render import build_command, probe, validate_paths, publish_no_replace
from version import __version__
from runtime_support import check_disk, configure_logging, source_key, ProbeCache, read_log_tail


from pipeline import export_worker
from analysis_lite import analyze


class Studio(QMainWindow):
    def __init__(self, data_dir):
        super().__init__()
        self.setWindowTitle(f'Shorts Studio AI {__version__} — локальный монтаж')
        self.resize(1000, 700)
        self.database_path = str(Path(data_dir) / 'jobs.sqlite3')
        self.store = JobStore(self.database_path)
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.current = None
        self.closing = False
        self.paused = False
        self.last_folder = None
        self.analysis_folder = Path(data_dir).parent / 'cache' / 'analysis'
        self.analyzing = False
        self.analysis_cancel = threading.Event()
        self.candidates = []
        self.transcript = None
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel(f'Shorts Studio AI {__version__}')
        title.setStyleSheet('font-size: 28px; font-weight: 700; padding: 10px 0;')
        layout.addWidget(title)
        layout.addWidget(QLabel('Локальные файлы • MP4 1080 × 1920 • постоянная очередь'))
        form = QFormLayout()
        file_row = QHBoxLayout()
        self.source = QLineEdit()
        choose = QPushButton('Выбрать видео')
        choose.clicked.connect(self.select_source)
        file_row.addWidget(self.source)
        file_row.addWidget(choose)
        form.addRow('Исходное видео', file_row)
        self.start = QDoubleSpinBox()
        self.start.setRange(0, 1000000)
        self.start.setDecimals(3)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.1, 86400)
        self.duration.setValue(30)
        self.duration.setDecimals(3)
        form.addRow('Начало, секунд', self.start)
        form.addRow('Длительность, секунд', self.duration)
        self.mode = QComboBox()
        self.mode.addItem('Вписать с полями — сохранить весь кадр', 'fit')
        self.mode.addItem('Обрезать по центру — края будут скрыты', 'crop')
        form.addRow('Кадрирование', self.mode)
        layout.addLayout(form)
        self.analyze_button = QPushButton('Анализ серии — Lite')
        self.analyze_button.clicked.connect(self.analyze_source)
        layout.addWidget(self.analyze_button)
        self.speech_button = QPushButton('Распознать речь и субтитры — локально')
        self.speech_button.clicked.connect(self.analyze_speech)
        layout.addWidget(self.speech_button)
        self.candidate_table = QTableWidget(0, 4)
        self.candidate_table.setHorizontalHeaderLabels(['Начало', 'Конец', 'Оценка Lite', 'Причина / проверка'])
        self.candidate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.candidate_table.horizontalHeader().setStretchLastSection(True)
        self.candidate_table.cellDoubleClicked.connect(self.use_candidate)
        tabs = QTabWidget()
        tabs.addTab(self.candidate_table, 'Предложения Lite')
        self.speech_table = QTableWidget(0, 3)
        self.speech_table.setHorizontalHeaderLabels(['Начало', 'Конец', 'Распознанная фраза — проверьте'])
        self.speech_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.speech_table.horizontalHeader().setStretchLastSection(True)
        tabs.addTab(self.speech_table, 'Речь')
        layout.addWidget(tabs)
        save_srt = QPushButton('Сохранить SRT исходника')
        save_srt.clicked.connect(self.save_subtitles)
        layout.addWidget(save_srt)
        layout.addWidget(QLabel('Двойной щелчок по предложению подставляет отрезок. Lite не понимает сюжет и речь.'))
        actions = QHBoxLayout()
        add = QPushButton('Добавить в очередь')
        add.clicked.connect(self.add_job)
        actions.addWidget(add)
        self.stop = QPushButton('Отменить текущее')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.cancel)
        actions.addWidget(self.stop)
        selected_cancel = QPushButton('Отменить выбранное')
        selected_cancel.clicked.connect(self.cancel_selected)
        actions.addWidget(selected_cancel)
        self.pause = QPushButton('Приостановить очередь')
        self.pause.clicked.connect(self.toggle_pause)
        actions.addWidget(self.pause)
        retry = QPushButton('Повторить выбранное')
        retry.clicked.connect(self.retry)
        actions.addWidget(retry)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.status = QLabel('Выберите видео. Файлы не отправляются в интернет.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['№', 'Состояние', 'Результат', 'Сообщение'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        folder = QPushButton('Открыть папку последнего результата')
        folder.clicked.connect(self.open_output)
        layout.addWidget(folder)
        layout.addWidget(QLabel('P1: локальная речь и Lite. Смысловой монтаж, сопровождение кадра и цензура ещё в работе.'))
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    def select_source(self):
        value, _ = QFileDialog.getOpenFileName(self, 'Выберите видео', '', 'Видео (*.mp4 *.mkv *.mov *.avi *.webm);;Все файлы (*)')
        if value:
            self.source.setText(value)

    def analyze_source(self):
        if self.current is not None or self.analyzing:
            return
        source = Path(self.source.text())
        if not source.is_file():
            QMessageBox.warning(self, 'Анализ', 'Выберите существующее видео.')
            return
        self.analyzing = True
        self.analysis_cancel = threading.Event()
        self.analyze_button.setEnabled(False)
        self.stop.setEnabled(True)
        self.status.setText('Lite: поиск смен планов и тишины…')
        self.progress.setRange(0, 0)
        def work():
            try:
                report = analyze(source, self.analysis_folder, self.analysis_cancel)
                self.events.put(('analysis_done', report))
            except Exception as error:
                self.events.put(('analysis_error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def use_candidate(self, row, column):
        if row < len(self.candidates):
            item = self.candidates[row]
            self.source.setText(self.analysis_source)
            self.start.setValue(item['start'])
            self.duration.setValue(item['end'] - item['start'])
            self.status.setText('Отрезок выбран. Проверьте его перед финальным использованием; экспорт — кнопкой «Добавить в очередь».')

    def analyze_speech(self):
        if self.current is not None or self.analyzing:
            return
        source = Path(self.source.text())
        if not source.is_file():
            QMessageBox.warning(self, 'Речь', 'Выберите видео.')
            return
        self.analyzing = True
        self.analysis_cancel = threading.Event()
        self.speech_button.setEnabled(False)
        self.analyze_button.setEnabled(False)
        self.stop.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText('Локальное распознавание речи. Модель запускается на компьютере…')
        def work():
            try:
                from speech import transcribe
                model = Path(os.environ.get('SHORTS_STUDIO_MODEL_DIR') or Path(__file__).parent / 'models' / 'faster-whisper-small')
                report = transcribe(source, model, self.analysis_folder.parent / 'speech', self.analysis_cancel,
                                    lambda value: self.events.put(('progress', value)))
                self.events.put(('speech_done', report))
            except ModuleNotFoundError:
                self.events.put(('analysis_error', 'В этой сборке нет AI-зависимостей. Запустите start.bat из папки проекта с установленным окружением.'))
            except Exception as error:
                self.events.put(('analysis_error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def save_subtitles(self):
        if self.transcript is None:
            QMessageBox.information(self, 'Субтитры', 'Сначала выполните распознавание речи.')
            return
        name, _ = QFileDialog.getSaveFileName(self, 'SRT полного исходника', '', 'SubRip (*.srt)')
        if not name:
            return
        try:
            from speech import srt
            with Path(name).with_suffix('.srt').open('x', encoding='utf-8') as output:
                output.write(srt(self.transcript['cues']))
        except OSError as error:
            QMessageBox.warning(self, 'Субтитры', str(error))

    def add_job(self):
        source = Path(self.source.text())
        if not source.is_file():
            QMessageBox.warning(self, 'Исходник', 'Выберите существующий видеофайл.')
            return
        name, _ = QFileDialog.getSaveFileName(self, 'Новый результат', str(source.with_name(source.stem + '-short.mp4')), 'MP4 (*.mp4)')
        if not name:
            return
        target = Path(name).with_suffix('.mp4')
        if target.exists():
            QMessageBox.warning(self, 'Результат', 'Выберите новое имя. Существующие файлы не перезаписываются.')
            return
        try:
            self.store.add(dict(source=str(source.resolve()), target=str(target.resolve()),
                start=self.start.value(), duration=self.duration.value(), mode=self.mode.currentData()))
            self.refresh()
        except ValueError as error:
            QMessageBox.warning(self, 'Очередь', str(error))

    def refresh(self):
        selected_id = self.selected_job_id()
        rows = self.store.rows()
        self.table.setRowCount(len(rows))
        names = dict(pending='В очереди', running='Обработка', completed='Готово',
                     failed='Ошибка', cancelled='Отменено', interrupted='Прервано')
        for i, row in enumerate(rows):
            values = [str(row['id']), names[row['status']], json.loads(row['parameters'])['target'], row['message']]
            for col, value in enumerate(values):
                self.table.setItem(i, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        if selected_id is not None:
            for index, row in enumerate(rows):
                if row['id'] == selected_id:
                    self.table.selectRow(index)
                    break

    def selected_job_id(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return int(item.text()) if item is not None else None

    def retry(self):
        job_id = self.selected_job_id()
        if job_id is None:
            return
        try:
            self.store.retry(job_id)
            self.refresh()
        except ValueError as error:
            QMessageBox.warning(self, 'Очередь', str(error))

    def cancel_selected(self):
        job_id = self.selected_job_id()
        if job_id is None:
            return
        if job_id == self.current:
            self.cancel()
        elif self.store.cancel_pending(job_id):
            self.status.setText(f'Задание {job_id} отменено до запуска.')
            self.refresh()
        else:
            self.status.setText('Выберите задание в очереди или текущее задание.')

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause.setText('Продолжить очередь' if self.paused else 'Приостановить очередь')

    def cancel(self):
        self.analysis_cancel.set()
        self.cancelled.set()
        self.status.setText('Отменяю текущее задание…')

    def open_output(self):
        if self.last_folder:
            os.startfile(self.last_folder)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'speech_done':
                    self.analyzing = False
                    self.speech_button.setEnabled(True)
                    self.analyze_button.setEnabled(True)
                    self.stop.setEnabled(False)
                    self.transcript = value
                    self.speech_table.setRowCount(len(value['cues']))
                    for i, cue in enumerate(value['cues']):
                        for col, key in enumerate(('start', 'end', 'text')):
                            self.speech_table.setItem(i, col, QTableWidgetItem(str(cue[key])))
                    self.progress.setValue(100)
                    self.status.setText(f"Речь: {len(value['words'])} слов, {len(value['pause_proposals'])} пауз для проверки. Автоматически ничего не вырезано.")
                elif kind in ('analysis_done', 'analysis_error'):
                    self.analyzing = False
                    self.speech_button.setEnabled(True)
                    self.analyze_button.setEnabled(True)
                    self.stop.setEnabled(False)
                    self.progress.setRange(0, 100)
                    if kind == 'analysis_error':
                        self.status.setText(value)
                    else:
                        self.candidates = value['candidates']
                        self.analysis_source = value['source']
                        self.candidate_table.setRowCount(len(self.candidates))
                        for i, candidate in enumerate(self.candidates):
                            for col, key in enumerate(('start', 'end', 'score', 'reason')):
                                self.candidate_table.setItem(i, col, QTableWidgetItem(str(candidate[key])))
                        self.progress.setValue(100)
                        self.status.setText(f"Lite: {len(value['scene_boundaries'])} смен планов, {len(self.candidates)} предложений. Требуется просмотр; ASR ещё нет.")
                elif kind == 'progress':
                    self.progress.setValue(int(value))
                elif kind == 'finished':
                    status, message = value
                    self.store.update(self.current, status, message)
                    self.current = None
                    self.stop.setEnabled(False)
                    self.status.setText(message)
                    if status == 'completed':
                        self.progress.setValue(100)
                        self.last_folder = str(Path(message).parent)
                    self.refresh()
        except queue.Empty:
            pass
        if self.closing:
            if self.current is None and not self.analyzing:
                self.close()
            return
        if self.current is None and not self.paused and not self.analyzing:
            row = self.store.next()
            if row:
                self.current = row['id']
                self.store.update(self.current, 'running')
                self.cancelled = threading.Event()
                self.progress.setValue(0)
                self.status.setText(f'Задание {self.current}: проверка и экспорт…')
                self.stop.setEnabled(True)
                self.refresh()
                parameters = json.loads(row['parameters'])
                parameters['_checkpoint_db'] = self.database_path
                threading.Thread(target=export_worker, args=(parameters, self.cancelled, self.events), daemon=True).start()

    def closeEvent(self, event):
        if self.current is not None or self.analyzing:
            self.closing = True
            self.cancel()
            event.ignore()
        else:
            self.timer.stop()
            self.store.close()
            event.accept()


def configure_font(app):
    # Offscreen Windows environments may not enumerate installed fonts.
    if sys.platform == 'win32' and not QFontDatabase.families():
        font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'segoeui.ttf'
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    if 'Segoe UI' in QFontDatabase.families():
        app.setFont(QFont('Segoe UI', 10))


def main():
    app = QApplication(sys.argv)
    configure_font(app)
    app.setApplicationName('ShortsStudioAI')
    app.setOrganizationName('ShortsStudioAI')
    data = Path(os.environ.get('SHORTS_STUDIO_DATA_DIR') or Path(__file__).resolve().parent / 'data')
    data.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data / 'app.lock'))
    if not lock.tryLock(100):
        QMessageBox.information(None, 'Shorts Studio AI', 'Приложение уже запущено или папка данных занята.')
        return 1
    configure_logging(data / 'logs')
    from auto_ui import AutoStudio
    window = AutoStudio(data)
    window.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == '__main__':
    sys.exit(main())

