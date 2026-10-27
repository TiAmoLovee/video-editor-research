"""Build three private, self-contained offline review pages from the frozen public manifest."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

FIELDS = [
    ('opening_complete', '开头完整', '开头未截半句'),
    ('ending_complete', '结尾完整', '结尾未截半句'),
    ('understandable', '能够理解', '脱离原片仍理解'),
    ('no_unrelated_content', '没有无关内容', '未混入无关话题'),
    ('event_coherent', '意思或事件完整', '一个意思或事件讲完整，而非只凑齐首尾'),
    ('accept', '愿意保留', '愿意保留为可用片段；独立判断，不由前五项自动填写'),
]

HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src 'self' file: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>独立看片评测</title><style>
body{font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#172033;background:#f5f7fb}h1{font-size:25px;margin-bottom:5px}h2{font-size:20px}.box{background:white;border:1px solid #dbe2ec;border-radius:10px;padding:18px;margin:16px 0}video{display:block;width:100%;max-height:440px;background:#111}button,.file{border:1px solid #b5c2d5;border-radius:6px;background:white;padding:9px 14px;margin:5px 6px 5px 0;cursor:pointer;font:inherit}button.primary{background:#2055a5;color:white}.choices{display:flex;gap:10px;flex-wrap:wrap}.choices label{border:1px solid #bccbdd;border-radius:6px;padding:8px 14px;cursor:pointer}.choices label:has(input:checked){background:#e5efff;border-color:#2055a5}fieldset{border:0;border-top:1px solid #e3e8ef;padding:15px 0;margin:0}legend{font-weight:600;padding-top:12px}.muted{font-size:14px;color:#526179}.notice{border-left:4px solid #b47c09;padding:10px;background:#fff9e7}.status{min-height:24px;color:#204b88;white-space:pre-wrap}table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}th,td{border:1px solid #d4dce8;padding:8px;text-align:center}input[type=file]{max-width:100%}.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:6px}#nav button.current{background:#dceaff}.small{font-size:13px;overflow-wrap:anywhere}.hidden{display:none}label.confirm{display:block;padding:12px;background:#edf4ff}a{color:#2055a5}
</style><h1 id="title"></h1><p id="scope" class="notice"></p><p id="meta" class="muted"></p><div id="app"></div><p id="status" class="status" role="status"></p>
<script>
'use strict';
const C=__CONFIG__;
const F=C.fields.map(x=>x[0]), V=['yes','no','uncertain'], LABEL={yes:'是',no:'否',uncertain:'不确定'};
const RESULT='week7-dual-review-result-v2';let summary=null;
const app=document.getElementById('app'),status=document.getElementById('status');
function say(s){status.textContent=s;}
function el(tag,text,cls){const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(cls)x.className=cls;return x;}
function button(text,fn,cls){const b=el('button',text,cls);b.type='button';b.onclick=fn;return b;}
function exactKeys(o,keys){return o&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).length===keys.length&&keys.every(k=>Object.hasOwn(o,k));}
function fail(s){throw new Error(s);}
function complete(r){return C.samples.length>0&&r.independence_confirmed===true&&r.items.every(x=>F.every(k=>V.includes(x.answers[k])));}
function validate(r,rater,requireComplete=false){
 if(!exactKeys(r,['schema_version','manifest_schema_version','batch_id','manifest_sha256','scope','campaign_id','batch_index','batch_count','source_count','total_campaign_source_count','selection','review_status','rater','sample_count','items','independence_confirmed','complete','exported_at']))fail('结果文件字段不匹配。');
 if(r.schema_version!==RESULT||r.manifest_schema_version!==C.schema_version)fail('结果版本不匹配。');
 if(r.batch_id!==C.batch_id||r.manifest_sha256!==C.manifest_sha256||r.scope!==C.scope||r.campaign_id!==C.campaign_id||r.batch_index!==C.batch_index||r.batch_count!==C.batch_count||r.source_count!==C.source_count||r.total_campaign_source_count!==C.total_campaign_source_count||r.selection!==C.selection)fail('批次或冻结清单 SHA 不匹配。');
 if(!['A','B'].includes(r.rater)||(rater&&r.rater!==rater))fail('评者编号不匹配；请只导入自己的进度。');
 if(r.sample_count!==C.samples.length||!Array.isArray(r.items)||r.items.length!==C.samples.length)fail('样本数量不匹配。');
 if(typeof r.independence_confirmed!=='boolean'||typeof r.complete!=='boolean'||typeof r.exported_at!=='string'||!Number.isFinite(Date.parse(r.exported_at)))fail('确认项或导出元数据无效。');
 const ids=new Set();
 for(const x of r.items){
  if(!exactKeys(x,['sample_id','sha256','answers'])||ids.has(x.sample_id))fail('样本字段或重复编号无效。');
  ids.add(x.sample_id);const m=C.samples.find(s=>s.sample_id===x.sample_id);
  if(!m||m.sha256!==x.sha256)fail('样本集合或片段哈希不匹配。');
  if(!exactKeys(x.answers,F)||!F.every(k=>x.answers[k]===null||V.includes(x.answers[k])))fail('存在不合法或缺失的题项。');
 }
 if(r.complete!==complete(r))fail('complete 标志与实际完整性不一致。');
 const expectedStatus=C.samples.length===0?'not_applicable':r.complete?'complete':'in_progress';
 if(r.review_status!==expectedStatus)fail('评测状态与样本及完整性不一致。');
 if(C.samples.length===0&&r.independence_confirmed)fail('空组不能标为已完成人评确认。');
 if(requireComplete&&C.samples.length>0&&!r.complete)fail('比较只接受全部题项已答且完成独立观看确认的结果。');
 return r;
}
function empty(rater){return {schema_version:RESULT,manifest_schema_version:C.schema_version,batch_id:C.batch_id,manifest_sha256:C.manifest_sha256,scope:C.scope,campaign_id:C.campaign_id,batch_index:C.batch_index,batch_count:C.batch_count,source_count:C.source_count,total_campaign_source_count:C.total_campaign_source_count,selection:C.selection,review_status:C.samples.length===0?'not_applicable':'in_progress',rater,sample_count:C.samples.length,items:C.samples.map(s=>({sample_id:s.sample_id,sha256:s.sha256,answers:Object.fromEntries(F.map(k=>[k,null]))})),independence_confirmed:false,complete:false,exported_at:new Date().toISOString()};}
function exportFile(name,data){const blob=new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'});const a=el('a');const url=URL.createObjectURL(blob);a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function readJSON(file){if(!file)fail('请选择 JSON 文件。');if(file.size>2*1024*1024)fail('结果文件过大。');try{return JSON.parse(await file.text());}catch{fail('JSON 文件无法读取。');}}
function inputFile(label,fn,onStart=()=>{}){const box=el('div');const l=el('label',label);const input=el('input');input.type='file';input.accept='.json,application/json';l.append(el('br'),input);box.append(l);input.onchange=async()=>{try{onStart();await fn(await readJSON(input.files[0]));}catch(e){say(e.message);}finally{input.value='';}};return box;}
function review(rater){
 let state=empty(rater),index=0;const order=rater==='B'?[...C.samples].reverse():C.samples;
 const key='clipforge:formal-v2:'+C.campaign_id+':'+C.batch_index+':'+C.batch_id+':'+C.manifest_sha256+':'+rater;
 try{const saved=localStorage.getItem(key);if(saved)state=validate(JSON.parse(saved),rater);}catch(e){say('本机进度未能读取；可导入之前导出的个人 JSON。');}
 const toolbar=el('div',undefined,'box toolbar');const progress=el('strong');toolbar.append(progress);
 function store(){state.exported_at=new Date().toISOString();state.complete=complete(state);state.review_status=C.samples.length===0?'not_applicable':state.complete?'complete':'in_progress';try{localStorage.setItem(key,JSON.stringify(state));}catch{say('浏览器不允许本地保存，请导出进度 JSON，之后可导入续填。');}update();}
 toolbar.append(button('导出我的结果 / 进度',()=>{store();exportFile('review-'+rater+'-'+C.campaign_id.slice(0,12)+'-g'+C.batch_index+'-'+C.batch_id.slice(0,12)+'.json',state);say(C.samples.length===0?'已导出不适用空组记录；不计已完成人评。':state.complete?'已导出完整结果。请将文件交给汇总者。':'已导出未完成进度；complete=false，可稍后导入续填。');},'primary'));
 toolbar.append(inputFile('导入我的进度',r=>{state=validate(r,rater);store();render();say('已导入本人进度。');}));app.append(toolbar);
 const nav=el('div',undefined,'box');app.append(nav);const panel=el('div',undefined,'box');app.append(panel);
 const confirmation=el('div',undefined,'box');const lab=el('label',undefined,'confirm');const check=el('input');check.type='checkbox';check.disabled=C.samples.length===0;lab.append(check,document.createTextNode(C.samples.length?' 我确认本组独立观看并填写，未与另一位讨论，也未查看对方答案。':' 本组无可评片段：不适用，不计已完成人评。'));confirmation.append(lab,el('p','全部六项均需独立选择。“愿意保留”不会根据前五项自动填写。','muted'));app.append(confirmation);
 check.onchange=()=>{state.independence_confirmed=check.checked;store();};
 function update(){const n=state.items.filter(x=>F.every(k=>V.includes(x.answers[k]))).length;progress.textContent=C.samples.length===0?'本组无可评片段 · 不适用':'已答完整 '+n+' / '+C.samples.length+' 段';check.checked=state.independence_confirmed;nav.replaceChildren();order.forEach((s,i)=>nav.append(button(s.sample_id+ (F.every(k=>V.includes(state.items.find(x=>x.sample_id===s.sample_id).answers[k]))?' ✓':''),()=>{index=i;render();},i===index?'current':'')));}
 function render(){panel.replaceChildren();if(order.length===0){panel.append(el('h2','本组无可评片段'),el('p','零候选来源已保留在总记录。本组没有题项需要填写；可导出不适用空组记录，不计已完成人评。'));update();return;}const s=order[index],row=state.items.find(x=>x.sample_id===s.sample_id);panel.append(el('h2',s.sample_id+' · '+s.duration_seconds.toFixed(1)+' 秒'));const video=el('video');video.controls=true;video.preload='metadata';video.src=s.media;video.setAttribute('playsinline','');panel.append(video);video.onerror=()=>say('视频未能读取。请将 HTML 与同目录 media 文件夹一起保留，再用浏览器打开。');
 C.fields.forEach(([field,title,definition])=>{const fs=el('fieldset');fs.append(el('legend',title),el('div',definition,'muted'));const options=el('div',undefined,'choices');V.forEach(value=>{const label=el('label');const radio=el('input');radio.type='radio';radio.name=s.sample_id+'-'+field;radio.value=value;radio.checked=row.answers[field]===value;radio.onchange=()=>{row.answers[field]=value;store();};label.append(radio,document.createTextNode(' '+LABEL[value]));options.append(label);});fs.append(options);panel.append(fs);});const controls=el('div',undefined,'toolbar');if(index>0)controls.append(button('上一段',()=>{index--;render();}));if(index<order.length-1)controls.append(button('下一段',()=>{index++;render();}));panel.append(controls);update();}
 render();
}
function kappa(a,b,labels){const n=a.length,m=labels.length;const matrix=Array.from({length:m},()=>Array(m).fill(0));for(let i=0;i<n;i++)matrix[labels.indexOf(a[i])][labels.indexOf(b[i])]++;const rows=matrix.map(r=>r.reduce((x,y)=>x+y,0)),cols=labels.map((_,j)=>matrix.reduce((s,r)=>s+r[j],0));const agree=matrix.reduce((s,r,i)=>s+r[i],0),expected=rows.reduce((s,r,i)=>s+r*cols[i],0),den=n*n-expected;return {labels,denominator:n,matrix,rater_A_counts:Object.fromEntries(labels.map((l,i)=>[l,rows[i]])),rater_B_counts:Object.fromEntries(labels.map((l,i)=>[l,cols[i]])),agreement_count:agree,Po:n===0?null:agree/n,Pe:n===0?null:expected/(n*n),expected_agreement_numerator:expected,expected_agreement_denominator:n*n,kappa:den===0?null:(agree*n-expected)/den,null_reason:n===0?'no_rateable_samples':den===0?'one_minus_Pe_is_zero':null,single_category_margin:{A:rows.filter(x=>x>0).length===1,B:cols.filter(x=>x>0).length===1}};}
function compare(){let left=null,right=null;const imports=el('div',undefined,'box');imports.append(el('p','分别导入 A 和 B 的评测结果。有片段时须完整填写；空组仅接受不适用记录。两份均须通过冻结批次校验。'));const progress=el('p');imports.append(inputFile('导入评者 A JSON',r=>{left=validate(r,'A',true);calculate();},()=>{left=null;summary=null;out.replaceChildren();calculate();}),inputFile('导入评者 B JSON',r=>{right=validate(r,'B',true);calculate();},()=>{right=null;summary=null;out.replaceChildren();calculate();}),progress);app.append(imports);const out=el('div');app.append(out);
 function calculate(){summary=null;out.replaceChildren();progress.textContent='A：'+(left?'已核验':'未导入')+'；B：'+(right?'已核验':'未导入');if(!left||!right)return;if(left.rater===right.rater)fail('必须是不同的 A 与 B。');const paired=C.samples.map(s=>({sample_id:s.sample_id,sha256:s.sha256,A:left.items.find(x=>x.sample_id===s.sample_id).answers,B:right.items.find(x=>x.sample_id===s.sample_id).answers}));const metrics=Object.fromEntries(F.map(field=>[field,kappa(paired.map(x=>x.A[field]),paired.map(x=>x.B[field]),V)]));const binary=kappa(paired.map(x=>x.A.accept==='yes'?'yes':'not_yes'),paired.map(x=>x.B.accept==='yes'?'yes':'not_yes'),['yes','not_yes']);summary={schema_version:'week7-dual-review-comparison-v2',manifest_schema_version:C.schema_version,batch_id:C.batch_id,manifest_sha256:C.manifest_sha256,scope:C.scope,scope_note:C.scope_note,campaign_id:C.campaign_id,batch_index:C.batch_index,batch_count:C.batch_count,source_count:C.source_count,total_campaign_source_count:C.total_campaign_source_count,selection:C.selection,review_status:C.samples.length===0?'not_applicable':'complete',complete:C.samples.length>0,empty_batch:C.samples.length===0,sample_count:C.samples.length,fixed_denominator:C.samples.length,raters:['A','B'],independence_confirmed:{A:left.independence_confirmed,B:right.independence_confirmed},generated_at:new Date().toISOString(),primary_analysis:'nominal_three_category_accept_Cohen_kappa',accept_nominal_three_class:metrics.accept,accept_yes_vs_rest_sensitivity:binary,all_question_nominal_metrics:metrics,uncertain_counts:{A:metrics.accept.rater_A_counts.uncertain,B:metrics.accept.rater_B_counts.uncertain},paired_answers:paired,limits:[C.scope_note,'uncertain保留在三类主分析内；yes-vs-rest只作敏感性分析。','不设通过阈值，不据此宣称总体剪辑质量。']};
 const box=el('div',undefined,'box');box.append(el('h2','本组双人评测汇总'),el('p','固定分母：'+C.samples.length+' 段；不确定不剔除。'));const k=metrics.accept;box.append(el('p','accept 三类 Cohen κ：'+(k.kappa===null?(k.denominator===0?'不计算（本组无可评片段）':'无法计算（1 − Pe = 0）'):k.kappa.toFixed(4))+'；观察一致率 Po：'+(k.Po===null?'不计算':k.Po.toFixed(4))+'；期望一致率 Pe：'+(k.Pe===null?'不计算':k.Pe.toFixed(4))));box.append(el('p',C.samples.length===0?'本组不适用：不计算任何题项比例。':'不确定：A '+k.rater_A_counts.uncertain+' / '+C.samples.length+'；B '+k.rater_B_counts.uncertain+' / '+C.samples.length));
 const table=el('table'),head=el('tr');head.append(el('th','A 行 / B 列'));V.forEach(x=>head.append(el('th',LABEL[x])));table.append(head);V.forEach((x,i)=>{const tr=el('tr');tr.append(el('th',LABEL[x]));V.forEach((_,j)=>tr.append(el('td',String(k.matrix[i][j]))));table.append(tr);});box.append(table);box.append(el('p','yes 对其余两类（敏感性）κ：'+(binary.kappa===null?(binary.denominator===0?'不计算（本组无可评片段）':'无法计算（1 − Pe = 0）'):binary.kappa.toFixed(4)),'muted'));if(k.single_category_margin.A||k.single_category_margin.B)box.append(el('p','提示：有评者仅使用一种类别；仍按标准公式报告，分母为零时才不报告 κ。','muted'));box.append(button('导出汇总 JSON',()=>exportFile('comparison-'+C.campaign_id.slice(0,12)+'-g'+C.batch_index+'-'+C.batch_id.slice(0,12)+'.json',summary),'primary'));box.append(el('p',C.scope_note,'notice'));out.append(box);say(C.samples.length===0?'本组不适用；两份空记录已核验，不计算一致率或κ，也不计已完成人评。':'两份完整结果已按样本编号配对，原始不确定答案已保留。');}
}
document.getElementById('title').textContent=C.mode==='compare'?'正式双人评测 · 汇总':'正式双人评测 · 评者 '+C.mode;
document.getElementById('scope').textContent=C.scope_note;
document.getElementById('meta').textContent='第 '+C.batch_index+' / '+C.batch_count+' 组 · '+C.samples.length+' 段 · 共 '+C.total_duration_seconds.toFixed(1)+' 秒 · '+(C.mode==='compare'?'有片段时只接受完整结果；空组记录不适用，不修改个人答案。':'请独立观看，不与另一位讨论。可中途导出进度。');
if(C.mode==='compare')compare();else review(C.mode);
</script></html>'''


def build(manifest_path):
    path=Path(manifest_path).resolve()
    if path.is_dir():path=path/'manifest.public.json'
    raw=path.read_bytes();m=json.loads(raw.decode('utf-8-sig'))
    assert m['schema_version']=='week7-dual-review-v2' and m['scope']=='formal30_mixed_exposure'
    assert re.fullmatch('[0-9a-f]{64}',m['batch_id'])
    assert isinstance(m['campaign_id'],str) and re.fullmatch('[0-9a-f]{64}',m['campaign_id'])
    assert type(m['batch_index']) is int and 1<=m['batch_index']<=6
    assert type(m['batch_count']) is int and m['batch_count']==6
    assert type(m['source_count']) is int and m['source_count']==5
    assert type(m['total_campaign_source_count']) is int and m['total_campaign_source_count']==30
    assert m['selection']=='frozen_main_default_rule_time_nms_top5'
    assert isinstance(m['scope_note'],str) and 1<=len(m['scope_note'])<=4000
    assert isinstance(m['samples'],list) and 0<=len(m['samples'])<=150
    samples=[];seen=set()
    for row in m['samples']:
        sid=row['sample_id'];assert re.fullmatch('R[0-9]{4}',sid) and 1<=int(sid[1:])<=150 and sid not in seen
        seen.add(sid);assert row['media']==f'media/{sid}.mp4'
        assert re.fullmatch('[0-9a-f]{64}',row['sha256'])
        duration=row['duration_seconds'];assert type(duration) in (int,float) and math.isfinite(duration) and duration>0
        media=path.parent/row['media'];assert media.is_file() and not media.is_symlink() and media.resolve().is_relative_to(path.parent)
        with media.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==row['sha256'],f'Media SHA differs: {sid}'
        samples.append({k:row[k] for k in ('sample_id','media','sha256','duration_seconds')})
    total=m['total_duration_seconds'];assert type(total) in (int,float) and math.isfinite(total) and total>=0 and abs(sum(s['duration_seconds'] for s in samples)-total)<.1
    config={k:m[k] for k in ('schema_version','batch_id','scope','scope_note','campaign_id','batch_index','batch_count','source_count','total_campaign_source_count','selection','total_duration_seconds')}
    config.update(manifest_sha256=hashlib.sha256(raw).hexdigest(),samples=samples,fields=FIELDS)
    files=[]
    for mode,name in (('A','rater-A.html'),('B','rater-B.html'),('compare','compare.html')):
        payload=json.dumps({**config,'mode':mode},ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
        content=HTML.replace('__CONFIG__',payload).encode('utf-8');target=path.parent/name;target.write_bytes(content)
        files.append({'name':name,'sha256':hashlib.sha256(content).hexdigest()})
    instructions='''正式双人评测使用说明

1. 保持三个 HTML 与 media 文件夹在同一目录，不需要启动服务、联网或调用模型。
2. A 打开 rater-A.html，B 打开 rater-B.html。分开观看和填写，不讨论，不查看对方答案。B 的播放顺序固定反向，结果按 R 编号配对。
3. 每段六项均选择“是／否／不确定”，没有默认答案。“愿意保留”须独立判断。
4. 空组结果为not_applicable、complete=false，不计已完成人评，可直接导出空组记录。可随时“导出我的结果 / 进度”；未填完或未勾选独立观看确认时 complete=false。关闭前建议导出备份，重开可导入自己的 JSON 续填。本机浏览器允许时会自动保存进度，A/B 与批次及 manifest SHA 分开存储。
5. 各自填完并勾选独立观看确认后，分别导出 A、B 的完整 JSON，交给汇总者。
6. 汇总者打开 compare.html，分别导入 A 与 B 文件；版本、批次、冻结清单 SHA、片段 SHA、样本集合、完整性和题项均需匹配，才显示统计。可导出汇总 JSON。
7. 主分析为 accept 三类 nominal Cohen κ；不确定保留在固定分母内。yes-vs-rest仅作敏感性分析。仅标准公式1−Pe=0时κ为null，单方单类或双方不同单类仍按公式计算。页面保留Po、Pe、3×3矩阵及各题项原始答案。
8. 本轮为30来源、混合接触情况的正式评测。以冻结协议与scope_note为准，不得把混合接触素材宣称为全部未见；零候选来源不从来源分母中删除。
'''
    (path.parent/'使用说明.txt').write_bytes(instructions.encode('utf-8'))
    print(json.dumps({'manifest_sha256':config['manifest_sha256'],'samples':len(samples),'files':files},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('manifest');build(p.parse_args().manifest)
