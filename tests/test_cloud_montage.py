import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from cloud_planner import OpenRouterPlanner,unique_chronological
from montage_render import aligned_segments,ass_document
from edit_plan import remap_words


class CloudMontageTests(unittest.TestCase):
    def test_no_authorization_no_network(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder,patch('urllib.request.urlopen') as request:
            p=OpenRouterPlanner('secret',folder,1,False)
            with self.assertRaises(ValueError):p.propose({'duration':30})
            request.assert_not_called()

    def test_budget_reserves_before_network_and_keeps_uncertain_charge(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder,patch('urllib.request.urlopen',side_effect=TimeoutError) as request:
            p=OpenRouterPlanner('secret',folder,.12,True)
            with self.assertRaises(ValueError):p.propose({'duration':30})
            with self.assertRaises(ValueError):p.propose({'duration':30})
            self.assertEqual(request.call_count,1)
            self.assertNotIn('secret',(Path(folder)/'usage.json').read_text())

    def test_duplicate_footage_dropped_and_chronology_sorted(self):
        def clip(title,a,b):return dict(title=title,source_segments=[{'in':a,'out':b}])
        result=unique_chronological(dict(candidates=[clip('later',20,30),clip('duplicate',25,40),clip('first',0,10)]))
        self.assertEqual([c['title'] for c in result['candidates']],['first','later'])
        self.assertEqual(len(result['rejected']),1)

    def test_whole_words_and_output_times_across_cuts(self):
        words=[dict(start=1,end=2,word='one'),dict(start=5,end=6,word='two')]
        plan=aligned_segments(dict(source_segments=[{'in':1.2,'out':2.2},{'in':5.2,'out':6.2}]),words,10)
        mapped=remap_words(words,plan)
        self.assertEqual(mapped[0]['start'],0)
        self.assertAlmostEqual(mapped[1]['start'],1.2)

    def test_subtitle_text_cannot_inject_ass_override(self):
        ass=ass_document([dict(start=0,end=1,text='{\\pos(0,0)}hi')],'Title',2)
        self.assertNotIn('{\\pos',ass)

    def test_cancel_before_network(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder,patch('urllib.request.urlopen') as request:
            stop=threading.Event();stop.set()
            with self.assertRaises(ValueError):OpenRouterPlanner('secret',folder,1,True).propose({'duration':30},cancelled=stop)
            request.assert_not_called()

    def test_response_validated_cached_and_key_not_persisted(self):
        from unittest.mock import MagicMock
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps(dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(schema=1,candidates=[]))))],usage=dict(cost=.001))).encode()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder,patch('urllib.request.urlopen',return_value=response) as request:
            planner=OpenRouterPlanner('private-key',folder,1,True)
            planner.propose({'duration':30});planner.propose({'duration':30})
            self.assertEqual(request.call_count,1)
            body=json.loads(request.call_args.args[0].data)
            self.assertEqual(body['model'],'google/gemini-2.5-flash-lite')
            self.assertFalse(body['provider']['allow_fallbacks'])
            for path in Path(folder).glob('*.json'):self.assertNotIn('private-key',path.read_text())
