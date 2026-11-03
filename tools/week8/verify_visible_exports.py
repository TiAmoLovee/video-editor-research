"""Real decoded-pixel QA for EDL labels; synthetic media, never old evidence.

Requires numpy and an FFmpeg build with libass. The pixel test detects a
high-contrast overlay in an otherwise blank ROI; contact sheets still need a
human check of the exact Chinese lettering. It does not use metadata as proof
that pixels were labeled, and it independently decodes source-frame barcodes.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from clipforge.editing.edl import candidates_to_edl, create_portrait_edl  # noqa: E402
from clipforge.editing.manifest import build_media_manifest  # noqa: E402
from clipforge.editing.render import render_edl  # noqa: E402
from clipforge.editing.subtitles import build_subtitles  # noqa: E402

WIDTH, HEIGHT, FPS, SOURCE_FRAMES = 640, 360, 30, 300
RANGES = [(0, 90), (150, 240)]


def run(command, timeout=120):
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if process.returncode:
        raise RuntimeError(process.stderr.decode('utf-8', errors='replace')[-6000:])
    return process.stdout


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def write_png(path, pixels):
    """Write a lossless grayscale screenshot without an imaging dependency."""
    height, width = pixels.shape
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + row.tobytes() for row in pixels.astype(np.uint8))
    with Path(path).open('xb') as output:
        output.write(b'\x89PNG\r\n\x1a\n')
        output.write(chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)))
        output.write(chunk(b'IDAT', zlib.compress(rows)))
        output.write(chunk(b'IEND', b''))


def source_video(path, level, ffmpeg):
    expression = ("if(between(Y,170,189)*between(X,240,399),"
                  f"if(bitand(N,pow(2,floor((X-240)/16))),220,32),{level})")
    graph = (f"color=black:size={WIDTH}x{HEIGHT}:rate={FPS}:duration=10,"
             f"geq=lum='{expression}':cb=128:cr=128,setsar=1,"
             "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709")
    run([ffmpeg, '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i', graph,
         '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=10',
         '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '0', '-pix_fmt', 'yuv420p',
         '-threads', '2', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
         '-c:a', 'aac', '-ar', '48000', '-shortest', str(path)])


def decode(path, width, height, ffmpeg):
    data = run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(path), '-map', '0:v:0',
                '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1'])
    if len(data) % (width * height):
        raise AssertionError('decoded bytes do not comprise complete video frames')
    return np.frombuffer(data, dtype=np.uint8).reshape(-1, height, width)


def overlay_counts(frames):
    _, height, width = frames.shape
    # Independent broad ROI, away from the center barcode and bottom captions.
    top, bottom = 0, max(2, int(height * .18))
    left, right = width // 2, max(width // 2 + 1, int(width * .98))
    roi = frames[:, top:bottom, left:right].astype(np.int16)
    background = np.median(frames[:, :bottom, :max(2, width // 4)], axis=(1, 2))
    changed = np.count_nonzero(np.abs(roi - background[:, None, None]) > 20, axis=(1, 2))
    spread = np.max(roi, axis=(1, 2)) - np.min(roi, axis=(1, 2))
    return changed, spread, (left, top, right, bottom)


def marker_points(project):
    points, row = [248 + 16 * bit for bit in range(10)], 180
    if project['schema_version'] == '0.2.0':
        rectangle = project['framing']['sources'][0]
        points = [int((point - rectangle['x']) * project['profile']['width'] / rectangle['width'])
                  for point in points]
        row = int((row - rectangle['y']) * project['profile']['height'] / rectangle['height'])
    elif project['schema_version'] == '0.3.0':
        rectangle = project['framing']['sources'][0]
        points = [int(rectangle['x'] + point * rectangle['width'] / WIDTH) for point in points]
        row = int(rectangle['y'] + row * rectangle['height'] / HEIGHT)
    return points, row


def inspect_pixels(path, project, ffmpeg, ffprobe, shots):
    profile = project['profile']
    width, height = profile['width'], profile['height']
    frames = decode(path, width, height, ffmpeg)
    expected = [index for clip in project['clips']
                for index in range(clip['source_in_frame'], clip['source_out_frame'])]
    if len(frames) != len(expected):
        raise AssertionError(f'{path.name}: decoded frame count differs')
    points, row = marker_points(project)
    observed = np.sum((frames[:, row, points] >= 128) * (1 << np.arange(10)), axis=1).tolist()
    if observed != expected:
        raise AssertionError(f'{path.name}: decoded source-frame barcode sequence differs')
    counts, spreads, roi = overlay_counts(frames)
    if not np.all(counts >= 12) or not np.all(spreads >= 90):
        raise AssertionError(f'{path.name}: visible ROI lacks contrast on one or more frames')
    selected = {0, len(frames) // 2, len(frames) - 1}
    for clip in project['clips'][1:]:
        selected.update((clip['timeline_in_frame'] - 1, clip['timeline_in_frame']))
    selected = sorted(selected)
    screenshots = []
    for index in selected:
        name = f'{path.stem}-frame-{index:04d}.png'
        write_png(shots / name, frames[index])
        screenshots.append({'frame': index, 'source_frame': expected[index], 'file': 'frames/' + name})
    facts = json.loads(run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
    audio = any(stream['codec_type'] == 'audio' for stream in facts['streams'])
    expected_audio = any(source['audio_stream_index'] is not None for source in project['sources'])
    if audio != expected_audio:
        raise AssertionError(f'{path.name}: audio presence changed')
    return {'file': path.name, 'sha256': digest(path), 'frames_checked': len(frames),
            'profile': profile, 'audio_present': audio, 'source_frame_sequence_matches': True,
            'decoded_source_frames': observed, 'roi': list(roi), 'roi_difference_threshold': 20,
            'minimum_changed_roi_pixels': int(counts.min()), 'minimum_roi_contrast': int(spreads.min()),
            'overlay_present_on_all_decoded_frames': True,
            'pixel_check_is_ocr_or_legal_certification': False, 'screenshots': screenshots,
            'metadata_for_separate_inspection': facts['format'].get('tags', {})}, [frames[i].copy() for i in selected]


def contact_sheet(rows, path):
    # Plain grid: row/column labels are kept in JSON and HTML alongside the PNG.
    cell_w, cell_h = 320, 200
    columns = max(len(row) for row in rows)
    sheet = np.full((len(rows) * cell_h, columns * cell_w), 100, dtype=np.uint8)
    for r, images in enumerate(rows):
        for c, pixels in enumerate(images):
            height, width = pixels.shape
            scale = min((cell_w - 8) / width, (cell_h - 8) / height)
            out_w, out_h = max(1, int(width * scale)), max(1, int(height * scale))
            sampled = pixels[np.minimum((np.arange(out_h) / scale).astype(int), height - 1)[:, None],
                             np.minimum((np.arange(out_w) / scale).astype(int), width - 1)[None, :]]
            y, x = r * cell_h + (cell_h - out_h) // 2, c * cell_w + (cell_w - out_w) // 2
            sheet[y:y + out_h, x:x + out_w] = sampled
    write_png(path, sheet)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--ffprobe', required=True)
    parser.add_argument('--font-file', required=True)
    parser.add_argument('--font-name', required=True)
    parser.add_argument('--shape', action='append', choices=('landscape', 'portrait', 'fit_pad'),
                        help='repeat to narrow the rendering geometry; default is all three')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources, shots = output / 'sources', output / 'frames'
    sources.mkdir()
    shots.mkdir()
    os.environ.update(SUBTITLE_FONT_FILE=args.font_file, SUBTITLE_FONT_NAME=args.font_name)
    original = (ROOT / 'docs/samples/analysis.example.json').read_bytes()
    evidence = {'kind': 'week8_real_visible_export_pixel_checks', 'started_at': datetime.now(timezone.utc).isoformat(),
                'synthetic_media_only': True, 'ffmpeg': args.ffmpeg, 'ffprobe': args.ffprobe,
                'font_sha256': digest(args.font_file), 'font_name': args.font_name,
                'source_example_sha256': hashlib.sha256(original).hexdigest(), 'cases': [], 'controls': [],
                'exact_text_visual_review': 'pending_contact_sheet_review'}
    code_paths = ['backend/clipforge/editing/render.py', 'backend/clipforge/editing/marking.py',
                  'backend/clipforge/editing/edl.py', 'backend/clipforge/editing/subtitles.py']
    evidence['implementation_sha256'] = {name: digest(ROOT / name) for name in code_paths}
    rows = []
    try:
        for background, level in [('dark', 16), ('light', 235)]:
            with_audio = sources / f'{background}-audio.mp4'
            source_video(with_audio, level, args.ffmpeg)
            for audio in (False, True):
                source_path = with_audio
                if not audio:
                    source_path = sources / f'{background}-silent.mp4'
                    run([args.ffmpeg, '-v', 'error', '-nostdin', '-n', '-i', str(with_audio),
                         '-map', '0:v:0', '-c', 'copy', '-an', str(source_path)])
                manifest = build_media_manifest(source_path, source_id=source_path.stem,
                                                ref=source_path.name, ffprobe=args.ffprobe, timeout=90)
                source = manifest['source']
                controls = decode(source_path, WIDTH, HEIGHT, args.ffmpeg)
                counts, _, _ = overlay_counts(controls)
                if len(controls) != SOURCE_FRAMES or not np.all(counts == 0):
                    raise AssertionError('unmarked control has an unexpected top-right overlay')
                evidence['controls'].append({'file': source_path.name, 'sha256': source['sha256'],
                                             'frames': len(controls), 'changed_roi_pixels': int(counts.max())})
                candidates = {'media': {'normalized_sha256': source['sha256'], 'time_reference': 'normalized_video',
                                         'has_audio': audio, 'duration_seconds': 10.0},
                              'candidates': [{'id': f'qa{i}', 'start': a / FPS, 'end': b / FPS,
                                              'source_sentences': []} for i, (a, b) in enumerate(RANGES)]}
                base = candidates_to_edl(candidates, ['qa0', 'qa1'], source, manifest['profile'])
                for shape in args.shape or ('landscape', 'portrait', 'fit_pad'):
                    project = base if shape == 'landscape' else create_portrait_edl(
                        base, width=360, height=640, mode='fit_pad' if shape == 'fit_pad' else 'center_crop')
                    for captions in ((False, True) if audio else (False,)):
                        case = f'{background}-{"audio" if audio else "silent"}-{shape}-{"subtitles" if captions else "plain"}'
                        print('Rendering ' + case, flush=True)
                        options = {}
                        if captions:
                            analysis = json.loads(original)
                            analysis['media']['normalized_sha256'] = source['sha256']
                            analyses = {source['id']: analysis}
                            plan = build_subtitles(project, analyses)
                            options = {'subtitle_plan': plan, 'analyses': analyses}
                        target = output / f'{case}.mp4'
                        report = render_edl(project, sources, target, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
                                            timeout=120, font_file=args.font_file, font_name=args.font_name, **options)
                        result, images = inspect_pixels(target, project, args.ffmpeg, args.ffprobe, shots)
                        result.update(background=background, captions=captions, shape=shape,
                                      render_verification=report, source_sha256=source['sha256'])
                        evidence['cases'].append(result)
                        rows.append(images)
                        write_json(output / f'{case}.edl.json', project)
                        if digest(source_path) != source['sha256']:
                            raise AssertionError('source bytes changed')
                        print(f'PASS {case}: {result["frames_checked"]} frames, ROI min={result["minimum_changed_roi_pixels"]}', flush=True)
        if (ROOT / 'docs/samples/analysis.example.json').read_bytes() != original:
            raise AssertionError('original analysis fixture changed')
        evidence.update(status='pixel_checks_passed', completed_at=datetime.now(timezone.utc).isoformat(),
                        total_decoded_output_frames=sum(case['frames_checked'] for case in evidence['cases']),
                        original_analysis_fixture_unchanged=True,
                        implementation_unchanged={name: digest(ROOT / name) for name in code_paths}
                        == evidence['implementation_sha256'])
    except Exception as error:
        evidence.update(status='failed', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        write_json(output / 'pixel-verification.json', evidence)
        if rows:
            contact_sheet(rows, output / 'contact-sheet.png')
            gallery = ['<!doctype html><meta charset="utf-8"><title>Visible export pixel QA</title>',
                       '<style>body{font-family:sans-serif}img{max-width:32%;height:auto}</style>',
                       '<h1>真实解码截图：每行按首帧、切点两侧、中间、末帧排序</h1>']
            for case in evidence['cases']:
                gallery.append('<h2>' + case['file'] + '</h2>')
                gallery.extend(f'<img src="{shot["file"]}" title="frame {shot["frame"]}">' for shot in case['screenshots'])
            (output / 'contact-sheet.html').write_text('\n'.join(gallery), encoding='utf-8')


if __name__ == '__main__':
    main()
