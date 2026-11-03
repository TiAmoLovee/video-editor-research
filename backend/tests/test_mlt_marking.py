"""New MLT has exactly one full-timeline label, independent of subtitle mode."""
from copy import deepcopy
import tempfile
import unittest
import xml.etree.ElementTree as ET

from backend.tests import test_mlt_effects as fixtures
from backend.tests.test_mlt_effects import props
from backend.tests.test_subtitles import source_fixture, edl_fixture
from clipforge.editing.mlt import export_mlt


class MltMarkingTests(unittest.TestCase):
    def test_plain_and_subtitled_projects_share_one_full_timeline_label(self):
        for portrait in (False, True):
            for silent in (False, True):
                project, analyses, plan = fixtures.MLTEffectTests().fixture(portrait=portrait, silent=silent)
                before = deepcopy((project, analyses, plan))
                with tempfile.TemporaryDirectory() as directory:
                    arguments = {'require_media': False, 'font_name': 'Noto Sans CJK SC'}
                    plain = ET.fromstring(export_mlt(project, directory, **arguments))
                    subtitled = ET.fromstring(export_mlt(project, directory, subtitle_plan=plan,
                                                         analyses=analyses, **arguments))
                for root in (plain, subtitled):
                    brand = root.findall("tractor/filter[@id='clipforge_brand']")
                    self.assertEqual(len(brand), 1)
                    self.assertEqual((brand[0].get('in'), brand[0].get('out')),
                                     ('0', str(project['duration_frames'] - 1)))
                    properties = props(brand[0])
                    self.assertEqual(properties['argument'], 'AI辅助剪辑')
                    self.assertEqual(properties['mlt_service'], 'qtext')
                    self.assertEqual((properties['halign'], properties['valign']), ('right', 'top'))
                    self.assertEqual([(int(e.get('in')), int(e.get('out')) + 1)
                                      for e in root.findall("playlist[@id='playlist0']/entry")],
                                     [(c['source_in_frame'], c['source_out_frame']) for c in project['clips']])
                self.assertEqual(props(plain.find("tractor/filter[@id='clipforge_brand']")),
                                 props(subtitled.find("tractor/filter[@id='clipforge_brand']")))
                self.assertEqual((project, analyses, plan), before)

    def test_legacy_plain_xml_is_only_available_via_recorded_policy(self):
        project, _, _ = fixtures.MLTEffectTests().fixture(silent=True)
        with tempfile.TemporaryDirectory() as directory:
            old = ET.fromstring(export_mlt(project, directory, require_media=False, _brand_policy_version=0))
            current = ET.fromstring(export_mlt(project, directory, require_media=False))
        self.assertFalse(old.findall('tractor/filter'))
        self.assertEqual(len(current.findall('tractor/filter')), 1)

    def test_single_frame_label_has_guard_and_tiny_unrenderable_profiles_fail(self):
        source = source_fixture(audio=False)
        project = edl_fixture([source], [('source', 2, 3)])
        with tempfile.TemporaryDirectory() as directory:
            root = ET.fromstring(export_mlt(project, directory, require_media=False))
            brand = root.find("tractor/filter[@id='clipforge_brand']")
            self.assertEqual((brand.get('in'), brand.get('out')), ('0', '0'))
            self.assertEqual(props(brand)['opacity'], '0=1;1=0')
            tiny = deepcopy(project)
            tiny['profile'].update(width=8, height=8)
            tiny['sources'][0].update(width=8, height=8)
            with self.assertRaises(ValueError):
                export_mlt(tiny, directory, require_media=False)

    def test_plain_fonts_and_policy_versions_are_validated(self):
        project, _, _ = fixtures.MLTEffectTests().fixture(silent=True)
        with tempfile.TemporaryDirectory() as directory:
            for name in ('', 'Bad,Font', 'Bad\\Font', None):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    export_mlt(project, directory, require_media=False, font_name=name)
            for version in (False, True, None, '1', 1.0, 2):
                with self.subTest(version=version), self.assertRaises(ValueError):
                    export_mlt(project, directory, require_media=False, _brand_policy_version=version)


if __name__ == '__main__':
    unittest.main()
