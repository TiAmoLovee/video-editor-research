"""Offline v4 contracts across generation, scoring, EDL and cached audit ZIPs.

Synthetic discourse examples test preservation and rejection behavior. They are
not new human quality labels; all model and media work is replaced by fixtures.
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
    CONTINUITY_TOPIC, EVENT_TOPIC, generate_configured_candidates,
)
from clipforge.decision.llm import apply_llm
from clipforge.decision.scoring import score_candidates, validate_scored
from clipforge.decision.topic_segments import TopicOptions, segment_topics
from clipforge.decision.validation import validate_candidates
from clipforge.editing.edl import candidates_to_edl, validate_edl
from clipforge.services import result_cache as cache
from clipforge.services.pipeline import process_video
from clipforge.storage.jobs import job_dir


def documents(*, independent=False, seconds=7.99, hard_gap=False):
    """A lexical valley divides an implicit question from a multi-unit response."""
    kitchen = 'cooking kitchen recipe ' * 8
    stars = 'astronomy galaxy telescope ' * 8
    sport = 'football team stadium ' * 8
    if independent:
        middle = [kitchen + '为什么改计划因为预算已经调整了。',
                  '所以项目按期完成了。' + stars + '结果已经公布。',
                  stars + '观测已经结束。', stars + '资料已经保存。', stars + '讨论已经结束。']
    else:
        middle = [kitchen + '我会好奇这样的变化',
                  '是因为' + stars + '成本提高了，我在生产方面',
                  '遇到了一些挑战，' + stars + '就是需要的一些材料',
                  '是我们之前很少使用的型号。' + stars + '讨论结束。',
                  stars + '资料已经保存。']
    texts = [kitchen + '准备完成。'] * 3 + middle + [sport + '赛事结束。'] * 3
    step = seconds + .41
    rows = [(.025 + i * step, .025 + i * step + seconds, text, i)
            for i, text in enumerate(texts[:8])]
    if hard_gap:
        rows = [(start + (4 if index >= 4 else 0),
                 end + (4 if index >= 4 else 0), text, group)
                for index, (start, end, text, group) in enumerate(rows)]
    last_end = rows[-1][1]
    rows += [(last_end + 4.21 + i * step, last_end + 4.21 + i * step + seconds, text, i + 8)
             for i, text in enumerate(texts[8:])]
    source = source_fixture()
    source['total_frames'] = int((rows[-1][1] + 2) * 30) + 1
    analysis = analysis_fixture(source, rows)
    # Cover only existing word intervals, so VAD cannot invent an ASR gap.
    analysis['speech'] = [{'start': start, 'end': end} for start, end, _, _ in rows]
    cursor = 0
    silence = []
    for start, end, _, _ in rows:
        if start > cursor:
            silence.append({'start': cursor, 'end': start})
        cursor = end
    silence.append({'start': cursor, 'end': analysis['media']['duration_seconds']})
    analysis['silence'] = silence
    return source, analysis


def settings(version):
    return {'version': version, 'options': asdict(TopicOptions())}


def selected_cuts(report):
    return {row['after_unit'] for row in report['boundaries']
            if row['decision'] in ('accepted_valley', 'hard_pause')}


def nearest_frame(seconds):
    position = Fraction(str(seconds)) * 30
    return (2 * position.numerator + position.denominator) // (2 * position.denominator)


class EventContinuityIntegrationTests(unittest.TestCase):
    def test_implicit_question_causal_chain_removes_only_existing_cuts_and_retains_text(self):
        _, analysis = documents()
        original = deepcopy(analysis)
        old = segment_topics(analysis, version=EVENT_TOPIC)
        new = segment_topics(analysis, version=CONTINUITY_TOPIC)
        self.assertIn(3, selected_cuts(old))
        self.assertLess(selected_cuts(new), selected_cuts(old))
        self.assertNotIn(3, selected_cuts(new))
        refs = [ref for segment in new['segments'] for ref in segment['source_sentences']]
        self.assertEqual(refs, [sentence['id'] for sentence in analysis['sentences']])
        audit = new['event_dependencies']
        self.assertFalse(audit['semantic_completeness_verified'])
        self.assertTrue(audit['obligations'])
        for obligation in audit['obligations']:
            self.assertTrue(obligation['link_indices'])
            self.assertTrue(all(type(index) is int and 0 <= index < len(audit['links'])
                                for index in obligation['link_indices']))
        # Every internal transition in this synthetically unfinished response is
        # protected, including the nominal complement beyond the first reply.
        for cut in (3, 4, 5):
            self.assertTrue(any(link['first_unit'] <= cut < link['last_unit']
                                for link in audit['links']), cut)
        windows = generate_configured_candidates(analysis, settings=settings(CONTINUITY_TOPIC))
        validate_candidates(windows, analysis)
        self.assertEqual(windows['candidate_count'], 2)
        self.assertEqual(windows['candidates'][0]['source_sentences'], [f's{i}' for i in range(8)])
        self.assertEqual(windows['candidates'][0]['text'],
                         '\n'.join(sentence['text'] for sentence in analysis['sentences'][:8]))
        self.assertEqual(analysis, original)

    def test_completed_inline_answer_and_independent_so_do_not_join_topics(self):
        _, analysis = documents(independent=True)
        old = segment_topics(analysis, version=EVENT_TOPIC)
        new = segment_topics(analysis, version=CONTINUITY_TOPIC)
        self.assertIn(3, selected_cuts(old))
        self.assertEqual(selected_cuts(new), selected_cuts(old))
        self.assertFalse(any(link['first_unit'] <= 3 < link['last_unit']
                             for link in new['event_dependencies']['links']))
        windows = generate_configured_candidates(analysis, settings=settings(CONTINUITY_TOPIC))
        validate_candidates(windows, analysis)
        self.assertEqual(windows['candidate_count'], 3)

    def test_hard_pause_and_duration_limit_do_not_create_short_incomplete_fallbacks(self):
        _, interrupted = documents(hard_gap=True)
        report = segment_topics(interrupted, version=CONTINUITY_TOPIC)
        self.assertEqual(report['boundaries'][3]['decision'], 'hard_pause')
        affected = [item for item in report['segments']
                    if any(f's{i}' in item['source_sentences'] for i in (3, 4))]
        self.assertTrue(affected)
        self.assertTrue(all(not item['eligible'] for item in affected))
        self.assertTrue(all('unresolved_event_dependency' in item['reasons'] for item in affected))
        windows = generate_configured_candidates(interrupted, settings=settings(CONTINUITY_TOPIC))
        validate_candidates(windows, interrupted)
        self.assertTrue(all(not ({'s3', 's4'} & set(item['source_sentences']))
                            for item in windows['candidates']))

        _, overlong = documents(seconds=12)
        report = segment_topics(overlong, version=CONTINUITY_TOPIC)
        linked = next(item for item in report['segments'] if 's3' in item['source_sentences'])
        self.assertEqual(linked['source_sentences'], [f's{i}' for i in range(8)])
        self.assertIn('above_max_seconds', linked['reasons'])
        windows = generate_configured_candidates(overlong, settings=settings(CONTINUITY_TOPIC))
        validate_candidates(windows, overlong)
        self.assertTrue(all(not ({f's{i}' for i in range(8)} & set(item['source_sentences']))
                            for item in windows['candidates']))

    def test_rule_mock_model_and_reversed_edl_preserve_source_frames_words_and_provenance(self):
        source, analysis = documents()
        original = deepcopy((source, analysis))
        windows = generate_configured_candidates(analysis, settings=settings(CONTINUITY_TOPIC))
        rule = score_candidates(windows, analysis)
        validate_scored(rule, analysis)
        transport = Mock(return_value=response())
        scored = apply_llm(rule, analysis, snapshot(), api_key='offline-fixture',
                           transport=transport, limiter=Mock())
        validate_scored(scored, analysis)
        self.assertEqual(transport.call_count, windows['candidate_count'])
        self.assertEqual(scored['generator'], settings(CONTINUITY_TOPIC))
        self.assertEqual(scored['windows_sha256'], content_hash(windows))
        by_id = {item['id']: item for item in windows['candidates']}
        for item in scored['candidates']:
            self.assertEqual({key: item[key] for key in ('start', 'end', 'text', 'source_sentences')},
                             {key: by_id[item['id']][key]
                              for key in ('start', 'end', 'text', 'source_sentences')})
        selection = [item['id'] for item in reversed(windows['candidates'])]
        self.assertEqual(len(selection), 2)
        edl = candidates_to_edl(scored, selection, source)
        validate_edl(edl)
        cursor = 0
        for selected_id, clip in zip(selection, edl['clips']):
            candidate = by_id[selected_id]
            start, end = nearest_frame(candidate['start']), nearest_frame(candidate['end'])
            self.assertEqual((clip['source_in_frame'], clip['source_out_frame']), (start, end))
            self.assertEqual((clip['timeline_in_frame'], clip['timeline_out_frame']),
                             (cursor, cursor + end - start))
            self.assertEqual(clip['provenance']['source_sentences'], candidate['source_sentences'])
            self.assertEqual(clip['provenance']['candidate_document_sha256'], content_hash(scored))
            self.assertEqual(clip['provenance']['original_source_sha256'], source['sha256'])
            cursor += end - start
        self.assertEqual(edl['duration_frames'], cursor)
        self.assertEqual((source, analysis), original)


class EventContinuityCacheIntegrationTests(unittest.TestCase):
    # Assign only fixtures; subclassing/importing the helper TestCase directly
    # would collect unrelated cache tests again in this module.
    job = cache_helpers.ResultCacheTests.job
    descriptor = cache_helpers.ResultCacheTests.descriptor
    pipeline = cache_helpers.ResultCacheTests.pipeline

    def setUp(self):
        cache_helpers.ResultCacheTests.setUp(self)
        self.example = documents()[1]
        environment = patch.dict(os.environ, {'CLIPFORGE_SCORER': 'rule'})
        environment.start()
        self.addCleanup(environment.stop)
        audio = patch('clipforge.services.pipeline.measure_audio', return_value=None)
        audio.start()
        self.addCleanup(audio.stop)

    def test_v3_v4_cache_isolation_zip_audit_and_tampered_chain_rejection(self):
        original = deepcopy(self.example)
        self.pipeline()
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': EVENT_TOPIC}):
            old = self.job()
            self.assertFalse(process_video(old)['cache_hit'])
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': CONTINUITY_TOPIC}):
            first = self.job()
            self.assertFalse(process_video(first)['cache_hit'])
            second = self.job('renamed.mp4')
            self.assertTrue(process_video(second)['cache_hit'])
            folder = job_dir(first)
            report_path = folder / 'topic_segmentation.json'
            saved = json.loads(report_path.read_text(encoding='utf-8'))
            analysis = json.loads((folder / 'analysis.json').read_text(encoding='utf-8'))
            self.assertEqual(saved, segment_topics(analysis, version=CONTINUITY_TOPIC))
            self.assertTrue(saved['event_dependencies']['obligations'])
            self.assertFalse(saved['event_dependencies']['semantic_completeness_verified'])
            self.assertEqual((job_dir(second) / 'topic_segmentation.json').read_bytes(), report_path.read_bytes())
            with zipfile.ZipFile(job_dir(second) / 'result.zip') as archive:
                self.assertEqual(archive.read('topic_segmentation.json'), report_path.read_bytes())
                self.assertEqual(archive.read('candidate_windows.json'),
                                 (folder / 'candidate_windows.json').read_bytes())
            task = cache.get_job(first)
            descriptor = self.descriptor(folder / 'source.mp4', folder / 'shot_options.json', '', '')[1]
            cache.validate_bundle(folder, folder / 'source.mp4', task['result'], descriptor)
            wrong = deepcopy(descriptor)
            wrong['generation_settings']['version'] = EVENT_TOPIC
            with self.assertRaisesRegex(ValueError, '生成策略'):
                cache.validate_bundle(folder, folder / 'source.mp4', task['result'], wrong)
            for change in ('remove_chain_audit', 'forged_chain_reference',
                           'remove_dependency', 'invent_semantic_pass'):
                bad = deepcopy(saved)
                if change == 'remove_chain_audit':
                    del bad['event_dependencies']['obligations']
                elif change == 'forged_chain_reference':
                    bad['event_dependencies']['obligations'][0]['link_indices'] = [
                        len(bad['event_dependencies']['links'])]
                elif change == 'remove_dependency':
                    bad['event_dependencies']['links'].pop()
                else:
                    bad['event_dependencies']['semantic_completeness_verified'] = True
                report_path.write_text(json.dumps(bad), encoding='utf-8')
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, '分段记录'):
                    cache.validate_bundle(folder, folder / 'source.mp4', task['result'], descriptor)
            report_path.write_text(json.dumps(saved), encoding='utf-8')
        self.assertEqual((self.asr.call_count, self.normalize.call_count, self.split.call_count), (2, 2, 2))
        self.assertEqual((job_dir(old) / 'asr.json').read_bytes(), (job_dir(first) / 'asr.json').read_bytes())
        self.assertEqual(self.example, original)


if __name__ == '__main__':
    unittest.main()
