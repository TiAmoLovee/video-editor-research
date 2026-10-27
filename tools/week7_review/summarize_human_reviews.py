"""Validate future A/B exports and summarize one frozen formal30 campaign.
No model/network calls. The only output is a new --output JSON file.
"""
import argparse
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ('opening_complete','ending_complete','understandable','no_unrelated_content','event_coherent','accept')
LABELS = ('yes','no','uncertain')
RESULT_KEYS = frozenset(('schema_version','manifest_schema_version','batch_id','manifest_sha256','scope',
    'campaign_id','batch_index','batch_count','source_count','total_campaign_source_count','selection',
    'review_status','rater','sample_count','items','independence_confirmed','complete','exported_at'))
MANIFEST_KEYS = frozenset(('schema_version','campaign_id','batch_id','batch_index','batch_count','scope',
    'scope_note','source_count','total_campaign_source_count','generated_at','total_duration_seconds','selection','samples'))
CLASSIFICATIONS = ('development_known','newly_added_user_attested_distinct')
TERMINAL = {'completed','completed_no_candidates','failed'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_hash(value):
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def json_pairs(pairs):
    value={}
    for key,item in pairs:
        require(key not in value,'Duplicate JSON field: '+key)
        value[key]=item
    return value


def read(path, max_bytes=8*1024*1024):
    path=Path(path)
    require(path.is_file() and not path.is_symlink(),'Expected ordinary JSON file: '+str(path))
    require(path.stat().st_size<=max_bytes,'JSON file exceeds bounded limit: '+str(path))
    def reject_constant(value):
        raise ValueError('Non-finite JSON constant: '+value)
    return json.loads(path.read_text(encoding='utf-8-sig'),object_pairs_hook=json_pairs,parse_constant=reject_constant)


def integer(value, low, high):
    return type(value) is int and low<=value<=high


def is_hash(value):
    return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None


def timestamp(value):
    if not isinstance(value,str):
        return False
    try:
        datetime.fromisoformat(value.replace('Z','+00:00'))
        return True
    except ValueError:
        return False


def kappa(left,right,labels=LABELS):
    require(len(left)==len(right),'Unpaired kappa input')
    n=len(left); positions={name:index for index,name in enumerate(labels)}
    matrix=[[0 for _ in labels] for _ in labels]
    for a,b in zip(left,right):
        require(a in positions and b in positions,'Invalid kappa category')
        matrix[positions[a]][positions[b]]+=1
    rows=[sum(row) for row in matrix]
    columns=[sum(row[index] for row in matrix) for index in range(len(labels))]
    agree=sum(matrix[index][index] for index in range(len(labels)))
    expected=sum(a*b for a,b in zip(rows,columns)); denominator=n*n-expected
    return {'labels':list(labels),'denominator':n,'matrix':matrix,
        'rater_A_counts':dict(zip(labels,rows)),'rater_B_counts':dict(zip(labels,columns)),
        'agreement_count':agree,'Po':agree/n if n else None,'Pe':expected/(n*n) if n else None,
        'expected_agreement_numerator':expected,'expected_agreement_denominator':n*n,
        'kappa':(agree*n-expected)/denominator if denominator else None,
        'null_reason':'no_rateable_samples' if not n else 'one_minus_Pe_is_zero' if not denominator else None,
        'single_category_margin':{'A':sum(value>0 for value in rows)==1,'B':sum(value>0 for value in columns)==1}}


def rater_metrics(paired,rater):
    n=len(paired)
    boundary=sum(all(row[rater][field]=='yes' for field in FIELDS[:2]) for row in paired)
    semantic=sum(all(row[rater][field]=='yes' for field in FIELDS[:5]) for row in paired)
    accept=sum(row[rater]['accept']=='yes' for row in paired)
    uncertain={field:sum(row[rater][field]=='uncertain' for row in paired) for field in FIELDS}
    yes={field:sum(row[rater][field]=='yes' for row in paired) for field in FIELDS}
    return {'fixed_clip_denominator':n,'boundary':{'yes_count':boundary,'yes_rate':boundary/n if n else None},
        'semantic_complete_five_items':{'yes_count':semantic,'yes_rate':semantic/n if n else None},
        'accept':{'yes_count':accept,'yes_rate':accept/n if n else None},
        'question_yes_counts':yes,'question_yes_rates':{field:count/n if n else None for field,count in yes.items()},
        'uncertain_counts_by_question':uncertain,
        'uncertain_rates_by_question':{field:count/n if n else None for field,count in uncertain.items()},
        'clips_with_any_uncertain':sum(any(row[rater][field]=='uncertain' for field in FIELDS) for row in paired)}


def aggregate(paired):
    questions={field:kappa([row['A'][field] for row in paired],[row['B'][field] for row in paired]) for field in FIELDS}
    binary=kappa(['yes' if row['A']['accept']=='yes' else 'not_yes' for row in paired],
                 ['yes' if row['B']['accept']=='yes' else 'not_yes' for row in paired],('yes','not_yes'))
    return {'fixed_clip_denominator':len(paired),'primary_analysis':'pooled_raw_answers_nominal_three_category_accept_Cohen_kappa',
        'accept_nominal_three_class':questions['accept'],'accept_yes_vs_rest_sensitivity':binary,
        'all_question_nominal_metrics':questions,'per_rater':{rater:rater_metrics(paired,rater) for rater in ('A','B')},
        'uncertain_is_excluded':False,'group_kappas_averaged':False}


def validate_manifest(path,protocol,index):
    manifest=read(path)
    require(isinstance(manifest,dict) and set(manifest)==MANIFEST_KEYS,'Unexpected public manifest fields')
    require(manifest['schema_version']=='week7-dual-review-v2' and manifest['scope']=='formal30_mixed_exposure','Wrong manifest schema/scope')
    require(manifest['campaign_id']==protocol['campaign_id'] and is_hash(manifest['batch_id']),'Manifest campaign/batch mismatch')
    for key,value in {'batch_index':index,'batch_count':6,'source_count':5,'total_campaign_source_count':30}.items():
        require(type(manifest[key]) is int and manifest[key]==value,'Invalid manifest identity integer: '+key)
    require(manifest['selection']=='frozen_main_default_rule_time_nms_top5','Wrong frozen selection metadata')
    require(timestamp(manifest['generated_at']) and isinstance(manifest['scope_note'],str) and 1<=len(manifest['scope_note'])<=4000,'Invalid manifest metadata')
    require(isinstance(manifest['samples'],list) and len(manifest['samples'])<=25,'A five-source group can have at most 25 clips')
    seen=set(); total=0
    for sample in manifest['samples']:
        require(isinstance(sample,dict) and set(sample)=={'sample_id','sha256','duration_seconds','media'},'Unexpected public sample fields')
        sid=sample['sample_id']
        require(isinstance(sid,str) and re.fullmatch('R[0-9]{4}',sid) and 1<=int(sid[1:])<=150 and sid not in seen,'Invalid/duplicate sample ID')
        seen.add(sid)
        require(is_hash(sample['sha256']) and sample['media']==f'media/{sid}.mp4','Invalid clip SHA/media identity')
        require(type(sample['duration_seconds']) in (int,float) and math.isfinite(sample['duration_seconds']) and sample['duration_seconds']>0,'Invalid clip duration')
        media=path.parent/sample['media']
        require(media.resolve().is_relative_to(path.parent.resolve()) and not media.is_symlink() and media.is_file(),'Missing/unsafe frozen review media')
        require(file_hash(media)==sample['sha256'],'Frozen review media SHA changed: '+sid)
        total+=sample['duration_seconds']
    require(type(manifest['total_duration_seconds']) in (int,float) and math.isfinite(manifest['total_duration_seconds'])
            and abs(total-manifest['total_duration_seconds'])<.1,'Manifest total duration mismatch')
    return manifest


def validate_result(result,manifest,manifest_sha):
    require(isinstance(result,dict) and set(result)==RESULT_KEYS,'Unexpected result fields; only original individual v2 exports are accepted')
    require(result['schema_version']=='week7-dual-review-result-v2' and result['manifest_schema_version']==manifest['schema_version'],'Wrong individual result schema')
    for key in ('batch_id','scope','campaign_id','selection'):
        require(type(result[key]) is type(manifest[key]) and result[key]==manifest[key],'Result frozen identity mismatch: '+key)
    require(result['manifest_sha256']==manifest_sha,'Result public manifest raw SHA mismatch')
    for key in ('batch_index','batch_count','source_count','total_campaign_source_count'):
        require(type(result[key]) is int and result[key]==manifest[key],'Result integer metadata mismatch: '+key)
    require(result['rater'] in ('A','B'),'Rater must be A or B')
    samples={sample['sample_id']:sample for sample in manifest['samples']}; n=len(samples)
    require(type(result['sample_count']) is int and result['sample_count']==n and isinstance(result['items'],list) and len(result['items'])==n,'Result sample count mismatch')
    require(type(result['independence_confirmed']) is bool and type(result['complete']) is bool and timestamp(result['exported_at']),'Invalid result export/confirmation metadata')
    seen=set()
    for item in result['items']:
        require(isinstance(item,dict) and set(item)=={'sample_id','sha256','answers'},'Unexpected result item fields')
        sid=item['sample_id']
        require(isinstance(sid,str) and sid in samples and sid not in seen,'Wrong/duplicate result sample set')
        seen.add(sid)
        require(item['sha256']==samples[sid]['sha256'],'Result clip SHA differs from frozen manifest')
        require(isinstance(item['answers'],dict) and set(item['answers'])==set(FIELDS),'Wrong/missing/extra question fields')
        require(all(value is None or isinstance(value,str) and value in LABELS for value in item['answers'].values()),'Invalid answer category')
    complete=bool(n>0 and result['independence_confirmed'] and all(item['answers'][field] in LABELS for item in result['items'] for field in FIELDS))
    require(result['complete']==complete,'complete flag differs from actual answers and independence confirmation')
    expected='not_applicable' if n==0 else 'complete' if complete else 'in_progress'
    require(result['review_status']==expected,'review_status differs from actual completion')
    require(n>0 or result['independence_confirmed'] is False,'An empty group cannot claim human review confirmation')
    return result



def source_terminal(record):
    return record.get('status') in TERMINAL and (
        record.get('stage')=='finished' or
        (record.get('status')=='failed' and record.get('stage')=='worker_exit'
         and type(record.get('returncode')) is int))


def load_context(root):
    protocol=read(root/'protocol.json')
    require(is_hash(protocol.get('campaign_id')),'Invalid frozen campaign ID')
    require(file_hash(root/'source-inventory.private.json')==protocol['source_inventory_sha256'],'Inventory differs from frozen protocol')
    inventory=read(root/'source-inventory.private.json')
    source_rows=inventory['sources']
    require(len(source_rows)==30 and len({row['video_id'] for row in source_rows})==30,'Require thirty unique frozen sources')
    sources={row['video_id']:row for row in source_rows}
    order=protocol['source_order']; groups=protocol['review_groups']
    require(len(order)==30 and len(set(order))==30 and set(order)==set(sources),'Invalid frozen source order')
    require(len(groups)==6 and all(isinstance(group,list) and len(group)==5 for group in groups)
            and [item for group in groups for item in group]==order,'Invalid six frozen source groups')
    classification=read(root/'source-classification.private.json')
    require(classification['source_inventory']['raw_file_sha256']==protocol['source_inventory_sha256'],'Classification inventory identity mismatch')
    classes=classification['sources']
    require(len(classes)==30 and {row['video_id'] for row in classes}==set(sources),'Classification must cover exactly the frozen thirty sources')
    classified={}
    for row in classes:
        vid=row['video_id']
        require(vid not in classified and row['source_sha256']==sources[vid]['source_sha256'],'Classification source identity mismatch')
        require(row['classification'] in CLASSIFICATIONS,'Unexpected exposure classification')
        classified[vid]=row['classification']
    require(Counter(classified.values())==Counter({'development_known':21,'newly_added_user_attested_distinct':9}),'Expected separate development21 and newly-added9 strata')
    statuses={}
    for vid in order:
        path=root/'sources'/vid/'status.json'
        if not path.exists():
            statuses[vid]={'video_id':vid,'status':'missing'}
            continue
        record=read(path)
        require(record.get('campaign_id')==protocol['campaign_id'] and record.get('video_id')==vid,'Source status campaign/video mismatch: '+vid)
        require(record.get('status') in TERMINAL|{'running'},'Unexpected source status: '+vid)
        if record['status']=='completed':
            require(integer(record.get('sample_count'),1,5),'Completed source must expose 1-5 review clips')
        if record['status']=='completed_no_candidates':
            require(record.get('sample_count')==0 and type(record.get('sample_count')) is int,'No-candidate source must expose zero clips')
        statuses[vid]=record
    return protocol,sources,classified,statuses


def validate_mapping(root,index,manifest,protocol,sources,statuses):
    mapping=read(root/f'group-{index:02d}-mapping.private.json')
    require(isinstance(mapping,dict) and set(mapping)=={'campaign_id','batch_id','batch_index','sources','samples'},'Unexpected private mapping fields')
    require(mapping['campaign_id']==protocol['campaign_id'] and mapping['batch_id']==manifest['batch_id']
            and type(mapping['batch_index']) is int and mapping['batch_index']==index,'Private mapping campaign/batch mismatch')
    group=protocol['review_groups'][index-1]
    source_records=mapping['sources']
    require(isinstance(source_records,list) and len(source_records)==5
            and [row['video_id'] for row in source_records]==group,'Private group source membership/order mismatch')
    records={}
    for row in source_records:
        vid=row['video_id']
        require(row['source_sha256']==sources[vid]['source_sha256'] and row['source_status']==statuses[vid]['status']
                and source_terminal(statuses[vid]),'Private mapping source identity/status mismatch')
        require(integer(row['ready_samples'],0,5),'Invalid mapping ready count')
        records[vid]=row
    identity={'campaign_id':protocol['campaign_id'],'batch_index':index,'source_ids':group,
              'source_records':source_records,'samples':manifest['samples'],'selection':protocol['selection']}
    batch=hashlib.sha256(json.dumps(identity,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    require(batch==manifest['batch_id'],'Public batch identity is not bound to the frozen source records/selection')
    public={sample['sample_id']:sample for sample in manifest['samples']}
    require(isinstance(mapping['samples'],list) and len(mapping['samples'])==len(public),'Mapping sample count mismatch')
    found={}; counts=Counter(); ranks={}
    for row in mapping['samples']:
        sid=row['sample_id']
        require(sid in public and sid not in found,'Wrong/duplicate private mapping sample set')
        require(all(row[key]==public[sid][key] for key in ('sample_id','media','sha256','duration_seconds')),'Mapping/public clip identity mismatch')
        vid=protocol['source_order'][(int(sid[1:])-1)//5]
        require(row['video_id']==vid and vid in group and row['source_sha256']==sources[vid]['source_sha256'],'Anonymous slot/source identity mismatch')
        require(integer(row['original_rank'],1,10000) and isinstance(row['candidate_id'],str) and row['candidate_id'],'Invalid private candidate identity')
        found[sid]=row; counts[vid]+=1
        ranks.setdefault(vid,[]).append((int(sid[1:]),row['original_rank']))
    for vid,row in records.items():
        require(counts[vid]==row['ready_samples'],'Private source/sample count mismatch')
        if row['source_status']=='completed':
            require(counts[vid]==statuses[vid]['sample_count'] and row['selected_count']==counts[vid],'Completed source cohort count mismatch')
        else:
            require(counts[vid]==0,'Failed/empty sources must not create review clips')
        ordered=sorted(ranks.get(vid,[]))
        require([rank for _,rank in ordered]==sorted({rank for _,rank in ordered}),'Private cohort must retain ascending distinct original ranks')
    return found


def summarize(root,answers_dir):
    root=Path(root).resolve(); answers_dir=Path(answers_dir).resolve()
    require(answers_dir.is_dir() and not answers_dir.is_symlink(),'--answers must be an existing ordinary directory')
    protocol,sources,classes,statuses=load_context(root)
    campaign=protocol['campaign_id']; manifests={}; mapping={}; missing=[]; group_reports=[]
    for index in range(1,7):
        path=root/'review'/f'G{index:02d}'/'manifest.public.json'
        if not path.exists():
            missing.append(index)
            continue
        manifest=validate_manifest(path,protocol,index)
        links=validate_mapping(root,index,manifest,protocol,sources,statuses)
        require(not set(mapping)&set(links),'Sample IDs overlap between frozen groups')
        mapping.update(links)
        manifests[index]={'manifest':manifest,'manifest_sha256':file_hash(path)}
    complete_records={}; progress=[]; na_records=[]; ignored=[]; invalid=[]; waiting=[]
    answer_paths=sorted(answers_dir.rglob('*.json'),key=lambda path:str(path).casefold())
    require(len(answer_paths)<=5000,'Too many answer JSON files; select the intended export folder')
    for path in answer_paths:
        display=str(path.relative_to(answers_dir))
        try:
            result=read(path,2*1024*1024)
            require(isinstance(result,dict),'Result root must be a JSON object')
            if isinstance(result.get('campaign_id'),str) and result['campaign_id']!=campaign:
                ignored.append({'file':display,'reason':'other_campaign','campaign_id':result['campaign_id']})
                continue
            if result.get('schema_version')=='week7-dual-review-comparison-v2':
                ignored.append({'file':display,'reason':'comparison_is_not_an_original_individual_review'})
                continue
            require(result.get('campaign_id')==campaign,'Missing/invalid current campaign identity')
            index=result.get('batch_index')
            require(integer(index,1,6),'Invalid result group index')
            if index not in manifests:
                waiting.append({'file':display,'batch_index':index,'reason':'frozen_group_manifest_not_yet_available'})
                continue
            spec=manifests[index]
            validate_result(result,spec['manifest'],spec['manifest_sha256'])
            record={'file':display,'raw_sha256':file_hash(path),'batch_index':index,'rater':result['rater'],
                    'exported_at':result['exported_at'],'review_status':result['review_status']}
            if result['review_status']=='in_progress':
                progress.append(record)
            elif result['review_status']=='not_applicable':
                na_records.append(record)
            else:
                fingerprint=canonical_hash({'rater':result['rater'],'items':sorted(result['items'],key=lambda item:item['sample_id'])})
                complete_records.setdefault((index,result['rater']),{}).setdefault(fingerprint,[]).append((record,result))
        except (OSError,ValueError,TypeError,KeyError,IndexError) as error:
            invalid.append({'file':display,'reason':str(error),'error_type':type(error).__name__})
    chosen={}; conflicts=[]; duplicates=[]
    for (index,rater),variants in sorted(complete_records.items()):
        if len(variants)>1:
            conflicts.append({'batch_index':index,'rater':rater,'reason':'multiple_different_complete_results_select_one',
                'alternatives':[{'semantic_sha256':fingerprint,'files':[record['file'] for record,_ in exports]}
                                for fingerprint,exports in sorted(variants.items())]})
            continue
        fingerprint,exports=next(iter(variants.items()))
        record,result=exports[0]
        chosen[(index,rater)]=(record,result)
        if len(exports)>1:
            duplicates.append({'batch_index':index,'rater':rater,'semantic_sha256':fingerprint,
                               'files':[entry['file'] for entry,_ in exports],'policy':'identical answers deduplicated; no score-based choice'})
    paired=[]; pending_reviews=[]; empty_na_missing=[]; nonempty_groups=0; complete_group_count=0
    for index in range(1,7):
        if index not in manifests:
            group_reports.append({'batch_index':index,'status':'pending_frozen_manifest','sample_count':None})
            continue
        spec=manifests[index]; manifest=spec['manifest']; count=len(manifest['samples'])
        group={'batch_index':index,'batch_id':manifest['batch_id'],'manifest_sha256':spec['manifest_sha256'],
               'sample_count':count,'raters':{}}
        if not count:
            group['status']='not_applicable'
            for rater in ('A','B'):
                records=[row for row in na_records if row['batch_index']==index and row['rater']==rater]
                group['raters'][rater]={'status':'not_applicable','na_export_files':[row['file'] for row in records],'human_review_complete':False}
                if not records:empty_na_missing.append({'batch_index':index,'rater':rater})
            group_reports.append(group)
            continue
        nonempty_groups+=1
        for rater in ('A','B'):
            if (index,rater) in chosen:
                group['raters'][rater]={'status':'complete','file':chosen[(index,rater)][0]['file'],'human_review_complete':True}
            elif (index,rater) in complete_records:
                group['raters'][rater]={'status':'conflicting_complete_results','human_review_complete':False}
            else:
                group['raters'][rater]={'status':'pending','human_review_complete':False}
                pending_reviews.append({'batch_index':index,'rater':rater})
        if all((index,rater) in chosen for rater in ('A','B')):
            group['status']='complete'; complete_group_count+=1
            by_rater={rater:{item['sample_id']:item['answers'] for item in chosen[(index,rater)][1]['items']} for rater in ('A','B')}
            for sample in manifest['samples']:
                sid=sample['sample_id']; vid=mapping[sid]['video_id']
                paired.append({'sample_id':sid,'sha256':sample['sha256'],'batch_index':index,
                               'video_id':vid,'exposure_stratum':classes[vid],'A':by_rater['A'][sid],'B':by_rater['B'][sid]})
        else:
            group['status']='conflict' if any(row['status']=='conflicting_complete_results' for row in group['raters'].values()) else 'pending'
        group_reports.append(group)
    all30_ready=all(source_terminal(record) for record in statuses.values())
    coverage_complete=bool(len(manifests)==6 and all30_ready and not pending_reviews and not conflicts and not invalid and not waiting)
    actual=sum(len(spec['manifest']['samples']) for spec in manifests.values())
    require(actual<=150,'Frozen actual clips exceed the fixed 150 planned slots')
    pooled=aggregate(paired) if coverage_complete else None
    strata={}
    for label in CLASSIFICATIONS:
        vids=[vid for vid,value in classes.items() if value==label]
        subset=[row for row in paired if row['exposure_stratum']==label]
        strata[label]={'fixed_source_denominator':len(vids),'planned_clip_slots':len(vids)*5,
            'actual_published_clip_count':sum(row['video_id'] in vids for row in mapping.values()),
            'source_status_counts':dict(Counter(statuses[vid]['status'] for vid in vids)),
            'statistics':aggregate(subset) if coverage_complete else None,
            'interpretation':'descriptive mixed-exposure stratum; no random-sample or all-unseen claim'}
    status_counts=Counter(record['status'] for record in statuses.values())
    blocked=bool(conflicts or invalid)
    report={'schema_version':'week7-formal30-human-summary-v1','campaign_id':campaign,
        'generated_at':datetime.now(timezone.utc).isoformat(),'scope':'formal30_mixed_exposure',
        'status':'blocked_input_requires_user_selection_or_correction' if blocked else
                 'complete' if coverage_complete and actual>0 else
                 'not_applicable_no_rateable_clips' if coverage_complete else 'pending',
        'all30_ready':all30_ready,'all_six_manifests_ready':len(manifests)==6,
        'complete':coverage_complete and actual>0,'coverage_complete':coverage_complete,
        'fixed_source_denominator':30,'planned_clip_slots':150,'actual_rateable_clip_count':actual,
        'actual_count_final':len(manifests)==6 and all30_ready,
        'unfilled_planned_slots':150-actual if len(manifests)==6 and all30_ready else None,
        'source_outcomes':{'status_counts':dict(status_counts),
            'failed_sources':[{'video_id':vid,'failed_stage':record.get('failed_stage')} for vid,record in statuses.items() if record['status']=='failed'],
            'no_candidate_sources':[vid for vid,record in statuses.items() if record['status']=='completed_no_candidates'],
            'pending_sources':[vid for vid,record in statuses.items() if record['status'] not in TERMINAL],
            'planned_slots_lost_to_source_failure':5*status_counts['failed'],
            'planned_slots_unfilled_no_candidates':5*status_counts['completed_no_candidates'],
            'planned_slots_unfilled_insufficient_candidates':sum(5-record['sample_count'] for record in statuses.values() if record['status']=='completed'),
            'source_failure_is_a_human_rejection':False},
        'human_review_coverage':{'expected_nonempty_groups_per_rater':nonempty_groups if len(manifests)==6 else None,
            'completed_nonempty_groups_per_rater':{rater:sum((index,rater) in chosen for index in manifests if manifests[index]['manifest']['samples']) for rater in ('A','B')},
            'completed_paired_nonempty_groups':complete_group_count,'pooled_paired_rows_available':len(paired),
            'empty_groups_not_counted_as_completed_human_reviews':6-nonempty_groups if len(manifests)==6 else None,
            'empty_na_exports_missing_nonblocking':empty_na_missing,'pending_reviews':pending_reviews,'missing_frozen_manifest_groups':missing},
        'groups':group_reports,'statistics':pooled,'strata':strata,
        'paired_answers':paired if coverage_complete else None,
        'input_audit':{'individual_complete_semantic_records':len(chosen),'in_progress_exports':progress,
            'not_applicable_exports':na_records,'identical_complete_duplicates':duplicates,'conflicts':conflicts,
            'invalid_files':invalid,'ignored_files':ignored,'answers_waiting_for_manifest':waiting},
        'required_action':'Select exactly one complete answer set per conflicted group/rater; move unchosen alternatives out of --answers, and rerun with a new --output file.' if conflicts else
            'Correct or remove the listed invalid files; rerun with a new --output file.' if invalid else
            'Collect both raters for every nonempty frozen group; no overall rates or kappa are published while pending.' if not coverage_complete else None,
        'limits':['The fixed source denominator is 30; the 150 planned slots and actual rateable clips are separate.',
            'Failures and missing candidates remain source outcomes and are never fabricated as human no answers.',
            'Nominal three-category accept kappa pools all raw paired rows, including uncertain; group kappas are not averaged.',
            'The yes-versus-rest result is a sensitivity analysis only.',
            'Development-known21 and newly-added9 are descriptive strata; this is not an all-unseen holdout or a random independent sample.',
            'No rateable human answers are invented for empty groups. No model calls, network requests or answer-file modifications are performed.']}
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--answers',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    output=args.output.resolve()
    if output.exists():
        parser.error('--output must name a new file; existing files are never overwritten')
    try:
        report=summarize(args.root,args.answers)
        with output.open('x',encoding='utf-8',newline='\n') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2,allow_nan=False); stream.write('\n')
    except (OSError,ValueError,KeyError,TypeError,IndexError) as error:
        parser.exit(2,f'SUMMARY NOT CREATED: {type(error).__name__}: {error}\n')
    print(json.dumps({'status':report['status'],'complete':report['complete'],
                     'fixed_source_denominator':30,'planned_clip_slots':150,
                     'actual_rateable_clip_count':report['actual_rateable_clip_count'],
                     'statistics_published':report['statistics'] is not None},ensure_ascii=False))
    return 2 if report['status']=='blocked_input_requires_user_selection_or_correction' else 0


if __name__=='__main__':
    raise SystemExit(main())

