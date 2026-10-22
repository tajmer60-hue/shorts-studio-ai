"""Restartable draft export. SQLite commits delimit completed stages."""
from pathlib import Path
import hashlib
import json
import logging
import sqlite3
import subprocess
import threading
import uuid

from render import probe, build_command, validate_paths, publish_no_replace
from runtime_support import ProbeCache, source_key, check_disk, read_log_tail
from process_guard import ProcessGuard
from delivery import write_sidecars


def digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def artifact(path):
    stat = Path(path).stat()
    return dict(size=stat.st_size, inode=stat.st_ino, device=stat.st_dev, sha256=digest(path))


def matches(path, expected):
    return Path(path).is_file() and artifact(path) == expected


class Journal:
    def __init__(self, database, key):
        self.db = sqlite3.connect(database, timeout=10)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS export_stages (key TEXT PRIMARY KEY, state TEXT NOT NULL)')
        self.db.commit()
        self.key = key
        row = self.db.execute('SELECT state FROM export_stages WHERE key=?', (key,)).fetchone()
        self.state = json.loads(row[0]) if row else {}

    def save(self, stage, **values):
        self.state.update(values, stage=stage)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO export_stages VALUES (?, ?)',
                            (self.key, json.dumps(self.state)))

    def close(self):
        self.db.close()


def export_worker(params, cancelled, events, stage_hook=None):
    logger = logging.getLogger('shorts_studio')
    journal = cache = process = guard = None
    watcher = None
    watcher_done = threading.Event()
    temporary = log = None
    result = ('failed', 'Не удалось завершить задание.')

    def checkpoint(stage, **values):
        journal.save(stage, **values)
        logger.info('Export stage: %s', stage)
        if stage_hook:
            stage_hook(stage)

    def stop_process():
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            except ProcessLookupError:
                pass

    try:
        if cancelled.is_set():
            result = ('cancelled', 'Отменено.')
            return
        source, target = Path(params['source']), Path(params['target'])
        identity = source_key(source)
        title_image = params.get('title_image')
        title_identity = digest(title_image) if title_image else None
        key_data = [identity, str(target.resolve()), params['start'], params['duration'], params['mode'], title_identity, 'export-v2']
        key = hashlib.sha256(json.dumps(key_data).encode()).hexdigest()
        database = params.get('_checkpoint_db', ':memory:')
        journal = Journal(database, key)
        state = journal.state
        if target.exists():
            if state.get('stage') in ('qc', 'published') and matches(target, state['artifact']):
                checkpoint('published')
                write_sidecars(params)
                result = ('completed', str(target))
                return
            raise ValueError('Выходной файл уже существует. Выберите другое имя.')
        validate_paths(source, target)
        cache = ProbeCache(database)
        total = cache.duration(identity, lambda: probe(source))
        command = build_command(source, target, params['start'], params['duration'], total, params['mode'])
        if title_image:
            insertion=command.index('-t')
            command[insertion:insertion]=['-i',str(title_image)]
            vf=command.index('-vf');base_filter=command[vf+1]
            command[vf:vf+2]=['-filter_complex',f"[0:v]{base_filter}[base];[base][1:v]overlay=0:0:enable='lt(t,3)'[titled]"]
            command[command.index('0:v:0')]='[titled]'
        if not state:
            checkpoint('probe', source_identity=identity)
        temporary = Path(state['temporary']) if state.get('temporary') else target.with_name(target.stem + '.' + uuid.uuid4().hex + '.partial.mp4')
        # A journal may only refer to a temporary file beside this job's target.
        if temporary.parent.resolve() != target.parent.resolve() or not temporary.name.startswith(target.stem + '.') or not temporary.name.endswith('.partial.mp4'):
            raise ValueError('Некорректный путь checkpoint.')
        log = temporary.with_suffix('.log')
        reusable = state.get('stage') in ('render', 'qc') and matches(temporary, state['artifact'])
        if not reusable:
            # Never remove an unknown partial: each new attempt gets a new name.
            temporary = target.with_name(target.stem + '.' + uuid.uuid4().hex + '.partial.mp4')
            log = temporary.with_suffix('.log')
            check_disk(target.parent, params['duration'])
            checkpoint('rendering', temporary=str(temporary))
            command[-1] = str(temporary)
            errors = []
            with log.open('w', encoding='utf-8') as stderr:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                guard = ProcessGuard(process)
                if stage_hook:
                    stage_hook('encoding')

                def watch():
                    while not watcher_done.wait(0.25) and process.poll() is None:
                        try:
                            check_disk(target.parent)
                            if cancelled.is_set():
                                stop_process()
                                return
                        except Exception as error:
                            errors.append(str(error))
                            stop_process()
                            return

                watcher = threading.Thread(target=watch, daemon=True)
                watcher.start()
                for line in process.stdout:
                    if line.startswith('out_time_us='):
                        try:
                            events.put(('progress', max(0, min(99, float(line.split('=')[1]) / 1000000 / params['duration'] * 100))))
                        except ValueError:
                            pass
                code = process.wait()
                watcher_done.set()
                watcher.join(timeout=6)
            if cancelled.is_set():
                result = ('cancelled', 'Отменено.')
                return
            if errors:
                raise ValueError(errors[0])
            if code:
                raise ValueError('Ошибка FFmpeg: ' + read_log_tail(log)[-1800:])
            checkpoint('render', artifact=artifact(temporary))
        if cancelled.is_set():
            result = ('cancelled', 'Отменено.')
            return
        if state.get('stage') != 'qc':
            actual = probe(temporary)
            if abs(actual - params['duration']) > 0.25:
                raise ValueError('Длительность результата не совпадает с планом.')
            checkpoint('qc')
        if source_key(source) != identity:
            raise ValueError('Исходник изменился во время экспорта.')
        publish_no_replace(temporary, target)
        if stage_hook:
            stage_hook('after_publish')
        checkpoint('published')
        write_sidecars(params)
        result = ('completed', str(target))
    except Exception as error:
        logger.exception('Export failed')
        result = ('failed', str(error))
    finally:
        watcher_done.set()
        try:
            stop_process()
            if guard:
                guard.close()
            if watcher:
                watcher.join(timeout=6)
            if process and process.stdout:
                process.stdout.close()
            # Keep validated render/QC artifacts for retry; never remove source/output.
            if temporary and journal and journal.state.get('stage') == 'rendering':
                temporary.unlink(missing_ok=True)
            if log:
                log.unlink(missing_ok=True)
        except OSError:
            logger.exception('Temporary cleanup failed')
        finally:
            if cache:
                cache.close()
            if journal:
                journal.close()
            events.put(('finished', result))
