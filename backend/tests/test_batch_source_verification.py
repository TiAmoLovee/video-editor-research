"""Batch reuse keeps actual source measurement, held locks and output checks."""
from contextlib import ExitStack
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from backend.tests.test_render import RenderHarness, edl_fixture, source_fixture
from backend.tests import test_manifest as manifest_fixtures
from backend.tests.test_manifest import frame_line
from clipforge.editing import render

class BatchSourceVerificationTests(unittest.TestCase):
    def test_one_batch_measures_source_once_and_each_output_in_full(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            data=edl_fixture([source_fixture(root)])
            harness=RenderHarness(data,root,root/'unused.mp4')
            with ExitStack() as stack:
                harness.install(stack)
                with render._BatchSourceVerification() as context:
                    reports=[render.render_edl(data,root,root/f'out-{i}.mp4',_batch_context=context) for i in range(3)]
                    self.assertEqual(len(harness.manifest_calls),4)
                    self.assertEqual(sum(options['source_id']=='rendered' for _,options in harness.manifest_calls),3)
                    self.assertTrue(all(options['_single_decode'] for _,options in harness.manifest_calls))
                    self.assertTrue(any(value>0 for value in harness.live.values()))
                    self.assertTrue(all(report['sources'][0]['source_ids']==['s0'] for report in reports))
                self.assertTrue(all(value==0 for value in harness.live.values()))
                self.assertEqual(context._records,{})
    def test_default_renders_do_not_reuse_a_previous_measurement(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();data=edl_fixture([source_fixture(root)])
            harness=RenderHarness(data,root,root/'unused.mp4')
            with ExitStack() as stack:
                harness.install(stack)
                for i in range(2):render.render_edl(data,root,root/f'normal-{i}.mp4')
            self.assertEqual(len(harness.manifest_calls),4)
            self.assertTrue(all('_single_decode' not in options for _,options in harness.manifest_calls))
    def test_inactive_closed_or_arbitrary_context_cannot_skip_measurement(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();data=edl_fixture([source_fixture(root)])
            inactive=render._BatchSourceVerification()
            with render._BatchSourceVerification() as closed:pass
            for context in (inactive,closed,{'manifest':'unverified snapshot'}):
                with self.subTest(context=type(context)),patch.object(render,'build_media_manifest') as measure:
                    with self.assertRaisesRegex(ValueError,'active measured'):
                        render.render_edl(data,root,root/'never.mp4',_batch_context=context)
                    measure.assert_not_called()
            with self.assertRaisesRegex(ValueError,'reopened'):closed.__enter__()
    def test_a_failed_first_measurement_is_never_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();data=edl_fixture([source_fixture(root)])
            harness=RenderHarness(data,root,root/'unused.mp4')
            with ExitStack() as stack:
                harness.install(stack)
                with render._BatchSourceVerification() as context:
                    with patch.object(render,'build_media_manifest',side_effect=ValueError('bad source')):
                        with self.assertRaisesRegex(ValueError,'bad source'):
                            render.render_edl(data,root,root/'failed.mp4',_batch_context=context)
                    self.assertEqual(context._records,{})
                    render.render_edl(data,root,root/'retry-explicit.mp4',_batch_context=context)
            self.assertEqual(len(harness.manifest_calls),2)
    def test_source_write_is_denied_or_rejected_before_cached_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();source=source_fixture(root);data=edl_fixture([source])
            harness=RenderHarness(data,root,root/'unused.mp4')
            with ExitStack() as stack:
                harness.install(stack)
                with render._BatchSourceVerification() as context:
                    render.render_edl(data,root,root/'one.mp4',_batch_context=context)
                    path=root/source['ref']
                    if os.name=='nt':
                        with self.assertRaises(OSError):path.write_bytes(b'changed')
                    else:
                        path.write_bytes(b'changed')
                        with self.assertRaisesRegex(ValueError,'source file changed|sha256'):
                            render.render_edl(data,root,root/'never.mp4',_batch_context=context)
                        self.assertFalse((root/'never.mp4').exists())
            path.write_bytes(b'released after batch')
    def test_source_replacement_is_denied_or_rejected_before_cached_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();source=source_fixture(root);data=edl_fixture([source])
            harness=RenderHarness(data,root,root/'unused.mp4')
            replacement=root/'replacement.mp4';replacement.write_bytes(b'new physical source')
            with ExitStack() as stack:
                harness.install(stack)
                with render._BatchSourceVerification() as context:
                    render.render_edl(data,root,root/'one.mp4',_batch_context=context)
                    path=root/source['ref']
                    if os.name=='nt':
                        with self.assertRaises(OSError):os.replace(replacement,path)
                    else:
                        os.replace(replacement,path)
                        with self.assertRaisesRegex(ValueError,'source file changed'):
                            render.render_edl(data,root,root/'never.mp4',_batch_context=context)
                        self.assertFalse((root/'never.mp4').exists())
    def test_new_context_remeasures_even_when_the_source_is_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();data=edl_fixture([source_fixture(root)])
            harness=RenderHarness(data,root,root/'unused.mp4')
            with ExitStack() as stack:
                harness.install(stack)
                for i in range(2):
                    with render._BatchSourceVerification() as context:
                        render.render_edl(data,root,root/f'batch-{i}.mp4',_batch_context=context)
            self.assertEqual(len(harness.manifest_calls),4)
    def test_batch_single_decode_still_validates_every_frame_and_pts(self):
        fixture=manifest_fixtures.ManifestTests()
        result,_,_,counter=fixture.run_fixture(_single_decode=True)
        self.assertEqual(result['source']['total_frames'],3)
        self.assertIsNone(counter)
        for lines in ([frame_line(0),frame_line(512),frame_line(1536)],
                      [frame_line(0),frame_line(512,width='640')],[]):
            with self.subTest(lines=lines),self.assertRaises(ValueError):
                fixture.run_fixture(lines=lines,_single_decode=True)

if __name__=='__main__':unittest.main()
