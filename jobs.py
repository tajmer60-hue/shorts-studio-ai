import json
import os
import sqlite3
from datetime import datetime, timezone


class JobStore:
    RETRYABLE = frozenset(('failed', 'cancelled', 'interrupted'))

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, created TEXT NOT NULL, parameters TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT "")')
        self.db.execute("UPDATE jobs SET status='interrupted', message='Прервано; можно повторить.' WHERE status='running'")
        self.db.commit()

    @staticmethod
    def target_key(target):
        return os.path.normcase(os.path.realpath(os.path.abspath(target)))

    def ensure_available(self, parameters, exclude_id=None):
        key = self.target_key(parameters['target'])
        rows = self.db.execute("SELECT id, parameters FROM jobs WHERE status IN ('pending', 'running')")
        for row in rows:
            if row['id'] != exclude_id and self.target_key(json.loads(row['parameters'])['target']) == key:
                raise ValueError('Имя результата уже занято заданием очереди.')

    def add(self, parameters):
        self.ensure_available(parameters)
        cursor = self.db.execute('INSERT INTO jobs(created, parameters, status) VALUES (?, ?, ?)',
            (datetime.now(timezone.utc).isoformat(), json.dumps(parameters), 'pending'))
        self.db.commit()
        return cursor.lastrowid

    def retry(self, job_id):
        row = self.db.execute('SELECT parameters, status FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None or row['status'] not in self.RETRYABLE:
            raise ValueError('Повторить можно только ошибочное, отменённое или прерванное задание.')
        self.ensure_available(json.loads(row['parameters']), exclude_id=job_id)
        self.update(job_id, 'pending')

    def cancel_pending(self, job_id):
        cursor = self.db.execute("UPDATE jobs SET status='cancelled', message='Отменено до запуска.' WHERE id=? AND status='pending'", (job_id,))
        self.db.commit()
        return cursor.rowcount == 1

    def update(self, job_id, status, message=''):
        self.db.execute('UPDATE jobs SET status=?, message=? WHERE id=?', (status, message, job_id))
        self.db.commit()

    def rows(self):
        return self.db.execute('SELECT * FROM jobs ORDER BY id DESC').fetchall()

    def next(self):
        return self.db.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY id LIMIT 1").fetchone()

    def close(self):
        self.db.close()
