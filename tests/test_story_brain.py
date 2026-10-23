import copy
from pathlib import Path
import tempfile
import threading
import unittest
from story_brain import event_map,story_plan,review_plan,plan_episode,MAP_PROMPT,EDIT_PROMPT,REVIEW_PROMPT


class StoryBrainTests(unittest.TestCase):
    def setUp(self):
        self.packet=dict(duration=100,source_in=0,source_out=60,
            dialogue=[dict(id='c000000',start=2,end=4,text='Вопрос'),dict(id='c000001',start=20,end=22,text='Ответ')],frame_times=[10.0])
        self.raw=dict(schema=1,events=[dict(summary='Событие для теста',**{'in':0,'out':30},cue_ids=['c000000'],frame_times=[10.0],needs_context=False)])
        self.events=event_map(self.raw,self.packet,0)['events']

    def test_event_cannot_cite_invented_evidence(self):
        for field,value in [('cue_ids',['invented']),('frame_times',[11.0])]:
            bad=copy.deepcopy(self.raw);bad['events'][0][field]=value
            with self.assertRaises(ValueError):event_map(bad,self.packet,0)

    def test_event_requires_evidence_and_window_bounds(self):
        bad=copy.deepcopy(self.raw);bad['events'][0].update(cue_ids=[],frame_times=[])
        with self.assertRaises(ValueError):event_map(bad,self.packet,0)
        bad=copy.deepcopy(self.raw);bad['events'][0]['out']=61
        with self.assertRaises(ValueError):event_map(bad,self.packet,0)

    def test_story_cannot_invent_event_or_footage(self):
        draft=dict(schema=1,candidates=[dict(title='История',rationale='Причина',event_ids=['w000-e00'],source_segments=[{'in':0,'out':25}])])
        self.assertEqual(story_plan(draft,self.events,100)['candidates'][0]['event_ids'],['w000-e00'])
        draft['candidates'][0]['source_segments'][0]['out']=31
        with self.assertRaises(ValueError):story_plan(draft,self.events,100)
        draft['candidates'][0]['event_ids']=['unknown']
        with self.assertRaises(ValueError):story_plan(draft,self.events,100)

    def test_review_cannot_skip_or_double_approve_clip(self):
        candidates=[dict(clip_id='clip000')]
        with self.assertRaises(ValueError):review_plan(dict(schema=1,reviews=[]),candidates)
        r=dict(clip_id='clip000',reason='Проверено',coherent=True,complete_speech=True,context_sufficient=True,no_repeated_event=True)
        with self.assertRaises(ValueError):review_plan(dict(schema=1,reviews=[r,r]),candidates)
        r['coherent']='true'
        with self.assertRaises(ValueError):review_plan(dict(schema=1,reviews=[r]),candidates)

    def test_three_pass_pipeline_drops_failed_story_and_persists_reason(self):
        # Deterministic protocol fixture. This does NOT evaluate any AI model.
        calls=[];raw=self.raw
        class FixturePlanner:
            def request(self,packet,prompt,validate,frames=(),cancelled=None):
                calls.append(prompt)
                if prompt==MAP_PROMPT:response=raw
                elif prompt==EDIT_PROMPT:
                    response=dict(schema=1,candidates=[dict(title='История',rationale='Причина',event_ids=['w000-e00'],source_segments=[{'in':0,'out':25}])])
                else:
                    assert 'surrounding_source_dialogue' in packet
                    response=dict(schema=1,reviews=[dict(clip_id='clip000',reason='Нет ответа на вопрос',coherent=False,complete_speech=True,context_sufficient=False,no_repeated_event=True)])
                return validate(response)
        transcript=dict(cues=[dict(start=2,end=4,text='Вопрос'),dict(start=20,end=22,text='Ответ')],words=[])
        manifest=dict(duration=100,packets=[dict(packet=self.packet,frames=[(10.0,'unused-test.jpg')])])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            result=plan_episode(FixturePlanner(),manifest,transcript,Path(folder),threading.Event(),lambda _:None)
            self.assertEqual(calls,[MAP_PROMPT,EDIT_PROMPT,REVIEW_PROMPT])
            self.assertEqual(result['candidates'],[])
            self.assertEqual(result['rejected'][0]['reason'],'Нет ответа на вопрос')
            self.assertTrue((Path(folder)/'story-review.json').is_file())
