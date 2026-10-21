"""FFmpeg measurements, not semantic or OCR analysis. Never uploads media."""
from pathlib import Path
import argparse
import json
import re
import subprocess
from render import executable


def run(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=1800, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return result


def analyze(source, report, artifacts):
    source, report, artifacts = Path(source), Path(report), Path(artifacts)
    if not source.is_file():
        raise FileNotFoundError(f'Эталон не найден: {source.resolve()}')
    artifacts.mkdir(parents=True, exist_ok=True)
    data = json.loads(run([executable('ffprobe'), '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_format',
        '-show_streams', '-of', 'json', str(source)]).stdout)
    duration = float(data['format']['duration'])
    video = next(s for s in data['streams'] if s['codec_type'] == 'video')
    base = [executable('ffmpeg'), '-hide_banner', '-nostdin', '-protocol_whitelist', 'file,pipe', '-i', str(source)]
    visual = run(base + ['-an', '-vf', "select='gt(scene,0.30)',showinfo", '-f', 'null', '-']).stderr
    cuts = [float(t) for t in re.findall(r'pts_time:([\d.]+)', visual)]
    blacks = run(base + ['-an', '-vf', 'blackdetect=d=0.08:pix_th=0.10', '-f', 'null', '-']).stderr
    black_intervals = re.findall(r'black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)', blacks)
    audio = any(s['codec_type'] == 'audio' for s in data['streams'])
    silence, volume = '', ''
    if audio:
        silence = run(base + ['-vn', '-af', 'silencedetect=noise=-35dB:d=0.20', '-f', 'null', '-']).stderr
        volume = run(base + ['-vn', '-af', 'volumedetect', '-f', 'null', '-']).stderr
    starts = re.findall(r'silence_start: ([\d.]+)', silence)
    ends = re.findall(r'silence_end: ([\d.]+) \| silence_duration: ([\d.]+)', silence)
    # Streaming tile of at most 120 samples, enough for a short reference contact sheet.
    step = max(0.5, duration / 120)
    sheet = artifacts / 'contact-sheet.jpg'
    run(base + ['-an', '-vf', f'fps=1/{step},scale=180:-2,tile=10x12',
                '-frames:v', '1', '-update', '1', '-y', str(sheet)])
    subtitles = [dict(codec=s.get('codec_name'), language=s.get('tags', {}).get('language'))
                 for s in data['streams'] if s['codec_type'] == 'subtitle']
    bounds = [0.0] + cuts + [duration]
    intervals = [b-a for a, b in zip(bounds, bounds[1:])]
    metrics = dict(duration=duration, width=video['width'], height=video['height'],
        frame_rate=video.get('avg_frame_rate'), cuts=cuts, estimated_shot_lengths=intervals,
        silence_starts=[float(x) for x in starts], silence_ends=ends,
        black_intervals=black_intervals, subtitle_streams=subtitles,
        sample_step_seconds=step)
    (artifacts / 'metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    for name, text in [('scenes.log', visual), ('black.log', blacks), ('silence.log', silence), ('volume.log', volume)]:
        (artifacts / name).write_text(text, encoding='utf-8')
    volume_values = re.findall(r'(mean_volume|max_volume): ([^\r\n]+)', volume)
    text = f'''# Анализ монтажного эталона

Файл: {source.name}. Анализ выполнен локально FFprobe/FFmpeg.

## Измеренные параметры

- Длительность: {duration:.3f} с; кадр: {video['width']} × {video['height']}.
- Частота кадров: {video.get('avg_frame_rate')}; кодек: {video.get('codec_name')}.
- Кандидаты смены плана (scene > 0.30): {len(cuts)}.
- Таймкоды кандидатов, с: {', '.join(f'{x:.3f}' for x in cuts) or 'не обнаружены'}.
- Средний интервал между кандидатами: {sum(intervals)/len(intervals):.3f} с.
- Начала тишины (ниже −35 dB не менее 0.20 с): {', '.join(starts) or 'не обнаружены'}.
- Концы тишины и длительности: {ends or 'не обнаружены'}.
- Уровни звука: {volume_values or 'нет аудио/данных'}.
- Интервалы чёрного кадра: {black_intervals or 'не обнаружены'}.
- Дорожки субтитров: {subtitles or 'не обнаружены'}.

## Кадры, переходы и субтитры

Контактный лист: cache/reference/contact-sheet.jpg. Шаг выборки: {step:.3f} с, максимум 120 кадров.
Сырые измерения сохранены в metrics.json и *.log рядом с контактным листом.
Порог scene показывает кандидаты резких изменений изображения; вспышки и движение могут давать ложные срабатывания. Чёрные интервалы могут быть переходами, но это требует визуального просмотра. Плавные переходы этим методом не классифицируются.
Наличие вшитых в изображение субтитров, их оформление и синхронизация требуют просмотра кадров/OCR; отсутствие subtitle-дорожки ничего не говорит об их наличии в картинке.

## Пределы интерпретации

Тишина не равна паузам речи: музыка может скрыть паузу, тихая речь — попасть под порог. Нужна транскрипция с временными метками. Средняя длина интервала не является готовым правилом монтажа. Эмоции, связность сюжета, качество диалогов и причины выбора сцены по этим измерениям не определяются.
Этот отчёт содержит измерения; художественные рекомендации следует добавить после просмотра контактного листа и прослушивания референса. Автоматическое применение профиля стиля пока не реализовано.
'''
    report.write_text(text, encoding='utf-8')
    return metrics


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', nargs='?', default='references/example.mp4')
    parser.add_argument('--report', default='REFERENCE_ANALYSIS.md')
    parser.add_argument('--artifacts', default='cache/reference')
    args = parser.parse_args()
    try:
        analyze(args.source, args.report, args.artifacts)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, str(error) + '\n')
