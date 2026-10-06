"""Freeze and render a complete time-NMS cohort for human review, without model calls."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess

from clipforge.decision.candidates import content_hash
from clipforge.decision.reviewed import file_hash
from clipforge.decision.selection import build_selection

VERSION = 'human-evaluation-batch-v1'
QUESTIONS = ('opening_complete', 'ending_complete', 'understandable', 'no_unrelated_content')
ANSWERS = ('yes', 'no', 'uncertain')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def freeze(analysis, scored, source_name):
    report = build_selection(scored, analysis)
    keep = {i['candidate_id'] for i in report['items'] if i['retained']}
    selected = sorted((c for c in scored['candidates'] if c['id'] in keep), key=lambda c: c['id'])
    samples = []
    for index, c in enumerate(selected, 1):
        first, last = math.floor(c['start']*30+1e-8), math.ceil(c['end']*30-1e-8)
        samples.append({'id': f'S{index:02}', 'candidate_id': c['id'], 'original_rank': c['rank'],
                        'original_score': c['score'], 'scorer': c['scorer'],
                        'original_start': c['start'], 'original_end': c['end'],
                        'start_frame': first, 'end_frame_exclusive': last, 'fps': 30,
                        'duration_seconds': (last-first)/30, 'source_sentences': c['source_sentences'],
                        'text': c['text'], 'file': f'S{index:02}.mp4'})
    plan = {'version': VERSION, 'source_name': source_name,
            'source_sha256': analysis['media']['source_sha256'],
            'normalized_sha256': analysis['media']['normalized_sha256'],
            'analysis_sha256': content_hash(analysis), 'candidates_sha256': content_hash(scored),
            'selection_version': report['version'], 'selection_config': report['config'],
            'cohort': 'all_time_nms_survivors_before_topic_folding',
            'presentation_order': 'candidate_id_ascending; scores hidden in review UI',
            'scope': 'single-source pilot; overlapping windows; not overall week4 acceptance',
            'denominator': len(samples), 'required_passes': math.ceil(len(samples)*.85),
            'metric': 'opening_complete=yes AND ending_complete=yes; uncertain is not pass',
            'rounding': 'outward <= one frame; no editorial extension; 540p review copies',
            'samples': samples, 'model_requests': 0}
    return {'batch_id': content_hash(plan), **plan}


def summary(manifest, reviews):
    n = manifest['denominator']
    if set(reviews) - {s['id'] for s in manifest['samples']}:
        raise ValueError('出现不属于本批次的评审')
    for entry in reviews.values():
        validate_answers(entry['answers'], entry['note'])
    boundary = sum(r['answers']['opening_complete'] == r['answers']['ending_complete'] == 'yes'
                   for r in reviews.values())
    usable = sum(all(r['answers'][q] == 'yes' for q in QUESTIONS) for r in reviews.values())
    complete = n > 0 and len(reviews) == n
    return {'denominator': n, 'reviewed': len(reviews), 'pending': n-len(reviews),
            'boundary_pass': boundary, 'usable_pass': usable,
            'uncertain_reviews': sum('uncertain' in r['answers'].values() for r in reviews.values()),
            'complete': complete, 'boundary_rate': boundary/n if complete else None,
            'usable_rate': usable/n if complete else None,
            'pilot_meets_85_percent': boundary/n >= .85 if complete else None,
            'overall_week4_acceptance': 'not_established'}


def validate_answers(answers, note):
    if (not isinstance(answers, dict) or set(answers) != set(QUESTIONS)
            or any(type(v) is not str or v not in ANSWERS for v in answers.values())
            or not isinstance(note, str) or len(note) > 1000):
        raise ValueError('请完成四项判断；备注不能超过 1000 字')


def probe(path, ffprobe):
    return json.loads(subprocess.run([ffprobe, '-v', 'error', '-count_frames', '-show_streams',
                                      '-of', 'json', str(path)], check=True, capture_output=True,
                                     encoding='utf-8', timeout=180).stdout)


def prepare(analysis, scored, source, output, source_name, ffmpeg, ffprobe):
    manifest = freeze(analysis, scored, source_name)
    if not manifest['denominator']:
        raise ValueError('没有可试听的候选，不创建空评测批次')
    if file_hash(source) != manifest['normalized_sha256']:
        raise ValueError('归一化视频哈希不匹配')
    output.mkdir(parents=True, exist_ok=False)
    (output/'plan.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    source_meta = probe(source, ffprobe)
    video = next(s for s in source_meta['streams'] if s['codec_type'] == 'video')
    audio = next((s for s in source_meta['streams'] if s['codec_type'] == 'audio'), None)
    if video['avg_frame_rate'] != '30/1':
        raise ValueError('试听要求已归一化为 30 fps 的视频')
    evidence = {}
    for item in manifest['samples']:
        first, last = item['start_frame'], item['end_frame_exclusive']
        if last > int(video['nb_read_frames']):
            raise ValueError('候选帧范围超出源视频')
        target = output/item['file']
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-i', str(source),
                   '-map', '0:v:0', '-vf', f'trim=start_frame={first}:end_frame={last},setpts=PTS-STARTPTS,scale=-2:540',
                   '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-pix_fmt', 'yuv420p', '-threads', '2', '-fps_mode', 'cfr']
        if audio:
            rate = int(audio['sample_rate'])
            command += ['-map', '0:a:0', '-af', f'atrim=start_sample={round(first*rate/30)}:end_sample={round(last*rate/30)},asetpts=PTS-STARTPTS',
                        '-c:a', 'aac', '-b:a', '128k', '-ar', str(rate)]
        command += ['-movflags', '+faststart', str(target)]
        subprocess.run(command, check=True, capture_output=True, timeout=600)
        actual = probe(target, ffprobe)
        v = next(s for s in actual['streams'] if s['codec_type'] == 'video')
        a = next((s for s in actual['streams'] if s['codec_type'] == 'audio'), None)
        if (int(v['nb_read_frames']) != last-first or v['avg_frame_rate'] != '30/1'
                or abs(float(v['start_time'])) > .001
                or (audio and (a is None or abs(float(a['duration'])-item['duration_seconds']) > .04
                               or abs(float(a['start_time'])) > .001))):
            raise ValueError('试听文件帧数或音画时长校验失败')
        evidence[item['id']] = {'file': item['file'], 'sha256': file_hash(target),
                                'frames': int(v['nb_read_frames']), 'duration_seconds': item['duration_seconds'],
                                'audio_duration_seconds': float(a['duration']) if a else None}
        print(f"Rendered {item['id']} ({len(evidence)}/{manifest['denominator']})", flush=True)
    # A batch becomes reviewable only after every exact-range copy has been verified.
    manifest['renders'] = evidence
    manifest['prepared_at'] = datetime.now(timezone.utc).isoformat()
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('analysis', 'candidates', 'source', 'output'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--source-name', required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--ffprobe', default='ffprobe')
    args = parser.parse_args()
    prepare(read(args.analysis), read(args.candidates), args.source, args.output, args.source_name, args.ffmpeg, args.ffprobe)


if __name__ == '__main__':
    main()
