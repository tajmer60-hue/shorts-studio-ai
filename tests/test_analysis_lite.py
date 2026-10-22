import unittest
from analysis_lite import candidates


class CandidateTests(unittest.TestCase):
    def test_covers_source_with_valid_nonoverlapping_ranges(self):
        result=sorted(candidates(170.37,[2,20,34,66,99,130,165],[[45,48]]),key=lambda x:x['start'])
        self.assertEqual(result[0]['start'],0)
        self.assertAlmostEqual(result[-1]['end'],170.37)
        for a,b in zip(result,result[1:]):self.assertEqual(a['end'],b['start'])
        for item in result:
            self.assertLess(item['start'],item['end'])
            self.assertLessEqual(item['end']-item['start'],60.001)
            self.assertTrue(item['needs_review'])

    def test_short_silent_video_is_still_reviewable(self):
        result=candidates(4,[],[[0,4]])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['score'],0)
