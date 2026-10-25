from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from auto_workflow import create_clips


class WorkflowCompletionTests(unittest.TestCase):
    def test_free_planner_list_ledger_reaches_render_completion(self):
        class Planner:
            ledger=[]
            quota=None
            def reserve_estimate(self):return 0
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);project=root/'episode';project.mkdir()
            transcript={'words':[],'cues':[]}
            manifest={'duration':30,'packets':[]}
            with patch('auto_workflow.prepare_episode',return_value=(project,transcript,manifest)), \
                 patch('auto_workflow.build_planner',return_value=Planner()), \
                 patch('auto_workflow.build_reframe',return_value=None), \
                 patch('auto_workflow.detect_and_save',return_value={'regions':[]}):
                result=create_clips(root/'source.mp4',root,'test',0,True,threading.Event(),lambda s:None,lambda c:None,settings={'provider':'free'})
            self.assertEqual(result['clips'],0)
            self.assertTrue((project/'selected.json').is_file())
