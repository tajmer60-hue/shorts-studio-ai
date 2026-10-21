"""Local video export. No network calls, credentials or third-party Python packages."""
from pathlib import Path
import json
import math
import os
import shutil
import subprocess


def executable(name):
    local = Path(__file__).parent / 'tools' / (name + '.exe')
    found = str(local) if local.is_file() else shutil.which(name)
    if not found:
        raise ValueError(f'Не найден {name}. Добавьте FFmpeg и FFprobe в PATH или папку tools.')
    return found


def probe(source):
    result = subprocess.run(
        [executable('ffprobe'), '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_format', '-show_streams',
         '-of', 'json', str(source)], capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('Не удалось прочитать видео: ' + result.stderr[-1500:])
    data = json.loads(result.stdout)
    if not any(s.get('codec_type') == 'video' for s in data['streams']):
        raise ValueError('В файле нет видеодорожки.')
    return float(data['format']['duration'])


def validate_paths(source, target):
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise ValueError('Выберите существующий видеофайл.')
    if source.resolve() == target.resolve():
        raise ValueError('Исходный файл нельзя перезаписывать.')
    if target.exists():
        raise ValueError('Выходной файл уже существует. Выберите другое имя.')
    if not target.parent.is_dir():
        raise ValueError('Папка результата не существует. Выберите другую папку.')
    return source, target


def publish_no_replace(temporary, target):
    """Publish atomically without replacing a concurrently created destination."""
    if os.name == 'nt':
        os.rename(temporary, target)
    else:
        # Both names are in the same folder. Caller removes the temporary link.
        os.link(temporary, target)


def build_command(source, target, start, duration, total, mode, ffmpeg=None):
    source, target = validate_paths(source, target)
    if not all(math.isfinite(x) for x in (start, duration, total)):
        raise ValueError('Время должно быть конечным числом.')
    if start < 0 or duration <= 0 or start + duration > total + 0.01:
        raise ValueError('Укажите отрезок внутри длительности видео.')
    filters = {
        'fit': 'scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1',
        'crop': 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1',
    }
    if mode not in filters:
        raise ValueError('Неизвестный режим кадрирования.')
    return [ffmpeg or executable('ffmpeg'), '-hide_banner', '-nostdin', '-n',
            '-protocol_whitelist', 'file,pipe', '-ss', str(start), '-i', str(source), '-t', str(duration),
            '-map', '0:v:0', '-map', '0:a:0?', '-vf', filters[mode],
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', str(target)]
