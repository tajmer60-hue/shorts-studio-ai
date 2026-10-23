"""One primary action; automatic preparation/planning/render, then review and save."""
import json
import os
from pathlib import Path
import queue
import shutil
import threading
import uuid
from PySide6.QtCore import QTimer,QUrl,Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QLineEdit,QFileDialog,QListWidget,QMessageBox,QCheckBox,QDoubleSpinBox,QGroupBox,QProgressBar,QSlider,QStackedWidget)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from auto_workflow import create_clips
from delivery import safe_name,timecode


class AutoStudio(QMainWindow):
    def __init__(self,data_dir):
        super().__init__();self.root=Path(__file__).resolve().parent
        self.settings=Path(data_dir)/'auto_preferences.json';self.events=queue.Queue();self.busy=False
        self.session_file=Path(data_dir)/'auto_session.json'
        self.cancelled=threading.Event();self.clips=[]
        self.setWindowTitle('Shorts Studio AI — автоматический монтаж');self.resize(1180,820)
        self.setStyleSheet('QMainWindow,QWidget{background:#101722;color:#edf2fa;font-size:14px;} QLineEdit,QListWidget{background:#1b2635;border:1px solid #35465c;border-radius:7px;padding:9px;} QPushButton{background:#283c55;padding:10px;border-radius:7px;} QPushButton:disabled{color:#8391a3;} QGroupBox{border:1px solid #35465c;border-radius:8px;margin-top:12px;padding-top:16px;}')
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root)
        title=QLabel('Из серии — в готовые истории');title.setStyleSheet('font-size:26px;font-weight:700;');layout.addWidget(title)
        layout.addWidget(QLabel('Загрузите видео. AI выберет события, соберёт ролики и добавит субтитры. Вы проверите результат.'))
        self.source=QLineEdit();self.source.setPlaceholderText('Выберите фильм или серию')
        row=QHBoxLayout();row.addWidget(self.source);pick=QPushButton('Выбрать видео');pick.clicked.connect(self.pick_source);row.addWidget(pick);layout.addLayout(row)
        self.folder=QLineEdit();self.folder.setPlaceholderText('Папка для готовых роликов')
        row=QHBoxLayout();row.addWidget(self.folder);pick=QPushButton('Папка сохранения');pick.clicked.connect(self.pick_folder);row.addWidget(pick);layout.addLayout(row)
        settings=QGroupBox('Подключение AI — OpenRouter / Gemini 2.5 Flash Lite');opts=QVBoxLayout(settings)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setPlaceholderText('API-ключ — только на время работы, на диск не сохраняется');opts.addWidget(self.key)
        self.allowed=QCheckBox('Разрешаю платный анализ и отправку реплик + обзорных кадров в OpenRouter');opts.addWidget(self.allowed)
        row=QHBoxLayout();row.addWidget(QLabel('Лимит проекта, USD:'));self.budget=QDoubleSpinBox();self.budget.setRange(0,20);self.budget.setValue(0);self.budget.setSingleStep(.5);row.addWidget(self.budget)
        link=QLabel('<a href="https://openrouter.ai/settings/keys">Создать ключ OpenRouter с лимитом</a>');link.setOpenExternalLinks(True);row.addWidget(link);opts.addLayout(row)
        opts.addWidget(QLabel('Пока разрешение выключено: только локальная подготовка. API не вызывается.'));layout.addWidget(settings)
        row=QHBoxLayout();self.start=QPushButton('Подготовить серию без расходов');self.start.setStyleSheet('background:#3478f6;font-size:17px;font-weight:700;padding:14px;');self.start.clicked.connect(self.begin);row.addWidget(self.start)
        self.stop=QPushButton('Остановить');self.stop.setEnabled(False);self.stop.clicked.connect(self.cancelled.set);row.addWidget(self.stop);layout.addLayout(row)
        self.allowed.toggled.connect(lambda on:self.start.setText('Создать ролики автоматически' if on else 'Подготовить серию без расходов'))
        row=QHBoxLayout();self.catalog=QListWidget();self.catalog.setMinimumWidth(360);self.catalog.currentRowChanged.connect(self.select);row.addWidget(self.catalog,1)
        right=QVBoxLayout();self.video=QVideoWidget();self.video.setMinimumSize(300,280)
        self.display=QStackedWidget();self.poster=QLabel('Здесь будет предпросмотр ролика');self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display.addWidget(self.poster);self.display.addWidget(self.video);right.addWidget(self.display,1)
        self.detail=QLabel('Готовые ролики появятся здесь. Равномерной нарезки на куски нет.');self.detail.setWordWrap(True);right.addWidget(self.detail)
        controls=QHBoxLayout();play=QPushButton('Смотреть / пауза');play.clicked.connect(self.play);controls.addWidget(play)
        self.save=QPushButton('Сохранить выбранный ролик');self.save.setEnabled(False);self.save.clicked.connect(self.save_clip);controls.addWidget(self.save);right.addLayout(controls)
        self.seek=QSlider(Qt.Orientation.Horizontal);right.addWidget(self.seek);row.addLayout(right,2);layout.addLayout(row,1)
        self.progress=QProgressBar();layout.addWidget(self.progress)
        self.status=QLabel('Для начала выберите серию. Облачные запросы сейчас выключены.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.player.setAudioOutput(self.audio);self.player.setVideoOutput(self.video)
        self.player.durationChanged.connect(lambda n:self.seek.setRange(0,n));self.player.positionChanged.connect(self.seek.setValue);self.seek.sliderMoved.connect(self.player.setPosition)
        self.player.errorOccurred.connect(lambda *_:self.status.setText('Не удалось воспроизвести: '+self.player.errorString()))
        self.video.videoSink().videoFrameChanged.connect(lambda frame:setattr(self,'received_video_frame',frame.isValid()))
        try:
            saved=json.loads(self.settings.read_text(encoding='utf-8'));self.folder.setText(saved['folder'])
        except (OSError,ValueError,KeyError):pass
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(100)
        try:
            saved=json.loads(self.session_file.read_text(encoding='utf-8'))
            self.source.setText(saved['source'])
            for clip in saved['clips']:
                if Path(clip['path']).is_file():self.events.put(('clip',clip))
        except (OSError,ValueError,KeyError):pass

    def pick_source(self):
        path,_=QFileDialog.getOpenFileName(self,'Выберите видео','','Видео (*.mp4 *.mkv *.mov *.avi *.webm)')
        if path:self.source.setText(path)

    def pick_folder(self):
        path=QFileDialog.getExistingDirectory(self,'Папка готовых роликов',self.folder.text())
        if path:
            self.folder.setText(path);self.settings.write_text(json.dumps(dict(folder=path),ensure_ascii=False),encoding='utf-8')

    def begin(self):
        if self.busy:return
        source=self.source.text().strip();authorized=self.allowed.isChecked();key=self.key.text();budget=self.budget.value()
        if not Path(source).is_file():self.status.setText('Выберите существующий видеофайл.');return
        if authorized and (not key.strip() or budget<=0):self.status.setText('Для API нужны ключ и ненулевой лимит.');return
        self.active_source=source
        self.player.stop();self.player.setSource(QUrl());self.catalog.clear();self.clips=[];self.save.setEnabled(False)
        from speech import atomic_json
        atomic_json(self.session_file,dict(source=source,clips=[]))
        self.busy=True;self.cancelled=threading.Event();self.start.setEnabled(False);self.stop.setEnabled(True);self.progress.setRange(0,0)
        def work():
            try:
                result=create_clips(source,self.root,key,budget,authorized,self.cancelled,
                    lambda t:self.events.put(('status',t)),lambda c:self.events.put(('clip',c)))
                self.events.put(('done',result))
            except Exception as error:self.events.put(('error',str(error)))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        while True:
            try:kind,value=self.events.get_nowait()
            except queue.Empty:return
            if kind=='status':self.status.setText(value)
            if kind=='saved':
                self.save.setEnabled(True);self.status.setText(value)
            if kind=='clip':
                self.clips.append(value);c=value['candidate'];a=c['source_segments'][0]['in'];b=c['source_segments'][-1]['out']
                self.catalog.addItem(f"{value['part']:02}  {c['title']}\nИсточник {timecode(a)} — {timecode(b)} · {len(c['source_segments'])} сцен")
                if self.catalog.count()==1:self.catalog.setCurrentRow(0)
                from speech import atomic_json
                atomic_json(self.session_file,dict(source=getattr(self,'active_source',self.source.text()),clips=self.clips))
            if kind in ('done','error'):
                self.busy=False;self.start.setEnabled(True);self.stop.setEnabled(False);self.progress.setRange(0,100)
                self.progress.setValue(100 if kind=='done' else 0)
                if kind=='error':self.status.setText(value)
                elif value['cloud']:self.status.setText(f"Готово роликов: {value['clips']}. Просмотрите монтаж и сохраните понравившиеся.")
                else:self.status.setText('Подготовлено: диалоги и кадры всей серии сохранены локально. AI-отбор не запускался: платные запросы выключены.')

    def select(self,index):
        if not 0<=index<len(self.clips):return
        clip=self.clips[index];self.player.setSource(QUrl.fromLocalFile(clip['path']))
        pixmap=QPixmap(str(Path(clip['path']).with_suffix('.jpg')))
        if pixmap.isNull():self.poster.setText('Нажмите «Смотреть», чтобы воспроизвести ролик')
        else:self.poster.setPixmap(pixmap.scaled(240,426,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        self.display.setCurrentWidget(self.poster)
        self.detail.setText(clip['candidate']['rationale']+'\nЦензура и сопровождение персонажа ещё не подключены.');self.save.setEnabled(True)

    def play(self):
        self.display.setCurrentWidget(self.video)
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.player.pause()
        else:self.player.play()

    def save_clip(self):
        index=self.catalog.currentRow();folder=Path(self.folder.text())
        if not 0<=index<len(self.clips):return
        if not self.folder.text() or not folder.is_dir():self.pick_folder();return
        clip=self.clips[index];target=folder/f"{clip['part']:02} - {safe_name(clip['candidate']['title'])} - {uuid.uuid4().hex[:8]}.mp4"
        # Copy bytes of the exact reviewed render, never re-render a different version.
        self.save.setEnabled(False);self.saving=True
        def copy():
            from render import publish_no_replace
            temp=target.with_suffix('.partial.mp4')
            try:
                with open(clip['path'],'rb') as src,temp.open('xb') as dst:shutil.copyfileobj(src,dst,1024*1024)
                source=Path(clip['path']).parent
                for name,suffix in [('captions.srt','.srt'),('montage.json','.json')]:
                    with (source/name).open('rb') as src,target.with_suffix(suffix).open('xb') as dst:shutil.copyfileobj(src,dst)
                publish_no_replace(temp,target)
                self.events.put(('saved','Сохранено: '+str(target)))
            except OSError:self.events.put(('saved','Не удалось сохранить полностью. Проверьте свободное место и права папки.'))
            finally:self.saving=False
        threading.Thread(target=copy,daemon=True).start()

    def closeEvent(self,event):
        if getattr(self,'saving',False):
            self.status.setText('Дождитесь окончания сохранения.');event.ignore();return
        if self.busy:
            self.cancelled.set();self.status.setText('Останавливаю обработку. Закройте окно после завершения текущего шага.');event.ignore();return
        self.player.stop();self.timer.stop();event.accept()
