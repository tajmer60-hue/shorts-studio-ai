import unittest
from speech import subtitle_cues,srt,pause_proposals


class SpeechTests(unittest.TestCase):
    def test_cues_preserve_words_and_source_timing(self):
        words=[dict(start=1,end=1.4,word='Привет,'),dict(start=1.5,end=2,word='мир.'),
               dict(start=4,end=4.5,word='Дальше.')]
        cues=subtitle_cues(words)
        self.assertEqual([c['text'] for c in cues],['Привет, мир.','Дальше.'])
        self.assertEqual((cues[0]['start'],cues[0]['end']),(1,2))
        self.assertIn('00:00:01,000 --> 00:00:02,000',srt(cues))

    def test_pauses_are_unapproved_and_keep_word_padding(self):
        words=[dict(start=0,end=1,word='Раз'),dict(start=2,end=3,word='Два')]
        proposals=pause_proposals(words)
        self.assertEqual(len(proposals),1)
        self.assertFalse(proposals[0]['approved'])
        self.assertEqual((proposals[0]['start'],proposals[0]['end']),(1.15,1.85))

    def test_no_trimming_normal_word_gaps(self):
        self.assertEqual(pause_proposals([dict(end=1),dict(start=1.2)]),[])

    def test_empty_transcript(self):
        self.assertEqual(subtitle_cues([]),[])
        self.assertEqual(pause_proposals([]),[])
