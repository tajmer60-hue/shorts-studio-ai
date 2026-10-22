import unittest
from edit_plan import build_plan,remap_words


class EditPlanTests(unittest.TestCase):
    def test_unapproved_does_not_cut(self):
        plan=build_plan('source',0,10,[dict(start=2,end=5,approved=False)])
        self.assertEqual(plan['duration'],10)

    def test_merge_overlaps_and_remap_source_times(self):
        plan=build_plan('source',10,20,[dict(start=12,end=14,approved=True),dict(start=13,end=15,approved=True)])
        self.assertEqual(plan['duration'],7)
        words=remap_words([dict(start=16,end=17,word='hello')],plan)
        self.assertEqual((words[0]['start'],words[0]['end']),(3,4))

    def test_word_boundary_protected(self):
        plan=build_plan('source',0,10,[dict(start=2,end=5,approved=True)])
        with self.assertRaises(ValueError):remap_words([dict(start=1,end=3,word='hello')],plan)

    def test_reject_empty_and_nonfinite_plan(self):
        with self.assertRaises(ValueError):build_plan('source',0,float('inf'),[])
        with self.assertRaises(ValueError):build_plan('source',0,10,[dict(start=0,end=10,approved=True)])
