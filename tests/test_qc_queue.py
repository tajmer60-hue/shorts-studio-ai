"""QC queue tests: doubts aggregate, acknowledgements persist, blockers block."""
import json
from pathlib import Path
import tempfile
import unittest

from qc_queue import acknowledge, blockers, collect, pending


class QueueTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.project = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def write(self, name, data):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def test_empty_project_has_an_empty_queue(self):
        self.assertEqual(collect(self.project), [])
        self.assertEqual(pending(self.project), [])
        self.assertEqual(blockers(self.project), [])

    def test_uncertain_speech_and_reframe_become_review_items(self):
        self.write('censor_review.json', dict(schema=1, hits=[
            dict(start=12.4, end=13.0, word='кзаапп', term=None, confidence='low'),
            dict(start=40.0, end=40.5, word='сука', term='сука', confidence='high')]))
        self.write('reframe.json', dict(schema=1, confidence='low'))
        items = collect(self.project)
        kinds = {item['kind'] for item in items}
        self.assertEqual(kinds, {'censor_speech', 'reframe'})
        speech = next(i for i in items if i['kind'] == 'censor_speech')
        self.assertIn('12.40', speech['timecode'])
        self.assertTrue(all(i['severity'] == 'review' for i in items))

    def test_qc_conflicts_block_and_qc_notes_review(self):
        render = self.project / 'renders' / 'abc123'
        self.write(str(render.relative_to(self.project) / 'preview.qc.json'),
                   dict(schema=1, needs_review=['Пик у потолка: микс может перегружаться.'],
                        censor=dict(conflicts=[dict(start=1.18, reason='Вырез цензуры пересекает границу монтажа.')])))
        items = collect(self.project)
        self.assertEqual({i['kind'] for i in items}, {'qc', 'censor_conflict'})
        self.assertEqual({i['severity'] for i in items}, {'review', 'block'})
        self.assertEqual([i['clip'] for i in items], ['abc123', 'abc123'])

    def test_weak_hook_is_a_review_item(self):
        render = self.project / 'renders' / 'def456'
        self.write(str(render.relative_to(self.project) / 'preview.hook.json'),
                   dict(hook=dict(needs_review=True, hook=dict(start=3.2, end=5.0, text='х')),
                        score=12, breakdown={}, explanation=''))
        items = collect(self.project)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['kind'], 'hook')
        self.assertIn('3.20', items[0]['timecode'])

    def test_acknowledgement_removes_from_pending_but_keeps_the_record(self):
        self.write('censor_review.json', dict(schema=1, hits=[
            dict(start=1.0, end=1.5, word='х', term=None, confidence='low')]))
        items = collect(self.project)
        self.assertEqual(len(pending(self.project)), 1)
        acknowledge(self.project, items[0]['id'], note='просмотрел, оставляю')
        self.assertEqual(pending(self.project), [])
        self.assertEqual(len(collect(self.project)), 1)
        acks = json.loads((self.project / 'qc-acks.json').read_text(encoding='utf-8'))
        self.assertIn(items[0]['id'], acks)

    def test_blockers_are_only_blocking_items(self):
        render = self.project / 'renders' / 'abc123'
        self.write(str(render.relative_to(self.project) / 'preview.qc.json'),
                   dict(schema=1, needs_review=['заметка'],
                        censor=dict(conflicts=[dict(start=2.0, reason='конфликт')])))
        self.write('censor_review.json', dict(schema=1, hits=[
            dict(start=1.0, end=1.5, word='х', term=None, confidence='low')]))
        self.assertEqual(len(pending(self.project)), 3)
        self.assertEqual(len(blockers(self.project)), 1)
        self.assertEqual(blockers(self.project)[0]['kind'], 'censor_conflict')

    def test_broken_artifacts_do_not_break_the_queue(self):
        (self.project / 'censor_review.json').write_text('{not json', encoding='utf-8')
        render = self.project / 'renders' / 'zzz'
        render.mkdir(parents=True)
        (render / 'preview.qc.json').write_text('[]', encoding='utf-8')
        self.assertEqual(collect(self.project), [])


if __name__ == '__main__':
    unittest.main()
