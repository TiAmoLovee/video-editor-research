"""规则方向性、音量证据、候选不变性及非法结果回归。"""

from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

from clipforge.decision.audio import AudioProfile, _read_pcm, measure_audio
from clipforge.decision.candidates import ROOT, content_hash, generate_candidates
from clipforge.decision.scorers import RuleConfig, RuleScorer, Scorer, ScorerRegistry, default_registry
from clipforge.decision.scoring import load_config, score_candidates, validate_scored
from backend.tests.test_candidates import fixture


def text_fixture(texts, intervals=None):
    intervals = intervals or [(i*10, (i+1)*10) for i in range(len(texts))]
    data = fixture(intervals)
    for word, sentence, text in zip(data['words'], data['sentences'], texts):
        word['text'] = sentence['text'] = text
    return data


class ScoringTests(unittest.TestCase):
    def test_registry_factory_duplicates_unknown_and_invalid(self):
        registry = default_registry()
        self.assertIsInstance(registry.create('rule'), Scorer)
        with self.assertRaises(ValueError):
            registry.create('llm')
        with self.assertRaises(ValueError):
            registry.register('rule', RuleScorer)
        registry.register('bad', lambda: object())
        with self.assertRaises(ValueError):
            registry.create('bad')
        with self.assertRaises(ValueError):
            ScorerRegistry().register('', RuleScorer)

    def test_invalid_config_rejected_and_external_config_loads(self):
        self.assertEqual(load_config().version, 'rule-v1')
        for values in ({'keywords':['']}, {'keywords':['AI','ai']}, {'keywords':'ai'},
                       {'pace_change_target':0}, {'target_seconds':14}, {'keyword_weight':math.inf},
                       {'volume_weight':True}, {'base_score':100}, {'version':'unknown'},
                       {'duration_penalty_weight':101}, {'keyword_density_target':1.1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                RuleConfig(**values)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.json'
            path.write_text('{"unknown": 1}', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_config(path)

    def test_keyword_longest_match_no_overlap_inflation(self):
        data = text_fixture(['人工智能智能', '其他内容'])
        candidate = generate_candidates(data)['candidates'][0]
        scorer = RuleScorer(RuleConfig(keywords=('智能','人工智能')))
        result = scorer.score(candidate, data)
        evidence = result['features']['keyword_density']['evidence']
        self.assertEqual(evidence['hits'], ['人工智能','智能'])
        self.assertEqual(evidence['covered_units'], 6)
        self.assertEqual(evidence['text_units'], 10)
        self.assertGreater(result['score'], RuleScorer(RuleConfig(keywords=())).score(candidate,data)['score'])

    def test_pace_change_uses_adjacent_sentence_character_rates(self):
        data = text_fixture(['一'*10, '二'*20])
        result = score_candidates(generate_candidates(data),data)
        feature = result['candidates'][0]['features']['pace_change']
        self.assertEqual(feature['evidence']['sentence_rates'], [1,2])
        self.assertAlmostEqual(feature['evidence']['relative_adjacent_change'], 2/3)
        self.assertEqual(feature['value'], 1)
        single = text_fixture(['一'*10], [(0,20)])
        result = score_candidates(generate_candidates(single),single)
        self.assertEqual(result['candidates'][0]['features']['pace_change']['value'],0)

    def test_qa_order_and_same_sentence(self):
        for texts, expected in [(['为什么这样','因为条件限制'],1),
                                (['因为条件限制','为什么这样'],0),
                                (['为什么这样因为条件限制','后续说明'],1),
                                (['正常说明','后续说明'],0)]:
            data = text_fixture(texts)
            feature = score_candidates(generate_candidates(data),data)['candidates'][0]['features']['qa_pattern']
            self.assertEqual(feature['value'], expected)

    def test_duration_penalty_and_score_range(self):
        scorer = RuleScorer(RuleConfig(keywords=()))
        values = []
        for duration in (15,30,90):
            data = text_fixture(['内容'],[(0,duration)])
            candidate = generate_candidates(data)['candidates'][0]
            value = scorer.score(candidate,data)['score']
            self.assertTrue(0 <= value <= 100)
            values.append(value)
        self.assertEqual(values,[10,20,0])

    def test_missing_audio_zero_contribution_and_warning(self):
        data = text_fixture(['为什么这样','因为条件限制'])
        result = score_candidates(generate_candidates(data),data)
        self.assertEqual(result['scoring']['audio']['status'],'unavailable')
        self.assertIn('volume_unavailable_zero_contribution',result['scoring']['warnings'])
        self.assertEqual(result['candidates'][0]['features']['volume_peak']['contribution'],0)

    def test_measured_audio_and_source_mismatch(self):
        data = text_fixture(['正常说明','后续说明'])
        audio = AudioProfile(data['media']['normalized_sha256'],21,'a'*64,
                             tuple([.01]*190+[.1]*20),tuple([.02]*190+[.2]*20),-40)
        result = score_candidates(generate_candidates(data),data,audio=audio)
        feature = result['candidates'][0]['features']['volume_peak']
        self.assertEqual(feature['value'],1)
        self.assertAlmostEqual(feature['evidence']['peak_rms_dbfs'],-20)
        self.assertAlmostEqual(feature['evidence']['sample_peak_dbfs'],20*math.log10(.2),places=5)
        for invalid in (replace(audio,normalized_sha256='b'*64),replace(audio,duration_seconds=22)):
            with self.assertRaises(ValueError):
                score_candidates(generate_candidates(data),data,audio=invalid)

    def test_silent_audio_never_earns_prominence(self):
        data = text_fixture(['正常说明','后续说明'])
        audio = AudioProfile(data['media']['normalized_sha256'],21,'a'*64,
                             tuple([0]*210),tuple([0]*210),-120)
        result = score_candidates(generate_candidates(data),data,audio=audio)
        self.assertEqual(result['candidates'][0]['features']['volume_peak']['value'],0)

    def test_stable_tie_ranking_and_input_unchanged(self):
        data = text_fixture(['内容','内容','内容'])
        windows = generate_candidates(data)
        before = deepcopy(windows)
        config = RuleConfig(keywords=(),keyword_weight=0,pace_weight=0,volume_weight=0,
                            qa_weight=0,duration_penalty_weight=0)
        result = score_candidates(windows,data,config=config)
        self.assertEqual(windows,before)
        self.assertEqual(result,score_candidates(windows,data,config=config))
        self.assertEqual([(c['start'],c['end']) for c in result['candidates']],[(0,20),(0,30),(10,30)])
        self.assertEqual([c['rank'] for c in result['candidates']],[1,2,3])
        self.assertEqual(result['windows_sha256'],content_hash(windows))

    def test_empty_candidates_and_no_audio(self):
        data = fixture([])
        data['media']['has_audio'] = False
        data['speech'] = []
        for key in ('vad','asr'):
            data['analyzers'][key]['status'] = 'no_audio'
        result = score_candidates(generate_candidates(data),data)
        self.assertEqual(result['candidate_count'],0)
        self.assertEqual(result['scoring']['audio']['status'],'no_audio')

    def test_schema_and_semantic_corruption(self):
        data = text_fixture(['为什么这样','因为条件限制','总结方法'])
        baseline = score_candidates(generate_candidates(data),data)
        changes = [lambda d: d['candidates'][0].update(score=99),
                   lambda d: d['candidates'][0].update(rank=2),
                   lambda d: d['candidates'][0].update(text='改文'),
                   lambda d: d['candidates'][0].update(reasons=[]),
                   lambda d: d['candidates'][0]['features']['volume_peak'].update(value=.5),
                   lambda d: d['scoring'].update(config_sha256='0'*64),
                   lambda d: d['scoring'].update(warnings=[]),
                   lambda d: d.update(windows_sha256='0'*64),
                   lambda d: d['candidates'].reverse(),
                   lambda d: d['candidates'][0]['features']['pace_change'].update(value=math.nan)]
        for change in changes:
            with self.subTest(change=change):
                result=deepcopy(baseline);change(result)
                with self.assertRaises(ValueError):
                    validate_scored(result,data)

    def test_cli_reproducible_and_protects_existing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);data=text_fixture(['正常说明','后续说明'])
            source=folder/'analysis.json';windows=folder/'windows.json';output=folder/'scored.json'
            source.write_text(json.dumps(data),encoding='utf-8')
            windows.write_text(json.dumps(generate_candidates(data)),encoding='utf-8')
            command=[sys.executable,'-m','clipforge.decision.scoring',str(source),str(windows),'--output',str(output)]
            completed=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(completed.returncode,0,completed.stderr)
            saved=output.read_bytes();validate_scored(json.loads(saved),data)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
            self.assertEqual(saved,output.read_bytes())


class AudioTests(unittest.TestCase):
    def test_known_pcm_rms_peak_and_silence(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'audio.wav'
            samples=np.array([0]*1600+[16384]*1600+[-32768]*1600,dtype='<i2')
            with wave.open(str(path),'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
                output.writeframes(samples.tobytes())
            profile=_read_pcm(path,.3,'a'*64)
            self.assertEqual(profile.rms,(0,.5,1))
            self.assertEqual(profile.peaks,(0,.5,1))
            self.assertEqual(profile.window(.1,.2)['peak_rms_dbfs'],-6.0206)
            self.assertEqual(profile.window(0,.1)['peak_rms_dbfs'],-120)
            with self.assertRaises(ValueError):
                _read_pcm(path,.4,'a'*64)

    def test_wrong_video_rejected_before_media_tools(self):
        data=fixture([(0,20)])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'wrong.mp4';path.write_bytes(b'wrong video')
            with patch('clipforge.decision.audio.probe_video') as probe:
                with self.assertRaisesRegex(ValueError,'哈希'):
                    measure_audio(path,data)
                probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
