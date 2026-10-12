"""验证 API 镜像 COPY 的运行时文件能完成只读候选校验，不依赖开发机资源。"""
from contextlib import ExitStack
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from clipforge.decision import candidates, validation, scoring, llm
from clipforge.decision.selection import build_selection
from clipforge.editing import edl, mlt, subtitles
from backend.tests.test_candidates import fixture
from backend.tests.test_edl import source_fixture


class ApiPackageTests(unittest.TestCase):
    def test_portrait_and_subtitles_require_packaged_v02_schema(self):
        root = candidates.ROOT
        source = source_fixture(total_frames=930)
        analysis = fixture([(0, 10), (10, 20), (20, 30)])
        analysis['media']['normalized_sha256'] = source['sha256']
        generated = candidates.generate_candidates(analysis)
        project = edl.candidates_to_edl(generated, [generated['candidates'][0]['id']], source)
        self.assertIn("'edl-v0.2.schema.json'", (root / 'deploy/Dockerfile.api').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            packaged = Path(temp)
            shutil.copytree(root / 'schemas', packaged / 'schemas')
            for module in (edl, subtitles):
                stack.enter_context(patch.object(module, 'ROOT', packaged))
            portrait = edl.create_portrait_edl(project)
            plan = subtitles.build_subtitles(portrait, {source['id']: analysis})
            self.assertIn('PlayResX: 1080', subtitles.to_ass(plan))
            self.assertIn('PlayResY: 1920', subtitles.to_ass(plan))
            (packaged / 'schemas/edl-v0.2.schema.json').unlink()
            with self.assertRaises(FileNotFoundError):
                edl.validate_edl(portrait)
            with self.assertRaises(FileNotFoundError):
                edl.create_portrait_edl(project)
            # Removing the new resource must not break legacy revisions.
            edl.validate_edl(project)

    def test_subtitles_use_packaged_schemas_and_container_font_configuration(self):
        root = candidates.ROOT
        source = source_fixture(total_frames=930)
        analysis = fixture([(0, 10), (10, 20), (20, 30)])
        analysis['media']['normalized_sha256'] = source['sha256']
        generated = candidates.generate_candidates(analysis)
        project = edl.candidates_to_edl(generated, [generated['candidates'][0]['id']], source)
        dockerfile = (root / 'deploy/Dockerfile.api').read_text(encoding='utf-8')
        self.assertIn('fonts-noto-cjk', dockerfile)
        self.assertIn('NotoSansCJK-Regular.ttc', dockerfile)
        self.assertIn("'subtitles.schema.json'", dockerfile)
        with tempfile.TemporaryDirectory() as temp:
            packaged = Path(temp)
            shutil.copytree(root / 'schemas', packaged / 'schemas')
            with patch.object(subtitles, 'ROOT', packaged):
                plan = subtitles.build_subtitles(project, {source['id']: analysis})
                self.assertTrue(subtitles.to_srt(plan))
                self.assertIn('AI辅助剪辑', subtitles.to_ass(plan))
                (packaged / 'schemas/subtitles.schema.json').unlink()
                with self.assertRaises(FileNotFoundError):
                    subtitles.to_srt(plan)

    def test_readonly_selection_with_only_packaged_resources(self):
        root = candidates.ROOT
        analysis = fixture([(0, 10), (10, 20), (20, 30)])
        result = scoring.score_candidates(candidates.generate_candidates(analysis), analysis)
        dockerfile = (root / 'deploy/Dockerfile.api').read_text(encoding='utf-8')
        runtime = dockerfile.split('FROM python:', 1)[1]
        copies = re.findall(r'^COPY --chown=appuser:appuser (schemas|config/scoring|prompts) (/app/\S+)$', runtime, re.M)
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            packaged = Path(temp)
            for source, destination in copies:
                shutil.copytree(root / source, packaged / destination.removeprefix('/app/'))
            for module in (candidates, validation, scoring, llm):
                stack.enter_context(patch.object(module, 'ROOT', packaged))
            report = build_selection(result, analysis)
            self.assertEqual(report['summary']['original_count'], 3)
            self.assertEqual(report['model_requests'], 0)

    def test_edl_mlt_with_only_packaged_resources(self):
        root = candidates.ROOT
        source = source_fixture()
        analysis = fixture([(0, 10), (10, 20), (20, 30)])
        analysis['media']['normalized_sha256'] = source['sha256']
        dockerfile = (root / 'deploy/Dockerfile.api').read_text(encoding='utf-8')
        runtime = dockerfile.split('FROM python:', 1)[1]
        schema_check = next(line for line in runtime.splitlines() if line.startswith('RUN python -c'))
        self.assertIn("'edl.schema.json'", schema_check)
        copies = re.findall(r'^COPY --chown=appuser:appuser (schemas|config/scoring|prompts) (/app/\S+)$', runtime, re.M)
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            packaged = Path(temp)
            for resource, destination in copies:
                shutil.copytree(root / resource, packaged / destination.removeprefix('/app/'))
            for module in (candidates, validation, edl):
                stack.enter_context(patch.object(module, 'ROOT', packaged))
            generated = candidates.generate_candidates(analysis)
            validation.validate_candidates(generated, analysis)
            selected = [generated['candidates'][-1]['id'], generated['candidates'][0]['id']]
            project = edl.candidates_to_edl(generated, selected, source)
            media_root = packaged / 'media'
            media_root.mkdir()
            (media_root / source['ref']).touch()
            exported = ET.fromstring(mlt.export_mlt(project, media_root))
            entries = exported.findall("playlist[@id='playlist0']/entry")
            self.assertEqual([clip['provenance']['candidate_id'] for clip in project['clips']], selected)
            self.assertEqual([(int(entry.get('in')), int(entry.get('out'))) for entry in entries],
                             [(clip['source_in_frame'], clip['source_out_frame'] - 1)
                              for clip in project['clips']])
            self.assertEqual(exported.find("chain/property[@name='resource']").text,
                             (media_root / source['ref']).resolve().as_posix())
            # Both import and export must load the packaged EDL schema; an
            # unpatched global ROOT or a fallback to checkout resources fails here.
            (packaged / 'schemas/edl.schema.json').unlink()
            with self.assertRaises(FileNotFoundError):
                edl.candidates_to_edl(generated, selected, source)
            with self.assertRaises(FileNotFoundError):
                mlt.export_mlt(project, media_root)


if __name__ == '__main__':
    unittest.main()
