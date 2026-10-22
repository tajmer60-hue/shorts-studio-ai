from pathlib import Path
import tempfile
import unittest
from delivery import safe_name,clip_cues,write_sidecars
from ai_contract import analysis_windows,validate_ai_plan


class DeliveryTests(unittest.TestCase):
    def test_windows_names(self):
        self.assertEqual(safe_name('CON'),'Видео')
        self.assertNotIn('/',safe_name('a/b: c?'))
        self.assertEqual(safe_name('..'),'Видео')

    def test_clip_subtitles_are_relative(self):
        cues=clip_cues(dict(cues=[dict(start=9,end=12,text='x'),dict(start=25,end=30,text='y')]),10,20)
        self.assertEqual(cues,[dict(start=0,end=2,text='x')])

    def test_sidecars_idempotent_and_preserve_foreign_data(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            target=Path(folder)/'video.mp4'
            params=dict(target=str(target),delivery=dict(title='Название',cues=[]))
            write_sidecars(params);write_sidecars(params)
            target.with_suffix('.json').write_text('foreign',encoding='utf-8')
            with self.assertRaises(ValueError):write_sidecars(params)
            self.assertEqual(target.with_suffix('.json').read_text(encoding='utf-8'),'foreign')

    def test_ai_windows_cover_entire_timeline(self):
        windows=analysis_windows(701,[],[])
        self.assertEqual([(w['source_in'],w['source_out']) for w in windows],[(0,300),(300,600),(600,701)])

    def test_ai_cannot_approve_or_invent_timecodes(self):
        candidate=dict(title='Title',rationale='Reason',source_segments=[{'in':0,'out':10}],approved=True)
        plan=validate_ai_plan(dict(schema=1,candidates=[candidate]),20)
        self.assertFalse(plan['candidates'][0]['approved'])
        candidate['source_segments'][0]['out']=21
        with self.assertRaises(ValueError):validate_ai_plan(dict(schema=1,candidates=[candidate]),20)
