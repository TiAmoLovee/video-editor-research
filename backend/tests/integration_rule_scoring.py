"""真实 FFmpeg 延迟音轨 -> 对齐音量 -> 候选评分；需 FFMPEG/FFPROBE。"""

import hashlib
import subprocess
import tempfile
from pathlib import Path
import unittest

from clipforge.config import media_tool
from clipforge.decision.audio import measure_audio
from clipforge.decision.candidates import generate_candidates
from clipforge.decision.scoring import score_candidates, validate_scored
from backend.tests.test_candidates import fixture


class RealRuleScoringTests(unittest.TestCase):
    def test_delayed_audio_alignment_and_complete_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'offset.mp4'
            ffmpeg, ffprobe = media_tool('ffmpeg'), media_tool('ffprobe')
            subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                            '-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=30:d=21',
                            '-itsoffset', '2', '-f', 'lavfi', '-i',
                            'sine=frequency=440:sample_rate=16000:duration=17',
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                            '-c:a', 'aac', '-t', '21', str(video)], check=True, timeout=60)
            analysis = fixture([(0, 10), (10, 20)])
            with video.open('rb') as stream:
                analysis['media']['normalized_sha256'] = hashlib.file_digest(stream,'sha256').hexdigest()
            audio = measure_audio(video, analysis, ffmpeg=ffmpeg, ffprobe=ffprobe)
            # 晚开始的音轨不能被移到视频零点；尾部必须正确补零。
            self.assertEqual(audio.window(0,1)['peak_rms_dbfs'], -120)
            self.assertEqual(audio.window(20,21)['peak_rms_dbfs'], -120)
            self.assertGreater(audio.window(3,4)['peak_rms_dbfs'], -30)
            windows = generate_candidates(analysis)
            scored = score_candidates(windows, analysis, audio=audio)
            validate_scored(scored,analysis)
            self.assertEqual(scored['candidate_count'],1)
            self.assertEqual(scored['scoring']['audio']['status'],'measured')
            self.assertEqual(scored,score_candidates(windows,analysis,audio=audio))


if __name__ == '__main__':
    unittest.main()
