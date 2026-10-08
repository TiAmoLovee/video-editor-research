from copy import deepcopy
import unittest

from backend.tests.test_context_proposals import case
from clipforge.decision.context_priority import question_context, rank_context_proposals
from clipforge.decision.context_proposals import build_context_proposals, discourse_anchors
from clipforge.decision.selection import temporal_iou
from clipforge.decision.candidates import content_hash


class ContextPriorityTests(unittest.TestCase):
    def test_conditional_you_think_is_not_promoted_as_new_question(self):
        a,c=case(['如果','你觉得','这个问题冒犯的时候','可以跳过。']+['还有一些补充。']*8)
        anchors=discourse_anchors(a['words'])
        self.assertTrue(question_context(a['words'],anchors[0]))
        r=rank_context_proposals(a,c)
        cue_items=[p for p in r['items'] if any(i['code']=='question_cue_in_condition'
                                             for i in p['context_priority']['issues'])]
        self.assertTrue(cue_items)
        self.assertTrue(all(p['queue_status']=='deferred_context_risk' for p in cue_items))

    def test_normal_question_not_conditional_just_because_answer_mentions_time(self):
        a,_=case(['请问','你觉得这是什么吗？','我觉得是','需要调整的时候。']*3)
        self.assertIsNone(question_context(a['words'],discourse_anchors(a['words'])[0]))

    def test_context_response_is_flagged_and_all_proposals_preserved(self):
        a,c=case(['判断不出来']+['学习']*30)
        generated=build_context_proposals(a,c)
        r=rank_context_proposals(a,c)
        self.assertEqual({p['id'] for p in generated['proposals']},{p['id'] for p in r['items']})
        flagged=[p for p in r['items'] if p['text'].startswith('判断不出来')]
        self.assertTrue(flagged)
        self.assertTrue(all(p['queue_status']=='deferred_context_risk' for p in flagged))

    def test_small_queue_no_duplicate_ranges_no_inherited_scores_no_mutation(self):
        a,c=case(['请问']+['有明确解释。']*9+['那么我们接下来谈']+['另一种情况。']*9)
        before=deepcopy((a,c))
        r=rank_context_proposals(a,c,max_recommendations=1)
        self.assertEqual(len(r['recommended_ids']),1)
        self.assertTrue(all(p['score'] is None for p in r['items']))
        self.assertEqual((a,c),before)
        self.assertEqual(r,rank_context_proposals(a,c,max_recommendations=1))
        self.assertFalse(r['automatic_acceptance'])

    def test_recommended_queue_respects_overlap_suppression(self):
        a,c=case(['学习']*35)
        r=rank_context_proposals(a,c)
        selected=[p for p in r['items'] if p['id'] in r['recommended_ids']]
        for i,p in enumerate(selected):
            self.assertTrue(all(temporal_iou(p,q)<.5 for q in selected[i+1:]))

    def test_invalid_capacity_rejected(self):
        a,c=case(['内容。']*10)
        for value in (0,11,True,1.5,'3'):
            with self.assertRaises(ValueError):
                rank_context_proposals(a,c,max_recommendations=value)

    def test_v2_ranks_filtered_generator_without_reintroducing_false_boundary(self):
        a,c=case(['请问']+['这是回答。']*8+['如果','你觉得','不方便的话']+
                 ['可以不作答。']*8+['接下来谈']+['另外一个话题。']*9)
        before=deepcopy((a,c))
        generated=build_context_proposals(a,c,exclude_conditional_cues=True)
        r=rank_context_proposals(a,c,exclude_conditional_cues=True)
        self.assertEqual(r['generator_report_sha256'],content_hash(generated))
        self.assertEqual(r['generator_version'],'context-proposals-v2')
        self.assertEqual(r['version'],'context-review-priority-v2')
        self.assertEqual({p['id'] for p in r['items']},{p['id'] for p in generated['proposals']})
        self.assertEqual(r['excluded_anchors'],generated['excluded_anchors'])
        full=next(p for p in r['items'] if p['start']==0 and p['end']==40)
        self.assertFalse(any(i['code'] in ('question_cue_in_condition','discourse_change_inside')
                             for i in full['context_priority']['issues']))
        self.assertEqual((a,c),before)
        self.assertFalse(r['automatic_acceptance'])

    def test_v2_internal_real_question_remains_a_risk_and_is_not_auto_recommended(self):
        a,c=case(['学习']*15+['好第一个问题']+['学习']*18)
        r=rank_context_proposals(a,c,exclude_conditional_cues=True)
        flagged=[p for p in r['items'] if any(i['code']=='discourse_change_inside'
                                             for i in p['context_priority']['issues'])]
        self.assertTrue(flagged)
        self.assertTrue(all(p['queue_status']=='deferred_context_risk' for p in flagged))
        self.assertTrue(all(p['score'] is None for p in r['items']))

    def test_default_priority_remains_legacy_and_v2_requires_explicit_boolean(self):
        a,c=case(['请问']+['完整回答。']*9)
        old=rank_context_proposals(a,c)
        self.assertEqual(old,rank_context_proposals(a,c,exclude_conditional_cues=False))
        self.assertEqual(old['version'],'context-review-priority-v1')
        self.assertNotIn('generator_version',old)
        for value in (1,None,'true'):
            with self.assertRaises(ValueError):
                rank_context_proposals(a,c,exclude_conditional_cues=value)


if __name__=='__main__':
    unittest.main()
