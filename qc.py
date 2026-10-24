"""Post-render automatic quality control with bounded auto-fix.

Every check is a measurement on the rendered artifact: stream durations, peak
level, silence at the opening, black bars via cropdetect, word-boundary safety
recomputed from the transcript. The six scores are editing-quality indicators
built from those measurements and are never presented as a virality forecast.

Auto-fix is deliberately capped: one corrective re-render at most. A problem the
fix cannot address becomes a review item, not an infinite loop.
"""
from pathlib import Path
import json
import re
import subprocess

from render import executable

MAX_FIX_ATTEMPTS = 1
CLIP_HEADROOM_DB = -0.5
BAR_TOLERANCE_PX = 8


def _run(args, timeout=300):
    return subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def measure(video, width=720, height=1280):
    """Everything the checks need, read from the artifact itself."""
    probe = _run([executable('ffprobe'), '-v', 'error', '-show_streams', '-show_format',
                  '-of', 'json', str(video)])
    if probe.returncode:
        raise ValueError('Не удалось прочитать результат: ' + probe.stderr[-500:])
    data = json.loads(probe.stdout)
    streams = data.get('streams', [])
    video_stream = next((s for s in streams if s['codec_type'] == 'video'), None)
    audio_stream = next((s for s in streams if s['codec_type'] == 'audio'), None)
    result = dict(duration=float(data.get('format', {}).get('duration', 0) or 0),
                  video_duration=float(video_stream.get('duration', 0) or 0) if video_stream else 0.0,
                  audio_duration=float(audio_stream.get('duration', 0) or 0) if audio_stream else 0.0,
                  has_audio=audio_stream is not None,
                  width=int(video_stream['width']) if video_stream else 0,
                  height=int(video_stream['height']) if video_stream else 0)
    volume = _run([executable('ffmpeg'), '-hide_banner', '-i', str(video),
                   '-af', 'volumedetect', '-f', 'null', '-'])
    match = re.search(r'max_volume:\s*([-\d.]+)\s*dB', volume.stderr)
    result['max_db'] = float(match.group(1)) if match else None
    silence = _run([executable('ffmpeg'), '-hide_banner', '-i', str(video),
                    '-af', 'silencedetect=noise=-40dB:d=0.4', '-f', 'null', '-'])
    windows, start = [], None
    for line in silence.stderr.splitlines():
        found = re.search(r'silence_start: ([-0-9.]+)', line)
        if found:
            start = float(found.group(1))
        found = re.search(r'silence_end: ([-0-9.]+)', line)
        if found and start is not None:
            windows.append((start, float(found.group(1))))
            start = None
    result['silences'] = windows
    result['opening_silent'] = any(a <= 0.4 and b >= 1.6 for a, b in windows)
    crop = _run([executable('ffmpeg'), '-hide_banner', '-i', str(video),
                 '-vf', 'cropdetect=limit=0.10:round=2', '-frames:v', '40', '-f', 'null', '-'])
    sizes = re.findall(r'crop=(\d+):(\d+):', crop.stderr)
    if sizes:
        widths = [int(w) for w, _h in sizes]
        heights = [int(h) for _w, h in sizes]
        result['crop_w'], result['crop_h'] = max(set(widths), key=widths.count), max(set(heights), key=heights.count)
    else:
        result['crop_w'], result['crop_h'] = result['width'], result['height']
    result['black_bars'] = (result['width'] - result['crop_w'] > BAR_TOLERANCE_PX
                            or result['height'] - result['crop_h'] > BAR_TOLERANCE_PX)
    return result


