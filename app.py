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
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from jobs import JobStore
from render import build_command, probe, validate_paths, publish_no_replace
from version import __version__
from runtime_support import check_disk, configure_logging, source_key, ProbeCache, read_log_tail


def export_worker(params, cancelled, events):
    temporary = log = process = cache = None
    logger = logging.getLogger('shorts_studio')
    disk_errors = []
    result = ('failed', 'Не удалось завершить задание.')

    def terminate():
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass

    try:
        if cancelled.is_set():
            result = ('cancelled', 'Отменено.')
            return
        source, target = validate_paths(params['source'], params['target'])
        identity = source_key(source)
        logger.info('Export started')
        if params.get('_checkpoint_db'):
            cache = ProbeCache(params['_checkpoint_db'])
            total = cache.duration(identity, lambda: probe(source))
        else:
            total = probe(source)
        command = build_command(source, target, params['start'],
                                params['duration'], total, params['mode'])
        if cancelled.is_set():
            result = ('cancelled', 'Отменено.')
            return
        check_disk(target.parent, params['duration'])
        temporary = target.with_name(target.stem + '.' + uuid.uuid4().hex + '.partial.mp4')
        log = temporary.with_suffix('.log')
        command[-1] = str(temporary)
        with log.open('w', encoding='utf-8') as stderr:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr,
                text=True, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

            def watch():
                while process.poll() is None:
                    try:
                        check_disk(target.parent)
                    except Exception as error:
                        disk_errors.append(str(error))
                        terminate()
                        return
                    if cancelled.wait(0.25):
                        terminate()
                        return
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            for line in process.stdout:
                if line.startswith('out_time_us='):
                    try:
                        progress = float(line.split('=')[1]) / 1000000 / params['duration'] * 100
                        events.put(('progress', max(0, min(99, progress))))
                    except ValueError:
                        pass
            code = process.wait()
            watcher.join(timeout=1)
        if cancelled.is_set():
            result = ('cancelled', 'Отменено.')
        elif disk_errors:
            raise ValueError(disk_errors[0])
        elif code:
            raise ValueError('Ошибка FFmpeg: ' + read_log_tail(log)[-1800:])
        else:
            actual = probe(temporary)
            if abs(actual - params['duration']) > 0.25:
                raise ValueError('Длительность результата не совпадает с планом.')
            if source_key(source) != identity:
                raise ValueError('Исходник изменился во время экспорта.')
            if cancelled.is_set():
                result = ('cancelled', 'Отменено.')
                return
            publish_no_replace(temporary, target)
            result = ('completed', str(target))
    except Exception as error:
        result = ('failed', str(error))
        logger.exception('Export failed')
    finally:
        try:
            if process is not None:
                terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
        except Exception as error:
            result = ('failed', 'Не удалось завершить процесс: ' + str(error))
        if cache is not None:
            cache.close()
        logger.info('Export finished: %s', result[0])
        for path in (temporary, log):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        events.put(('finished', result))


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
        layout.addWidget(QLabel('M1: ручная нарезка. AI-выбор сцен, субтитры и Smart Censor ещё не реализованы.'))
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    def select_source(self):
        value, _ = QFileDialog.getOpenFileName(self, 'Выберите видео', '', 'Видео (*.mp4 *.mkv *.mov *.avi *.webm);;Все файлы (*)')
        if value:
            self.source.setText(value)

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
        self.cancelled.set()
        self.status.setText('Отменяю текущее задание…')

    def open_output(self):
        if self.last_folder:
            os.startfile(self.last_folder)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress':
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
            if self.current is None:
                self.close()
            return
        if self.current is None and not self.paused:
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
        if self.current is not None:
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
    window = Studio(data)
    window.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == '__main__':
    sys.exit(main())
