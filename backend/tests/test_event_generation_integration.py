"""Offline v3 generation contracts through scoring, frame EDL and result cache.

Synthetic surface dependencies are regression evidence, not human quality labels.
No media decode, external model, production task or network request is used.
"""

from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
import json
import os
import unittest
from unittest.mock import Mock, patch
import zipfile

from backend.tests import test_result_cache as cache_helpers
from backend.tests.test_llm_scoring import response, snapshot
from backend.tests.test_subtitles import analysis_fixture, source_fixture
from clipforge.decision.candidates import content_hash
from clipforge.decision.generation import (
    EVENT_TOPIC, GUARDED_TOPIC, LEGACY, generate_configured_candidates, settings_snapshot,
)
from clipforge.decision.llm import apply_llm
from clipforge.decision.scoring import score_candidates, validate_scored
from clipforge.decision.topic_segments import TopicOptions, segment_topics
from clipforge.decision.validation import validate_candidates
from clipforge.editing.edl import candidates_to_edl, validate_edl
from clipforge.services import result_cache as cache
from clipforge.services.pipeline import process_video
from clipforge.storage.jobs import job_dir


def documents():
    """A lexical valley splits '那个 / 时候'; a real pause ends that event."""
    source = source_fixture()
    source['total_frames'] = 3000
    texts = (['cooking kitchen recipe。'] * 3 + ['cooking kitchen recipe那个']
             + ['时候astronomy galaxy telescope。'] + ['astronomy galaxy telescope。'] * 3
             + ['football team stadium。'] * 3)
    rows = [(.025 + i * 8.4, 8.015 + i * 8.4, text, i)
            for i, text in enumerate(texts[:8])]
    rows += [(71.025 + i * 8.4, 79.015 + i * 8.4, text, i + 8)
             for i, text in enumerate(texts[8:])]
    analysis = analysis_fixture(source, rows)
    analysis['speech'] = [{'start': .025, 'end': 66.815}, {'start': 71.025, 'end': 95.815}]
    analysis['silence'] = [{'start': 0, 'end': .025}, {'start': 66.815, 'end': 71.025},
                           {'start': 95.815, 'end': 100}]
    return source, analysis


def settings(version):
    return {'version': version, 'options': asdict(TopicOptions())}


def nearest_frame(seconds):
    position = Fraction(str(seconds)) * 30
    return (2 * position.numerator + position.denominator) // (2 * position.denominator)


class EventGenerationIntegrationTests(unittest.TestCase):
    def test_event_selection_mock_llm_and_edl_preserve_original_ranges_and_asr(self):
        source, analysis = documents()
        original = deepcopy((source, analysis))
        windows = generate_configured_candidates(analysis, settings=settings(EVENT_TOPIC))
        validate_candidates(windows, analysis)
        self.assertEqual([(item['start'], item['end']) for item in windows['candidates']],
                         [(.025, 66.815), (71.025, 95.815)])
        self.assertEqual(windows['candidates'][0]['source_sentences'], [f's{i}' for i in range(8)])
        rule = score_candidates(windows, analysis)
        transport = Mock(return_value=response())
        model = apply_llm(rule, analysis, snapshot(), api_key='offline-fixture',
                          transport=transport, limiter=Mock())
        validate_scored(model, analysis)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(model['generator'], settings(EVENT_TOPIC))
        self.assertEqual(model['windows_sha256'], content_hash(windows))
        by_id = {item['id']: item for item in windows['candidates']}
        for item in model['candidates']:
            self.assertEqual({key: item[key] for key in ('start', 'end', 'text', 'source_sentences')},
                             {key: by_id[item['id']][key] for key in ('start', 'end', 'text', 'source_sentences')})
        # An explicit user selection controls timeline order, independently of ranking.
        selection = [item['id'] for item in reversed(windows['candidates'])]
        edl = candidates_to_edl(model, selection, source)
        validate_edl(edl)
        cursor = 0
        for index, clip in enumerate(edl['clips']):
            item = by_id[selection[index]]
            start, end = nearest_frame(item['start']), nearest_frame(item['end'])
            self.assertEqual((clip['source_in_frame'], clip['source_out_frame']), (start, end))
            self.assertEqual((clip['timeline_in_frame'], clip['timeline_out_frame']),
                             (cursor, cursor + end - start))
            self.assertEqual(clip['provenance']['source_sentences'], item['source_sentences'])
            self.assertEqual(clip['provenance']['candidate_document_sha256'], content_hash(model))
            cursor += end - start
        self.assertEqual(edl['duration_frames'], cursor)
        self.assertEqual((source, analysis), original)

    def test_partial_event_and_generator_substitution_are_rejected_without_changing_defaults(self):
        _, analysis = documents()
        event = generate_configured_candidates(analysis, settings=settings(EVENT_TOPIC))
        guarded = generate_configured_candidates(analysis, settings=settings(GUARDED_TOPIC))
        self.assertEqual(guarded['candidate_count'], 3)
        self.assertEqual(event['candidate_count'], 2)
        # All individual sentences remain complete, but this range breaks the event link.
        partial = deepcopy(event)
        partial['candidates'][0] = deepcopy(guarded['candidates'][0])
        partial['candidates'][0]['id'] = 'c_' + content_hash({
            'analysis': event['analysis_sha256'], 'version': EVENT_TOPIC,
            'options': event['generator']['options'],
            'source_sentences': partial['candidates'][0]['source_sentences']})
        substituted = deepcopy(event)
        substituted['generator']['version'] = GUARDED_TOPIC
        for bad in (partial, substituted):
            with self.subTest(strategy=bad['generator']['version']), self.assertRaises(ValueError):
                validate_candidates(bad, analysis)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(settings_snapshot()['version'], LEGACY)
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': GUARDED_TOPIC}):
            self.assertEqual(settings_snapshot(), settings(GUARDED_TOPIC))
        self.assertEqual(generate_configured_candidates(analysis, settings=settings(GUARDED_TOPIC)), guarded)