def qc_report(video, plan=None, words=(), cues=(), censor=None, hook=None, reframe_used=False):
    """Checks plus six explained scores. Nothing here promises views."""
    m = measure(video)
    checks, notes = [], []

    def check(name, ok, value, explanation):
        checks.append(dict(name=name, ok=bool(ok), value=value, explanation=explanation))
        return ok

    sync_ok = check('av_sync', abs(m['video_duration'] - m['audio_duration']) <= 0.15,
                    round(abs(m['video_duration'] - m['audio_duration']), 3),
                    'Расхождение дорожек по длительности в допуске 0.15 с.'
                    if abs(m['video_duration'] - m['audio_duration']) <= 0.15
                    else 'Видео и аудио расходятся по длительности: возможен рассинхрон.')
    plan_ok = True
    if plan:
        plan_ok = check('duration_matches_plan', abs(m['duration'] - plan['duration']) <= 0.3,
                        round(m['duration'], 2),
                        'Длительность совпадает с монтажным планом.'
                        if abs(m['duration'] - plan['duration']) <= 0.3
                        else 'Длительность результата разошлась с планом.')
    bars_ok = check('no_black_bars', not m['black_bars'],
                    f"{m['crop_w']}x{m['crop_h']} из {m['width']}x{m['height']}",
                    'Полезный кадр занимает всю вертикальную площадь.'
                    if not m['black_bars'] else 'Обнаружены чёрные полосы: кадр не заполнен.')
    clip_ok = check('no_clipping', m['max_db'] is not None and m['max_db'] <= CLIP_HEADROOM_DB,
                    m['max_db'], 'Пик ниже 0 dBFS, перегрузки нет.'
                    if m['max_db'] is not None and m['max_db'] <= CLIP_HEADROOM_DB
                    else 'Пик у потолка: микс может перегружаться.')
    hook_ok = check('hook_audible', not m['opening_silent'], m['opening_silent'],
                    'Первые секунды несут звук: начало не пустое.'
                    if not m['opening_silent'] else 'Первые ~2 с тишина: хук не работает.')
    word_cuts = 0
    if plan and words:
        for segment in plan.get('segments', []):
            for edge in (segment['source_in'], segment['source_out']):
                word_cuts += sum(1 for w in words if w['start'] < edge < w['end'])
    words_ok = check('no_word_cuts', word_cuts == 0, word_cuts,
                     'Границы монтажа не разрезают распознанные слова.'
                     if word_cuts == 0 else f'Разрезано слов: {word_cuts}.')
    conflicts = (censor or {}).get('conflicts', [])
    censor_ok = check('censor_consistent', not conflicts, len(conflicts),
                      'Цензура согласована со склейками.' if not conflicts
                      else 'Интервалы цензуры конфликтуют со склейками: нужен человек.')
    long_cues = [c for c in cues if len(c.get('text', '')) > 42]
    subs_ok = check('subtitles_readable', not long_cues, len(long_cues),
                    'Строки субтитров умещаются в мобильную ширину.'
                    if not long_cues else f'Слишком длинные строки субтитров: {len(long_cues)}.')

    hook_score = float((hook or {}).get('score', 0) or 0)
    story = 100.0 - (0 if plan_ok else 40) - (20 if conflicts else 0)
    editing = max(0.0, 100.0 - 25 * word_cuts - (30 if not bars_ok else 0))
    audio = max(0.0, 100.0 - (50 if not clip_ok else 0) - (30 if not hook_ok else 0)
                - (20 if not sync_ok else 0))
    visual = max(0.0, 100.0 - (60 if not bars_ok else 0) - (0 if reframe_used else 15))
    overall = round(0.25 * hook_score + 0.2 * story + 0.2 * editing + 0.15 * audio
                    + 0.2 * visual, 1)
    scores = dict(hook=round(hook_score, 1), story=round(story, 1), editing=round(editing, 1),
                  audio=round(audio, 1), visual=round(visual, 1), overall=overall)
    for bad in [c for c in checks if not c['ok']]:
        notes.append(bad['explanation'])
    return dict(schema=1, video=str(video), checks=checks, scores=scores,
                needs_review=notes + [c['explanation'] for c in []],
                measurement=dict(max_db=m['max_db'], silences=m['silences'],
                                 crop=[m['crop_w'], m['crop_h']], size=[m['width'], m['height']]),
                limitations=['Оценки построены на измерениях результата и не предсказывают охваты.',
                             'Смысловую связность и качество склеек на слух подтверждает человек.'])


def auto_fixes(report):
    """At most one corrective action; anything else becomes a review item."""
    fixes = []
    by_name = {c['name']: c for c in report['checks']}
    if not by_name.get('no_clipping', {}).get('ok', True):
        fixes.append(dict(action='lower_music_db', delta=-3.0,
                          reason='Пик у потолка: понижаем музыку на 3 dB и пересобираем.'))
    return fixes[:MAX_FIX_ATTEMPTS]


def run_qc_with_fixes(render, qc, max_attempts=MAX_FIX_ATTEMPTS):
    """Render, check, fix at most once, check again. Never loop forever."""
    attempts, report = 0, None
    while True:
        artifact = render()
        report = qc(artifact)
        report['attempts'] = attempts
        fixes = auto_fixes(report)
        if not fixes or attempts >= max_attempts:
            break
        attempts += 1
        report = qc(render(fixes=fixes))
        report['attempts'] = attempts
    report['unresolved'] = [c['name'] for c in report['checks'] if not c['ok']]
    return report
