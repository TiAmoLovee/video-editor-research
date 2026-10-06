from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from clipforge.decision.candidates import content_hash
from clipforge.decision.reviewed import file_hash, rebuild, rescore_rule
from backend.tests.test_selection import sample


class ReviewedCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / 'selected.mp4'
        self.source = Path(self.tmp.name) / 'source.mp4'
        self.video.write_bytes(b'synthetic selected file identity; not measured audio')
        self.source.write_bytes(b'synthetic source identity')
        self.analysis, self.scored = sample()
        self.parent = next(c for c in self.scored['candidates']
                           if c['start'] == 1.43 and c['end'] == 19.74)
        self.acceptance = {
            'version': 'human-audition-acceptance-v1', 'task_id': 'fixture-task',
            'rendered_boundary_review': 'accepted_by_user',
            'user_feedback_verbatim': '两个都可以，B 没看到场景转换',
            'fps': 30, 'start_frame': 43, 'end_frame_exclusive': 535,
            'start_seconds': 43/30, 'end_seconds_exclusive': 535/30,
            'duration_seconds': 492/30, 'selected_sha256': file_hash(self.video),
            'input_clip_sha256': file_hash(self.source),
        }
        self.render = {'task_id': 'fixture-task', 'fps': 30,
                       'source_sha256': file_hash(self.source), 'variants': [{
                           'sha256': file_hash(self.video), 'start_frame': 43,
                           'end_frame_exclusive': 535, 'video_frames_decoded': 492}]}

    def build(self):
        return rebuild(self.analysis, self.scored, self.parent['id'], self.acceptance,
                       self.render, self.video, self.source)

    def test_projects_accepted_range_without_mutating_original_words_or_scores(self):
        before = content_hash([self.analysis, self.scored, self.acceptance, self.render])
        result = self.build()
        candidate = result['candidate']
        self.assertNotEqual(candidate['id'], self.parent['id'])
        self.assertIsNone(candidate['score'])
        self.assertEqual(candidate['source_words'], ['w0', 'wa', 'wb'])
        self.assertTrue(candidate['text'].endswith('面对这种局面'))
        self.assertNotIn('签着了', candidate['text'])
        self.assertEqual(candidate['start'], 43/30)
        self.assertEqual(candidate['end'], 535/30)
        view = result['scoring_context']
        self.assertEqual(view['words'][0]['start'], 0)
        self.assertEqual(view['words'][-1]['end'], 16.4)
        adjustments = result['provenance']['timing_adjustments']
        self.assertEqual([a['word_id'] for a in adjustments], ['w0', 'wb'])
        self.assertEqual(adjustments[-1]['original_end'], 17.9)
        self.assertEqual(before, content_hash([self.analysis, self.scored, self.acceptance, self.render]))

    def test_rule_rescores_clip_and_keeps_pending_input_immutable(self):
        pending = self.build()
        scored = rescore_rule(pending)
        self.assertIsNone(pending['candidate']['score'])
        self.assertEqual(scored['candidate']['scorer'], 'rule')
        self.assertEqual(scored['model_requests'], 0)
        self.assertFalse(scored['original_score_reused'])
        self.assertEqual(scored['candidate']['id'], pending['candidate']['id'])
        self.assertEqual(scored['scoring']['audio']['status'], 'unavailable')

    def test_rejects_changed_files(self):
        for path in (self.video, self.source):
            before = path.read_bytes()
            path.write_bytes(before + b'changed')
            with self.assertRaises(ValueError):
                self.build()
            path.write_bytes(before)

    def test_rejects_unaccepted_or_inconsistent_bounds(self):
        original = deepcopy(self.acceptance)
        for edit in (
            {'rendered_boundary_review': 'pending'}, {'user_feedback_verbatim': ''},
            {'task_id': 'other'}, {'fps': 25}, {'start_frame': True},
            {'end_seconds_exclusive': 19.74}, {'start_frame': 0, 'start_seconds': 0,
                                              'duration_seconds': 535/30},
        ):
            with self.subTest(edit=edit):
                self.acceptance = {**original, **edit}
                with self.assertRaises(ValueError):
                    self.build()

    def test_rejects_render_record_disagreement_and_corrupt_parent(self):
        self.render['variants'][0]['video_frames_decoded'] = 491
        with self.assertRaises(ValueError):
            self.build()
        self.render['variants'][0]['video_frames_decoded'] = 492
        self.parent['text'] += '并不存在的文字'
        with self.assertRaises(ValueError):
            self.build()

    def test_identity_stable_but_changes_for_different_render(self):
        before = self.build()['candidate']['id']
        self.assertEqual(before, self.build()['candidate']['id'])
        self.video.write_bytes(b'another accepted render')
        self.acceptance['selected_sha256'] = file_hash(self.video)
        self.render['variants'][0]['sha256'] = file_hash(self.video)
        self.assertNotEqual(before, self.build()['candidate']['id'])


if __name__ == '__main__':
    unittest.main()
