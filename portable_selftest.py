"""Verify the extracted portable runtime and a real export using only bundled files."""
from pathlib import Path
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from portable_launch import prepare


def verify():
    root = prepare()
    if Path(sys.base_prefix).resolve() != (root / 'runtime').resolve():
        raise RuntimeError('This test must run using the bundled runtime/python.exe.')
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontMetrics
    from app import configure_font, export_worker
    from auto_ui import AutoStudio as Studio
    from render import executable
    app = QApplication([])
    configure_font(app)
    assert QFontMetrics(app.font()).inFontUcs4(ord('Я'))
    folder = root / 'cache' / ('selftest-' + uuid.uuid4().hex)
    folder.mkdir()
    window = Studio(folder)
    window.paused = True
    window.show()
    app.processEvents()
    window.grab().save(str(folder / 'window.png'))
    window.close()
    source, target = folder / 'source.mp4', folder / 'result.mp4'
    subprocess.run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=320x180:rate=24:duration=2', '-c:v', 'libx264', str(source)],
        check=True, capture_output=True, timeout=30)
    events = queue.Queue()
    export_worker(dict(source=str(source), target=str(target), start=0.25,
        duration=1, mode='fit'), threading.Event(), events)
    result = None
    while not events.empty():
        kind, value = events.get_nowait()
        if kind == 'finished':
            result = value
    if result is None or result[0] != 'completed':
        raise RuntimeError(str(result))
    data = json.loads(subprocess.check_output([executable('ffprobe'), '-v', 'error',
        '-show_streams', '-show_format', '-of', 'json', str(target)], text=True))
    video = next(s for s in data['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height']) == (1080, 1920)
    assert abs(float(data['format']['duration']) - 1) < 0.15
    return dict(ok=True, runtime=sys.executable, prefix=sys.base_prefix,
        gui='OK', cyrillic='OK', export='1080x1920 MP4, 1 second', artifacts=str(folder))


if __name__ == '__main__':
    try:
        result = verify()
    except Exception as error:
        import traceback
        result = dict(ok=False, error=str(error), traceback=traceback.format_exc())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    Path(__file__).with_name('data').mkdir(exist_ok=True)
    (Path(__file__).parent / 'data' / 'selftest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    sys.exit(0 if result['ok'] else 1)
