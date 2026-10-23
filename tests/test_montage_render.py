from pathlib import Path
import json
import subprocess
import tempfile
import threading
import unittest
from montage_render import render_montage
from render import executable


class MontageRenderTests(unittest.TestCase):
    def test_disjoint_silent_segments_burn_subtitles_and_cache(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root=Path(temp);source=root/'silent.mp4'
            subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30:duration=3','-c:v','libx264',str(source)],check=True,capture_output=True)
            c=dict(title='Монтаж',rationale='Test only',source_segments=[{'in':0,'out':.6},{'in':2,'out':2.6}])
            t=dict(words=[dict(start=.1,end=.4,word='первый'),dict(start=2.1,end=2.4,word='второй')])
            stop=threading.Event();p=render_montage(source,c,t,root/'renders',stop)
            data=json.loads(subprocess.check_output([executable('ffprobe'),'-v','error','-show_format','-show_streams','-of','json',str(p)]))
            self.assertAlmostEqual(float(data['format']['duration']),1.2,delta=.15)
            self.assertTrue(any(s['codec_type']=='audio' for s in data['streams']))
            self.assertEqual(next(s['width'] for s in data['streams'] if s['codec_type']=='video'),720)
            self.assertIn('второй',(p.parent/'captions.ass').read_text(encoding='utf-8'))
            stamp=p.stat().st_mtime_ns
            self.assertEqual(render_montage(source,c,t,root/'renders',stop),p)
            self.assertEqual(p.stat().st_mtime_ns,stamp)
            stop.set()
            with self.assertRaises(ValueError):render_montage(source,c,t,root/'renders',stop)
