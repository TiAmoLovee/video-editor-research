"""Optional real FFmpeg batch test: independently decode every saved frame cut."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from datetime import datetime, timezone
from uuid import uuid4
from zipfile import ZipFile

from backend.tests import test_editing_service as fixtures
from backend.tests.test_candidates import fixture
from clipforge.decision.candidates import generate_candidates
from clipforge.decision.scoring import score_candidates
from clipforge.services import batch_export, editing, editing_changes

FFMPEG = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
FFPROBE = os.environ.get('FFPROBE') or shutil.which('ffprobe')


@unittest.skipUnless(FFMPEG and FFPROBE, 'set FFMPEG/FFPROBE to run the real media verification')
class BatchExportMediaTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)

    def test_three_edited_clips_have_exact_independent_frame_markers_and_audio(self):
        width, height, frame_count = 160, 48, 930
        # Each input frame has an independent grayscale marker. Source hashes
        # and output frame counts alone cannot establish the cut's content.
        def marker(index):
            return [224 if index & (1 << bit) else 32 for bit in range(10)]
        frames = b''.join(b''.join(bytes([value]) * (width // 10) * 3 for value in marker(index)) * height
                          for index in range(frame_count))
        source_path = self.folder / 'normalized.mp4'
        subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                        '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{width}x{height}',
                        '-framerate', '30', '-i', 'pipe:0', '-f', 'lavfi', '-i', 'sine=frequency=1000:sample_rate=48000:duration=31',
                        '-vf', 'setsar=1', '-c:v', 'libx264', '-crf', '0', '-pix_fmt', 'yuv420p',
                        '-x264-params', 'colorprim=bt709:transfer=bt709:colormatrix=bt709',
                        '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                        '-c:a', 'aac', '-shortest', str(source_path)],
                       input=frames, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        analysis = fixture([(0, 10), (10, 20), (20, 30)])
        analysis['media']['normalized_sha256'] = source_hash
        candidates = score_candidates(generate_candidates(analysis), analysis)
        for name, value in (('analysis.json', analysis), ('candidates.json', candidates)):
            (self.folder / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        ids = [candidate['id'] for candidate in candidates['candidates']]
        baseline = editing.create_task_edl(self.task_id, ids, ffprobe=FFPROBE, timeout=30)
        ranges = [(1, 9), (37, 48), (103, 112)]
        clips = [{'candidate_id': identifier, 'source_in_frame': start, 'source_out_frame': end}
                 for identifier, (start, end) in zip(ids, ranges, strict=True)]
        revision = editing_changes.create_task_edit(self.task_id, baseline['revision_id'], clips, editing_elapsed_ms=None)
        record = batch_export.create_task_batch_export(self.task_id, revision['revision_id'], str(uuid4()),
                                                       ffmpeg=FFMPEG, ffprobe=FFPROBE, timeout=90)
        saved = batch_export.load_task_batch_export(self.task_id, revision['revision_id'], record['export_id'])
        archive_path = self.folder / 'edits' / revision['revision_id'] / 'exports' / 'batch' / record['export_id'] / 'clips.zip'
        evidence = {'artifact_kind': 'week6_batch_real_media_acceptance', 'verified_at': datetime.now(timezone.utc).isoformat(),
                    'source_frame_count': frame_count, 'source_profile': {'width': width, 'height': height, 'fps': 30},
                    'source_sha256': source_hash, 'export': record, 'clips': [], 'marker_tolerance': 3}
        with ZipFile(archive_path) as archive:
            for item, (start, end) in zip(saved['manifest']['clips'], ranges, strict=True):
                path = self.folder / f"check-{item['index']}.mp4"
                path.write_bytes(archive.read(item['filename']))
                decoded = subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(path),
                                          '-map', '0:v:0', '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1'],
                                         check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20).stdout
                self.assertEqual(len(decoded), (end - start) * width * height)
                observed = [[decoded[index * width * height + (height // 2) * width + bit * (width // 10) + width // 20]
                             for bit in range(10)]
                            for index in range(end - start)]
                expected = [marker(index) for index in range(start, end)]
                errors = [max(abs(a - b) for a, b in zip(actual, wanted, strict=True))
                          for actual, wanted in zip(observed, expected, strict=True)]
                self.assertTrue(all(error <= 3 for error in errors),
                                f'wrong source content/order: {observed} vs {expected}')
                # Ten independent stripes encode the full source frame index.
                # A cut error flips at least one stripe by 192 levels, so the
                # three-level encoding tolerance cannot hide an off-by-one.
                inferred = [sum(int(value >= 128) << bit for bit, value in enumerate(actual)) for actual in observed]
                self.assertEqual(inferred, list(range(start, end)))
                self.assertEqual(item['frame_count'], end - start)
                self.assertEqual(item['verification']['output']['total_frames'], end - start)
                self.assertEqual(item['verification']['audio']['sample_rate'], 48000)
                self.assertEqual(item['verification']['audio']['pre_encode_total_samples'], (end - start) * 1600)
                self.assertNotEqual(item['source_out_frame'], item['original_out_frame'])
                facts = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-count_frames', '-show_streams',
                                                   '-of', 'json', str(path)], check=True, stdout=subprocess.PIPE,
                                                  stderr=subprocess.PIPE, timeout=20).stdout)
                self.assertEqual(int(next(stream for stream in facts['streams'] if stream['codec_type'] == 'video')['nb_read_frames']), end - start)
                self.assertTrue(any(stream['codec_type'] == 'audio' for stream in facts['streams']))
                evidence['clips'].append({'source_in_frame': start, 'source_out_frame': end,
                    'expected_frame_count': end - start, 'ffprobe_frame_count': end - start,
                    'decoded_source_frame_indices': inferred, 'expected_markers': expected, 'observed_markers': observed,
                    'maximum_marker_error': max(errors), 'source_cut_error_frames': 0,
                    'audio_sample_rate': 48000, 'audio_pre_encode_samples': (end - start) * 1600,
                    'clip_sha256': item['sha256']})
                path.unlink()
        self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), source_hash)
        self.assertEqual((self.folder / 'result.zip').read_bytes(), b'old ZIP unchanged')
        self.assertEqual(len(list(self.folder.rglob('normalized.mp4'))), 1)
        evidence.update(total_verified_frames=sum(end - start for start, end in ranges),
                        source_unchanged=True, legacy_zip_unchanged=True, normalized_media_copy_count=1,
                        all_frames_in_expected_order=True)
        evidence_dir = os.environ.get('CLIPFORGE_BATCH_EVIDENCE_DIR')
        if evidence_dir:
            target = Path(evidence_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / 'real-media-verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
            (target / 'manifest.json').write_text(json.dumps(saved['manifest'], ensure_ascii=False, indent=2), encoding='utf-8')
            shutil.copyfile(archive_path, target / 'verified-clips.zip')


if __name__ == '__main__':
    unittest.main()
