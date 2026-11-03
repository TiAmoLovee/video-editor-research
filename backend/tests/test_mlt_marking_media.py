"""Optional native MLT rendering; no GUI or third-party export claims."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from backend.tests.test_subtitles import edl_fixture, analysis_fixture
from clipforge.editing.edl import create_portrait_edl
from clipforge.editing.manifest import build_media_manifest
from clipforge.editing.mlt import export_mlt
from clipforge.editing.subtitles import build_subtitles

MELT = os.environ.get('MLT_MELT') or shutil.which('melt')
FFMPEG = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
FFPROBE = os.environ.get('FFPROBE') or shutil.which('ffprobe')
FONT = os.environ.get('MLT_TEST_FONT') or os.environ.get('SUBTITLE_FONT_NAME')


@unittest.skipUnless(MELT and FFMPEG and FFPROBE and FONT,
                     'native MLT/FFmpeg tools and MLT_TEST_FONT or SUBTITLE_FONT_NAME are required')
class MltMarkingMediaTests(unittest.TestCase):
    def test_native_plain_subtitle_portrait_and_single_frame_marking(self):
        retained = os.environ.get('MLT_TEST_ARTIFACTS')
        if retained:
            root = Path(retained).resolve()
            root.mkdir(parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name).resolve()
        environment = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_FONT_DPI': '96'}
        sources = {}
        for audio in (False, True):
            path = root / f'synthetic-{audio}.mp4'
            command = [FFMPEG, '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i',
                       'testsrc2=s=1280x720:r=30:d=1']
            if audio:
                command += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
                            '-c:a', 'aac', '-ac', '2']
            command += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-x264-params',
                        'colorprim=bt709:transfer=bt709:colormatrix=bt709', str(path)]
            subprocess.run(command, capture_output=True, check=True, timeout=30)
            sources[audio] = build_media_manifest(path, source_id='s0', ref=path.name,
                                                  ffprobe=FFPROBE, timeout=30)['source']
        results = []
        for name, portrait, with_subtitles, audio, single in (
                ('plain-landscape', False, False, True, False),
                ('subtitled-landscape', False, True, True, False),
                ('plain-portrait-silent', True, False, False, False),
                ('subtitled-portrait', True, True, True, False),
                ('plain-single-frame', False, False, False, True)):
            with self.subTest(case=name):
                source = sources[audio]
                ranges = [('s0', 29, 30)] if single else [('s0', 0, 8), ('s0', 15, 24)]
                project = edl_fixture([deepcopy(source)], ranges)
                if portrait:
                    project = create_portrait_edl(project, 720, 1280)
                options = {'font_name': FONT}
                if with_subtitles:
                    analyses = {'s0': analysis_fixture(source, [(.05, .2, '第一段', 0), (.55, .7, '第二段', 1)])}
                    plan = build_subtitles(project, analyses)
                    self.assertTrue(plan['cues'])
                    options.update(subtitle_plan=plan, analyses=analyses)
                before = deepcopy(project)
                xml = export_mlt(project, root, **options)
                self.assertEqual(project, before)
                parsed = ET.fromstring(xml)
                labels = parsed.findall("tractor/filter[@id='clipforge_brand']")
                self.assertEqual(len(labels), 1)
                self.assertEqual([(int(e.get('in')), int(e.get('out')) + 1)
                                  for e in parsed.findall("playlist[@id='playlist0']/entry")],
                                 [(a, b) for _, a, b in ranges])
                brand = labels[0]
                x, y, width, height = map(int, brand.find("property[@name='geometry']").text.split())
                actual_path = root / f'{name}.mlt'
                actual_path.write_text(xml, encoding='utf-8')
                # Removing the last qtext changes MLT's YUV/RGB conversion path.
                # Keep identical layout and conversion, but make only its ink
                # transparent, to isolate the watermark's pixel footprint.
                for key, value in (('fgcolour', '0xffffff00'), ('olcolour', '0x00000000'),
                                   ('bgcolour', '0x00000000')):
                    brand.find(f"property[@name='{key}']").text = value
                baseline_path = root / f'{name}-comparison-only.mlt'
                baseline_path.write_bytes(ET.tostring(parsed, encoding='utf-8', xml_declaration=True))
                arrays = []
                for kind, path in (('marked', actual_path), ('comparison', baseline_path)):
                    output = root / f'{name}-{kind}.mkv'
                    command = [MELT, str(path), '-consumer', 'avformat:' + str(output),
                               'vcodec=ffv1', 'pix_fmt=bgr0', 'an=1', 'real_time=-1', 'threads=1']
                    done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                          timeout=90, env=environment)
                    (root / f'{name}-{kind}.log').write_bytes(done.stderr)
                    self.assertEqual(done.returncode, 0, done.stderr[-1000:])
                    raw = subprocess.check_output([FFMPEG, '-v', 'error', '-i', str(output),
                                                   '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], timeout=30)
                    profile = project['profile']
                    frames = np.frombuffer(raw, np.uint8).reshape(-1, profile['height'], profile['width'], 3)
                    self.assertEqual(len(frames), project['duration_frames'])
                    arrays.append(frames)
                difference = np.abs(arrays[0].astype(np.int16) - arrays[1].astype(np.int16))
                region = difference[:, y:y + height, x:x + width]
                changed_pixels = (region.max(axis=3) > 8).sum(axis=(1, 2))
                self.assertTrue(np.all(changed_pixels >= 20), changed_pixels.tolist())
                outside = difference.copy()
                outside[:, y:y + height, x:x + width] = 0
                self.assertEqual(int(outside.max()), 0, 'watermark must not change pixels outside its rectangle')
                checked = sorted({0, max(0, len(arrays[0]) // 2), len(arrays[0]) - 1,
                                  *(c['timeline_in_frame'] for c in project['clips'])})
                for frame in checked:
                    subprocess.run([FFMPEG, '-v', 'error', '-i', str(root / f'{name}-marked.mkv'),
                                    '-vf', f'select=eq(n\\,{frame})', '-frames:v', '1',
                                    str(root / f'{name}-frame-{frame}.png')], capture_output=True, check=True, timeout=30)
                results.append({'case': name, 'frames': len(arrays[0]), 'source_ranges': [[a, b] for _, a, b in ranges],
                                'brand_count': 1, 'font_name': FONT, 'geometry': [x, y, width, height],
                                'all_frames_have_changed_mark_pixels': True,
                                'min_changed_pixels_in_mark': int(changed_pixels.min()),
                                'comparison': 'same qtext filter and conversion with transparent ink',
                                'max_rgb_error_outside_mark': int(outside.max()), 'saved_frame_indices': checked,
                                'mlt_sha256': hashlib.sha256(actual_path.read_bytes()).hexdigest()})
                del arrays, difference, region, outside
        self.assertEqual(len(results), 5, 'do not publish a passing summary for an incomplete matrix')
        (root / 'native-mlt-verification.json').write_text(json.dumps({
            'scope': 'native melt synthetic-video rendering; no GUI save/reopen or end-user export validation',
            'test_status': 'passed', 'cases': results, 'qt_font_dpi': 96,
            'audio_validation': 'video-only native consumer; source/EDL audio metadata retained, audio output not tested',
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
