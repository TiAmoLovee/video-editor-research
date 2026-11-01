"""Release fixed word targets only after a complete blind reference is submitted.

The first reference file is never edited. Target timing predictions are never
included in the released HTML. No models, media jobs, or network are used.
"""
import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path



def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pairs(values):
    result = {}
    for key, value in values:
        require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result


def read(path):
    require(path.is_file() and not path.is_symlink(), 'Expected ordinary JSON file')
    require(path.stat().st_size <= 2 * 1024 * 1024, 'JSON too large')
    return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))


def timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None
    except ValueError:
        return False


def validate_blind(result, manifest, manifest_sha):
    expected = {'schema_version', 'campaign_id', 'manifest_sha256', 'reviewer',
                'independence_confirmed', 'transcript_excerpts', 'shot_reference',
                'complete', 'exported_at'}
    require(isinstance(result, dict) and set(result) == expected, 'Unexpected blind reference fields')
    require(result['schema_version'] == 'week7-independent-reference-result-v1', 'Wrong blind schema')
    require(result['campaign_id'] == manifest['campaign_id'] and result['manifest_sha256'] == manifest_sha,
            'Blind reference campaign/manifest mismatch')
    require(isinstance(result['reviewer'], str) and 0 < len(result['reviewer'].strip()) <= 80,
            'Reviewer code required')
    require(result['complete'] is True and result['independence_confirmed'] is True,
            'Blind reference is incomplete or independence not declared')
    require(timestamp(result['exported_at']), 'Invalid export timestamp')
    items = result['transcript_excerpts']
    require(isinstance(items, list) and len(items) == 3, 'All three blind excerpts required')
    require([x.get('excerpt_id') for x in items] == [x['excerpt_id'] for x in manifest['transcript_excerpts']],
            'Blind excerpt identity/order mismatch')
    for item, media in zip(items, manifest['transcript_excerpts']):
        require(set(item) == {'excerpt_id', 'media_sha256', 'reference_status', 'text', 'reviewed', 'complete'},
                'Unexpected blind excerpt fields')
        require(item['media_sha256'] == media['sha256'] and item['reviewed'] is True
                and item['complete'] is True, 'Blind excerpt is not completely reviewed')
        text, status = item['text'], item['reference_status']
        require(isinstance(text, str) and len(text) <= 12000, 'Invalid human transcript')
        valid = (status == 'no_speech' and not text.strip()
                 or status == 'complete' and bool(text.strip()) and '[听不清]' not in text
                 or status == 'partially_unintelligible' and bool(text.strip()) and '[听不清]' in text)
        require(valid, 'Human transcript status/text mismatch')
    shot = result['shot_reference']
    require(isinstance(shot, dict) and set(shot) == {'reference_id', 'media_sha256', 'fps',
            'total_frames', 'cut_frames', 'reviewed', 'no_cuts_confirmed', 'notes', 'complete'},
            'Unexpected shot reference fields')
    full = manifest['full_shot_source']
    require(shot['reference_id'] == full['reference_id'] and shot['media_sha256'] == full['sha256']
            and type(shot['fps']) is int and shot['fps'] == 30
            and type(shot['total_frames']) is int and shot['total_frames'] == full['normalized_total_frames'],
            'Full shot video identity/timebase mismatch')
    require(shot['reviewed'] is True and shot['complete'] is True
            and type(shot['no_cuts_confirmed']) is bool, 'Full shot review unfinished')
    cuts = shot['cut_frames']
    require(isinstance(cuts, list) and cuts == sorted(set(cuts))
            and all(type(x) is int and 0 < x < shot['total_frames'] for x in cuts),
            'Invalid internal human cut frames')
    require(bool(cuts) != shot['no_cuts_confirmed'], 'Empty cuts need explicit no-cut confirmation')
    require(isinstance(shot['notes'], str) and len(shot['notes']) <= 4000, 'Invalid shot note')
    return result


HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>第七周 · 固定20词独立时间参考</title>
<style>body{font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif;background:#f3f5f9;color:#253148;margin:0}main{max-width:1000px;margin:32px auto;padding:0 20px}section,.intro{background:#fff;border:1px solid #dce3ed;border-radius:12px;padding:22px;margin:20px 0}h1{font-size:28px}h2{font-size:22px}video{display:block;width:100%;max-height:470px;background:#111}label{display:block;margin:9px 0}input,select,button,textarea{font:inherit}input[type=number]{width:105px;padding:5px}select,button,input[type=text]{padding:6px}button{cursor:pointer;margin:4px;border:1px solid #b6c4d9;border-radius:6px;background:#fff}table{width:100%;border-collapse:collapse}td,th{text-align:left;vertical-align:top;padding:13px 8px;border-bottom:1px solid #e1e6ef}small,.muted{color:#586b84}.scroll{overflow-x:auto}.target{font-size:20px;font-weight:700}textarea{width:95%;min-height:55px}.warn{padding:12px;background:#fff3d7}.error{color:#b3261e}footer{position:sticky;bottom:0;background:#f3f5f9ee;padding:14px;border-top:1px solid #c6d0df}.primary{background:#2155b8;color:white}</style>
<main><h1>第二阶段：20 个固定词的时间参考</h1><div class="intro"><p>第一阶段盲写 JSON 已提交并锁定。这里显示此前按固定位置预选的 20 个模型词，仅用于核对它们的实际发音起止时间；模型预测时间没有展示，也没有填入输入框。</p><p>听原音，填写该词在<strong>当前 20 秒片段</strong>内的开始和结束秒数。上下文只帮助区分同词重复出现的位置，也可能有识别错误。</p><p class="warn">词不对就选“模型词不符合原音”；无法分辨哪个词或听不清就选“无法定位”。这两种情况不用编时间，20 个目标仍完整保留。可定位时填写估计的不确定范围，例如 ±0.05 秒；不要照抄播放器进度作为人工判断。</p><label>标注者代号 <input id="reviewer" type="text" maxlength="80" autocomplete="off"></label><p id="saveNote" class="muted">原音时长共 1 分钟，可反复播放并放慢。</p></div><div id="cards"></div><section><label><input type="checkbox" id="independent"> 我按原音测量，没有查看模型预测起止时间；无法定位或错误词已如实标记</label><p class="muted">所有目标须有状态，含错词和无法定位。未填写完也可下载进度，未完成文件不会记作完整测量。</p></section><footer><button class="primary" id="download">下载20词时间参考 JSON</button><span id="progress"></span><div id="message" role="status"></div></footer></main><script>
'use strict';const C=__CONFIG__;const $=id=>document.getElementById(id);const ids=['reviewer','independent'];const KEY='week7-word-reference:'+C.campaign_id+':'+C.blind_reference_sha256;
function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
function input(id,type){const n=node('input');n.id=id;n.type=type;if(type==='number'){n.min='0';n.max='20';n.step='.001';}ids.push(id);return n;}
function make(){for(const core of C.excerpts){const section=node('section');section.append(node('h2',core.excerpt_id+' · 20 秒原音'));const video=node('video');video.id=core.excerpt_id+'-video';video.src=core.media;video.controls=true;video.preload='metadata';section.append(video);const buttons=node('p');for(const rate of [1,.75,.5]){const b=node('button',rate+' 倍速');b.onclick=()=>{video.playbackRate=rate;};buttons.append(b);}for(const delta of [-.05,-.01,.01,.05]){const b=node('button',(delta>0?'+':'')+delta.toFixed(2)+' 秒');b.onclick=()=>{video.pause();video.currentTime=Math.max(0,Math.min(19.999,video.currentTime+delta));};buttons.append(b);}const time=node('span','播放位置：0.000 秒');video.addEventListener('timeupdate',()=>{time.textContent='播放位置：'+video.currentTime.toFixed(3)+' 秒';});buttons.append(time);section.append(buttons);const scroll=node('div');scroll.className='scroll';const table=node('table');const head=node('tr');for(const t of ['目标及上下文','状态','人工时间与不确定度'])head.append(node('th',t));table.append(head);for(const target of C.targets.filter(x=>x.excerpt_id===core.excerpt_id)){const id=target.target_id;const row=node('tr');const label=node('td');label.append(node('strong',id),node('div','目标词：'+target.target_text),node('small','上下文：'+target.context));const state=node('td');const select=node('select');select.id=id+'-status';ids.push(select.id);for(const [value,text]of [['','请选择'],['locatable','可定位'],['mismatch','模型词不符合原音'],['unlocatable','无法定位']]){const option=node('option',text);option.value=value;select.append(option);}state.append(select);const controls=node('td');for(const [suffix,text] of [['start','开始秒'],['end','结束秒'],['uncertainty','不确定范围 ±秒']]){const l=node('label',text+' ');l.append(input(id+'-'+suffix,'number'));controls.append(l);}for(const [suffix,label]of [['start','用当前播放位置记起点'],['end','用当前播放位置记终点']]){const b=node('button',label);b.onclick=()=>{video.pause();$(id+'-'+suffix).value=video.currentTime.toFixed(3);save();};controls.append(b);}const note=node('textarea');note.id=id+'-note';note.maxLength=1000;ids.push(note.id);note.placeholder='备注（可选）';controls.append(note);row.append(label,state,controls);table.append(row);}scroll.append(table);section.append(scroll);$('cards').append(section);}}
function numberOrNull(id){const s=$(id).value.trim();if(!s)return null;const v=Number(s);if(!Number.isFinite(v))throw Error('时间必须为有限数字。');return v;}
function collect(){const items=C.targets.map(t=>{const id=t.target_id,status=$(id+'-status').value||null,start=numberOrNull(id+'-start'),end=numberOrNull(id+'-end'),uncertainty=numberOrNull(id+'-uncertainty');const numbers=start!==null&&end!==null&&uncertainty!==null&&start>=0&&end>start&&end<=20&&uncertainty>=0&&uncertainty<=20;const absent=start===null&&end===null&&uncertainty===null;return {target_id:id,excerpt_id:t.excerpt_id,original_word_id:t.original_word_id,target_text:t.target_text,reference_status:status,human_start_seconds:start,human_end_seconds:end,uncertainty_seconds:uncertainty,note:$(id+'-note').value,complete:status==='locatable'?numbers:(status==='mismatch'||status==='unlocatable')&&absent};});const independent=$('independent').checked;return {schema_version:'week7-independent-word-timing-result-v1',campaign_id:C.campaign_id,manifest_sha256:C.manifest_sha256,selection_private_sha256:C.selection_private_sha256,blind_reference_sha256:C.blind_reference_sha256,reviewer:$('reviewer').value.trim(),independence_confirmed:independent,fixed_target_denominator:20,items,complete:items.length===20&&items.every(x=>x.complete)&&independent&&$('reviewer').value.trim()!=='',exported_at:new Date().toISOString()};}
function update(){try{const data=collect();$('progress').textContent='已填写 '+data.items.filter(x=>x.complete).length+'/20；'+(data.complete?'完整':'待填');$('message').textContent='';}catch(e){$('message').textContent=e.message;}}
function save(){try{localStorage.setItem(KEY,JSON.stringify(Object.fromEntries(ids.map(id=>[id,$(id).type==='checkbox'?$(id).checked:$(id).value]))));}catch(_){$('saveNote').textContent='浏览器未保存进度，请及时下载 JSON。';}update();}
function restore(){try{const d=JSON.parse(localStorage.getItem(KEY)||'null');if(d)for(const id of ids){const v=d[id];if($(id).type==='checkbox'){if(typeof v==='boolean')$(id).checked=v;}else if(typeof v==='string')$(id).value=v;}}catch(_){}}
function download(){try{const data=collect();const blob=new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'});const url=URL.createObjectURL(blob);const a=node('a');a.href=url;a.download='week7-word-timing-'+C.campaign_id.slice(0,12)+'-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);$('message').textContent=data.complete?'完整参考已下载。':'已下载进度；还未填完。';}catch(e){$('message').textContent=e.message;}}
make();for(const id of ids)$(id).addEventListener('input',save);$('download').onclick=download;restore();update();
</script></html>'''


def build(validation_root, blind_path, output, campaign_root=None):
    root = Path(validation_root).expanduser().resolve()
    require(root.is_dir() and not root.is_symlink(), 'validation-root must be an existing ordinary packet directory')
    pack = root / 'human-reference-pack'
    manifest_path = pack / 'manifest.public.json'
    manifest = read(manifest_path)
    private_path = root / 'new-reference-selection.private.json'
    selection = read(private_path)
    require(selection['new_reference_campaign_id'] == manifest['campaign_id']
            and sha(private_path) == manifest['selection_private_sha256'], 'Selection mismatch')
    manifest_sha = sha(manifest_path)
    result = validate_blind(read(blind_path), manifest, manifest_sha)
    blind_sha = sha(blind_path)
    require(output.parent == pack.resolve() and output.suffix.lower() == '.html',
            'Output HTML must be beside the frozen manifest/media')
    require(not output.exists(), 'Output exists; do not overwrite a released reference form')
    receipt_path = root / 'word-timing-release.private.json'
    require(not receipt_path.exists(), 'A fixed target form was already released; inspect its receipt')
    config = {'schema_version': 'week7-fixed-word-target-release-v1',
              'campaign_id': manifest['campaign_id'], 'manifest_sha256': manifest_sha,
              'selection_private_sha256': sha(private_path), 'blind_reference_sha256': blind_sha,
              'excerpts': manifest['transcript_excerpts'], 'targets': []}
    for core in selection['sources']:
        asr_path = (Path(campaign_root).expanduser().resolve() / 'sources' / core['video_id'] / 'asr.json'
                    if campaign_root is not None else Path(core['normalized_path']).parent / 'asr.json')
        require(sha(asr_path) == core['original_asr_sha256'], 'Frozen ASR target source changed')
        words = read(asr_path)['words']
        positions = {w['id']: i for i, w in enumerate(words)}
        for target in core['predeclared_word_targets']:
            i = positions[target['original_word_id']]
            require(words[i]['text'] == target['predicted_text'], 'Fixed target text changed')
            start, end = target['predicted_start_excerpt_seconds'], target['predicted_end_excerpt_seconds']
            require(math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= 20,
                    'Fixed target is outside the excerpt')
            config['targets'].append({'target_id': target['target_id'],
                'excerpt_id': core['excerpt_id'], 'original_word_id': target['original_word_id'],
                'target_text': target['predicted_text'],
                'context': ''.join(x['text'] for x in words[max(0, i - 3):i + 4])})
    require([t['target_id'] for t in config['targets']] == [f'T{i:02}' for i in range(1, 21)],
            'The fixed twenty targets must all be preserved')
    encoded = json.dumps(config, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    html = HTML.replace('__CONFIG__', encoded)
    require('predicted_start' not in html and 'predicted_end' not in html,
            'Predicted timing must remain hidden')
    require(sha(blind_path) == blind_sha, 'Blind file changed while releasing targets')
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(html)
    receipt = {'schema_version': 'week7-fixed-word-target-release-receipt-v1',
        'released_at': datetime.now(timezone.utc).isoformat(), 'campaign_id': manifest['campaign_id'],
        'blind_reference_sha256': blind_sha, 'blind_exported_at': result['exported_at'],
        'blind_reference_original_modified': False,
        'independence_status': 'User declared in original submitted reference, not externally verified',
        'manifest_sha256': manifest_sha, 'selection_private_sha256': sha(private_path),
        'target_count': 20, 'html_sha256': sha(output), 'generator_sha256': sha(Path(__file__)),
        'predicted_timestamps_disclosed': False, 'target_filtering_after_reference': False}
    with receipt_path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': 'released_after_valid_blind_reference', 'target_count': 20,
                      'page': str(output)}, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validation-root', type=Path, required=True, help='Local accuracy packet root; not included in this repository')
    parser.add_argument('--campaign-root', type=Path, help='Relocated frozen full campaign root for predeclared ASR context')
    parser.add_argument('--blind-reference', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.validation_root, args.blind_reference.resolve(), args.output.resolve(), args.campaign_root)


if __name__ == '__main__':
    main()
