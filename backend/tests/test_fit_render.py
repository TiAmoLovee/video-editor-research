"""Full-frame fitting preserves native media, edit decisions and output subtitles."""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from backend.tests.test_portrait_render import PortraitHarness, native_source
from backend.tests.test_render import edl_fixture
from backend.tests.test_subtitles import analysis_fixture
from backend.tests.test_subtitle_render import font_fixture, good_log
from clipforge.editing import edl, render, subtitles, mlt


def properties(element):
    return {item.get('name'): item.text for item in element.findall('property')}


class FitRenderTests(unittest.TestCase):
    def fitted(self, root, *, audio=True, width=1920, height=1080):
        source = native_source(root, width=width, height=height, audio=audio)
        return edl.create_portrait_edl(edl_fixture([source]), mode='fit_pad')

    def run_render(self, project, root, **options):
        harness = PortraitHarness(project, root, root / 'fit.mp4')
        with ExitStack() as stack:
            harness.install(stack)
            report = render.render_edl(project, root, harness.target, **options)
        return report, harness

    def test_real_render_entrypoint_keeps_complete_picture_and_exact_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = self.fitted(root)
            before = deepcopy(project)
            report, harness = self.run_render(project, root)
            self.assertEqual(project, before)
            self.assertEqual(report['framing'], project['framing'])
            self.assertEqual(report['output']['total_frames'], 31)
            self.assertEqual(report['output']['profile'], project['profile'])
            self.assertEqual([row['source_in_frame'] for row in report['clips']], [30, 0])
            self.assertEqual(report['audio']['pre_encode_total_samples'], 31 * 1600)
            self.assertIn('scale=w=1080:h=608:', harness.graph)
            self.assertIn('format=yuv420p,setsar=1,pad=w=1080:h=1920:x=0:y=656:color=black[v0]', harness.graph)
            self.assertNotIn('crop=', harness.graph)
            self.assertEqual(sum('source_id' in call[1] for call in harness.manifest_calls), 2)

    def test_mixed_native_sizes_and_silence_use_each_saved_destination_rectangle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = native_source(root)
            second = native_source(root, 's1', width=1080, height=2160, audio=False)
            project = edl_fixture([first, second], [('s1', 60, 61), ('s0', 30, 60), ('s1', 0, 2)])
            project.update(schema_version='0.3.0', profile={'width': 1080, 'height': 1920,
                                                           'fps': first['fps']})
            project['framing'] = {'mode': 'fit_pad', 'background': 'black', 'sources': [
                {'source_id': 's0', 'x': 0, 'y': 656, 'width': 1080, 'height': 608},
                {'source_id': 's1', 'x': 60, 'y': 0, 'width': 960, 'height': 1920}]}
            report, harness = self.run_render(project, root)
            self.assertEqual(report['output']['total_frames'], 33)
            self.assertEqual([row['audio_silence'] for row in report['clips']], [True, False, True])
            self.assertEqual(harness.graph.count('scale=w=960:h=1920:'), 2)
            self.assertEqual(harness.graph.count('pad=w=1080:h=1920:x=60:y=0:color=black'), 2)
            self.assertIn('pad=w=1080:h=1920:x=0:y=656:color=black', harness.graph)

    def test_silent_native_portrait_keeps_full_canvas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = self.fitted(root, audio=False, width=1080, height=1920)
            report, harness = self.run_render(project, root)
            self.assertEqual(report['audio']['policy'], 'absent')
            self.assertIn('pad=w=1080:h=1920:x=0:y=0:color=black', harness.graph)

    def test_subtitles_burn_on_final_canvas_after_fit_and_concat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = self.fitted(root)
            analyses = {'s0': analysis_fixture(project['sources'][0])}
            plan = subtitles.build_subtitles(project, analyses)
            self.assertEqual(plan['policy'], subtitles.PORTRAIT_POLICY)
            font = root / 'font.ttf'
            font.write_bytes(font_fixture('AI辅助剪辑' + ''.join(cue['text'] for cue in plan['cues'])))
            harness = PortraitHarness(project, root, root / 'subtitled.mp4')

            def subtitle_tool(command, timeout, *, cwd=None):
                if '-filters' in command:
                    return ' ... ass V->V Render ASS subtitles\n', ''
                ass = Path(cwd, 'subtitles.ass').read_text(encoding='utf-8')
                self.assertIn('PlayResX: 1080\nPlayResY: 1920', ass)
                simulated = command.copy()
                simulated[simulated.index('-filter_complex_script') + 1] = str(Path(cwd, 'filters.txt'))
                simulated[-1] = str(Path(cwd, 'rendered.mp4'))
                harness.tool(simulated, timeout)
                return '', good_log()

            with ExitStack() as stack:
                harness.install(stack)
                stack.enter_context(patch.object(render, '_subtitle_tool', side_effect=subtitle_tool))
                report = render.render_edl(project, root, harness.target, subtitle_plan=plan,
                                           analyses=analyses, font_file=font, font_name='TestFont')
            self.assertLess(harness.graph.index('pad='), harness.graph.index('concat=n=2:v=1:a=0'))
            self.assertLess(harness.graph.index('concat=n=2:v=1:a=0'), harness.graph.index('[vout]ass='))
            self.assertTrue(report['checks']['subtitles_burned'])
            self.assertEqual(report['framing'], project['framing'])

    def test_mlt_affine_is_native_full_frame_and_cloned_for_backward_audio_seek(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = native_source(root)
            project = edl.create_portrait_edl(edl_fixture([source], [('s0', 30, 60), ('s0', 0, 30)]),
                                              mode='fit_pad')
            analyses = {'s0': analysis_fixture(project['sources'][0])}
            plan = subtitles.build_subtitles(project, analyses)
            document = ET.fromstring(mlt.export_mlt(project, root, require_media=False,
                                                   subtitle_plan=plan, analyses=analyses))
            chains = document.findall('chain')
            self.assertEqual(len(chains), 2)
            for chain in chains:
                effect = properties(chain.find('filter'))
                self.assertEqual(effect['mlt_service'], 'affine')
                self.assertEqual(effect['transition.rect'], '0 656 1080 608 1')
                self.assertEqual(effect['transition.b_scaled'], '0')
                self.assertEqual(effect['transition.distort'], '1')
                self.assertNotIn('left', effect)
            entries = document.findall("playlist[@id='playlist0']/entry")
            self.assertEqual([(row.get('in'), row.get('out')) for row in entries], [('30', '59'), ('0', '29')])
            self.assertNotEqual(entries[0].get('producer'), entries[1].get('producer'))
            # Literal subtitle filters belong to the composed output, not the source geometry.
            self.assertTrue(document.findall('tractor/filter'))
            self.assertEqual(len({effect.get('id') for effect in document.findall('.//filter')}),
                             len(document.findall('.//filter')))

    def test_fit_tampering_fails_before_media_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = self.fitted(root)
            project['framing']['sources'][0]['height'] = 606
            with patch.object(render, 'build_media_manifest') as tool, self.assertRaises(ValueError):
                render.render_edl(project, root, root / 'bad.mp4')
            tool.assert_not_called()

    def test_fit_export_requires_packaged_v03_schema_without_affecting_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = native_source(root)
            native = edl_fixture([source], [('s0', 30, 60)])
            package = root / 'package'
            shutil.copytree(edl.ROOT / 'schemas', package / 'schemas')
            with patch.object(edl, 'ROOT', package), patch.object(subtitles, 'ROOT', package):
                project = edl.create_portrait_edl(native, mode='fit_pad')
                analyses = {'s0': analysis_fixture(source)}
                plan = subtitles.build_subtitles(project, analyses)
                self.assertIn('affine', mlt.export_mlt(project, root, require_media=False,
                                                     subtitle_plan=plan, analyses=analyses))
                (package / 'schemas' / 'edl-v0.3.schema.json').unlink()
                with self.assertRaises(FileNotFoundError):
                    mlt.export_mlt(project, root, require_media=False)
                edl.validate_edl(native)
                edl.create_portrait_edl(native)


if __name__ == '__main__':
    unittest.main()
