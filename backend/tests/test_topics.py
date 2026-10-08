from copy import deepcopy
import unittest

from clipforge.decision.topics import group_topics, near_duplicate


def candidate(rank, text):
    return {'id': f'candidate-{rank}', 'rank': rank, 'text': text, 'score': 100-rank,
            'start': rank*100, 'end': rank*100+30}


class TopicTests(unittest.TestCase):
    def test_duplicate_speech_at_different_times_folds_without_mutation(self):
        text = '人工智能的发展改变了我们的生活也让未来科技出现更多可能性。'
        values = [candidate(1, text), candidate(2, text), candidate(3, '炒菜前先清洗青菜然后倒入食用油在铁锅里面翻炒直到熟透')]
        before = deepcopy(values)
        report = group_topics(values)
        self.assertEqual(report['summary']['duplicate_count'], 1)
        self.assertEqual(report['items']['candidate-2']['duplicate_of'], 'candidate-1')
        self.assertEqual(report['summary']['group_count'], 2)
        self.assertEqual(report, group_topics(list(reversed(values))))
        self.assertEqual(values, before)
        self.assertEqual(report['model_requests'], 0)

    def test_similar_topic_is_not_automatically_duplicate(self):
        values = [candidate(1, '人工智能的发展改变了我们的生活也让未来科技出现更多可能性'),
                  candidate(2, '人工智能的发展改变了我们的生活但教育和监管需要提前准备')]
        report = group_topics(values)
        self.assertEqual(report['summary']['group_count'], 1)
        self.assertEqual(report['summary']['duplicate_count'], 0)

    def test_negation_numbers_order_and_short_text_are_not_folded(self):
        base = '我们可以使用这个系统来判断人工智能的表现并记录完整的研究结果'
        for a, b in [(base, base.replace('可以', '不可以')),
                     (base+'提高20%', base+'提高21%'),
                     (base+'提高三倍', base+'提高四倍'),
                     ('this system is reliable and useful for all the applications discussed today',
                      'this system is not reliable and useful for all the applications discussed today'),
                     ('人工智能', '人工智能'),
                     ('先洗菜再炒菜然后盛菜准备吃饭接着收拾桌子最后清洗餐具',
                      '最后清洗餐具接着收拾桌子然后盛菜准备吃饭先洗菜再炒菜')]:
            with self.subTest(a=a, b=b):
                self.assertFalse(near_duplicate(a, b, 1.0))
                self.assertEqual(group_topics([candidate(1, a), candidate(2, b)])['summary']['duplicate_count'], 0)

    def test_complete_link_prevents_bridge_from_merging_two_unrelated_topics(self):
        left = '苹果果园采摘水果丰收果农运输苹果'
        right = '火箭发射卫星进入轨道宇宙太空探索'
        result = group_topics([candidate(1, left), candidate(2, left+right), candidate(3, right)])
        self.assertEqual(result['summary']['group_count'], 2)
        self.assertNotEqual(result['items']['candidate-1']['group_id'], result['items']['candidate-3']['group_id'])

    def test_empty_and_nonlexical_input_have_no_false_similarity(self):
        self.assertEqual(group_topics([])['groups'], [])
        report = group_topics([candidate(1, '！？'), candidate(2, '...')])
        self.assertEqual(report['summary']['group_count'], 2)
        self.assertEqual(report['summary']['duplicate_count'], 0)


if __name__ == '__main__':
    unittest.main()
