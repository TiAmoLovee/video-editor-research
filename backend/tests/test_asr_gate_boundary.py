"""Only proven terminal PCM ceil quantization may cross the CFR video endpoint."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace as Object
import unittest
from unittest.mock import Mock,patch
from clipforge.analysis.asr import _speech_gate_on_video_timeline,transcribe_video
from clipforge.analysis.coverage import audit_word_coverage

class AudioFixture:
    def __init__(self,samples):self.samples=samples
    def __len__(self):return self.samples

class SpeechGateBoundaryTests(unittest.TestCase):
    duration=2624/30
    samples=1399467
    def gate(self,end=None):
        return {'no_speech_skipped':False,'speech_intervals':[{'start':81.784,'end':self.samples/16000 if end is None else end}],
                'retained_seconds':5.6826875,'parameters':{'speech_pad_ms':200}}
    def convert(self,gate=None,**options):
        return _speech_gate_on_video_timeline(self.gate() if gate is None else gate,self.duration,
                                             pcm_samples=options.get('pcm_samples',self.samples),
                                             sample_rate=options.get('sample_rate',16000))
    def test_observed_one_third_sample_terminal_interval_is_bounded_and_auditable(self):
        gate,report=self.convert()
        self.assertEqual(gate['speech_intervals'][0]['end'],self.duration)
        self.assertEqual(len(report['adjustments']),1)
        change=report['adjustments'][0]
        self.assertEqual((change['before'],change['after']),(87.4666875,self.duration))
        self.assertAlmostEqual(change['overshoot_samples'],1/3,places=6)
        self.assertEqual(report['actual_pcm_samples'],report['expected_pcm_samples'])
        self.assertEqual(report['version'],'silero-pcm-to-cfr-boundary-v1')
        self.assertEqual(gate['retained_seconds'],self.duration-81.784)
    def test_raw_gate_and_ordinary_intervals_are_not_mutated(self):
        raw=self.gate();raw['speech_intervals'].insert(0,{'start':0,'end':1})
        before=copy.deepcopy(raw)
        gate,report=self.convert(raw)
        self.assertEqual(raw,before)
        self.assertEqual(report['raw_gate'],before)
        self.assertEqual(gate['speech_intervals'][0],before['speech_intervals'][0])
        gate['parameters']['speech_pad_ms']=0
        self.assertEqual(raw,before)
        self.assertEqual(report['raw_gate'],before)
    def test_one_extra_sample_beyond_pcm_horizon_is_rejected(self):
        with self.assertRaises(ValueError):self.convert(self.gate((self.samples+1)/16000))
    def test_genuine_hundred_millisecond_overflow_is_rejected(self):
        with self.assertRaises(ValueError):self.convert(self.gate(self.duration+.1))
    def test_tiny_nonterminal_overflow_is_not_silently_clipped(self):
        with self.assertRaises(ValueError):self.convert(self.gate(self.duration+1e-6))
    def test_actual_pcm_length_must_match_exact_expected_ceil(self):
        for count in (self.samples-1,self.samples+1,True,float(self.samples)):
            with self.subTest(count=count),self.assertRaises(ValueError):self.convert(pcm_samples=count)
    def test_rate_must_be_actual_sixteen_khz(self):
        for rate in (8000,44100,True,16000.0):
            with self.subTest(rate=rate),self.assertRaises(ValueError):self.convert(sample_rate=rate)
    def test_nan_and_infinite_interval_coordinates_are_rejected(self):
        for key in ('start','end'):
            for value in (float('nan'),float('inf'),float('-inf')):
                raw=self.gate();raw['speech_intervals'][0][key]=value
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.convert(raw)
    def test_reverse_negative_and_boolean_coordinates_are_rejected(self):
        for row in ({'start':2,'end':1},{'start':-.1,'end':1},{'start':0,'end':-.1},
                    {'start':True,'end':1},{'start':0,'end':True}):
            with self.subTest(row=row),self.assertRaises(ValueError):self.convert({'speech_intervals':[row]})
    def test_ordinary_valid_intervals_remain_identical(self):
        raw={'speech_intervals':[{'start':0,'end':1},{'start':2,'end':self.duration}],
             'retained_seconds':1+self.duration-2,'no_speech_skipped':False}
        actual,report=self.convert(raw)
        self.assertEqual(actual,raw)
        self.assertEqual(report['adjustments'],[])
    def test_integral_pcm_duration_does_not_create_an_adjustment(self):
        raw={'speech_intervals':[{'start':0,'end':1}]}
        actual,report=_speech_gate_on_video_timeline(raw,1,pcm_samples=16000,sample_rate=16000)
        self.assertEqual(actual,raw);self.assertEqual(report['adjustments'],[])
    def test_invalid_duration_or_gate_structure_fails(self):
        for duration in (0,-1,True,float('nan'),float('inf')):
            with self.subTest(duration=duration),self.assertRaises(ValueError):
                _speech_gate_on_video_timeline(self.gate(),duration,pcm_samples=self.samples,sample_rate=16000)
        for gate in (None,{}, {'speech_intervals':None},{'speech_intervals':[{}]}):
            with self.subTest(gate=gate),self.assertRaises(ValueError):
                _speech_gate_on_video_timeline(gate,self.duration,pcm_samples=self.samples,sample_rate=16000)
    def test_generic_word_and_webrtc_coverage_remain_strict(self):
        for key in ('words','speech'):
            value={'media':{'duration_seconds':self.duration},'words':[],'speech':[]}
            value[key]=[{'start':0,'end':self.duration+1e-6}]
            with self.subTest(key=key),self.assertRaises(ValueError):audit_word_coverage(value)

class TranscribeGateBoundaryIntegrationTests(unittest.TestCase):
    def run_transcribe(self,invalid=False):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.mp4';source.write_bytes(b'private fixture')
            duration=2624/30
            raw={'streams':[{'index':0,'codec_type':'video','codec_name':'h264','avg_frame_rate':'30/1'},
                            {'index':1,'codec_type':'audio','codec_name':'aac'}]}
            gate={'speech_intervals':[{'start':81.784,'end':duration+.1 if invalid else 1399467/16000}],
                  'no_speech_skipped':False,'retained_seconds':5.6826875}
            before=copy.deepcopy(gate);audio=AudioFixture(1399467)
            word=Object(start=82,end=duration,word='sample',probability=.9)
            segment=Object(start=82,end=duration,text='sample',words=[word])
            recognizer=Object(transcribe=Mock(return_value=(iter([segment]),Object(language='en',language_probability=.99))))
            with patch('clipforge.analysis.asr.probe_video',return_value=raw), \
                 patch('clipforge.analysis.asr.read_frame_count',return_value=2624), \
                 patch('clipforge.analysis.asr.extract_pcm'), \
                 patch('clipforge.analysis.asr.pcm_has_signal',return_value=True), \
                 patch('clipforge.analysis.asr.prepare_speech',return_value=(audio,gate)), \
                 patch('clipforge.analysis.asr._load_model',return_value=(recognizer,{})) as load, \
                 patch.dict('os.environ',{'CLIPFORGE_ASR_MODEL':'base','CLIPFORGE_ASR_LANGUAGE':''}):
                if invalid:
                    with self.assertRaises(ValueError):transcribe_video(source)
                    load.assert_not_called();recognizer.transcribe.assert_not_called()
                    return
                result=transcribe_video(source)
            self.assertEqual(gate,before)
            self.assertEqual(recognizer.transcribe.call_count,1)
            self.assertIs(recognizer.transcribe.call_args.args[0],audio)
            parameters=result['analyzer']['parameters']
            self.assertEqual(parameters['speech_gate']['speech_intervals'][0]['end'],duration)
            self.assertEqual(parameters['speech_gate_boundary']['raw_gate'],before)
            self.assertEqual(len(parameters['speech_gate_boundary']['adjustments']),1)
            self.assertEqual(result['words'][0]['text'],'sample')
            self.assertEqual(parameters['coverage_recovery']['status'],'not_needed')
    def test_corrected_gate_reaches_real_coverage_audit_and_metadata(self):self.run_transcribe()
    def test_genuine_overflow_fails_before_whisper_loading(self):self.run_transcribe(invalid=True)

if __name__=='__main__':unittest.main()
