"""Bounded logs, conservative disk guard and versioned probe checkpoints."""
from pathlib import Path
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import shutil
import sqlite3

RESERVE_BYTES = 512 * 1024 * 1024

def check_disk(directory, duration=0):
    # CRF has no fixed bitrate: estimate is advisory, live reserve is mandatory.
    if not math.isfinite(duration) or duration < 0:
        raise ValueError('Некорректная длительность.')
    required = RESERVE_BYTES + math.ceil(duration * 24_000_000 / 8)
    free = shutil.disk_usage(directory).free
    if free < required:
        raise ValueError(f'Недостаточно места: свободно {free // 1048576} МиБ, нужно примерно {required // 1048576} МиБ.')
    return free

def configure_logging(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('shorts_studio')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    handler = RotatingFileHandler(folder / 'application.log', maxBytes=2_000_000,
                                  backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)

def source_key(source):
    source = Path(source).resolve()
    stat = source.stat()
    # Quick identity, not a content hash. Source modifications during processing
    # are unsupported; metadata is checked again before publication.
    return hashlib.sha256(json.dumps([str(source), stat.st_size, stat.st_mtime_ns,
                                     'probe-v1']).encode()).hexdigest()

class ProbeCache:
    """Worker-owned connection; never resets job state in another thread."""
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute('CREATE TABLE IF NOT EXISTS probe_cache (key TEXT PRIMARY KEY, duration REAL NOT NULL)')
        self.db.commit()

    def duration(self, key, compute):
        row = self.db.execute('SELECT duration FROM probe_cache WHERE key=?', (key,)).fetchone()
        if row and math.isfinite(row[0]) and row[0] > 0:
            return row[0]
        duration = compute()
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Не удалось определить длительность.')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO probe_cache VALUES (?, ?)', (key, duration))
        return duration

    def close(self):
        self.db.close()

def read_log_tail(path, limit=8192):
    """Read a bounded byte suffix even after a long FFmpeg run."""
    with open(path, 'rb') as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read(limit).decode('utf-8', errors='replace')
