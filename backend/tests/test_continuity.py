from copy import deepcopy
import unittest

from backend.tests.test_candidates import fixture
from clipforge.decision.candidates import content_hash, generate_candidates, WindowOptions
from clipforge.decision.scoring import score_candidates
from clipforge.decision.continuity import tail_signal, review_continuity


def sample(gap=.5):
    analysis = fixture([(0, 9), (9.5, 20), (20+gap, 29+gap), (30+gap, 38+gap)])
    texts = ['这里讨论智能识别问题', '智能差距有一定范围如果超出了这个范围',
             '你真的没法判断它是智能还是自然现象', '下面我们换一个话题']
    for word, sentence, text in zip(analysis['words'], analysis['sentences'], texts):
        word['text'] = sentence['text'] = text
    scored = score_candidates(generate_candidates(analysis, WindowOptions(max_gap_seconds=10)), analysis)
    return analysis, scored


class ContinuityTests(unittest.TestCase):
    def test_demonstrative_warning_is_not_a_verdict(self):
        for text in ('主要原因就是这种', '目标是那个。'):
            result = tail_signal(text)
            self.assertEqual(result['code'], 'demonstrative_tail_needs_review')
            self.assertIn(result['quote'], text)
        for text in ('主要原因就是这种天气', '结果就是这样', '你说的是这种？', '他说“是这种”'):
            self.assertIsNone(tail_signal(text))

    def test_shorter_option_uses_own_score_and_requires_audition(self):
        analysis = fixture([(0, 10), (10.5, 20), (20.5, 24)])
        texts = ['欢迎来到今天的节目', '很高兴参加这次访谈', '主要原因就是这种']
        for word, sentence, text in zip(analysis['words'], analysis['sentences'], texts):
            word['text'] = sentence['text'] = text
        scored = score_candidates(generate_candidates(analysis), analysis)
        before = content_hash([analysis, scored])
        report = review_continuity(scored, analysis)
        original = next(c for c in scored['candidates'] if c['start'] == 0 and c['end'] == 24)
        item = next(i for i in report['items'] if i['candidate_id'] == original['id'])
        shorter = item['existing_contraction']
        self.assertEqual(shorter['end'], 20)
        saved = next(c for c in scored['candidates'] if c['id'] == shorter['id'])
        self.assertEqual(shorter['score'], saved['score'])
        self.assertEqual(shorter['source_sentences'], saved['source_sentences'])
        self.assertEqual(item['contraction_status'], 'needs_audition')
        self.assertEqual(before, content_hash([analysis, scored]))

    def test_shorter_option_cannot_cross_a_long_pause_or_invent_short_clips(self):
        for bounds in ([(0, 8), (12, 20), (20.5, 24)], [(0, 10), (10.5, 20)]):
            analysis = fixture(bounds)
            analysis['sentences'][-1]['text'] = analysis['words'][-1]['text'] = '主要原因就是这种'
            scored = score_candidates(generate_candidates(analysis, WindowOptions(max_gap_seconds=10)), analysis)
            report = review_continuity(scored, analysis)
            original = next(c for c in scored['candidates'] if c['start'] == 0 and c['end'] == bounds[-1][1])
            item = next(i for i in report['items'] if i['candidate_id'] == original['id'])
            self.assertIsNone(item['existing_contraction'])

    def test_narrow_signals_preserve_exact_quote(self):
        for text in ('如果明天下雨', '差距较大如果超出了这个范围。', '它远超人类的话你就'):
            with self.subTest(text=text):
                result = tail_signal(text)
                self.assertIsNotNone(result)
                self.assertIn(result['quote'], text)

    def test_complete_conditional_question_and_quoted_expression_are_not_flagged(self):
        for text in ('如果明天下雨我们就留在家里', '如果超出范围会发生什么？',
                     '如果是你呢', '他说“如果明天下雨”', '面对这种局面',
                     '完全无法理解它也没法判断它的智能到哪一步', ''):
            with self.subTest(text=text):
                self.assertIsNone(tail_signal(text))

    def test_shortest_existing_extension_and_scores_are_traceable(self):
        analysis, scored = sample()
        before = content_hash([analysis, scored])
        report = review_continuity(scored, analysis)
        original = next(c for c in scored['candidates'] if c['start'] == 0 and c['end'] == 20)
        item = next(i for i in report['items'] if i['candidate_id'] == original['id'])
        extension = item['existing_extension']
        self.assertEqual(extension['end'], 29.5)
        saved = next(c for c in scored['candidates'] if c['id'] == extension['id'])
        self.assertEqual(extension['score'], saved['score'])
        self.assertEqual(extension['text'], saved['text'])
        self.assertEqual(item['extension_status'], 'needs_audition')
        self.assertEqual(before, content_hash([analysis, scored]))
        self.assertEqual(report['model_requests'], 0)

    def test_extension_must_not_jump_a_long_pause(self):
        analysis, scored = sample(gap=5)
        report = review_continuity(scored, analysis)
        original = next(c for c in scored['candidates'] if c['start'] == 0 and c['end'] == 20)
        item = next(i for i in report['items'] if i['candidate_id'] == original['id'])
        self.assertIsNone(item['existing_extension'])

    def test_no_future_candidate_does_not_invent_an_extension(self):
        analysis = fixture([(0, 20)])
        analysis['sentences'][0]['text'] = analysis['words'][0]['text'] = '如果超出了范围'
        scored = score_candidates(generate_candidates(analysis), analysis)
        self.assertIsNone(review_continuity(scored, analysis)['items'][0]['existing_extension'])

    def test_no_signal_does_not_claim_acceptance_and_invalid_scores_rejected(self):
        analysis = fixture([(0, 20)])
        scored = score_candidates(generate_candidates(analysis), analysis)
        self.assertEqual(review_continuity(scored, analysis)['items'], [])
        bad = deepcopy(scored)
        bad['candidates'][0]['score'] += 1
        with self.assertRaises(ValueError):
            review_continuity(bad, analysis)
