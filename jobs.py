import json
import sqlite3
from datetime import datetime, timezone


class JobStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, created TEXT NOT NULL, parameters TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT "")')
        self.db.execute("UPDATE jobs SET status='interrupted', message='Прервано; можно повторить.' WHERE status='running'")
        self.db.commit()

    def add(self, parameters):
        for row in self.rows():
            if row['status'] in ('pending', 'running') and json.loads(row['parameters'])['target'] == parameters['target']:
                raise ValueError('Имя результата уже занято заданием очереди.')
        cursor = self.db.execute('INSERT INTO jobs(created, parameters, status) VALUES (?, ?, ?)',
            (datetime.now(timezone.utc).isoformat(), json.dumps(parameters), 'pending'))
        self.db.commit()
        return cursor.lastrowid

    def update(self, job_id, status, message=''):
        self.db.execute('UPDATE jobs SET status=?, message=? WHERE id=?', (status, message, job_id))
        self.db.commit()

    def rows(self):
        return self.db.execute('SELECT * FROM jobs ORDER BY id DESC').fetchall()

    def next(self):
        return self.db.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY id LIMIT 1").fetchone()

    def close(self):
        self.db.close()
