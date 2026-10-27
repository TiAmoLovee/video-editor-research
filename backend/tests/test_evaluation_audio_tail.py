"""Real local FFmpeg regression for review clips whose audio ends before video."""
import array
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from clipforge.decision.candidates import content_hash
from clipforge.decision.evaluation import probe, render_plan
from clipforge.decision.reviewed import file_hash


def executable(name):
    configured=os.environ.get(name.upper())
    return shutil.which(configured) if configured else shutil.which(name)


FFMPEG,FFPROBE=executable('ffmpeg'),executable('ffprobe')


@unittest.skipUnless(FFMPEG and FFPROBE,'Real audio-tail tests require local FFMPEG/FFPROBE executables; no downloads are attempted')
class EvaluationAudioTailTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='evaluation-audio-tail-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def make_source(self,audio_seconds):
        source=self.root/'synthetic-normalized.mp4'
        command=[FFMPEG,'-hide_banner','-loglevel','error','-nostdin','-n',
                 '-f','lavfi','-i','color=c=blue:s=160x90:r=30:d=3']
        if audio_seconds is not None:
            command+=['-f','lavfi','-i',f'sine=frequency=1000:sample_rate=48000:duration={audio_seconds}',
                      '-map','0:v:0','-map','1:a:0','-c:a','aac','-b:a','128k','-ar','48000']
        else:
            command+=['-map','0:v:0','-an']
        command+=['-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p',
                  '-threads','2','-bf','0','-fps_mode','cfr','-movflags','+faststart',str(source)]
        subprocess.run(command,check=True,capture_output=True,timeout=30)
        metadata=probe(source,FFPROBE)
        video=next(stream for stream in metadata['streams'] if stream['codec_type']=='video')
        self.assertEqual(int(video['nb_read_frames']),90)
        self.assertEqual(video['avg_frame_rate'],'30/1')
        return source,metadata

    def plan(self,source):
        sample={'id':'S01','candidate_id':'synthetic-tail-fixture','original_rank':1,'original_score':50,
                'scorer':'rule','original_start':1.0,'original_end':3.0,
                'start_frame':30,'end_frame_exclusive':90,'fps':30,'duration_seconds':2.0,
                'source_sentences':['synthetic-only'],'text':'Synthetic fixture; no human assessment.',
                'file':'S01.mp4'}
        plan={'version':'human-evaluation-batch-v1','source_name':source.name,
              'source_sha256':file_hash(source),'normalized_sha256':file_hash(source),
              'analysis_sha256':'a'*64,'candidates_sha256':'b'*64,
              'selection_version':'boundary-nms-v2','selection_config':{'iou_threshold':.5},
              'cohort':'synthetic_audio_tail_regression','presentation_order':'fixture_only',
              'scope':'synthetic local renderer regression; not human evaluation',
              'denominator':1,'required_passes':None,'metric':'not_applicable_synthetic_fixture',
              'rounding':'exact integer frames; no editorial extension','samples':[sample],'model_requests':0}
        return {'batch_id':content_hash(plan),**plan}

    def pcm(self,path):
        command=[FFMPEG,'-hide_banner','-loglevel','error','-nostdin','-i',str(path),
                 '-map','0:a:0','-ac','1','-ar','48000','-f','s16le','pipe:1']
        data=subprocess.run(command,check=True,capture_output=True,timeout=30).stdout
        samples=array.array('h'); samples.frombytes(data)
        if sys.byteorder!='little':samples.byteswap()
        return samples

    def rms(self,samples):
        return math.sqrt(sum(value*value for value in samples)/len(samples)) if samples else 0

    def render(self,source):
        original_hash=file_hash(source); plan=self.plan(source)
        result=render_plan(plan,source,self.root/'review',FFMPEG,FFPROBE)
        output=self.root/'review/S01.mp4'; metadata=probe(output,FFPROBE)
        video=next(stream for stream in metadata['streams'] if stream['codec_type']=='video')
        self.assertEqual(int(video['nb_read_frames']),60)
        self.assertEqual(video['avg_frame_rate'],'30/1')
        self.assertLessEqual(abs(float(video['start_time'])),.001)
        self.assertEqual(file_hash(source),original_hash)
        self.assertEqual(result['samples'][0]['start_frame'],30)
        self.assertEqual(result['samples'][0]['end_frame_exclusive'],90)
        self.assertEqual(result['renders']['S01']['frames'],60)
        self.assertEqual(result['renders']['S01']['sha256'],file_hash(output))
        self.assertTrue((self.root/'review/manifest.json').is_file())
        return output,metadata,result

    def assert_audio_clock(self,metadata):
        audio=next(stream for stream in metadata['streams'] if stream['codec_type']=='audio')
        self.assertEqual(int(audio['sample_rate']),48000)
        self.assertLessEqual(abs(float(audio['start_time'])),.001)
        self.assertLessEqual(abs(float(audio['duration'])-2.0),.04)
        # Check the MP4 sample clock independently of rounded duration strings.
        num,den=map(int,audio['time_base'].split('/'))
        seconds=int(audio['duration_ts'])*num/den
        self.assertLessEqual(abs(seconds-96000/48000),1/48000)
        return audio

    def test_short_source_audio_gets_only_silent_tail_without_changing_frames(self):
        source,source_meta=self.make_source(2.75)
        original_audio=next(stream for stream in source_meta['streams'] if stream['codec_type']=='audio')
        self.assertGreater(3.0-float(original_audio['duration']),.04)
        output,metadata,result=self.render(source)
        self.assert_audio_clock(metadata)
        samples=self.pcm(output)
        self.assertGreaterEqual(len(samples),96000)
        self.assertGreater(self.rms(samples[4800:48000]),1000)
        self.assertLess(self.rms(samples[91200:96000]),20)
        self.assertLessEqual(abs(result['renders']['S01']['audio_duration_seconds']-2.0),.04)
        saved=output.read_bytes()
        with self.assertRaises(FileExistsError):
            render_plan(self.plan(source),source,self.root/'review',FFMPEG,FFPROBE)
        self.assertEqual(output.read_bytes(),saved)

    def test_full_length_audio_is_preserved_through_the_last_frame(self):
        source,_=self.make_source(3.0)
        output,metadata,_=self.render(source)
        self.assert_audio_clock(metadata)
        samples=self.pcm(output)
        self.assertGreater(self.rms(samples[4800:48000]),1000)
        self.assertGreater(self.rms(samples[91200:96000]),1000)

    def test_audio_ending_before_candidate_is_silent_for_exact_candidate_length(self):
        source,_=self.make_source(.5)
        output,metadata,_=self.render(source)
        self.assert_audio_clock(metadata)
        samples=self.pcm(output)
        self.assertGreaterEqual(len(samples),96000)
        self.assertLess(self.rms(samples[:96000]),20)

    def test_no_audio_source_does_not_gain_an_audio_track(self):
        source,_=self.make_source(None)
        _,metadata,result=self.render(source)
        self.assertFalse(any(stream['codec_type']=='audio' for stream in metadata['streams']))
        self.assertIsNone(result['renders']['S01']['audio_duration_seconds'])


if __name__=='__main__':
    unittest.main(verbosity=2)

