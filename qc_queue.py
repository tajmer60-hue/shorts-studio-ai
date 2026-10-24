"""QC queue: every doubt the pipeline produced, in one reviewable list.

Smart Censor and the post-render QC never treat uncertainty as safety, so their
doubts accumulate as queue items with timecodes. The queue is pure bookkeeping
over files the pipeline already writes; acknowledging an item records a human
decision and never changes the rendered video by itself.
"""
from pathlib import Path
import hashlib
import json

from speech import atomic_json

SCHEMA = 1
ACKS = 'qc-acks.json'


def _item(kind, clip, timecode, text, severity='review'):
    identity = hashlib.sha256(json.dumps([kind, clip, timecode, text],
                                         ensure_ascii=False).encode()).hexdigest()[:16]
    return dict(id=identity, kind=kind, clip=clip, timecode=timecode, text=text,
                severity=severity)


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def collect(project_dir):
    """Gather doubts from censor review, hook review, reframe and QC reports."""
    project = Path(project_dir)
    items = []

    censor = _read(project / 'censor_review.json')
    if isinstance(censor, dict):
        for hit in censor.get('hits', []):
            if hit.get('confidence') == 'low' or hit.get('term') is None:
                items.append(_item('censor_speech', 'вся серия',
                                   f"{hit.get('start', 0):.2f}",
                                   f"Слово «{hit.get('word', '')}» распознано неуверенно: "
                                   f"решите, глушить или оставить.", 'review'))

    reframe = _read(project / 'reframe.json')
    if isinstance(reframe, dict) and reframe.get('confidence') == 'low':
        items.append(_item('reframe', 'вся серия', '0.00',
                           'Сопровождение кадра построено локальной оценкой значимости: '
                           'просмотрите кадры, где субъект мог быть потерян.', 'review'))

    renders = project / 'renders'
    qc_files = sorted(renders.rglob('*.qc.json')) if renders.is_dir() else []
    hook_files = sorted(renders.rglob('*.hook.json')) if renders.is_dir() else []
    for path in qc_files:
        report = _read(path)
        clip = path.parent.name
        if isinstance(report, dict):
            for note in report.get('needs_review', []):
                items.append(_item('qc', clip, '', note, 'review'))
            for conflict in (report.get('censor') or {}).get('conflicts', []):
                items.append(_item('censor_conflict', clip,
                                   f"{conflict.get('start', 0):.2f}",
                                   conflict.get('reason', 'Конфликт цензуры и склейки.'), 'block'))
    for path in hook_files:
        hook = _read(path)
        clip = path.parent.name
        inner = (hook or {}).get('hook') or {}
        if isinstance(hook, dict) and inner.get('needs_review'):
            start = (inner.get('hook') or {}).get('start', 0.0)
            items.append(_item('hook', clip, f'{start:.2f}',
                               'Начало ролика слабое: хук не усилен, решите вручную.', 'review'))
    return items


def _acks_path(project_dir):
    return Path(project_dir) / ACKS


def pending(project_dir):
    acks = _read(_acks_path(project_dir)) or {}
    return [item for item in collect(project_dir) if item['id'] not in acks]


def acknowledge(project_dir, item_id, note=''):
    path = _acks_path(project_dir)
    acks = _read(path) or {}
    acks[item_id] = dict(note=note, schema=SCHEMA)
    atomic_json(path, acks)
    return acks


def blockers(project_dir):
    """Items that must not ship: censor/scan conflicts, not mere review notes."""
    return [item for item in pending(project_dir) if item['severity'] == 'block']