class EventResultCacheIntegrationTests(unittest.TestCase):
    # Reuse only fixture methods; importing the TestCase as a module attribute
    # avoids unittest rediscovering every existing ResultCacheTests method here.
    job = cache_helpers.ResultCacheTests.job
    descriptor = cache_helpers.ResultCacheTests.descriptor
    pipeline = cache_helpers.ResultCacheTests.pipeline

    def setUp(self):
        cache_helpers.ResultCacheTests.setUp(self)
        self.example = documents()[1]
        env = patch.dict(os.environ, {'CLIPFORGE_SCORER': 'rule'})
        env.start()
        self.addCleanup(env.stop)
        audio = patch('clipforge.services.pipeline.measure_audio', return_value=None)
        audio.start()
        self.addCleanup(audio.stop)

    def test_v2_v3_cache_isolation_exact_event_audit_zip_and_tamper_rejection(self):
        original = deepcopy(self.example)
        self.pipeline()
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': GUARDED_TOPIC}):
            old = self.job()
            self.assertFalse(process_video(old)['cache_hit'])
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': EVENT_TOPIC}):
            first = self.job()
            self.assertFalse(process_video(first)['cache_hit'])
            second = self.job('renamed.mp4')
            self.assertTrue(process_video(second)['cache_hit'])
            folder = job_dir(first)
            report_path = folder / 'topic_segmentation.json'
            saved = json.loads(report_path.read_text(encoding='utf-8'))
            analysis = json.loads((folder / 'analysis.json').read_text(encoding='utf-8'))
            expected = segment_topics(analysis, version=EVENT_TOPIC)
            self.assertEqual(saved, expected)
            self.assertTrue(saved['event_dependencies']['links'])
            self.assertFalse(saved['event_dependencies']['semantic_completeness_verified'])
            self.assertEqual((job_dir(second) / 'topic_segmentation.json').read_bytes(), report_path.read_bytes())
            with zipfile.ZipFile(job_dir(second) / 'result.zip') as archive:
                self.assertEqual(archive.read('topic_segmentation.json'), report_path.read_bytes())
                self.assertEqual(archive.read('candidate_windows.json'), (folder / 'candidate_windows.json').read_bytes())
            task = cache.get_job(first)
            descriptor = self.descriptor(folder / 'source.mp4', folder / 'shot_options.json', '', '')[1]
            cache.validate_bundle(folder, folder / 'source.mp4', task['result'], descriptor)
            wrong = deepcopy(descriptor)
            wrong['generation_settings']['version'] = GUARDED_TOPIC
            with self.assertRaisesRegex(ValueError, '生成策略'):
                cache.validate_bundle(folder, folder / 'source.mp4', task['result'], wrong)
            # Semantic reconstruction must reject a forged audit even when JSON is valid.
            for mutate in ('remove_audit', 'retention', 'quality_claim'):
                bad = deepcopy(saved)
                if mutate == 'remove_audit':
                    del bad['event_dependencies']
                elif mutate == 'retention':
                    bad['event_dependencies']['links'][0]['retention'] = 'discarded'
                else:
                    bad['event_dependencies']['semantic_completeness_verified'] = True
                report_path.write_text(json.dumps(bad), encoding='utf-8')
                with self.subTest(mutate=mutate), self.assertRaisesRegex(ValueError, '分段记录'):
                    cache.validate_bundle(folder, folder / 'source.mp4', task['result'], descriptor)
            report_path.write_text(json.dumps(saved), encoding='utf-8')
        self.assertEqual((self.asr.call_count, self.normalize.call_count, self.split.call_count), (2, 2, 2))
        self.assertEqual((job_dir(old) / 'asr.json').read_bytes(), (job_dir(first) / 'asr.json').read_bytes())
        self.assertEqual(self.example, original)


if __name__ == '__main__':
    unittest.main()
