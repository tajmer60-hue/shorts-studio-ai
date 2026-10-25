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
    QLineEdit,QFileDialog,QListWidget,QListWidgetItem,QMessageBox,QCheckBox,QComboBox,QDoubleSpinBox,
    QGroupBox,QProgressBar,QSlider,QStackedWidget,QInputDialog,QDialog,QScrollArea)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from auto_workflow import PROVIDERS,create_clips
from delivery import safe_name,timecode
from credential_store import load_key


class AutoStudio(QMainWindow):
    def __init__(self,data_dir):
        super().__init__();self.root=Path(__file__).resolve().parent
        self.settings=Path(data_dir)/'auto_preferences.json';self.events=queue.Queue();self.busy=False
        self.session_file=Path(data_dir)/'auto_session.json'
        self.cancelled=threading.Event();self.clips=[]
        self.setWindowTitle('Shorts Studio AI — автоматический монтаж');self.resize(1180,820)
        self.setStyleSheet('QMainWindow,QWidget{background:#101722;color:#edf2fa;font-size:14px;} QLineEdit,QListWidget{background:#1b2635;border:1px solid #35465c;border-radius:7px;padding:9px;} QPushButton{background:#283c55;padding:10px;border-radius:7px;} QPushButton:disabled{color:#8391a3;} QGroupBox{border:1px solid #35465c;border-radius:8px;margin-top:12px;padding-top:16px;}')
        root=QWidget();self.scroll=QScrollArea();self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(root);self.setCentralWidget(self.scroll);layout=QVBoxLayout(root)
        title=QLabel('Из серии — в готовые истории');title.setStyleSheet('font-size:26px;font-weight:700;');layout.addWidget(title)
        layout.addWidget(QLabel('Загрузите видео. AI выберет события, соберёт ролики и добавит субтитры. Вы проверите результат.'))
        self.source=QLineEdit();self.source.setPlaceholderText('Выберите фильм или серию')
        row=QHBoxLayout();row.addWidget(self.source);pick=QPushButton('Выбрать видео');pick.clicked.connect(self.pick_source);row.addWidget(pick);layout.addLayout(row)
        self.folder=QLineEdit();self.folder.setPlaceholderText('Папка для готовых роликов')
        row=QHBoxLayout();row.addWidget(self.folder);pick=QPushButton('Папка сохранения');pick.clicked.connect(self.pick_folder);row.addWidget(pick);layout.addLayout(row)
        settings=QGroupBox('Подключение AI — провайдер заменяем, запросов без разрешения нет');opts=QVBoxLayout(settings)
        prow=QHBoxLayout();prow.addWidget(QLabel('Провайдер:'))
        self.provider=QComboBox();self.provider.addItems(list(PROVIDERS));self.provider.setCurrentText('free')
        self.provider.currentTextChanged.connect(self.provider_changed);prow.addWidget(self.provider,1);opts.addLayout(prow)
        # One row per provider so a hidden key is never silently sent.
        self.key_rows={};self.keys={}
        for name,label,url in [('gemini_key','Gemini API — основной, видит кадры','https://aistudio.google.com/apikey'),
                               ('groq_key','Groq — только текст и речь','https://console.groq.com/keys'),
                               ('openrouter_key','OpenRouter — агрегатор, возвращает фактическую цену','https://openrouter.ai/settings/keys')]:
            holder=QWidget();row=QHBoxLayout(holder);row.setContentsMargins(0,0,0,0)
            field=QLineEdit();field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText(label+' — ключ только на время работы, на диск не сохраняется')
            row.addWidget(field,1)
            link=QLabel('<a href="%s">получить ключ</a>'%url);link.setOpenExternalLinks(True);row.addWidget(link)
            opts.addWidget(holder);self.key_rows[name]=holder;self.keys[name]=field
        self.connection_status=QLabel('OpenRouter: введите ключ или используйте защищённое подключение.')
        try:
            saved_key=load_key(Path(data_dir)/'openrouter.dpapi')
            if saved_key:
                self.keys['openrouter_key'].setText(saved_key)
                self.connection_status.setText('OpenRouter подключён: ключ загружен из защищённого хранилища Windows. Анализ сам не запускается.')
        except (OSError,ValueError):
            self.connection_status.setText('Windows не смогла открыть сохранённый ключ. Введите ключ вручную.')
        opts.addWidget(self.connection_status)
        self.allowed=QCheckBox('Разрешаю облачный анализ и отправку реплик + обзорных кадров выбранному провайдеру');opts.addWidget(self.allowed)
        self.training=QCheckBox('Бесплатные модели: разрешаю провайдеру обрабатывать отправленные данные')
        self.training.setToolTip('Бесплатные эндпоинты OpenRouter недоступны при запрете обработки данных. Платные модели отправляются с запретом (deny).')
        opts.addWidget(self.training)
        self.quota_label=QLabel('')
        self.quota_label.setWordWrap(True);opts.addWidget(self.quota_label)
        row=QHBoxLayout();row.addWidget(QLabel('Лимит проекта, USD:'));self.budget=QDoubleSpinBox();self.budget.setRange(0,20);self.budget.setValue(0);self.budget.setSingleStep(.5);row.addWidget(self.budget);opts.addLayout(row)
        opts.addWidget(QLabel('Без разрешения облака работает локальный монтаж по признакам речи. Это не AI-отбор.'));layout.addWidget(settings)
        mode_box=QGroupBox('Режим AI-монтажа');mbox=QVBoxLayout(mode_box)
        mrow=QHBoxLayout();mrow.addWidget(QLabel('Режим:'))
        self.mode=QComboBox();self.mode.addItem('Serial Cutter — связные Part 1..N','serial')
        self.mode.addItem('Viral Moments — самостоятельные пики','viral')
        mrow.addWidget(self.mode,1);mbox.addLayout(mrow)
        drow=QHBoxLayout();drow.addWidget(QLabel('Длительность ролика, с:'))
        self.dur_min=QDoubleSpinBox();self.dur_min.setRange(10,180);self.dur_min.setValue(45);drow.addWidget(self.dur_min)
        drow.addWidget(QLabel('—'));self.dur_max=QDoubleSpinBox();self.dur_max.setRange(10,180);self.dur_max.setValue(90);drow.addWidget(self.dur_max)
        drow.addWidget(QLabel('Темп:'));self.aggr=QComboBox()
        self.aggr.addItem('мягко','soft');self.aggr.addItem('нормально','normal');self.aggr.addItem('быстро','fast')
        self.aggr.setCurrentIndex(1);drow.addWidget(self.aggr);mbox.addLayout(drow)
        prow2=QHBoxLayout();prow2.addWidget(QLabel('Профиль канала:'))
        self.profile=QComboBox()
        from profiles import load_profiles
        self.profile_data=load_profiles(self.root/'profiles')
        for item in self.profile_data['profiles']:
            self.profile.addItem(item['name'],item['id'])
        self.profile.currentIndexChanged.connect(self.apply_profile);prow2.addWidget(self.profile,1)
        prow2.addWidget(QLabel('Интенсивность:'));self.intensity=QComboBox()
        for label,value in [('Cinematic','cinematic'),('Balanced','balanced'),('Aggressive','aggressive')]:
            self.intensity.addItem(label,value)
        self.intensity.setCurrentIndex(1);prow2.addWidget(self.intensity)
        mbox.addLayout(prow2)
        rrow=QHBoxLayout();self.reference=QLineEdit();self.reference.setPlaceholderText('Референс своего стиля (необязательно): короткий ролик')
        rrow.addWidget(self.reference,1);pick=QPushButton('Выбрать референс');pick.clicked.connect(self.pick_reference);rrow.addWidget(pick)
        mbox.addLayout(rrow)
        self.mode_note=QLabel('');self.mode_note.setWordWrap(True);mbox.addWidget(self.mode_note)
        self.mode.currentIndexChanged.connect(self.mode_changed);layout.addWidget(mode_box)
        self.mode_changed(0)
        self.apply_profile(0)
        reframe_box=QGroupBox('Smart Reframe — вертикальный кадр следует за сюжетом');rbox=QVBoxLayout(reframe_box)
        self.reframe=QCheckBox('Вести кадр за субъектом вместо мёртвого центра');self.reframe.setChecked(True)
        rbox.addWidget(self.reframe)
        rbox.addWidget(QLabel('Локально, без сети и без денег: оценка значимости кадра. Это НЕ детектор лиц, '
                              'поэтому результат помечается как низкая уверенность и требует просмотра. '
                              'Если субъект не помещается в честное вертикальное окно — размытый фон, не чёрные полосы.'))
        layout.addWidget(reframe_box)
        music_box=QGroupBox('Музыка и лицензии — без файла и лицензии трек не звучит');mubox=QVBoxLayout(music_box)
        self.tracks=QListWidget();self.tracks.setMaximumHeight(110);mubox.addWidget(self.tracks)
        mrow=QHBoxLayout()
        import_btn=QPushButton('Прикрепить файл к выбранному треку');import_btn.clicked.connect(self.import_music)
        mrow.addWidget(import_btn)
        refresh=QPushButton('Обновить список');refresh.clicked.connect(self.refresh_tracks);mrow.addWidget(refresh)
        mubox.addLayout(mrow)
        mubox.addWidget(QLabel('Треки из подборок помечены «заявлено для платформ»: некоммерчески и только '
                               'на указанных платформах, и только после прикрепления вашего файла. '
                               'Коммерция требует проверенной лицензии.'))
        layout.addWidget(music_box)
        self.refresh_tracks()
        censor_box=QGroupBox('Спорные слова — глушатся только после вашего выбора');cbox=QVBoxLayout(censor_box)
        self.profanity=QListWidget();self.profanity.setMaximumHeight(120)
        self.profanity.addItem('Список появится после локальной подготовки серии.')
        cbox.addWidget(self.profanity)
        self.apply_censor=QPushButton('Пересобрать ролики с выбранным глушением');self.apply_censor.setEnabled(False)
        self.apply_censor.clicked.connect(self.rebuild_with_censor);cbox.addWidget(self.apply_censor)
        qrow=QHBoxLayout();self.qc_button=QPushButton('Открыть очередь QC (сомнения цензуры и монтажа)')
        self.qc_button.clicked.connect(self.open_qc_queue);qrow.addWidget(self.qc_button)
        self.qc_count=QLabel('');qrow.addWidget(self.qc_count);cbox.addLayout(qrow)
        layout.addWidget(censor_box)
        # Initialise from the combo's actual value, not from an assumed default.
        self.provider_changed(self.provider.currentText())
        row=QHBoxLayout();self.start=QPushButton('Создать ролики локально');self.start.setStyleSheet('background:#3478f6;font-size:17px;font-weight:700;padding:14px;');self.start.clicked.connect(self.begin);row.addWidget(self.start)
        self.stop=QPushButton('Остановить');self.stop.setEnabled(False);self.stop.clicked.connect(self.cancelled.set);row.addWidget(self.stop);layout.addLayout(row)
        self.allowed.toggled.connect(lambda on:self.start.setText('Создать ролики автоматически' if on else 'Создать ролики локально'))
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
        for label in root.findChildren(QLabel):
            label.setWordWrap(True)
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

    def apply_profile(self,_index):
        from profiles import get_profile,profile_to_settings
        try:settings=profile_to_settings(get_profile(self.root/'profiles',self.profile.currentData()))
        except ValueError as error:
            self.status.setText(str(error));return
        self.mode.setCurrentIndex(0 if settings['mode']=='serial' else 1)
        self.dur_min.setValue(settings['duration_min']);self.dur_max.setValue(settings['duration_max'])
        self.aggr.setCurrentIndex({'soft':0,'normal':1,'fast':2}[settings['aggressiveness']])
        self.intensity.setCurrentIndex({'cinematic':0,'balanced':1,'aggressive':2}[settings['intensity']])
        self.profile_settings=settings

    def refresh_tracks(self):
        import music as music_lib
        self.music_catalog=music_lib.load_catalog(self.root/'music')
        self.tracks.clear()
        for track in self.music_catalog['tracks']:
            state='файл есть' if music_lib.license_ok(track) else ('заявлено, нет файла'
                    if track['license']=='CLAIMED_PLATFORM' else 'ориентир, не очищено')
            item=QListWidgetItem(f"{track['title']} — {track['artist']} [{track['license']}] ({state})")
            item.setData(Qt.ItemDataRole.UserRole,track['id'])
            self.tracks.addItem(item)

    def import_music(self):
        import music as music_lib
        row=self.tracks.currentRow()
        if not 0<=row<self.tracks.count():
            self.status.setText('Выберите трек в списке.');return
        track_id=self.tracks.item(row).data(Qt.ItemDataRole.UserRole)
        path,_=QFileDialog.getOpenFileName(self,'Лицензированный аудиофайл трека','',
                                           'Аудио (*.mp3 *.wav *.ogg *.m4a *.flac)')
        if not path:return
        license_id,ok=QInputDialog.getText(self,'Лицензия',
            'Название лицензии и источник (например, Mixkit Free License, https://...):')
        if not ok or not license_id.strip():
            self.status.setText('Без названия лицензии импорт отклоняется.');return
        attribution=QMessageBox.question(self,'Атрибуция','Требуется ли указывать автора в описании?')==QMessageBox.StandardButton.Yes
        entry=next(t for t in self.music_catalog['tracks'] if t['id']==track_id)
        try:
            music_lib.import_track(self.root/'music',path,entry['title'],entry['artist'],
                license_id.strip(),'',entry.get('moods',[]) or ['none'],attribution_required=attribution)
        except ValueError as error:
            QMessageBox.warning(self,'Музыка',str(error));return
        self.refresh_tracks()
        self.status.setText('Трек прикреплён и станет доступен для микса в своих настроениях.')

    def pick_reference(self):
        path,_=QFileDialog.getOpenFileName(self,'Референс монтажа','','Видео (*.mp4 *.mkv *.mov *.webm)')
        if path:self.reference.setText(path)

    def mode_changed(self,_index):
        value=self.mode.currentData()
        if value=='serial':
            self.mode_note.setText('Хронология сохраняется: вопрос остаётся с ответом, угроза с развязкой. '
                                   'Каждая часть по возможности заканчивается на напряжении.')
        else:
            self.mode_note.setText('Независимые самодостаточные моменты: конфликт, поворот, юмор, реакция. '
                                   'Порядок частей не обязан быть хронологией исходника.')

    def provider_changed(self,value):
        wanted={'free':('openrouter_key',),'auto':tuple(self.keys),'gemini':('gemini_key',),
                'groq':('groq_key',),'openrouter':('openrouter_key',)}.get(value,())
        for name,holder in self.key_rows.items():holder.setVisible(name in wanted)
        # Free endpoints only work when data processing is allowed, so the choice is explicit.
        self.training.setVisible(value in ('free','auto'))
        self.budget.setEnabled(value not in ('free',))
        if value=='free':
            self.budget.setValue(0)
            self.quota_label.setText('Бесплатно: лимит считается запросами в сутки (обычно 50), а не деньгами. '
                                     'Серия 55 минут — это примерно 14 запросов.')
        else:
            self.quota_label.setText('')
        self.key_hint='Нужен ключ: '+', '.join(wanted) if wanted else 'Нужен хотя бы один ключ.'

    def begin(self,censor_ops=None):
        if self.busy:return
        source=self.source.text().strip();authorized=self.allowed.isChecked();budget=self.budget.value()
        provider=self.provider.currentText()
        keys={name:field.text().strip() for name,field in self.keys.items()}
        if not Path(source).is_file():self.status.setText('Выберите существующий видеофайл.');return
        if authorized:
            required={'free':('openrouter_key',),'auto':tuple(self.keys),'gemini':('gemini_key',),
                      'groq':('groq_key',),'openrouter':('openrouter_key',)}.get(provider,())
            if not any(keys[name] for name in required):
                self.status.setText(self.key_hint+' Запрос не отправлен.');return
            if provider in ('free','auto') and not self.training.isChecked() and keys['openrouter_key']:
                self.status.setText('Бесплатные модели требуют разрешения на обработку данных. '
                                    'Отметьте галочку или выберите платного провайдера.');return
            # A zero-price model is limited by requests per day, not by money.
            if provider not in ('free',) and budget<=0:
                self.status.setText('Нужен ненулевой лимит проекта: без него расходы неконтролируемы.');return
        if self.dur_min.value()>self.dur_max.value():
            self.status.setText('Нижняя граница длительности больше верхней.');return
        settings=dict(provider=provider,budget=budget,training_allowed=self.training.isChecked(),
                    reframe=self.reframe.isChecked(),mode=self.mode.currentData(),
                    reference=self.reference.text().strip(),
                    profile=self.profile.currentData(),intensity=self.intensity.currentData(),
                    music_moods=getattr(self,'profile_settings',{}).get('music_moods',[]),
                    duration_min=self.dur_min.value(),duration_max=self.dur_max.value(),
                    aggressiveness=self.aggr.currentData(),**keys)
        self.active_source=source
        self.player.stop();self.player.setSource(QUrl());self.catalog.clear();self.clips=[];self.save.setEnabled(False)
        from speech import atomic_json
        atomic_json(self.session_file,dict(source=source,clips=[]))
        self.busy=True;self.cancelled=threading.Event();self.start.setEnabled(False);self.stop.setEnabled(True);self.progress.setRange(0,0)
        def work():
            try:
                result=create_clips(source,self.root,keys['openrouter_key'],budget,authorized,self.cancelled,
                    lambda t:self.events.put(('status',t)),lambda c:self.events.put(('clip',c)),
                    settings=settings,censor_ops=censor_ops)
                self.events.put(('done',result))
            except Exception as error:self.events.put(('error',str(error)))
        threading.Thread(target=work,daemon=True).start()

    def load_censor_review(self,project):
        """Show detected words as proposals. Nothing is muted until the user ticks it."""
        from censor import audio_ops
        self.profanity.clear();self.apply_censor.setEnabled(False);self.censor_hits=[]
        try:
            review=json.loads((Path(project)/'censor_review.json').read_text(encoding='utf-8'))
        except (OSError,ValueError,KeyError):
            self.profanity.addItem('Список спорных слов не найден.');return
        self.censor_hits=review.get('hits',[])
        if not self.censor_hits:
            self.profanity.addItem('Совпадений со словарём не найдено. Это не гарантия отсутствия мата.');return
        for hit in self.censor_hits:
            mark='сомнительно' if hit['confidence']=='low' else 'уверенно'
            item=QListWidgetItem(f"[{mark}] {timecode(hit['start'])} «{hit['word']}» — {hit['reason']}")
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole,hit)
            self.profanity.addItem(item)
        self.apply_censor.setEnabled(True)

    def open_qc_queue(self):
        from qc_queue import pending,acknowledge,blockers
        project=self.last_project if getattr(self,'last_project',None) else None
        if not project:
            QMessageBox.information(self,'QC','Сначала выполните обработку: очередь собирается из её результатов.')
            return
        items=pending(project)
        dialog=QDialog(self);dialog.setWindowTitle('Очередь QC');dialog.resize(760,420)
        layout=QVBoxLayout(dialog)
        listing=QListWidget()
        for item in items:
            mark='БЛОКИРУЕТ' if item['severity']=='block' else 'проверить'
            listing.addItem(f"[{mark}] {item['kind']} · {item['clip']} · {item['timecode']} — {item['text']}")
        layout.addWidget(listing)
        layout.addWidget(QLabel('Отметка не меняет видео: она фиксирует, что вы посмотрели момент. '
                                'Блокирующие пункты нужно решить до публикации.'))
        row=QHBoxLayout()
        ack=QPushButton('Отметить выбранные просмотренными')
        def do_ack():
            for index in range(listing.count()):
                if listing.isItemSelected(listing.item(index)) and index<len(items):
                    acknowledge(project,items[index]['id'])
            self.refresh_qc_count();dialog.accept()
        ack.clicked.connect(do_ack);row.addWidget(ack)
        close=QPushButton('Закрыть');close.clicked.connect(dialog.reject);row.addWidget(close)
        layout.addLayout(row)
        dialog.exec()
        self.refresh_qc_count()

    def refresh_qc_count(self):
        from qc_queue import pending,blockers
        project=getattr(self,'last_project',None)
        if not project:
            self.qc_count.setText('');return
        items=pending(project)
        self.qc_count.setText(f'Сомнений: {len(items)}, из них блокирующих: {len(blockers(project))}')

    def rebuild_with_censor(self):
        from censor import audio_ops
        chosen=[]
        for index in range(self.profanity.count()):
            item=self.profanity.item(index)
            if item.checkState()==Qt.CheckState.Checked:chosen.append(item.data(Qt.ItemDataRole.UserRole))
        if not chosen:
            self.status.setText('Отметьте слова для глушения. Без отметок ничего не меняется.');return
        for hit in chosen:hit['approved']=True
        try:
            ops=audio_ops(chosen)
        except ValueError as error:
            QMessageBox.warning(self,'Цензура',str(error));return
        self.status.setText(f'Пересобираю с глушением {len(ops)} интервалов…')
        self.begin(censor_ops=ops)

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
                if kind=='error':
                    self.status.setText('Обработка остановлена: '+str(value))
                    continue
                count=value.get('clips',0)
                if count:
                    mode='AI' if value.get('cloud') else 'локального алгоритма'
                    self.status.setText(f'Готово роликов: {count} ({mode}). Просмотрите и сохраните понравившиеся.')
                else:
                    self.status.setText('Ролики не созданы. '+str(value.get('reason') or 'Подходящих моментов не найдено; попробуйте более длинный фрагмент или другой режим.'))
                if isinstance(value,dict) and value.get('project'):
                    self.last_project=Path(value['project']);self.load_censor_review(value['project'])
                    self.refresh_qc_count()

    def select(self,index):
        if not 0<=index<len(self.clips):return
        clip=self.clips[index];self.player.setSource(QUrl.fromLocalFile(clip['path']))
        pixmap=QPixmap(str(Path(clip['path']).with_suffix('.jpg')))
        if pixmap.isNull():self.poster.setText('Нажмите «Смотреть», чтобы воспроизвести ролик')
        else:self.poster.setPixmap(pixmap.scaled(240,426,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        self.display.setCurrentWidget(self.poster)
        muted=len((clip.get('censor') or {}).get('ops',[]))
        note=('\nЗаглушено интервалов: %d (только одобренные вами).'%muted) if muted else '\nГлушение мата не применялось.'
        self.detail.setText(clip['candidate']['rationale']+note+'\nАвтоматическое кадрирование требует просмотра: локальный алгоритм не гарантирует удержание нужного персонажа.');self.save.setEnabled(True)

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
