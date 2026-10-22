"""Human-readable export names, opening title and source-time sidecars."""
from pathlib import Path
import hashlib
import json
import re


def timecode(seconds):
    seconds=max(0,int(seconds));h,seconds=divmod(seconds,3600);m,s=divmod(seconds,60)
    return f'{h:02}:{m:02}:{s:02}'


def safe_name(title):
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')[:70].rstrip(' .')
    if not value or value.upper().split('.')[0] in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        value='Видео'
    return value


def clip_cues(transcript,start,end):
    if not transcript:return []
    return [dict(start=max(0,c['start']-start),end=min(end,c['end'])-start,text=c['text'])
            for c in transcript['cues'] if c['end']>start and c['start']<end]


def opening_title(folder,title,part):
    from PySide6.QtCore import Qt,QRect
    from PySide6.QtGui import QImage,QPainter,QColor,QFont
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(json.dumps([title,part,'title-v1'],ensure_ascii=False).encode()).hexdigest()
    path=folder/(key+'.png')
    if path.exists():return path
    image=QImage(1080,1920,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(15,20,30,215));painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRect(70,190,940,260),28,28)
    painter.setPen(QColor('white'));painter.setFont(QFont('Segoe UI',20))
    painter.drawText(QRect(100,210,880,45),Qt.AlignmentFlag.AlignCenter,f'Фрагмент {part:02}')
    painter.setFont(QFont('Segoe UI',32,QFont.Weight.Bold))
    painter.drawText(QRect(100,265,880,160),Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,title[:90])
    painter.end()
    if not image.save(str(path),'PNG'):raise ValueError('Не удалось подготовить заголовок.')
    return path


def write_sidecars(params):
    if not params.get('delivery'):return
    from speech import srt
    target=Path(params['target'])
    data=params['delivery']
    documents={target.with_suffix('.json'):json.dumps(data,ensure_ascii=False,indent=2)}
    if data.get('cues'):documents[target.with_suffix('.srt')]=srt(data['cues'])
    for path,content in documents.items():
        if path.exists():
            if path.read_text(encoding='utf-8')!=content:
                raise ValueError('Сопроводительный файл уже существует: '+path.name)
        else:
            with path.open('x',encoding='utf-8',newline='') as stream:stream.write(content)
