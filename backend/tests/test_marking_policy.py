"""Font identity, last-frame duration and cached marking evidence boundaries."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.tests.test_subtitle_render import font_fixture, inputs
from clipforge.compliance import EXPORT_TAGS, inspect_metadata
from clipforge.editing.marking import (
    font_fingerprint, marking_layout, prepare_watermark, validate_render_marking, watermark_ass,
)
from clipforge.editing import render
from clipforge.editing.subtitles import build_subtitles, to_ass
from clipforge.services import result_cache


class MarkingPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.font = self.root / 'font.ttf'
        self.font.write_bytes(font_fixture())
        env = patch.dict(os.environ, {'SUBTITLE_FONT_FILE': str(self.font), 'SUBTITLE_FONT_NAME': 'TestFont'})
        env.start()
        self.addCleanup(env.stop)

    def test_single_frame_and_fractional_fps_label_reaches_final_frame(self):
        for frames, fps, ending in ((1, {'num': 30, 'den': 1}, '0:00:00.04'),
                                    (31, {'num': 30000, 'den': 1001}, '0:00:01.04')):
            ass = watermark_ass({'width': 320, 'height': 180, 'fps': fps}, frames, 'TestFont')
            self.assertIn(f'Dialogue: 1,0:00:00.00,{ending},Brand,', ass)
            self.assertEqual(ass.count('AI辅助剪辑'), 1)
        with self.assertRaises(ValueError):
            marking_layout(2, 2)

    def test_tiny_canvas_cannot_claim_a_complete_visible_label(self):
        for width, height in ((14, 180), (160, 2), (160, 16)):
            profile = {'width': width, 'height': height, 'fps': {'num': 30, 'den': 1}}
            with self.subTest(width=width, height=height), ExitStack() as stack, \
                    patch.object(render, '_ass_state') as state:
                with self.assertRaisesRegex(ValueError, 'too small'):
                    prepare_watermark(stack, profile, 1, 'ffmpeg', __import__('time').monotonic() + 30)
                state.assert_not_called()
        self.assertEqual(marking_layout(160, 48)['brand_size'], 8)

    def test_new_subtitle_render_rejects_clipped_brand_and_preserves_saved_ass(self):
        for width, height in ((48, 48), (160, 2)):
            edl, analyses, _ = inputs(self.root)
            edl['profile'].update(width=width, height=height)
            edl['sources'][0].update(width=width, height=height)
            plan = build_subtitles(edl, analyses)
            saved_ass = to_ass(plan, font_name='TestFont')
            target = self.root / f'unpublished-{width}-{height}.mp4'
            with self.subTest(width=width, height=height), \
                    patch.object(render, '_ass_state') as state, \
                    patch.object(render, 'build_media_manifest') as measure:
                with self.assertRaisesRegex(ValueError, 'too small'):
                    render.render_edl(edl, self.root, target, subtitle_plan=plan, analyses=analyses)
                state.assert_not_called()
                measure.assert_not_called()
            self.assertFalse(target.exists())
            self.assertEqual(to_ass(plan, font_name='TestFont'), saved_ass)

    def test_small_supported_subtitle_canvas_keeps_existing_ass_bytes(self):
        edl, analyses, _ = inputs(self.root)
        edl['profile'].update(width=160, height=48)
        edl['sources'][0].update(width=160, height=48)
        plan = build_subtitles(edl, analyses)
        saved_ass = to_ass(plan, font_name='TestFont')
        with ExitStack() as stack, patch.object(render, '_ass_state', return_value={'report': {}}) as state:
            render._subtitle_state(stack, plan, edl, analyses, None, 'TestFont', 'ffmpeg', 30)
        self.assertEqual(state.call_args.args[1], saved_ass)
        self.assertEqual(to_ass(plan, font_name='TestFont'), saved_ass)

    def test_missing_font_or_brand_glyph_is_not_a_system_font_fallback(self):
        with patch.dict(os.environ, {'SUBTITLE_FONT_FILE': ''}):
            with self.assertRaisesRegex(ValueError, 'requires'):
                font_fingerprint()
        self.font.write_bytes(font_fixture('ABC'))
        with self.assertRaisesRegex(ValueError, 'glyph'):
            font_fingerprint()

    def test_new_unsubtitled_output_requires_libass(self):
        with ExitStack() as stack, patch.object(render, '_subtitle_tool', return_value=(' ... scale V->V\n', '')):
            with self.assertRaisesRegex(RuntimeError, 'libass'):
                prepare_watermark(stack, {'width': 320, 'height': 180, 'fps': {'num': 30, 'den': 1}},
                                  10, 'ffmpeg', __import__('time').monotonic() + 30)

    def test_font_bytes_change_cache_key_without_source_or_code_changes(self):
        source = self.root / 'source.mp4'
        source.write_bytes(b'input')
        with patch.object(result_cache, 'verify_model', return_value={'revision': 'fixed'}), \
             patch.object(result_cache, 'version', return_value='fixed'), \
             patch.object(result_cache.subprocess, 'run', return_value=SimpleNamespace(stdout='fixed')):
            first, descriptor = result_cache.fingerprint(source, None, 'ffmpeg', 'ffprobe')
            self.font.write_bytes(self.font.read_bytes() + b'\0')
            second, changed = result_cache.fingerprint(source, None, 'ffmpeg', 'ffprobe')
        self.assertNotEqual(first, second)
        self.assertNotEqual(descriptor['visible_marking']['font_sha256'], changed['visible_marking']['font_sha256'])

    def test_cache_cannot_restore_old_or_wrong_font_marking(self):
        expected = font_fingerprint()
        profile = {'width': 320, 'height': 180, 'fps': {'num': 30, 'den': 1}}
        record = {'visible': {**expected, 'duration_frames': 10, 'fps': profile['fps'],
                              'layout': marking_layout(320, 180), 'all_output_frames': True,
                              'verified_font_selection': True,
                              'ass_sha256': hashlib.sha256(watermark_ass(profile, 10, 'TestFont').encode()).hexdigest()},
                  'metadata': inspect_metadata(EXPORT_TAGS)}
        validate_render_marking(record, expected, 10)
        for key, value in [('font_sha256', '0' * 64), ('all_output_frames', False),
                           ('duration_frames', 9), ('ass_sha256', '0' * 64)]:
            bad = deepcopy(record)
            bad['visible'][key] = value
            with self.assertRaises(ValueError):
                validate_render_marking(bad, expected, 10)
        with self.assertRaises(ValueError):
            validate_render_marking(None, expected, 10)


if __name__ == '__main__':
    unittest.main()
