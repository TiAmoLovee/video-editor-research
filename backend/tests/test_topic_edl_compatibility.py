"""Generated topics retain the Week 2 frame contract through Week 5 exports.

Audition clips use outward floor/ceil rounding. EDL deliberately keeps its
existing nearest-frame, half-up contract; audition approval is not proof of an
identical-frame EDL or of a measured semantic acceptance percentage.
"""

from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
import tempfile
import unittest
from xml.etree import ElementTree as ET

from backend.tests.test_subtitles import analysis_fixture, source_fixture
from clipforge.decision.generation import generate_configured_candidates
from clipforge.decision.scoring import score_candidates, validate_scored
from clipforge.decision.topic_segments import TopicOptions
from clipforge.editing.edl import candidates_to_edl, create_portrait_edl, validate_edl
from clipforge.editing.mlt import export_mlt
from clipforge.editing.subtitles import build_subtitles, validate_subtitles


def nearest_frame(seconds):
    """Independent decimal-rational half-up oracle at the fixture's 30 fps."""
    position = Fraction(str(seconds)) * 30
    return (2 * position.numerator + position.denominator) // (2 * position.denominator)


class TopicEDLCompatibilityTests(unittest.TestCase):
    def documents(self, version):
        source = source_fixture()
        source['total_frames'] = 3000
        words = [(.025 + i * 8.4, 8.015 + i * 8.4,
                  ('cooking kitchen recipe' if i < 4 else 'astronomy galaxy telescope') + '。', i)
                 for i in range(8)]
        analysis = analysis_fixture(source, words)
        analysis['speech'] = [{'start': words[0][0], 'end': words[-1][1]}]
        analysis['silence'] = [{'start': 0, 'end': words[0][0]},
                               {'start': words[-1][1], 'end': 100}]
        settings = {'version': version, 'options': asdict(TopicOptions())}
        windows = generate_configured_candidates(analysis, settings=settings)
        scored = score_candidates(windows, analysis)
        validate_scored(scored, analysis)
        self.assertEqual(windows['generator']['version'], version)
        self.assertEqual(windows['candidate_count'], 2)
        selected = [item['id'] for item in reversed(windows['candidates'])]
        return source, analysis, scored, selected

    def test_v2_and_legacy_v1_keep_explicit_order_frames_and_complete_provenance(self):
        for version in ('topic-valley-v2', 'topic-valley-v1'):
            with self.subTest(version=version):
                source, analysis, scored, selected = self.documents(version)
                before = deepcopy((source, analysis, scored))
                project = candidates_to_edl(scored, selected, source)
                validate_edl(project)
                by_id = {item['id']: item for item in scored['candidates']}
                digest = hashlib.sha256(json.dumps(
                    scored, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                    allow_nan=False).encode('utf-8')).hexdigest()
                self.assertEqual(project['schema_version'], '0.1.0')
                self.assertEqual(project['sources'], [source])
                self.assertEqual([clip['provenance']['candidate_id'] for clip in project['clips']], selected)
                cursor = 0
                for index, clip in enumerate(project['clips']):
                    candidate = by_id[selected[index]]
                    start, end = nearest_frame(candidate['start']), nearest_frame(candidate['end'])
                    self.assertEqual((clip['source_in_frame'], clip['source_out_frame']), (start, end))
                    self.assertEqual((clip['timeline_in_frame'], clip['timeline_out_frame']),
                                     (cursor, cursor + end - start))
                    self.assertEqual(clip['provenance'], {
                        'candidate_id': candidate['id'], 'candidate_document_sha256': digest,
                        'original_source_id': source['id'], 'original_source_sha256': source['sha256'],
                        'original_start_seconds': candidate['start'],
                        'original_end_seconds': candidate['end'],
                        'original_in_frame': start, 'original_out_frame': end,
                        'original_selection_index': index, 'source_sentences': candidate['source_sentences']})
                    cursor += end - start
                self.assertEqual(project['duration_frames'], cursor)
                self.assertEqual((source, analysis, scored), before)
                with tempfile.TemporaryDirectory() as root:
                    xml = ET.fromstring(export_mlt(project, root, require_media=False))
                entries = xml.findall("playlist[@id='playlist0']/entry")
                self.assertEqual([(int(item.get('in')), int(item.get('out'))) for item in entries],
                                 [(clip['source_in_frame'], clip['source_out_frame'] - 1)
                                  for clip in project['clips']])

    def test_v2_subtitles_portrait_and_mlt_share_saved_cut_order(self):
        source, analysis, scored, selected = self.documents('topic-valley-v2')
        native = candidates_to_edl(scored, selected, source)
        portrait = create_portrait_edl(native, 1080, 1920)
        self.assertEqual(native['clips'], portrait['clips'])
        analyses = {source['id']: analysis}
        for project in (native, portrait):
            with self.subTest(schema=project['schema_version']):
                before = deepcopy(project)
                plan = build_subtitles(project, analyses)
                validate_subtitles(plan, project, analyses)
                with tempfile.TemporaryDirectory() as root:
                    xml = ET.fromstring(export_mlt(project, root, require_media=False,
                                                   subtitle_plan=plan, analyses=analyses))
                entries = xml.findall("playlist[@id='playlist0']/entry")
                self.assertEqual([(int(item.get('in')), int(item.get('out'))) for item in entries],
                                 [(clip['source_in_frame'], clip['source_out_frame'] - 1)
                                  for clip in project['clips']])
                self.assertEqual(int(xml.find('profile').get('width')), project['profile']['width'])
                self.assertEqual(int(xml.find('profile').get('height')), project['profile']['height'])
                self.assertEqual(project, before)

    def test_v2_does_not_allow_source_hash_swap(self):
        source, _, scored, selected = self.documents('topic-valley-v2')
        source['sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'candidate media must match'):
            candidates_to_edl(scored, selected, source)


if __name__ == '__main__':
    unittest.main()
