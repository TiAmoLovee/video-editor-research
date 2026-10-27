"""Only fabricated fixtures. Never reads real campaign reviews or source statuses."""
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[2]/'tools'/'week7_review'/'summarize_human_reviews.py'
spec=importlib.util.spec_from_file_location('formal_summary',SCRIPT)
summary=importlib.util.module_from_spec(spec); spec.loader.exec_module(summary)
CAMPAIGN='1'*64


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='fabricated-summary-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'campaign'; self.root.mkdir()
        self.answers=Path(self.temp.name)/'answers'; self.answers.mkdir()
        self.create_campaign()

    def create_campaign(self,empty_groups=(),failed_sources=()):
        source_order=[f'V{number:03d}' for number in range(1,31)]
        inventory={'sources':[{'video_id':vid,'source_sha256':hashlib.sha256(vid.encode()).hexdigest()} for vid in source_order]}
        write(self.root/'source-inventory.private.json',inventory)
        protocol={'campaign_id':CAMPAIGN,'source_order':source_order,
            'review_groups':[source_order[start:start+5] for start in range(0,30,5)],
            'source_inventory_sha256':summary.file_hash(self.root/'source-inventory.private.json'),
            'selection':{'version':'boundary-nms-v2','iou_threshold':.5,'top_k':5}}
        write(self.root/'protocol.json',protocol)
        classification={'source_inventory':{'raw_file_sha256':protocol['source_inventory_sha256']},
            'sources':[{'video_id':row['video_id'],'source_sha256':row['source_sha256'],
                'classification':'development_known' if index<21 else 'newly_added_user_attested_distinct'}
                for index,row in enumerate(inventory['sources'])]}
        write(self.root/'source-classification.private.json',classification)
        self.manifests={}; self.results={}
        for index,group in enumerate(protocol['review_groups'],1):
            samples=[]; private=[]; records=[]
            for position,vid in enumerate(group):
                has_sample=index not in empty_groups and position==0 and vid not in failed_sources
                state='failed' if vid in failed_sources else 'completed' if has_sample else 'completed_no_candidates'
                source_sha=next(row['source_sha256'] for row in inventory['sources'] if row['video_id']==vid)
                status={'campaign_id':CAMPAIGN,'video_id':vid,'status':state,'stage':'finished'}
                if state!='failed':status['sample_count']=1 if has_sample else 0
                else:status['failed_stage']='asr'
                write(self.root/'sources'/vid/'status.json',status)
                records.append({'video_id':vid,'source_sha256':source_sha,'source_status':state,
                    'selected_count':None if state=='failed' else 1 if has_sample else 0,
                    'ready_samples':1 if has_sample else 0,'failure_stage':'asr' if state=='failed' else None})
                if has_sample:
                    sid=f'R{(index-1)*25+1:04d}'; data=f'FABRICATED NON-VIDEO {sid}'.encode()
                    media=self.root/'review'/f'G{index:02d}'/'media'/f'{sid}.mp4'
                    media.parent.mkdir(parents=True,exist_ok=True); media.write_bytes(data)
                    row={'sample_id':sid,'sha256':hashlib.sha256(data).hexdigest(),'duration_seconds':16.5,'media':f'media/{sid}.mp4'}
                    samples.append(row)
                    private.append({**row,'video_id':vid,'source_sha256':source_sha,'candidate_id':f'fake-{vid}','original_rank':1})
            identity={'campaign_id':CAMPAIGN,'batch_index':index,'source_ids':group,
                'source_records':records,'samples':samples,'selection':protocol['selection']}
            batch_id=hashlib.sha256(json.dumps(identity,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            manifest={'schema_version':'week7-dual-review-v2','campaign_id':CAMPAIGN,'batch_id':batch_id,
                'batch_index':index,'batch_count':6,'scope':'formal30_mixed_exposure','scope_note':'FABRICATED QA ONLY',
                'source_count':5,'total_campaign_source_count':30,'generated_at':'2026-10-27T00:00:00Z',
                'total_duration_seconds':sum(row['duration_seconds'] for row in samples),
                'selection':'frozen_main_default_rule_time_nms_top5','samples':samples}
            path=self.root/'review'/f'G{index:02d}'/'manifest.public.json'
            write(path,manifest); self.manifests[index]=manifest
            write(self.root/f'group-{index:02d}-mapping.private.json',
                {'campaign_id':CAMPAIGN,'batch_id':batch_id,'batch_index':index,'sources':records,'samples':private})
            for rater in ('A','B'):
                result={key:manifest[key] for key in ('batch_id','scope','campaign_id','batch_index','batch_count',
                    'source_count','total_campaign_source_count','selection')}
                result.update(schema_version='week7-dual-review-result-v2',manifest_schema_version=manifest['schema_version'],
                    manifest_sha256=summary.file_hash(path),rater=rater,sample_count=len(samples),items=[
                        {'sample_id':row['sample_id'],'sha256':row['sha256'],'answers':{field:'yes' for field in summary.FIELDS}}
                        for row in samples],independence_confirmed=bool(samples),complete=bool(samples),
                    review_status='complete' if samples else 'not_applicable',exported_at='2026-10-27T01:00:00Z')
                self.results[(index,rater)]=result
                if samples:write(self.answers/f'g{index}-{rater}.json',result)

    def report(self):
        return summary.summarize(self.root,self.answers)

    def test_complete_pools_raw_rows_instead_of_averaging_group_kappas(self):
        left=['yes','yes','no','no','uncertain','uncertain']
        right=['yes','no','no','uncertain','uncertain','yes']
        for index in range(1,7):
            for rater,values in (('A',left),('B',right)):
                value=deepcopy(self.results[(index,rater)])
                value['items'][0]['answers']['accept']=values[index-1]
                write(self.answers/f'g{index}-{rater}.json',value)
        report=self.report(); metrics=report['statistics']
        self.assertTrue(report['complete']); self.assertEqual(report['fixed_source_denominator'],30)
        self.assertEqual(report['planned_clip_slots'],150); self.assertEqual(report['actual_rateable_clip_count'],6)
        self.assertEqual(report['unfilled_planned_slots'],144)
        self.assertAlmostEqual(metrics['accept_nominal_three_class']['kappa'],.25)
        self.assertEqual(metrics['accept_nominal_three_class']['matrix'],[[1,1,0],[0,1,1],[1,0,1]])
        self.assertEqual(metrics['per_rater']['A']['semantic_complete_five_items']['yes_rate'],1)
        self.assertEqual(metrics['per_rater']['A']['accept']['yes_count'],2)
        self.assertEqual(metrics['per_rater']['B']['uncertain_counts_by_question']['accept'],2)
        self.assertEqual(metrics['accept_nominal_three_class']['denominator'],6)
        self.assertFalse(metrics['group_kappas_averaged'])
        self.assertEqual(report['strata']['development_known']['fixed_source_denominator'],21)
        self.assertEqual(report['strata']['newly_added_user_attested_distinct']['fixed_source_denominator'],9)
        self.assertEqual(report['strata']['newly_added_user_attested_distinct']['statistics']['fixed_clip_denominator'],1)

    def test_missing_one_group_rater_keeps_all_overall_statistics_null(self):
        (self.answers/'g6-B.json').unlink()
        report=self.report()
        self.assertFalse(report['complete']); self.assertEqual(report['status'],'pending')
        self.assertIsNone(report['statistics']); self.assertIsNone(report['paired_answers'])
        self.assertTrue(all(row['statistics'] is None for row in report['strata'].values()))

    def test_in_progress_is_reported_and_does_not_replace_missing_complete(self):
        result=deepcopy(self.results[(6,'B')])
        result.update(independence_confirmed=False,complete=False,review_status='in_progress')
        result['items'][0]['answers']['accept']=None
        write(self.answers/'g6-B.json',result)
        report=self.report()
        self.assertIsNone(report['statistics']); self.assertEqual(len(report['input_audit']['in_progress_exports']),1)
        self.assertEqual(report['human_review_coverage']['completed_nonempty_groups_per_rater']['B'],5)

    def test_other_campaign_and_comparison_exports_are_ignored_and_reported(self):
        result=deepcopy(self.results[(1,'A')]); result['campaign_id']='2'*64
        write(self.answers/'foreign.json',result)
        write(self.answers/'comparison.json',{'schema_version':'week7-dual-review-comparison-v2','campaign_id':CAMPAIGN})
        report=self.report()
        self.assertTrue(report['complete']); self.assertEqual(len(report['input_audit']['ignored_files']),2)

    def test_different_complete_exports_conflict_without_choosing_highest(self):
        result=deepcopy(self.results[(1,'A')]); result['items'][0]['answers']['accept']='no'
        write(self.answers/'g1-A-second.json',result)
        report=self.report()
        self.assertIsNone(report['statistics']); self.assertEqual(len(report['input_audit']['conflicts']),1)
        self.assertEqual(report['status'],'blocked_input_requires_user_selection_or_correction')
        self.assertIn('Select exactly one',report['required_action'])

    def test_identical_answers_with_different_export_timestamp_are_deduplicated(self):
        result=deepcopy(self.results[(1,'A')]); result['exported_at']='2026-10-27T02:00:00Z'
        write(self.answers/'g1-A-copy.json',result)
        report=self.report()
        self.assertTrue(report['complete']); self.assertEqual(len(report['input_audit']['identical_complete_duplicates']),1)
        self.assertEqual(report['statistics']['fixed_clip_denominator'],6)

    def test_exact_result_fields_metadata_question_and_clip_hash_tampering(self):
        mutations=[lambda row:row.update(extra=True),lambda row:row.update(selection='manual'),
            lambda row:row.update(batch_index=True),lambda row:row.update(manifest_sha256='9'*64),
            lambda row:row['items'][0].update(sha256='9'*64),
            lambda row:row['items'][0]['answers'].update(extra='yes'),
            lambda row:row['items'][0]['answers'].update(accept=True),
            lambda row:row.update(review_status='in_progress'),
            lambda row:row.update(rater='C')]
        path=self.answers/'g1-A.json'
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result=deepcopy(self.results[(1,'A')]); mutation(result); write(path,result)
                report=self.report()
                self.assertIsNone(report['statistics']); self.assertEqual(len(report['input_audit']['invalid_files']),1)
        write(path,self.results[(1,'A')])

    def test_empty_group_needs_no_fake_human_exports(self):
        for path in self.answers.glob('*.json'):path.unlink()
        self.create_campaign(empty_groups=(1,))
        report=self.report()
        self.assertTrue(report['coverage_complete']); self.assertEqual(report['actual_rateable_clip_count'],5)
        self.assertEqual(report['groups'][0]['status'],'not_applicable')
        self.assertEqual(report['human_review_coverage']['completed_nonempty_groups_per_rater'],{'A':5,'B':5})
        self.assertEqual(len(report['human_review_coverage']['empty_na_exports_missing_nonblocking']),2)
        self.assertEqual(report['statistics']['fixed_clip_denominator'],5)

    def test_valid_na_export_does_not_count_as_completed_human_review(self):
        for path in self.answers.glob('*.json'):path.unlink()
        self.create_campaign(empty_groups=(1,))
        for rater in ('A','B'):write(self.answers/f'g1-{rater}.json',self.results[(1,rater)])
        report=self.report()
        self.assertTrue(report['coverage_complete']); self.assertEqual(len(report['input_audit']['not_applicable_exports']),2)
        self.assertFalse(report['groups'][0]['raters']['A']['human_review_complete'])

    def test_all_empty_groups_have_null_rates_and_no_completed_human_reviews(self):
        for path in self.answers.glob('*.json'):path.unlink()
        self.create_campaign(empty_groups=range(1,7))
        report=self.report()
        self.assertFalse(report['complete']); self.assertTrue(report['coverage_complete'])
        self.assertEqual(report['status'],'not_applicable_no_rateable_clips')
        self.assertEqual(report['human_review_coverage']['completed_nonempty_groups_per_rater'],{'A':0,'B':0})
        self.assertIsNone(report['statistics']['accept_nominal_three_class']['kappa'])
        self.assertIsNone(report['statistics']['per_rater']['A']['boundary']['yes_rate'])

    def test_source_failures_are_not_fabricated_as_human_rejections(self):
        for path in self.answers.glob('*.json'):path.unlink()
        self.create_campaign(failed_sources=('V001',))
        report=self.report()
        self.assertTrue(report['complete']); self.assertEqual(report['statistics']['fixed_clip_denominator'],5)
        self.assertEqual(report['source_outcomes']['planned_slots_lost_to_source_failure'],5)
        self.assertFalse(report['source_outcomes']['source_failure_is_a_human_rejection'])
        self.assertEqual(report['statistics']['per_rater']['A']['accept']['yes_count'],5)

    def test_all30_ready_and_six_manifests_gate_partial_campaign(self):
        (self.root/'review/G06/manifest.public.json').unlink()
        status=summary.read(self.root/'sources/V030/status.json'); status['status']='running'
        write(self.root/'sources/V030/status.json',status)
        report=self.report()
        self.assertFalse(report['all30_ready']); self.assertFalse(report['all_six_manifests_ready'])
        self.assertIsNone(report['statistics']); self.assertEqual(report['status'],'pending')
        self.assertEqual(report['human_review_coverage']['missing_frozen_manifest_groups'],[6])

    def test_private_slot_mapping_cannot_reassign_source_stratum(self):
        path=self.root/'group-01-mapping.private.json'; mapping=summary.read(path)
        mapping['samples'][0]['video_id']='V022'; write(path,mapping)
        with self.assertRaises(ValueError):self.report()

    def test_manifest_raw_sha_binding_detects_whitespace_changes(self):
        path=self.root/'review/G01/manifest.public.json'
        path.write_text(path.read_text(encoding='utf-8')+'\n',encoding='utf-8')
        report=self.report()
        self.assertIsNone(report['statistics']); self.assertEqual(len(report['input_audit']['invalid_files']),2)

    def test_frozen_clip_bytes_are_verified(self):
        path=next((self.root/'review/G01/media').glob('*.mp4')); path.write_bytes(b'CHANGED FABRICATED CLIP')
        with self.assertRaises(ValueError):self.report()

    def test_standard_kappa_zero_and_degenerate_margins(self):
        self.assertEqual(summary.kappa(['yes']*3,['no']*3)['kappa'],0)
        self.assertEqual(summary.kappa(['yes']*3,['yes','no','uncertain'])['kappa'],0)
        self.assertIsNone(summary.kappa(['yes']*3,['yes']*3)['kappa'])
        self.assertEqual(summary.kappa(['yes']*3,['yes']*3)['null_reason'],'one_minus_Pe_is_zero')
        self.assertEqual(summary.kappa([],[])['null_reason'],'no_rateable_samples')

    def test_cli_requires_new_output_and_never_changes_answers(self):
        before={path.name:path.read_bytes() for path in self.answers.glob('*.json')}
        output=Path(self.temp.name)/'summary.json'
        command=[sys.executable,str(SCRIPT),'--root',str(self.root),'--answers',str(self.answers),'--output',str(output)]
        first=subprocess.run(command,capture_output=True,text=True,timeout=30,check=False)
        self.assertEqual(first.returncode,0,first.stderr)
        saved=output.read_bytes(); second=subprocess.run(command,capture_output=True,text=True,timeout=30,check=False)
        self.assertEqual(second.returncode,2); self.assertEqual(output.read_bytes(),saved)
        self.assertEqual(before,{path.name:path.read_bytes() for path in self.answers.glob('*.json')})

    def test_duplicate_json_fields_are_rejected(self):
        (self.answers/'bad.json').write_text('{"campaign_id":"'+CAMPAIGN+'","campaign_id":"'+CAMPAIGN+'"}',encoding='utf-8')
        report=self.report()
        self.assertIsNone(report['statistics']); self.assertEqual(len(report['input_audit']['invalid_files']),1)



    def test_finished_gate_blocks_source_postcheck_window_and_recovers(self):
        status_path=self.root/'sources/V026/status.json'
        record=summary.read(status_path)
        record['stage']='source_postcheck'
        write(status_path,record)
        _protocol,_sources,_classes,statuses=summary.load_context(self.root)
        self.assertFalse(all(summary.source_terminal(value) for value in statuses.values()))
        # An accidentally early public manifest must fail closed as well.
        with self.assertRaisesRegex(ValueError,'source identity/status mismatch'):
            self.report()
        manifest_path=self.root/'review/G06/manifest.public.json'
        frozen_manifest=manifest_path.read_bytes()
        manifest_path.unlink()
        pending=self.report()
        self.assertFalse(pending['all30_ready'])
        self.assertIsNone(pending['statistics'])
        self.assertIsNone(pending['paired_answers'])
        self.assertTrue(all(value['statistics'] is None for value in pending['strata'].values()))
        record['stage']='finished'; write(status_path,record)
        manifest_path.write_bytes(frozen_manifest)
        (self.answers/'g6-B.json').unlink()
        awaiting_person=self.report()
        self.assertTrue(awaiting_person['all30_ready'])
        self.assertIsNone(awaiting_person['statistics'])
        self.assertEqual(awaiting_person['status'],'pending')
        write(self.answers/'g6-B.json',self.results[(6,'B')])
        complete=self.report()
        self.assertTrue(complete['all30_ready'])
        self.assertTrue(complete['complete'])

    def test_unfinished_running_and_missing_stage_never_form_a_terminal30_set(self):
        records=[{'status':'completed','stage':'finished'} for _ in range(30)]
        self.assertTrue(all(summary.source_terminal(record) for record in records))
        for status in ('completed','completed_no_candidates','failed','running'):
            for stage in ('source_postcheck','asr',None):
                with self.subTest(status=status,stage=stage):
                    changed=[dict(record) for record in records]
                    changed[17]={'status':status}
                    if stage is not None:changed[17]['stage']=stage
                    self.assertFalse(all(summary.source_terminal(record) for record in changed))
        records[17]={'status':'running','stage':'finished'}
        self.assertFalse(all(summary.source_terminal(record) for record in records))

    def test_controller_worker_exit_requires_failed_status_and_integer_returncode(self):
        self.assertTrue(summary.source_terminal({'status':'failed','stage':'worker_exit','returncode':137}))
        for record in ({'status':'failed','stage':'worker_exit'},
                       {'status':'failed','stage':'worker_exit','returncode':True},
                       {'status':'failed','stage':'worker_exit','returncode':'137'},
                       {'status':'completed','stage':'worker_exit','returncode':137},
                       {'status':'running','stage':'worker_exit','returncode':137}):
            with self.subTest(record=record):
                self.assertFalse(summary.source_terminal(record))
        for path in self.answers.glob('*.json'):path.unlink()
        self.create_campaign(failed_sources=('V001',))
        path=self.root/'sources/V001/status.json'; record=summary.read(path)
        record.update(stage='worker_exit',returncode=137)
        write(path,record)
        report=self.report()
        self.assertTrue(report['all30_ready'])
        self.assertTrue(report['complete'])
        self.assertEqual(report['statistics']['fixed_clip_denominator'],5)
        self.assertEqual(report['source_outcomes']['planned_slots_lost_to_source_failure'],5)


if __name__=='__main__':
    unittest.main(verbosity=2)





