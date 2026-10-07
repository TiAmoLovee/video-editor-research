from copy import deepcopy
import json
import math
import unittest

from backend.tests.test_candidates import fixture
from clipforge.analysis.sentences import split_sentences
from clipforge.decision.candidates import content_hash, generate_candidates
from clipforge.decision.context_proposals import build_context_proposals, discourse_anchors
from clipforge.decision.scoring import score_candidates
from clipforge.decision.word_followup import freeze_word_followup


def case(texts):
    a = fixture([(0,len(texts)*2)])
    a['words'] = [dict(id=f'w{i}',start=i*2,end=(i+1)*2,text=t,probability=.9) for i,t in enumerate(texts)]
    a['sentences'] = split_sentences(a['words'])
    return a, score_candidates(generate_candidates(a),a)


class ContextProposalTests(unittest.TestCase):
    def test_full_context_segments_are_unscored_and_convert_to_valid_word_review(self):
        a,c = case(['请问']+['这个说法需要解释。']*9+['那么我们接下来谈']+['另一种情况。']*9)
        before = deepcopy((a,c))
        r = build_context_proposals(a,c)
        segments = [p for p in r['proposals'] if p['origins'][0]['kind']=='discourse_segment']
        self.assertEqual([(p['start'],p['end']) for p in segments],[(0,20),(20,40)])
        for p in segments:
            self.assertIsNone(p['score'])
            self.assertFalse(p['semantic_completeness_verified'])
            edit=dict(label='synthetic',parent_candidate_id=p['parent_candidate_id'],
                      first_word_id=p['source_words'][0],last_word_id=p['source_words'][-1],reason='synthetic')
            plan=freeze_word_followup(a,c,'synthetic',[edit],dict(parent_batch_id='a'*64,
                parent_manifest_sha256='b'*64,parent_review_sha256='c'*64,reason='synthetic only'))
            self.assertEqual(plan['samples'][0]['start_frame'],p['start_frame'])
            self.assertEqual(plan['samples'][0]['end_frame_exclusive'],p['end_frame_exclusive'])
        self.assertEqual((a,c),before)
        self.assertEqual(r,build_context_proposals(a,c))
        self.assertEqual(r,json.loads(json.dumps(r)))

    def test_cue_inside_asr_token_is_not_used_as_cut(self):
        words=[dict(id='w1',text='前面请问',start=0,end=2),dict(id='w2',text='别的内容',start=2,end=20)]
        self.assertEqual(discourse_anchors(words),[])

    def test_short_segments_abstain_instead_of_padding_past_next_question(self):
        a,c=case(['请问','这是回答。','那么我们接下来谈','另一个回答。']+['没有足够证据。']*5)
        r=build_context_proposals(a,c)
        self.assertTrue(any(i['reason']=='duration_limit' for i in r['abstentions']))
        self.assertFalse(any(p['start']==0 for p in r['proposals']))

    def test_overlong_segment_is_not_truncated_in_middle_to_meet_limit(self):
        a,c=case(['请问']+['这个解释还在继续。']*50)
        r=build_context_proposals(a,c)
        self.assertEqual(r['proposals'],[])
        self.assertEqual(r['abstentions'][0]['reason'],'duration_limit')

    def test_mechanical_expansions_are_bounded_and_never_change_source_candidates(self):
        a,c=case(['学习']*35)
        before=content_hash(c)
        r=build_context_proposals(a,c,max_context_seconds=20)
        expansions=[p for p in r['proposals'] if any(o['kind']=='context_expansion' for o in p['origins'])]
        self.assertTrue(expansions)
        for p in expansions:
            self.assertTrue(15 <= p['duration_seconds'] <= 90)
            for o in p['origins']:
                if o['kind']=='context_expansion':
                    e=o['evidence']
                    self.assertTrue(p['start']<=e['original_start']<e['original_end']<=p['end'])
                    self.assertLessEqual(e['original_start']-p['start']+p['end']-e['original_end'],20)
            self.assertEqual(p['start_frame'],math.floor(p['start']*30+1e-8))
        self.assertEqual(before,content_hash(c))

    def test_expansion_never_adds_a_new_discourse_section(self):
        a,c=case(['学习']*15+['那么我们接下来谈']+['学习']*18)
        r=build_context_proposals(a,c)
        for p in r['proposals']:
            for o in p['origins']:
                if o['kind']=='context_expansion':
                    e=o['evidence']
                    self.assertFalse(any(p['start']<x['start']<e['original_start']
                        or e['original_end']<=x['start']<p['end'] for x in r['anchors']))

    def test_invalid_context_limits_fail(self):
        a,c=case(['内容。']*10)
        for value in (True,0,-1,31,float('nan'),'20'):
            with self.assertRaises(ValueError):
                build_context_proposals(a,c,max_context_seconds=value)


if __name__=='__main__':
    unittest.main()
