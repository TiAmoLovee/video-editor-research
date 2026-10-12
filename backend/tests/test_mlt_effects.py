"""Saved native crop and literal, frame-bound subtitle export contracts."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from clipforge.editing.edl import create_portrait_edl
from clipforge.editing.mlt import export_mlt
from clipforge.editing.subtitles import build_subtitles, subtitle_layout
from backend.tests.test_subtitles import source_fixture, edl_fixture, analysis_fixture


def props(element):
    return {item.get('name'): item.text for item in element.findall('property')}


class MLTEffectTests(unittest.TestCase):
    def xml(self, project, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            return ET.fromstring(export_mlt(project, directory, require_media=False, **kwargs))

    def fixture(self, *, portrait=False, silent=False, fps=None):
        source = source_fixture(audio=not silent, fps=fps)
        project = edl_fixture([source], [('source', 30, 60), ('source', 0, 30), ('source', 30, 60)])
        if portrait:
            project = create_portrait_edl(project, 90, 160)
        analyses = {'source': analysis_fixture(source, [(.1, .6, '第一段', 0),
                                                       (1.1, 1.6, '中文 #timecode# <b>字</b> & "词"', 1)])}
        return project, analyses, build_subtitles(project, analyses)

    def test_native_crop_edges_and_output_profile_for_multiple_source_sizes(self):
        first, second = source_fixture(audio=False), source_fixture('other', audio=False)
        second.update(width=320, height=180)
        project = create_portrait_edl(edl_fixture([first], [('source', 299, 300)]), 90, 160)
        project['sources'].append(second)
        project['framing']['sources'].append({'source_id': 'other', 'x': 114, 'y': 10, 'width': 90, 'height': 160})
        root = self.xml(project)
        self.assertEqual((root.find('profile').get('width'), root.find('profile').get('height')), ('90', '160'))
        for chain, expected in zip(root.findall('chain'), [(62, 62, 12, 14), (114, 116, 10, 10)]):
            effect = chain.find('filter')
            p = props(effect)
            self.assertEqual([int(p[k]) for k in ('left', 'right', 'top', 'bottom')], list(expected))
            self.assertEqual({k: p[k] for k in ('active', 'center', 'use_profile')},
                             {'active': '0', 'center': '0', 'use_profile': '0'})
            self.assertEqual((effect.get('in'), effect.get('out')), ('0', '299'))
        entry = root.find("playlist[@id='playlist0']/entry")
        self.assertEqual((entry.get('in'), entry.get('out')), ('299', '299'))

    def test_reordered_repeated_cues_are_on_output_timeline_with_inclusive_out(self):
        project, analyses, plan = self.fixture(fps={'num': 30000, 'den': 1001})
        before = deepcopy((project, analyses, plan))
        root = self.xml(project, subtitle_plan=plan, analyses=analyses)
        self.assertEqual((project, analyses, plan), before)
        self.assertFalse(root.findall('chain/filter'))
        filters = root.findall('tractor/filter')
        self.assertEqual(len(filters), len(plan['cues']) + 1)
        for effect, cue in zip(filters, plan['cues']):
            self.assertEqual((int(effect.get('in')), int(effect.get('out'))),
                             (cue['timeline_in_frame'], cue['timeline_out_frame'] - 1))
            self.assertEqual(props(effect)['argument'], cue['text'])
        self.assertEqual(filters[-1].get('id'), 'clipforge_brand')
        self.assertEqual((filters[-1].get('in'), filters[-1].get('out')), ('0', '89'))

    def test_markup_and_metadata_tokens_stay_literal_with_independent_font_style(self):
        project, analyses, _ = self.fixture()
        analyses['source']['words'][1]['text'] += r' \text #frame#' + ' 😀'
        analyses['source']['sentences'][1]['text'] = analyses['source']['words'][1]['text']
        plan = build_subtitles(project, analyses)
        before = deepcopy((project, analyses, plan))
        family = 'Name " <>& ;color:red'
        root = self.xml(project, subtitle_plan=plan, analyses=analyses, font_name=family)
        self.assertEqual((project, analyses, plan), before)
        self.assertTrue(any('\n' in cue['text'] for cue in plan['cues']))
        for effect, cue in zip(root.findall('tractor/filter'), plan['cues']):
            p = props(effect)
            self.assertEqual(p['mlt_service'], 'qtext')
            self.assertEqual(p['shotcut:filter'], 'dynamicText')
            self.assertEqual(p['argument'], cue['text'])
            self.assertEqual(p['family'], family)
            self.assertEqual({key: p[key] for key in ('fgcolour', 'olcolour', 'halign', 'valign', 'outline', 'pad',
                                                     'typewriter', 'shotcut:usePointSize')},
                             {'fgcolour': '0xffffffff', 'olcolour': '0x00000000',
                              'halign': 'center', 'valign': 'bottom',
                              'outline': '0', 'pad': '0', 'typewriter': '0', 'shotcut:usePointSize': '1'})
            self.assertNotIn('html', p)
            self.assertNotIn('resource', p)
        brand = props(root.findall('tractor/filter')[-1])
        self.assertEqual((brand['argument'], brand['family'], brand['halign'], brand['valign']),
                         ('AI辅助剪辑', family, 'right', 'top'))

    def test_legacy_text_style_keeps_original_rich_text_and_single_line_box(self):
        project, analyses, plan = self.fixture()
        before = deepcopy((project, analyses, plan))
        root = self.xml(project, subtitle_plan=plan, analyses=analyses, text_style_version=1)
        effect = root.find("tractor/filter[@id='subtitle1']")
        original_html = ('<html><head><meta name="qrichtext" content="1" /></head><body>'
                         '<p align="center" style="margin:0px;line-height:12px;">'
                         '<font face="Noto Sans CJK SC">'
                         '<span style="font-size:8px;color:#ffffff;">第一段</span>'
                         '</font></p></body></html>')
        self.assertEqual([(p.get('name'), p.text) for p in effect.findall('property')],
                         [('mlt_service', 'qtext'), ('shotcut:filter', 'richText'),
                          ('shotcut:name', 'ClipForge 字幕'), ('argument', '第一段'),
                          ('html', original_html), ('geometry', '8 65 144 20'),
                          ('overflow-y', '0'), ('pixel_ratio', '1'), ('opacity', '1'),
                          ('bgcolour', '0x00000080'), ('halign', 'center'), ('valign', 'top'),
                          ('clipforge:cue_id', 'subtitle1')])
        self.assertEqual((project, analyses, plan), before)

    def test_explicit_version_two_matches_default_xml_bytes_and_invalid_versions_fail(self):
        project, analyses, plan = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            options = {'require_media': False, 'subtitle_plan': plan, 'analyses': analyses}
            self.assertEqual(export_mlt(project, directory, **options),
                             export_mlt(project, directory, text_style_version=2, **options))
        for version in (None, True, False, 0, 3, 1.0, 2.0, '1', '2'):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'integer 1 or 2'):
                self.xml(project, text_style_version=version)

    def test_font_size_rounds_down_to_stable_96_dpi_point_pixel_pairs(self):
        source = source_fixture()
        source.update(width=360, height=240)
        project = edl_fixture([source])
        analyses = {'source': analysis_fixture(source)}
        plan = build_subtitles(project, analyses)
        before = deepcopy((project, analyses, plan))
        # The shared plan requests 11px subtitles / 8px brand; only MLT sizes change.
        self.assertEqual((subtitle_layout(plan)['subtitle_size'], subtitle_layout(plan)['brand_size']), (11, 8))
        root = self.xml(project, subtitle_plan=plan, analyses=analyses)
        for effect in root.findall('tractor/filter')[:-1]:
            self.assertEqual((props(effect)['size'], props(effect)['shotcut:pointSize']), ('8', '6'))
        self.assertEqual((props(root.findall('tractor/filter')[-1])['size'],
                          props(root.findall('tractor/filter')[-1])['shotcut:pointSize']), ('8', '6'))
        self.assertEqual((project, analyses, plan), before)
        portrait = create_portrait_edl(project, 126, 224)
        portrait_plan = build_subtitles(portrait, analyses)
        self.assertEqual(subtitle_layout(portrait_plan)['brand_size'], 6)
        root = self.xml(portrait, subtitle_plan=portrait_plan, analyses=analyses)
        self.assertEqual((props(root.findall('tractor/filter')[-1])['size'],
                          props(root.findall('tractor/filter')[-1])['shotcut:pointSize']), ('4', '3'))

    def test_font_size_below_four_pixels_fails_without_changing_plan(self):
        source = source_fixture(audio=False)
        project = create_portrait_edl(edl_fixture([source]), 72, 128)
        analyses = {'source': analysis_fixture(source)}
        plan = build_subtitles(project, analyses)
        self.assertEqual(subtitle_layout(plan)['brand_size'], 3)
        before = deepcopy((project, analyses, plan))
        with self.assertRaisesRegex(ValueError, 'at least 4 pixels'):
            self.xml(project, subtitle_plan=plan, analyses=analyses)
        self.assertEqual((project, analyses, plan), before)

    def test_simple_text_utf16_limit_counts_newline_and_astral_characters(self):
        for text, units in [('字' * 255, 256), ('字' * 256, 257),
                            ('字' * 253 + '😀', 256), ('字' * 254 + '😀', 257)]:
            with self.subTest(units=units, astral='😀' in text):
                source = source_fixture()
                project = edl_fixture([source])
                analyses = {'source': analysis_fixture(source, [(1.1, 1.5, text, 0)])}
                plan = build_subtitles(project, analyses)
                cue = plan['cues'][0]
                self.assertEqual(len(cue['text'].encode('utf-16-le')) // 2, units)
                before = deepcopy((project, analyses, plan))
                if units == 256:
                    root = self.xml(project, subtitle_plan=plan, analyses=analyses)
                    self.assertEqual(props(root.find('tractor/filter'))['argument'], cue['text'])
                    self.assertEqual(cue['text'].replace('\n', ''), text)
                else:
                    with self.assertRaisesRegex(ValueError, '256 UTF-16-unit limit'):
                        self.xml(project, subtitle_plan=plan, analyses=analyses)
                self.assertEqual((project, analyses, plan), before)

    def test_portrait_panel_fits_saved_output_and_uses_shared_layout(self):
        project, analyses, plan = self.fixture(portrait=True)
        layout = subtitle_layout(plan)
        root = self.xml(project, subtitle_plan=plan, analyses=analyses)
        for effect in root.findall('tractor/filter'):
            x, y, width, height = map(int, props(effect)['geometry'].split())
            self.assertEqual(x, layout['margin_x'])
            self.assertGreaterEqual(y, layout['margin_y'])
            self.assertLessEqual(x + width, project['profile']['width'] - layout['margin_x'])
            self.assertLessEqual(y + height, project['profile']['height'] - layout['margin_y'])

    def test_single_and_two_line_cues_reserve_policy_box_with_same_bottom_margin(self):
        project, analyses, _ = self.fixture()
        analyses['source']['words'][1]['text'] += ' 加一行中文来验证布局'
        analyses['source']['sentences'][1]['text'] = analyses['source']['words'][1]['text']
        plan = build_subtitles(project, analyses)
        self.assertEqual({cue['text'].count('\n') + 1 for cue in plan['cues']}, {1, 2})
        root = self.xml(project, subtitle_plan=plan, analyses=analyses)
        filters = root.findall('tractor/filter')[:-1]
        self.assertEqual({props(effect)['geometry'] for effect in filters}, {'8 53 144 32'})
        self.assertEqual({props(effect)['valign'] for effect in filters}, {'bottom'})

    def test_silent_project_has_no_word_cues_but_keeps_brand(self):
        project, analyses, plan = self.fixture(portrait=True, silent=True)
        self.assertEqual(plan['cues'], [])
        root = self.xml(project, subtitle_plan=plan, analyses=analyses)
        self.assertEqual(len(root.findall('tractor/filter')), 1)
        self.assertEqual(props(root.find('chain'))['audio_index'], '-1')

    def test_first_frame_event_stops_despite_mlt_zero_out_sentinel(self):
        source = source_fixture()
        project = edl_fixture([source], [('source', 0, 2), ('source', 298, 300)])
        analyses = {'source': analysis_fixture(source, [(0, .02, '首帧', 0)])}
        plan = build_subtitles(project, analyses)
        self.assertEqual([(c['timeline_in_frame'], c['timeline_out_frame']) for c in plan['cues']], [(0, 1)])
        root = self.xml(project, subtitle_plan=plan, analyses=analyses)
        first = root.find('tractor/filter')
        self.assertEqual((first.get('in'), first.get('out')), ('0', '0'))
        self.assertEqual(props(first)['opacity'], '0=1;1=0')
        # A one-frame whole project must also guard its persistent AI label.
        silent = source_fixture(audio=False)
        project = edl_fixture([silent], [('source', 0, 1)])
        silent_analysis = {'source': analysis_fixture(silent)}
        plan = build_subtitles(project, silent_analysis)
        root = self.xml(project, subtitle_plan=plan, analyses=silent_analysis)
        self.assertEqual(props(root.findall('tractor/filter')[-1])['opacity'], '0=1;1=0')

    def test_audio_single_frame_fails_closed_but_silent_frame_remains_supported(self):
        for start in (0, 238, 299):
            source = source_fixture()
            project = edl_fixture([source], [('source', start, start + 1)])
            with self.subTest(start=start), self.assertRaisesRegex(ValueError, 'single-frame clip.*MP4 renderer'):
                self.xml(project)
            source['audio_stream_index'] = None
            project = edl_fixture([source], [('source', start, start + 1)])
            self.assertIsNotNone(self.xml(project).find('playlist/entry'))

    def test_changed_plan_or_original_words_and_orphan_analysis_rejected(self):
        project, analyses, plan = self.fixture()
        changed = deepcopy(plan)
        changed['cues'][0]['timeline_out_frame'] -= 1
        with self.assertRaises(ValueError):
            self.xml(project, subtitle_plan=changed, analyses=analyses)
        changed_analysis = deepcopy(analyses)
        changed_analysis['source']['words'][0]['text'] = 'different'
        with self.assertRaises(ValueError):
            self.xml(project, subtitle_plan=plan, analyses=changed_analysis)
        with self.assertRaises(ValueError):
            self.xml(project, subtitle_plan=plan)
        with self.assertRaisesRegex(ValueError, 'require a subtitle plan'):
            self.xml(project, analyses=analyses)

    def test_bad_crop_and_control_font_fail_before_creating_graph(self):
        project, analyses, plan = self.fixture(portrait=True)
        for family in ('bad\nfont', 'bad,CSV', r'bad\font', ''):
            with self.subTest(family=family), self.assertRaises(ValueError):
                self.xml(project, subtitle_plan=plan, analyses=analyses, font_name=family)
        project['framing']['sources'][0]['x'] += 2
        with self.assertRaises(ValueError):
            self.xml(project)

    def test_cli_saved_plan_and_analysis_pairs_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = source_fixture()
            source['sha256'] = hashlib.sha256(b'fixture').hexdigest()
            (folder / source['ref']).write_bytes(b'fixture')
            project = create_portrait_edl(edl_fixture([source]), 90, 160)
            analysis = analysis_fixture(source)
            plan = build_subtitles(project, {'source': analysis})
            for name, value in [('project.edl.json', project), ('subtitles.json', plan), ('analysis.json', analysis)]:
                (folder / name).write_text(json.dumps(value), encoding='utf-8')
            output = folder / 'project.mlt'
            command = [sys.executable, '-m', 'clipforge.editing.mlt', str(folder / 'project.edl.json'),
                       '--source-root', str(folder), '--output', str(output),
                       '--subtitles', str(folder / 'subtitles.json'),
                       '--analysis', 'source=' + str(folder / 'analysis.json')]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            before = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(before, output.read_bytes())
            command[command.index('--output') + 1] = str(folder / 'invalid.mlt')
            command += ['--analysis', 'source=' + str(folder / 'analysis.json')]
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertFalse((folder / 'invalid.mlt').exists())


if __name__ == '__main__':
    unittest.main()
