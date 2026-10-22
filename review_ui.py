"""Guided review workflow over the existing processing and queue UI."""
from pathlib import Path
import json
import queue
import threading
import uuid
import subprocess
from PySide6.QtCore import QUrl,Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QLineEdit,QListWidget,QTextEdit,QCheckBox,QTabWidget,QFileDialog,QMessageBox,QStackedWidget)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from app import Studio
from pipeline import export_worker
from delivery import timecode,safe_name,opening_title,clip_cues
from render import executable
from runtime_support import source_key


class ReviewStudio(Studio):
    def __init__(self,data_dir):
        super().__init__(data_dir)
        self.resize(1150,850)
        self.settings_file=Path(data_dir)/'preferences.json'
        self.preview_events=queue.Queue()
        self.preview_path=None
        self.selected_candidate=None
        self._catalog_key=None
        self._preview_token=None
        self._active_preview_token=None
        legacy=self.takeCentralWidget()
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root)
        layout.addWidget(QLabel('Shorts Studio AI  •  Выберите → посмотрите → сохраните'))
        source_row=QHBoxLayout();source_row.addWidget(QLabel('Исходник'));source_row.addWidget(self.source)
        choose=QPushButton('Выбрать видео');choose.clicked.connect(self.select_source);source_row.addWidget(choose)
        layout.addLayout(source_row)
        folder_row=QHBoxLayout();folder_row.addWidget(QLabel('Готовые видео сохранять сюда:'))
        self.output_folder=QLineEdit();self.output_folder.setReadOnly(True);folder_row.addWidget(self.output_folder)
        folder=QPushButton('Выбрать папку');folder.clicked.connect(self.choose_folder);folder_row.addWidget(folder)
        layout.addLayout(folder_row)
        try:self.output_folder.setText(json.loads(self.settings_file.read_text(encoding='utf-8'))['output_folder'])
        except (OSError,ValueError,KeyError):pass
        tabs=QTabWidget();layout.addWidget(tabs,1)
        workflow=QWidget();columns=QHBoxLayout(workflow);left=QVBoxLayout();right=QVBoxLayout()
        columns.addLayout(left,1);columns.addLayout(right,1)
        left.addWidget(QLabel('1. Подготовьте предложения и реплики'))
        self.analyze_button.setText('Найти фрагменты');self.speech_button.setText('Распознать реплики')
        left.addWidget(self.analyze_button);left.addWidget(self.speech_button)
        left.addWidget(QLabel('2. Выберите фрагмент (пока отбор по простым признакам)'))
        self.catalog=QListWidget();self.catalog.currentRowChanged.connect(self.select_clip);left.addWidget(self.catalog,2)
        self.excerpt=QTextEdit();self.excerpt.setReadOnly(True);self.excerpt.setPlaceholderText('Здесь будут реплики выбранного фрагмента.');left.addWidget(self.excerpt,1)
        right.addWidget(QLabel('3. Название на первых трёх секундах'))
        self.clip_title=QLineEdit();self.clip_title.setMaxLength(90);self.clip_title.setPlaceholderText('Короткое название — можно изменить');right.addWidget(self.clip_title)
        self.origin_label=QLabel('Выберите фрагмент слева');self.origin_label.setWordWrap(True);right.addWidget(self.origin_label)
        self.preview_stack=QStackedWidget()
        self.poster=QLabel('Здесь появится первый кадр после подготовки');self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.poster.setStyleSheet('background:#17212b;color:#dbe5ee;border-radius:8px;')
        self.video=QVideoWidget();self.video.setMinimumSize(280,320)
        self.preview_stack.addWidget(self.poster);self.preview_stack.addWidget(self.video);right.addWidget(self.preview_stack,1)
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.player.setAudioOutput(self.audio);self.player.setVideoOutput(self.video)
        preview_row=QHBoxLayout();preview=QPushButton('Подготовить предпросмотр');preview.clicked.connect(self.prepare_preview);preview_row.addWidget(preview)
        play=QPushButton('▶ / пауза');play.clicked.connect(self.toggle_play);preview_row.addWidget(play);right.addLayout(preview_row)
        self.approved=QCheckBox('Я просмотрел этот вариант и подтверждаю его');self.approved.setEnabled(False);right.addWidget(self.approved)
        self.get_video=QPushButton('Получить видео');self.get_video.setMinimumHeight(44);self.get_video.clicked.connect(self.receive_video);right.addWidget(self.get_video)
        tabs.addTab(workflow,'Выбор и просмотр');tabs.addTab(legacy,'Ручной режим, речь и очередь')
        layout.addWidget(self.progress);layout.addWidget(self.status)
        actions=QHBoxLayout();actions.addWidget(self.stop);open_folder=QPushButton('Открыть папку готовых видео');open_folder.clicked.connect(self.show_export_folder);actions.addWidget(open_folder);layout.addLayout(actions)
        self.clip_title.textChanged.connect(self.invalidate_preview)
        self.mode.currentIndexChanged.connect(self.invalidate_preview)
        self.source.textChanged.connect(self.source_changed)

    def source_changed(self):
        self.invalidate_preview()
        if self.source.text()!=getattr(self,'analysis_source',None):
            self.selected_candidate=None;self.catalog.clear();self._catalog_key=None

    def choose_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'Папка готовых видео',self.output_folder.text())
        if folder:
            self.output_folder.setText(folder)
            self.settings_file.write_text(json.dumps(dict(output_folder=folder),ensure_ascii=False),encoding='utf-8')

    def show_export_folder(self):
        import os
        folder=self.output_folder.text()
        if Path(folder).is_dir():os.startfile(folder)

    def invalidate_preview(self,*args):
        self.preview_path=None;self._preview_token=None
        self.approved.setChecked(False);self.approved.setEnabled(False)
        self.player.stop();self.player.setSource(QUrl())
        self.poster.clear();self.poster.setText('Подготовьте предпросмотр');self.preview_stack.setCurrentWidget(self.poster)

    def select_clip(self,row):
        self.invalidate_preview()
        if row<0 or row>=len(self.ordered):
            self.selected_candidate=None;return
        self.selected_candidate=dict(self.ordered[row],part=row+1)
        item=self.selected_candidate
        matching=self.transcript if self.transcript and self.transcript['source']==self.analysis_source else None
        cues=clip_cues(matching,item['start'],item['end'])
        title=(cues[0]['text'][:90] if cues else f'Фрагмент {row+1:02}')
        self.clip_title.setText(title)
        self.origin_label.setText(f"{Path(self.analysis_source).name} • фрагмент {row+1:02} из {len(self.ordered)}\n{timecode(item['start'])} — {timecode(item['end'])} исходника")
        self.excerpt.setPlainText('\n'.join(c['text'] for c in cues) or 'Нажмите «Распознать реплики», чтобы увидеть текст. Название пока задайте вручную.')

    def clip_parameters(self,target):
        if not self.selected_candidate:raise ValueError('Сначала выберите фрагмент.')
        title=self.clip_title.text().strip()
        if not title:raise ValueError('Введите короткое название.')
        item=self.selected_candidate
        image=opening_title(self.analysis_folder.parent/'titles',title,item['part'])
        transcript=self.transcript if self.transcript and self.transcript['source']==self.analysis_source else None
        return dict(source=self.analysis_source,target=str(target),start=item['start'],duration=item['end']-item['start'],mode=self.mode.currentData(),
                    title_image=str(image),delivery=dict(title=title,part=item['part'],source=Path(self.analysis_source).name,
                    source_in=item['start'],source_out=item['end'],cues=clip_cues(transcript,item['start'],item['end'])))

    def prepare_preview(self):
        if self.analyzing or self.current is not None:return
        try:
            folder=self.analysis_folder.parent/'previews';folder.mkdir(parents=True,exist_ok=True)
            params=self.clip_parameters(folder/(uuid.uuid4().hex+'.mp4'))
            params.pop('delivery',None)
            token=(params['source'],params['start'],params['duration'],self.clip_title.text(),params['mode'],source_key(params['source']))
        except (ValueError,OSError) as error:
            QMessageBox.information(self,'Предпросмотр',str(error));return
        self.invalidate_preview();self.analyzing=True;self.analysis_cancel=threading.Event()
        self._active_preview_token=token
        self.stop.setEnabled(True);self.progress.setRange(0,100);self.status.setText('Готовлю полный предпросмотр выбранного фрагмента…')
        outer=self.preview_events
        class PreviewSink:
            def put(inner,event):
                kind,value=event
                if kind=='finished' and value[0]=='completed':
                    poster=Path(value[1]).with_suffix('.png')
                    try:
                        subprocess.run([executable('ffmpeg'),'-v','error','-nostdin','-n','-i',value[1],
                            '-frames:v','1','-vf','scale=270:480',str(poster)],capture_output=True,check=True,timeout=15,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        outer.put(('poster',str(poster)))
                    except (OSError,subprocess.SubprocessError):pass
                outer.put(event)
        threading.Thread(target=export_worker,args=(params,self.analysis_cancel,PreviewSink()),daemon=True).start()

    def toggle_play(self):
        if not self.preview_path:return
        self.preview_stack.setCurrentWidget(self.video)
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.player.pause()
        else:self.player.play()

    def receive_video(self):
        if not self.preview_path or not self.approved.isChecked():
            QMessageBox.information(self,'Подтверждение','Подготовьте предпросмотр и подтвердите выбранный вариант.');return
        folder=Path(self.output_folder.text())
        if not self.output_folder.text() or not folder.is_dir():
            QMessageBox.information(self,'Папка','Выберите папку для готовых видео.');return
        try:
            if source_key(self.analysis_source)!=self._preview_token[-1]:
                self.invalidate_preview()
                raise ValueError('Исходник изменился. Подготовьте новый предпросмотр.')
            name=f"{self.selected_candidate['part']:02} - {safe_name(self.clip_title.text())} - {uuid.uuid4().hex[:6]}.mp4"
            params=self.clip_parameters(folder/name)
            self.store.add(params);self.refresh();self.status.setText('Видео добавлено на сохранение. Рядом будут подпись с таймкодами и субтитры, если речь распознана.')
        except (ValueError,OSError) as error:QMessageBox.warning(self,'Сохранение',str(error))

    def poll(self):
        while True:
            try:kind,value=self.preview_events.get_nowait()
            except queue.Empty:break
            if kind=='progress':self.progress.setValue(int(value))
            if kind=='poster':
                self.poster.setPixmap(QPixmap(value));self.preview_stack.setCurrentWidget(self.poster)
            if kind=='finished':
                self.analyzing=False;self.stop.setEnabled(False)
                status,message=value
                item=self.selected_candidate
                try:
                    current=(self.analysis_source,item['start'],item['end']-item['start'],self.clip_title.text(),self.mode.currentData(),source_key(self.analysis_source)) if item else None
                except OSError:current=None
                if status=='completed' and current==self._active_preview_token:
                    self.preview_path=message;self._preview_token=current
                    self.player.setSource(QUrl.fromLocalFile(message));self.approved.setEnabled(True)
                    self.status.setText('Предпросмотр готов. Нажмите ▶, проверьте заголовок и кадр, затем подтвердите.')
                else:
                    self.invalidate_preview()
                    self.status.setText(message if status!='completed' else 'Настройки изменились: подготовьте новый предпросмотр.')
        super().poll()
        key=(getattr(self,'analysis_source',None),repr(self.candidates),id(self.transcript))
        if key!=self._catalog_key and self.candidates and self.source.text()==getattr(self,'analysis_source',None):
            self._catalog_key=key;self.ordered=sorted(self.candidates,key=lambda x:x['start']);self.catalog.clear()
            matching=self.transcript if self.transcript and self.transcript['source']==self.analysis_source else None
            for i,item in enumerate(self.ordered):
                cues=clip_cues(matching,item['start'],item['end'])
                label=cues[0]['text'][:65] if cues else f'Фрагмент {i+1:02}'
                self.catalog.addItem(f"{i+1:02}  {label}\n{timecode(item['start'])} — {timecode(item['end'])} • {item['end']-item['start']:.0f} с\n{item['reason']}")

    def closeEvent(self,event):
        self.player.stop()
        super().closeEvent(event)
